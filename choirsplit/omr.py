"""PDF / image input via optical music recognition (OMR).

The default engine is Audiveris (https://audiveris.github.io), run as a subprocess in
batch mode; it reads PDFs and images and exports MusicXML (.mxl). Install it user-locally
with ``scripts/install_omr.sh``. Engine discovery, in order:

1. ``CHOIRSPLIT_OMR_CMD``   - any OMR command line, as a template. ``{input}`` is replaced
   by the score file and ``{outdir}`` by a directory the command must write .mxl /
   .musicxml files into. Use ``{image}`` instead of ``{input}`` for engines that only
   read images: PDFs are then rasterised page by page (pdftoppm or PyMuPDF) and the
   command runs once per page, e.g. ``CHOIRSPLIT_OMR_CMD='oemer -o {outdir} {image}'``.
2. ``CHOIRSPLIT_AUDIVERIS`` - the Audiveris launcher, or its install directory.
3. ``Audiveris`` / ``audiveris`` on PATH.
4. ``$CHOIRSPLIT_HOME/audiveris`` (default ``~/.local/share/choirsplit`` on Linux, where
   the install script puts it), then where the official installers put it:
   ``/opt/audiveris`` (.deb), ``C:\\Program Files\\Audiveris`` (.msi),
   ``/Applications/Audiveris.app`` (.dmg).

Other settings: ``CHOIRSPLIT_OMR_TIMEOUT`` (seconds; default scales with the page count),
``CHOIRSPLIT_OMR_CACHE`` (cache directory, or ``0`` to disable; default
``omr`` in choirsplit's cache directory, see ``platforms.cache_dir``).

OMR output is messy in ways that matter for per-voice practice tracks, so the
recognised MusicXML is cleaned up before it is handed on:

* Parts are re-collated by staff position. Audiveris links parts across systems by
  their printed names, so "Soprano 1" on the first system and "S1" (or the OCR misread
  "81") later become different parts, each only visible in some systems. Here the k-th
  visible staff group of every system/page/movement is the k-th voice; names are taken
  from the most complete label seen.
* Measures are put on one shared time grid and over-full measures (misread rhythms)
  are clipped, so one misread note cannot shift a voice out of sync for the rest of
  the piece.
* Spurious voices (e.g. a whole-bar rest in voice 1 plus the notes in voice 2) are
  merged back into one line; real two-voices-per-staff writing is kept.
* If no staff carries a name, staves are named by layout (S/A/T/B, SSAATTBB, ...).
* Stray notes next to a sung staff's printed whole-bar rest are dropped, and missed
  augmentation dots are restored where note positions line up with the other staves.
* A time signature inside a system that OMR read as notes is removed again, and the
  bars of its meter get the length the voices sing (see `drop_misread_time_signatures`).
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from music21 import clef, converter, instrument, layout, meter, note, stream, text, tie

from . import platforms
from .check import Issue, voice_range
from .score import DUPLICATE_LINE_SHARE, NoteEvent, _differing_share, is_accompaniment

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
MUSICXML_SUFFIXES = {".mxl", ".musicxml", ".xml"}

# Names Audiveris (and other engines) give parts when no name was recognised.
DEFAULT_NAME_RE = re.compile(r"^\s*(|voice|noname|piano|p\d+|part\s*\d*|staff\s*\d*|music\s*\d*)\s*$", re.I)



def _system_locations() -> list[Path]:
    """Where the official Audiveris installers put it (.msi, .dmg, .deb)."""
    if platforms.is_windows():
        return [d / "Audiveris" for d in platforms.program_dirs()]
    if platforms.is_macos():
        return [d / "Audiveris.app" for d in platforms.program_dirs()]
    return [Path("/opt/audiveris")]


SYSTEM_LOCATIONS = _system_locations()
POLYPHONY_THRESHOLD = 0.15  # fraction of sounding time where two voices overlap
CACHE_VERSION = "1"  # bump when the raw engine invocation changes
# Audiveris setting for the resolution PDF pages are rendered at (its default is 300 dpi).
AUDIVERIS_PDF_RESOLUTION = "org.audiveris.omr.image.ImageLoading.pdfResolution"
# A second reading at this resolution often sees note heads the default one misses
# (Mendelssohn's Psalm 100: 5 empty sung bars at 250 dpi against 14 at 300 and 26 at 400).
SECOND_READING_DPI = 250
AUDIVERIS_DEFAULT_DPI = 300
# Audiveris drops every page with more pixels than its limit (20 million): a poster-sized
# PDF page at 300 dpi. Such PDFs are read again at a resolution that fits.
TOO_LARGE_IMAGE = re.compile(r"Too large image: ([\d,.]+) pixels \(vs ([\d,.]+) max\)")
# A page with no staves (a title, notes or lyrics page) fails, and with it the whole export.
# Such PDFs are read again without those pages.
NO_STAVES = re.compile(r"#(\d+)\]\s.*No regularly spaced lines found")
_EPS = 1e-6
# Lines that bury Audiveris's own error in its output: Tesseract prints one "Estimating
# resolution" per text block, and Java stack frames push out the exception message.
_NOISE = re.compile(r"^\s*(Estimating resolution as \d+|at [\w$.<>/]+\(.*\)|\.\.\. \d+ more)\s*$")


def _error(msg: str) -> Exception:
    from .loaders import ScoreLoadError

    return ScoreLoadError(msg)


# --------------------------------------------------------------------------- engine discovery


@dataclass
class Engine:
    name: str  # "audiveris" or "custom"
    argv: list[str]  # executable (audiveris) or command template tokens (custom)
    version: str = "unknown"
    per_page: bool = False  # custom template uses {image}: run once per rasterised page
    env: dict[str, str] = field(default_factory=dict)

    @property
    def cache_tag(self) -> str:
        return f"{self.name}|{self.version}|{' '.join(self.argv)}|{CACHE_VERSION}"


def data_home() -> Path:
    return platforms.data_dir()


def install_hint() -> str:
    return (
        "PDF and image scores need an optical music recognition engine (Audiveris), "
        "and none was found.\n"
        "  Windows: the 'windowsConsole' .msi installer, macOS: the .dmg, both from\n"
        "      https://github.com/Audiveris/audiveris/releases\n"
        "  Linux: scripts/install_omr.sh (no root needed, about 210 MB)\n"
        "Or point CHOIRSPLIT_AUDIVERIS at an existing Audiveris program or install folder, "
        "or set CHOIRSPLIT_OMR_CMD to another OMR "
        "command such as 'oemer -o {outdir} {image}'.\n"
        "Alternatively export MusicXML from your notation program and use that file."
    )


# Launcher locations inside an install folder: Linux (.deb / install script), Windows (.msi),
# macOS (.app bundle).
_LAUNCHERS = ("bin/Audiveris", "Audiveris", "bin/audiveris", "Audiveris.exe", "Contents/MacOS/Audiveris")


def _audiveris_from(path: Path) -> Path | None:
    if path.is_dir():
        for cand in (path / rel for rel in _LAUNCHERS):
            if cand.is_file() and os.access(cand, os.X_OK):
                return cand
        return None
    return path if path.is_file() and os.access(path, os.X_OK) else None


def _audiveris_version(exe: Path) -> str:
    here = exe.resolve().parent
    # Linux: bin/../lib/app, Windows: app next to Audiveris.exe, macOS: Contents/MacOS/../app.
    for cfg in (here.parent / "lib" / "app" / "Audiveris.cfg", here / "app" / "Audiveris.cfg",
                here.parent / "app" / "Audiveris.cfg"):
        try:
            m = re.search(r"app-version=([\w.\-]+)", cfg.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if m:
            return m.group(1)
    try:
        return str(int(exe.stat().st_mtime))
    except OSError:
        return "unknown"


def _audiveris_engine(exe: Path) -> Engine:
    env = {}
    tessdata = data_home() / "tessdata"
    if not os.environ.get("TESSDATA_PREFIX") and tessdata.is_dir():
        env["TESSDATA_PREFIX"] = str(tessdata)
    return Engine("audiveris", [str(exe)], _audiveris_version(exe), env=env)


def split_command(command: str) -> list[str]:
    """Split a command line into arguments; on Windows, backslashes in paths are kept."""
    if not platforms.is_windows():
        return shlex.split(command)
    tokens = shlex.split(command, posix=False)
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in tokens]


def find_engine() -> Engine | None:
    """Locate an OMR engine (see module docstring for the search order)."""
    custom = os.environ.get("CHOIRSPLIT_OMR_CMD", "").strip()
    if custom:
        tokens = split_command(custom)
        joined = " ".join(tokens)
        if "{image}" not in joined and "{input}" not in joined:
            raise _error("CHOIRSPLIT_OMR_CMD must contain {input} or {image} (and usually {outdir})")
        return Engine("custom", tokens, per_page="{image}" in joined)

    explicit = os.environ.get("CHOIRSPLIT_AUDIVERIS", "").strip()
    if explicit:
        exe = _audiveris_from(Path(explicit).expanduser())
        if exe is None:
            raise _error(f"CHOIRSPLIT_AUDIVERIS={explicit} is not an Audiveris launcher or install directory")
        return _audiveris_engine(exe)

    for name in ("Audiveris", "audiveris"):
        found = shutil.which(name)
        if found:
            return _audiveris_engine(Path(found))

    for base in (data_home() / "audiveris", *SYSTEM_LOCATIONS):
        exe = _audiveris_from(base)
        if exe is not None:
            return _audiveris_engine(exe)
    return None


# --------------------------------------------------------------------------- running the engine


def pdf_page_count(path: Path) -> int | None:
    if shutil.which("pdfinfo"):
        try:
            out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=60).stdout
            m = re.search(r"^Pages:\s+(\d+)", out, re.M)
            if m:
                return int(m.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import fitz  # PyMuPDF

        with fitz.open(str(path)) as doc:
            return doc.page_count
    except Exception:
        return None


def default_timeout(path: Path) -> float:
    env = os.environ.get("CHOIRSPLIT_OMR_TIMEOUT", "").strip()
    if env:
        try:
            return float(env)
        except ValueError:
            raise _error(f"CHOIRSPLIT_OMR_TIMEOUT must be a number of seconds, not {env!r}") from None
    pages = 1 if path.suffix.lower() in IMAGE_SUFFIXES else (pdf_page_count(path) or 20)
    return 120.0 + 120.0 * pages


def rasterize_pdf(path: Path, outdir: Path, dpi: int = 300) -> list[Path]:
    """Render every PDF page to PNG, in page order."""
    outdir.mkdir(parents=True, exist_ok=True)
    if shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(path), str(outdir / "page")],
                       check=True, capture_output=True, timeout=600)
    else:
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise _error("rasterising a PDF needs pdftoppm (poppler-utils) or PyMuPDF") from None
        with fitz.open(str(path)) as doc:
            for i, page in enumerate(doc, 1):
                page.get_pixmap(dpi=dpi).save(str(outdir / f"page-{i:04d}.png"))
    return sorted(outdir.glob("page*.png"), key=natural_key)


def natural_key(p: Path | str) -> list:
    """Sort key putting 'x.mvt2.mxl' before 'x.mvt10.mxl'."""
    name = p.name if isinstance(p, Path) else str(p)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def collect_outputs(outdir: Path) -> list[Path]:
    files = [p for p in outdir.rglob("*") if p.is_file() and p.suffix.lower() in MUSICXML_SUFFIXES
             and not p.name.startswith(".") and p.parent.name != "META-INF"]
    # Order by directory (page runs) first, then natural file-name order (movements).
    return sorted(files, key=lambda p: (natural_key(str(p.parent.relative_to(outdir))), natural_key(p)))


def _tail(text_: str, lines: int = 15) -> str:
    keep = [ln for ln in text_.strip().splitlines()
            if ln.strip() and not _NOISE.match(ln)][-lines:]
    return "\n".join("    " + ln for ln in keep)


def _run(argv: list[str], timeout: float, env: dict[str, str], what: str) -> None:
    full_env = {**os.environ, **env}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=timeout, env=full_env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise _error(f"{what} timed out after {timeout:.0f} s "
                     "(set CHOIRSPLIT_OMR_TIMEOUT to allow more time)") from None
    except OSError as exc:
        raise _error(f"could not run {what} ({argv[0]}): {exc}") from exc
    # stdout last: it carries Audiveris's errors, which the tail of the output must keep.
    _run.last_output = proc.stderr + proc.stdout  # for error messages and retries
    if proc.returncode != 0:
        raise _error(f"{what} failed with exit code {proc.returncode}:\n{_tail(_run.last_output)}")


_run.last_output = ""


def fitting_dpi(output: str, dpi: int | None) -> int | None:
    """The resolution at which the largest page Audiveris refused as too large (see
    TOO_LARGE_IMAGE in its `output`) fits its pixel limit; None if it refused none."""
    sizes = [(int(re.sub(r"\D", "", px)), int(re.sub(r"\D", "", top)))
             for px, top in TOO_LARGE_IMAGE.findall(output)]
    if not sizes:
        return None
    pixels, limit = max(sizes)
    return int((dpi or AUDIVERIS_DEFAULT_DPI) * (limit / pixels) ** 0.5 * 0.99)


def _run_audiveris(engine: Engine, path: Path, outdir: Path, timeout: float,
                   dpi: int | None) -> int | None:
    """Run Audiveris on `path`; returns the resolution the PDF was read at (None: its
    default). Pages too large for Audiveris are read again once at a resolution that fits,
    and a PDF with pages that have no staves (notes, lyrics) again without those pages."""
    from .loaders import ScoreLoadError

    is_pdf = path.suffix.lower() == ".pdf"
    skipped: set[int] = set()
    pages = 0  # the PDF's page count, once pages are skipped
    resized = False
    while True:
        options = ["-constant", f"{AUDIVERIS_PDF_RESOLUTION}={dpi}"] if dpi is not None and is_pdf else []
        if skipped:
            options += ["-sheets", *_sheet_ranges(sorted(set(range(1, pages + 1)) - skipped))]
        argv = [*engine.argv, "-batch", "-export", *options, "-output", str(outdir), "--", str(path)]
        try:
            _run(argv, timeout, engine.env, "Audiveris")
            failure = None
        except ScoreLoadError as exc:
            failure = exc
        smaller = fitting_dpi(_run.last_output, dpi)
        if smaller is None:
            if failure is None:
                return dpi
            staffless = {int(n) for n in NO_STAVES.findall(_run.last_output)} - skipped
            if is_pdf and staffless and not pages:
                pages = pdf_page_count(path) or 0
            if not staffless or not set(range(1, pages + 1)) - skipped - staffless:
                raise failure
            skipped |= staffless
            print(f"choirsplit: {path.name}: no staves found on page{'s' if len(skipped) > 1 else ''} "
                  f"{', '.join(map(str, sorted(skipped)))}; reading the other pages", file=sys.stderr)
            shutil.rmtree(outdir, ignore_errors=True)
            outdir.mkdir(parents=True)
            continue
        if not is_pdf or resized or smaller < 50:
            m = TOO_LARGE_IMAGE.search(_run.last_output)
            raise _error(f"{path.name} is too large for Audiveris ({m.group(1)} pixels, at most "
                         f"{m.group(2)}); scale it down and try again")
        print(f"choirsplit: the pages of {path.name} are too large for Audiveris at "
              f"{dpi or AUDIVERIS_DEFAULT_DPI} dpi; reading them at {smaller} dpi", file=sys.stderr)
        dpi, resized = smaller, True
        shutil.rmtree(outdir, ignore_errors=True)
        outdir.mkdir(parents=True)


def _sheet_ranges(numbers: list[int]) -> list[str]:
    """Audiveris's -sheets arguments for sorted page `numbers`: [1, 2, 3, 5] -> ["1-3", "5"]."""
    runs: list[list[int]] = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return [f"{a}-{b}" if b > a else str(a) for a, b in runs]


def run_engine(engine: Engine, path: Path, outdir: Path, timeout: float | None = None,
               dpi: int | None = None) -> tuple[list[Path], int | None]:
    """Run OMR on `path`, return the produced MusicXML files in score order and the
    resolution a PDF was read at. `dpi` is the resolution to render a PDF at (default:
    Audiveris's own, 300 for {image} engines); Audiveris may need a lower one."""
    timeout = default_timeout(path) if timeout is None else timeout
    outdir.mkdir(parents=True, exist_ok=True)
    if engine.name == "audiveris":
        dpi = _run_audiveris(engine, path, outdir, timeout, dpi)
    else:
        if engine.per_page and path.suffix.lower() == ".pdf":
            pages = rasterize_pdf(path, outdir / "_pages", dpi=dpi or 300)
            if not pages:
                raise _error(f"{path.name}: no pages could be rendered")
        else:
            pages = [path]
        per_page_timeout = timeout / max(len(pages), 1) if len(pages) > 1 else timeout
        for i, page in enumerate(pages, 1):
            page_out = outdir / f"page-{i:04d}" if len(pages) > 1 else outdir
            page_out.mkdir(parents=True, exist_ok=True)
            argv = [t.replace("{input}", str(page)).replace("{image}", str(page))
                     .replace("{outdir}", str(page_out)) for t in engine.argv]
            _run(argv, max(per_page_timeout, 60.0), engine.env, f"OMR command ({engine.argv[0]})")
        shutil.rmtree(outdir / "_pages", ignore_errors=True)
    files = collect_outputs(outdir)
    if not files:
        raise _error(
            f"OMR produced no MusicXML for {path.name}; the scan may be too blurry, skewed or "
            f"low-resolution (300 dpi recommended).\nEngine output:\n{_tail(_run.last_output)}"
        )
    return files, dpi


# --------------------------------------------------------------------------- cache


def _cache_root() -> Path | None:
    env = os.environ.get("CHOIRSPLIT_OMR_CACHE")
    if env is not None and env.strip().lower() in {"0", "off", "no", "false", ""}:
        return None
    if env:
        return Path(env).expanduser()
    return platforms.cache_dir() / "omr"


def cache_key(path: Path, engine: Engine, dpi: int | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    h.update(engine.cache_tag.encode())
    if dpi is not None:  # (the default keeps the keys of existing cache entries)
        h.update(f"|dpi={dpi}".encode())
    return h.hexdigest()[:32]


def recognise(path: Path, engine: Engine, dpi: int | None = None) -> tuple[list[stream.Score], int | None]:
    """Run OMR on `path` (or reuse the cached result) and parse the MusicXML it produced.
    Also returns the resolution the PDF was read at (None: the engine's default)."""
    root = _cache_root()
    entry = root / cache_key(path, engine, dpi) if root else None
    if entry is not None and (entry / "done").exists():
        files = collect_outputs(entry)
        if files:
            stamp = (entry / "done").read_text(encoding="utf-8").split("\n")
            read_at = stamp[2] if len(stamp) > 2 else ""
            return parse_outputs(files), int(read_at) if read_at.isdigit() else dpi
    with tempfile.TemporaryDirectory(prefix="choirsplit-omr-") as tmp:
        files, read_at = run_engine(engine, path, Path(tmp) / "out", dpi=dpi)
        scores = parse_outputs(files)
        if entry is not None:
            try:
                _store(entry, files, f"{path.name}\n{engine.cache_tag}\n{read_at or ''}\n")
            except OSError as exc:  # a read-only cache must not break loading
                print(f"choirsplit: could not cache OMR result: {exc}", file=sys.stderr)
    return scores, read_at


def _store(entry: Path, files: list[Path], stamp: str) -> None:
    staging = entry.with_name(entry.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for i, f in enumerate(files, 1):
        shutil.copy2(f, staging / f"{i:04d}-{f.name}")
    (staging / "done").write_text(stamp, encoding="utf-8")
    shutil.rmtree(entry, ignore_errors=True)
    staging.rename(entry)


def parse_outputs(files: list[Path]) -> list[stream.Score]:
    scores = []
    for f in files:
        try:
            parsed = converter.parse(str(f), format="musicxml")
        except Exception as exc:
            raise _error(f"could not parse OMR output {f.name}: {exc}") from exc
        if isinstance(parsed, stream.Opus):
            scores.extend(parsed.scores)
        elif isinstance(parsed, stream.Score):
            scores.append(parsed)
        elif isinstance(parsed, stream.Part):
            wrapper = stream.Score()
            wrapper.insert(0, parsed)
            scores.append(wrapper)
    return [s for s in scores if s.parts]


# --------------------------------------------------------------------------- loading


def omr_dpi(dpi: int | None = None) -> int | None:
    """The resolution to recognise PDFs at: `dpi`, else $CHOIRSPLIT_OMR_DPI, else None
    (the engine's default)."""
    if dpi is not None:
        return dpi
    env = os.environ.get("CHOIRSPLIT_OMR_DPI", "").strip()
    if not env:
        return None
    try:
        value = int(env)
    except ValueError:
        raise _error(f"CHOIRSPLIT_OMR_DPI must be a whole number, got {env!r}") from None
    if not 100 <= value <= 1200:
        raise _error(f"CHOIRSPLIT_OMR_DPI must be between 100 and 1200, got {value}")
    return value


def load(path: Path, dpi: int | None = None) -> stream.Score:
    path = Path(path)
    engine = find_engine()
    if engine is None:
        raise _error(f"cannot read {path.name}: " + install_hint())
    dpi = omr_dpi(dpi)
    scores, read_at = recognise(path, engine, dpi)
    if not scores:
        raise _error(f"OMR found no music in {path.name}")
    if dpi is None and engine.name == "audiveris" and path.suffix.lower() == ".pdf":
        missed = sum(len(unrecognised_bars(seg)) for s in scores for seg in segments_of(s))
        if missed:
            # Pages too large for the default resolution are read again at the same ratio.
            again = SECOND_READING_DPI if read_at is None else \
                round(read_at * SECOND_READING_DPI / AUDIVERIS_DEFAULT_DPI)
            second_reading(path, engine, scores, missed, again)
    return assemble(scores, title_hint=path.stem)


def second_reading(path: Path, engine: Engine, scores: list[stream.Score], missed: int,
                   dpi: int = SECOND_READING_DPI) -> int:
    """Read the PDF again at `dpi` and fill the `missed` empty sung bars of `scores` from
    it where the readings agree. Returns the bars filled."""
    from .loaders import ScoreLoadError

    print(f"choirsplit: {missed} sung bar{'s' if missed > 1 else ''} not recognised; reading "
          f"{path.name} again at {dpi} dpi", file=sys.stderr)
    try:
        second, dpi = recognise(path, engine, dpi)
    except ScoreLoadError as exc:
        print(f"choirsplit: second reading failed: {exc}", file=sys.stderr)
        return 0
    return fill_from_second_reading(scores, second, dpi)


# --------------------------------------------------------------------------- post-processing


@dataclass
class Unit:
    """One instrument/voice group on one system: one Part, or the PartStaffs of one part."""
    staves: list[stream.Part]
    name: str
    abbreviation: str
    clefs: tuple[str, ...]
    key: str  # normalised label used for matching across systems


@dataclass
class Segment:
    """A run of measures (one system, or a whole score without layout info)."""
    units: list[Unit]
    measure_range: range  # measure indices into each staff's measure list
    new_page: bool = False  # the system starts a new page


def _measures(part: stream.Part) -> list[stream.Measure]:
    return list(part.getElementsByClass(stream.Measure))


def _group_units(score: stream.Score) -> list[list[stream.Part]]:
    groups: dict[str, list[stream.Part]] = {}
    for i, part in enumerate(score.parts):
        if isinstance(part, stream.PartStaff):
            k = re.sub(r"-Staff\d+$", "", str(part.id))
        else:
            k = f"{part.id}#{i}"
        groups.setdefault(k, []).append(part)
    return list(groups.values())


_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}
_OCR_LETTER = {"8": "s", "5": "s", "0": "o", "4": "a", "7": "t"}


def name_key(name: str) -> str:
    """'Soprano 1', 'S1', 'Sop. I' and the misread '81' all map to 's1'."""
    n = name.strip().lower()
    if not n or DEFAULT_NAME_RE.match(n):
        return ""
    if n[0] in _OCR_LETTER and len(n) <= 3:
        n = _OCR_LETTER[n[0]] + n[1:]
    m = re.match(r"([a-z])[a-z.]*\s*(\d+|i{1,3}|iv)?\b", n)
    if not m:
        return n
    num = m.group(2) or ""
    return m.group(1) + _ROMAN.get(num, num)


def _clef_sign(part: stream.Part, measure: stream.Measure) -> str:
    c = measure.getElementsByClass(clef.Clef).first() or measure.getContextByClass(clef.Clef)
    if c is None:
        c = part.flatten().getElementsByClass(clef.Clef).first()
    return getattr(c, "sign", None) or "?"


def _is_default_name(name: str | None) -> bool:
    return bool(DEFAULT_NAME_RE.match(name or ""))


def _part_name(part: stream.Part) -> str:
    return (part.partName or "").strip()


def segments_of(score: stream.Score) -> list[Segment]:
    """Split a recognised score into systems, listing the staff groups visible in each."""
    groups = _group_units(score)
    n_measures = max((len(_measures(p)) for g in groups for p in g), default=0)
    if n_measures == 0:
        return []
    starts = {0}
    pages = set()
    for g in groups:
        for p in g:
            for i, m in enumerate(_measures(p)):
                if any(getattr(x, "isNew", False) for x in m.getElementsByClass((layout.SystemLayout, layout.PageLayout))):
                    starts.add(i)
                if any(getattr(x, "isNew", False) for x in m.getElementsByClass(layout.PageLayout)):
                    pages.add(i)
    bounds = sorted(starts) + [n_measures]
    segments = []
    for a, b in zip(bounds, bounds[1:]):
        units = []
        for g in groups:
            visible = False
            clefs = []
            for p in g:
                ms = _measures(p)
                if a >= len(ms):
                    continue
                hidden = any(sl.hidden for sl in ms[a].getElementsByClass(layout.StaffLayout))
                visible |= not hidden
                clefs.append(_clef_sign(p, ms[a]))
            if not visible:
                continue
            name = _part_name(g[0])
            abbr = (g[0].partAbbreviation or "").strip()
            units.append(Unit(list(g), name, abbr, tuple(clefs), name_key(name) or name_key(abbr)))
        if units:
            segments.append(Segment(units, range(a, b), new_page=a in pages))
    return segments


def _similarity(u: Unit, ref: Unit) -> float:
    s = 0.0
    if u.key and ref.key:
        if u.key == ref.key:
            s += 3
        elif u.key[0] == ref.key[0]:
            s += 1
        else:
            s -= 1
    s += 1 if u.clefs == ref.clefs else -1
    s += 0 if len(u.staves) == len(ref.staves) else -2
    return s


def align_units(units: list[Unit], refs: list[Unit]) -> list[int]:
    """Order-preserving assignment of `units` to reference slots (len(units) <= len(refs))."""
    n, k = len(units), len(refs)
    if n == k:
        return list(range(n))
    neg = float("-inf")
    # best[i][j]: best score placing the first i units into the first j slots
    best = [[neg] * (k + 1) for _ in range(n + 1)]
    best[0] = [0.0] * (k + 1)
    for i in range(1, n + 1):
        for j in range(i, k + 1):
            best[i][j] = max(best[i][j - 1], best[i - 1][j - 1] + _similarity(units[i - 1], refs[j - 1]))
    out, j = [], k
    for i in range(n, 0, -1):
        while best[i][j] == best[i][j - 1] and j > i:
            j -= 1
        out.append(j - 1)
        j -= 1
    return out[::-1]


def _content_length(m: stream.Measure) -> float:
    return float(m.highestTime)


def _nominal_lengths(seg: Segment, meters: dict[int, float] | None = None) -> list[float]:
    """Length (quarters) of each measure of a segment: the most common across its staves.

    A whole-bar rest only says the staff is silent: OMR sizes it to the meter it assumes,
    so it votes for the meter in effect. That is the time signature, or for measures in
    `meters` the bar length of a time signature OMR missed (see `meters_of`).
    """
    meters = meters or {}
    out = []
    for i in seg.measure_range:
        lengths = []
        bar = meters.get(i)
        if bar is None:
            ms = [m for _, _, m in _bar_measures(seg, i)]
            ts = next((ts for m in ms for ts in m.getElementsByClass(meter.TimeSignature)), None)
            if ts is None and ms:
                ts = ms[0].getContextByClass(meter.TimeSignature)
            bar = float(ts.barDuration.quarterLength) if ts is not None else None
        for u in seg.units:
            for p in u.staves:
                ms = _measures(p)
                if i < len(ms):
                    rest_only = bar is not None and _bar_rest(ms[i]) and not _has_notes(ms[i])
                    lengths.append(bar if rest_only else round(_content_length(ms[i]), 4))
        lengths = [x for x in lengths if x > 0]
        if not lengths:
            out.append(bar or 4.0)
            continue
        counts = Counter(lengths).most_common()
        top = [v for v, c in counts if c == counts[0][1]]
        out.append(bar if bar is not None and any(abs(v - bar) < _EPS for v in top) else max(top))
    return out


def clip_measure(m: stream.Measure, length: float) -> bool:
    """Drop or shorten notes/rests that run past the end of a `length`-quarter measure.
    Returns True if a note (not just a rest) was cut."""
    cut = False
    for el in list(m.recurse().notesAndRests):
        start = float(el.getOffsetInHierarchy(m))
        end = start + float(el.duration.quarterLength)
        if start >= length - _EPS:
            el.activeSite.remove(el)
            cut |= not el.isRest
        elif end > length + _EPS:
            el.duration.quarterLength = length - start
            cut |= not el.isRest
    return cut


# ---- missed augmentation dots

DOT_X_TOLERANCE = 6.0  # MusicXML tenths; noteheads struck together line up within this


def _x(el) -> float | None:
    x = getattr(el.style, "absoluteX", None) if el.hasStyleInformation else None
    return float(x) if isinstance(x, (int, float)) else None


def _lines(m: stream.Measure) -> list[list]:
    """The rhythmic lines of a measure: its direct notes/rests, and each Voice's."""
    out = []
    for container in [m, *m.voices]:
        els = [e for e in container.notesAndRests if float(e.duration.quarterLength) > 0]
        if els:
            out.append(els)
    return out


def _complete_lines(measures: list[stream.Measure], length: float) -> list[tuple[float | None, float, float, bool]]:
    """(x position, onset, duration, is a note) of every note/rest in the lines of these
    measures that fill the bar exactly."""
    ref = []
    for m in measures:
        for line in _lines(m):
            total = sum(float(e.duration.quarterLength) for e in line)
            if abs(total - length) > _EPS:
                continue
            for e in line:
                ref.append((_x(e), float(e.offset), float(e.duration.quarterLength), not e.isRest))
    return ref


def _repair_line(line: list, ref: list[tuple[float | None, float, float, bool]], length: float) -> int:
    """Add the dot OMR missed to notes of an under-full line, if the line's note heads
    line up with the onsets of the complete lines in the same system and adding a dot
    (and nothing else) to some notes makes every note start where its head is printed.

    Lengthening the *last* note is only done when the complete lines confirm it: at
    least two of their notes start and end with it and nothing starts during the
    added time. Otherwise the gap is more likely a rest OMR missed (a half note and
    an unrecognised quarter rest, not a dotted half). Returns the number of dots added."""
    if any(e.duration.tuplets or e.duration.isGrace for e in line):
        return 0
    durs = [float(e.duration.quarterLength) for e in line]
    if sum(durs) > length - _EPS or float(line[0].offset) > _EPS:
        return False  # complete, or a voice that only starts later in the bar
    onsets = []
    for k, e in enumerate(line):
        x = _x(e)
        if x is None:
            return False
        hits = {round(o, 4) for rx, o, _, _ in ref if rx is not None and abs(rx - x) <= DOT_X_TOLERANCE}
        if len(hits) > 1:
            return False  # ambiguous
        if not hits:
            if k == 0:
                onsets.append(0.0)
                continue
            return False
        onsets.append(hits.pop())
    if onsets[0] != 0.0 or any(b <= a + _EPS for a, b in zip(onsets, onsets[1:])):
        return False
    new = [b - a for a, b in zip(onsets, onsets[1:])] + [length - onsets[-1]]
    changed = []
    for e, old, nd in zip(line, durs, new):
        if abs(nd - old) < _EPS:
            continue
        if abs(nd - 1.5 * old) < _EPS and e.duration.dots == 0 and e.duration.type not in ("complex", "inexpressible"):
            changed.append(e)
            continue
        return False  # anything but a single missing dot: leave the bar alone
    if not changed:
        return False
    if changed[-1] is line[-1]:
        start, old_end = onsets[-1], onsets[-1] + durs[-1]
        if any(old_end - _EPS <= o < length - _EPS for _, o, _, _ in ref):
            return False
        if sum(1 for _, o, d, is_note in ref
               if is_note and abs(o - start) < _EPS and abs(o + d - length) < _EPS) < 2:
            return False
    for e in changed:
        e.duration.dots = 1
    for e, onset in zip(line, onsets):
        site = e.activeSite
        site.setElementOffset(e, onset)
    for container in {id(e.activeSite): e.activeSite for e in line}.values():
        container.coreElementsChanged()
    return len(changed)


Report = Callable[[int, int, int, str], None]  # (measure index, unit index, staff index, detail)


def repair_missing_dots(seg: Segment, lengths: list[float], report: Report | None = None) -> int:
    """Restore augmentation dots OMR missed (a dotted quarter read as a quarter).

    A missed dot leaves a line short of the bar and pulls its later notes early. Within
    one system, notes struck together are printed at the same x position on every
    staff, so the complete lines of the bar tell where each note of the short line
    really starts. Only lines where every note then lines up and the only differences
    are single missing dots are changed. Returns the number of lines repaired.
    """
    repaired = 0
    for i, length in zip(seg.measure_range, lengths):
        located = _bar_measures(seg, i)
        measures = [m for _, _, m in located]
        ref = None
        for u, s, m in located:
            for line in _lines(m):
                if sum(float(e.duration.quarterLength) for e in line) < length - _EPS:
                    if ref is None:
                        ref = _complete_lines(measures, length)
                    dots = _repair_line(line, ref, length)
                    repaired += bool(dots)
                    if dots and report is not None:
                        report(i, u, s, f"restored {dots} missed dot{'s' if dots > 1 else ''}")
    return repaired


def _retime_line(line: list, ref: list[tuple[float | None, float, float, bool]], length: float) -> int:
    """Move the notes of an under-full line to the onsets their note heads line up with
    in the complete lines of the system, filling the gaps with rests. For a rest (or a
    note length) OMR missed in mid-bar, which pulls every later note early.

    Note lengths are kept as printed; the line is only changed if every note lines up
    with exactly one onset, no note moves earlier, and no note would then run into the
    next one or past the bar line. Returns the number of notes moved."""
    if any(e.duration.tuplets or e.duration.isGrace for e in line):
        return 0
    if sum(float(e.duration.quarterLength) for e in line) > length - _EPS or float(line[0].offset) > _EPS:
        return 0
    notes = [e for e in line if not e.isRest]
    if not notes:
        return 0
    onsets = []
    for e in notes:
        x = _x(e)
        hits = {round(o, 4) for rx, o, _, _ in ref
                if x is not None and rx is not None and abs(rx - x) <= DOT_X_TOLERANCE}
        if not hits and e is line[0]:
            hits = {0.0}  # nothing else starts with the first note: it stays on the downbeat
        if len(hits) != 1:
            return 0
        onsets.append(hits.pop())
    moved = 0
    for k, (e, onset) in enumerate(zip(notes, onsets)):
        next_onset = onsets[k + 1] if k + 1 < len(notes) else length
        if (onset < float(e.offset) - _EPS or next_onset <= onset + _EPS
                or onset + float(e.duration.quarterLength) > next_onset + _EPS):
            return 0
        moved += abs(onset - float(e.offset)) > _EPS
    if not moved:
        return 0
    site = line[0].activeSite
    for e in line:
        if e.isRest:
            site.remove(e)
    pos = 0.0
    for e, onset in zip(notes, onsets):
        if onset > pos + _EPS:
            site.insert(pos, note.Rest(quarterLength=onset - pos))
        site.setElementOffset(e, onset)
        pos = onset + float(e.duration.quarterLength)
    site.coreElementsChanged()
    return moved


def repair_onsets(seg: Segment, lengths: list[float], report: Report | None = None) -> int:
    """Re-time lines that are still short of the bar after `repair_missing_dots`, using
    the x positions of their note heads (see `_retime_line`). Returns lines repaired."""
    repaired = 0
    for i, length in zip(seg.measure_range, lengths):
        located = _bar_measures(seg, i)
        ref = None
        for u, s, m in located:
            for line in _lines(m):
                if len({id(e.activeSite) for e in line}) != 1:
                    continue
                if sum(float(e.duration.quarterLength) for e in line) < length - _EPS:
                    if ref is None:
                        ref = _complete_lines([m for _, _, m in located], length)
                    moved = _retime_line(line, ref, length)
                    repaired += bool(moved)
                    if moved and report is not None:
                        report(i, u, s, f"moved {moved} note{'s' if moved > 1 else ''} to where "
                                        "they are printed (missed rest or note length)")
    return repaired


def _bar_rest(m: stream.Measure) -> bool:
    """The measure shows a whole-bar rest (MusicXML ``<rest measure="yes">``)."""
    return any(getattr(r, "fullMeasure", False) is True for r in m.recurse().getElementsByClass(note.Rest))


def _sung_units(seg: Segment) -> set[int]:
    """Indices of the sung staff groups of a segment (all of them in a closed score)."""
    acc = [is_accompaniment(unit.staves[0]) for unit in seg.units]
    return {u for u, a in enumerate(acc) if not a} or set(range(len(seg.units)))


def _nothing_recognised(m: stream.Measure) -> bool:
    """OMR left the bar empty: music21 filled it with its own bar rest ("auto", without
    a position). A printed bar always shows a note or a rest, so something was missed."""
    return all(e.isRest and getattr(e, "fullMeasure", None) == "auto" and _x(e) is None
               for e in m.recurse().notesAndRests)


def unrecognised_bars(seg: Segment) -> list[tuple[int, int, int]]:
    """(measure index, unit index, staff index) of the sung staff bars of a segment where
    nothing was recognised although another sung staff has notes in the bar."""
    sung = _sung_units(seg)
    out = []
    for i in seg.measure_range:
        located = [(u, s, m) for u, s, m in _bar_measures(seg, i) if u in sung]
        for u, s, m in located:
            if _nothing_recognised(m) and any(_has_notes(m2) for _, _, m2 in located if m2 is not m):
                out.append((i, u, s))
    return out


def _notes_signature(m: stream.Measure) -> list[tuple[float, tuple[float, ...]]]:
    return sorted((round(off, 4), tuple(sorted(p.ps for p in el.pitches))) for _, off, el in _note_elements(m))


def fill_from_second_reading(first: list[stream.Score], second: list[stream.Score], dpi: int) -> int:
    """Fill the staff bars the first reading left empty (`unrecognised_bars`) with the same
    bar of a second reading of the page at another resolution, which often sees note
    heads the first one missed (whole notes on ledger lines under a fermata, ...).

    A bar is only taken when the two readings demonstrably line up there: the same
    systems, measures and staves, the same clef on that staff, and every other sung staff
    of the bar holding the same notes in both. Filled measures are marked with
    ``choirsplit_filled = dpi`` so `assemble` can log them. Returns the bars filled."""
    if len(first) != len(second):
        return 0
    filled = 0
    for s1, s2 in zip(first, second):
        segs1, segs2 = segments_of(s1), segments_of(s2)
        if len(segs1) != len(segs2):
            continue
        for seg1, seg2 in zip(segs1, segs2):
            if (seg1.measure_range != seg2.measure_range
                    or [len(u.staves) for u in seg1.units] != [len(u.staves) for u in seg2.units]):
                continue
            sung = _sung_units(seg1)
            for i, u, s in unrecognised_bars(seg1):
                ones = {(uu, ss): m for uu, ss, m in _bar_measures(seg1, i)}
                twos = {(uu, ss): m for uu, ss, m in _bar_measures(seg2, i)}
                m1, m2 = ones.get((u, s)), twos.get((u, s))
                if m1 is None or m2 is None or not _has_notes(m2):
                    continue
                if _clef_sign(seg1.units[u].staves[s], m1) != _clef_sign(seg2.units[u].staves[s], m2):
                    continue
                checked = [(key, mm) for key, mm in ones.items()
                           if key[0] in sung and key != (u, s) and _has_notes(mm)]
                if not checked or any(key not in twos or _notes_signature(mm) != _notes_signature(twos[key])
                                      for key, mm in checked):
                    continue
                for el in list(m1.notesAndRests) + list(m1.voices):
                    m1.remove(el)
                for el in list(m2.voices) + list(m2.notesAndRests):
                    m1.insert(float(el.offset), copy.deepcopy(el))
                m1.choirsplit_filled = dpi
                filled += 1
    return filled


def drop_unaligned_beside_bar_rests(seg: Segment, report: Report | None = None) -> int:
    """Drop notes that share a bar with a whole-bar rest and line up with nothing the
    other voices sing.

    A whole-bar rest says the staff is silent. Audiveris still reads things next to it
    as notes: small cue notes of another instrument printed in the resting staff
    ("Vc." passages), and marks at the bar's edges such as the bar line or a courtesy
    time signature. None of those line up with a note of another sung staff. A divisi
    voice that really sings while the other voice of its staff rests does, so on a
    single-line staff the notes of such a bar are dropped only when *none* of them
    starts where a note of another sung staff (without a bar rest) starts.
    Returns the number of staff bars cleaned.
    """
    sung = _sung_units(seg)
    polyphonic: dict[int, bool] = {}
    cleaned = 0
    for i in seg.measure_range:
        located = [(u, s, m) for u, s, m in _bar_measures(seg, i) if u in sung]
        resting = [(u, s, m) for u, s, m in located if _bar_rest(m) and _has_notes(m)]
        if not resting:
            continue
        ref = [x for _, _, m in located if not _bar_rest(m)
               for _, _, el in _note_elements(m) for x in [_x(el)] if x is not None]
        for u, s, m in resting:
            staff = seg.units[u].staves[s]
            if id(staff) not in polyphonic:  # a real two-voice staff keeps everything
                others = [mm for mm in _measures(staff) if not (_bar_rest(mm) and _has_notes(mm))]
                polyphonic[id(staff)] = _polyphony(others) >= POLYPHONY_THRESHOLD
            if polyphonic[id(staff)]:
                continue
            notes = [el for _, _, el in _note_elements(m)]
            xs = [_x(el) for el in notes]
            if any(x is None for x in xs) or any(abs(x - r) <= DOT_X_TOLERANCE for x in xs for r in ref):
                continue
            for el in notes:
                el.activeSite.remove(el)
            cleaned += 1
            if report is not None:
                report(i, u, s, "dropped notes beside a whole-bar rest (cue notes or stray marks)")
    return cleaned


TIME_SIGNATURE_X = 8.0  # MusicXML tenths; a note head this close to the bar line is a time signature


def _time_signature_heads(m: stream.Measure) -> list:
    """Notes at the very start of a measure, printed where its bar line's time signature is."""
    return [el for container in [m, *m.voices] for el in container.notes
            if float(el.getOffsetBySite(container)) < _EPS
            and (x := _x(el)) is not None and x <= TIME_SIGNATURE_X]


def _remove_heads(m: stream.Measure, heads: list) -> None:
    """Remove `heads` and move what follows each of them back to the start of the bar."""
    for el in heads:
        site, length = el.activeSite, float(el.duration.quarterLength)
        site.remove(el)
        for e in list(site.notesAndRests):
            off = float(e.getOffsetBySite(site))
            if getattr(e, "fullMeasure", None) is not True and off >= length - _EPS:
                site.setElementOffset(e, off - length)
        site.coreElementsChanged()
    m.coreElementsChanged()


def _tie_slurred_repeats(prev: stream.Measure, m: stream.Measure) -> None:
    """Make a tie of an arc from the last note of `prev` to the same pitch at the start of
    `m`. OMR took it for a slur because the note it saw next was the misread time
    signature, which has another pitch."""
    before = list(prev.recurse().notes)
    if not before:
        return
    last = max(float(n.getOffsetInHierarchy(prev)) for n in before)
    for n in m.recurse().getElementsByClass(note.Note):
        if float(n.getOffsetInHierarchy(m)) > _EPS:
            continue
        for slur in n.getSpannerSites("Slur"):
            start = slur.getFirst()
            if (start is not n and isinstance(start, note.Note) and start.pitch == n.pitch
                    and any(start is b for b in before)
                    and abs(float(start.getOffsetInHierarchy(prev)) - last) < _EPS):
                held = start.tie is not None and start.tie.type in ("stop", "continue")
                start.tie = tie.Tie("continue" if held else "start")
                n.tie = tie.Tie("stop")


def drop_misread_time_signatures(seg: Segment, report: Report | None = None) -> dict[int, float | None]:
    """Remove time signatures OMR read as notes, and tell the meter they printed.

    Audiveris misses some time signatures printed inside a system (a 3/4 bar between 5/4
    bars), and their digits then come out as a note on every staff, right at the bar line
    and before the bar's first real note. It sizes that note to fill the bar to the old
    meter, so the bar keeps the old length and every voice sings a note that isn't
    there. Such a column of notes on most staves of the bar is removed, and the bar's
    length is read from what the staves really sing: if at least two staves with notes
    agree on a whole number of eighths, that is the new meter.

    Returns {measure index: new bar length in quarters, or None if it couldn't be told}.
    """
    found: dict[int, float | None] = {}
    for i in seg.measure_range[1:]:  # a system's first time signature is read with its clefs
        located = _bar_measures(seg, i)
        marked = [(u, s, m, heads) for u, s, m in located if (heads := _time_signature_heads(m))]
        if len(marked) < 2 or 2 * len(marked) < len(located):
            continue
        for u, s, m, heads in marked:
            _remove_heads(m, heads)
            if report is not None:
                report(i, u, s, "removed a time signature misread as a note")
        for u, s, m in located:
            _tie_slurred_repeats(_measures(seg.units[u].staves[s])[i - 1], m)
        sung = Counter(round(_content_length(m), 4) for _, _, m in located
                       if _has_notes(m) and not _bar_rest(m))
        length, votes = sung.most_common(1)[0] if sung else (0.0, 0)
        found[i] = length if votes >= 2 and votes == sum(sung.values()) and (2 * length).is_integer() else None
    return found


def meters_of(seg: Segment, found: dict[int, float | None], carried: float | None
              ) -> tuple[dict[int, float], float | None]:
    """Bar lengths of the measures of a segment that are in a meter OMR missed.

    A meter told by `drop_misread_time_signatures` holds until the next time signature
    (one OMR read, or another one it missed), across systems. `carried` is the meter
    still in effect from the previous segment. Returns ({measure index: bar length},
    the meter in effect after the segment)."""
    out: dict[int, float] = {}
    for i in seg.measure_range:
        if i in found:
            carried = found[i]
        elif any(m.getElementsByClass(meter.TimeSignature) for _, _, m in _bar_measures(seg, i)):
            carried = None
        if carried is not None:
            out[i] = carried
    return out, carried


def _time_signature(length: float) -> meter.TimeSignature:
    return meter.TimeSignature(f"{int(length)}/4" if float(length).is_integer() else f"{int(2 * length)}/8")


def _bar_measures(seg: Segment, i: int) -> list[tuple[int, int, stream.Measure]]:
    """(unit index, staff index, measure) of measure `i` on every staff of a segment."""
    return [(u, s, ms[i]) for u, unit in enumerate(seg.units) for s, p in enumerate(unit.staves)
            for ms in [_measures(p)] if i < len(ms)]


def _rest_measure(length: float) -> stream.Measure:
    m = stream.Measure()
    r = note.Rest(quarterLength=length)
    m.insert(0, r)
    return m


def _carry_context(src_part: stream.Part, src_m: stream.Measure, dst_m: stream.Measure) -> None:
    """Give a measure copied from a new source the clef/time signature in effect there."""
    for cls in (clef.Clef, meter.TimeSignature):
        if dst_m.getElementsByClass(cls).first() is None:
            ctx = src_m.getContextByClass(cls)
            if ctx is not None:
                dst_m.insert(0, copy.deepcopy(ctx))


def _closed_score(units: list[list[stream.Part]]) -> bool:
    """Every staff group is an unlabelled keyboard-like group: Audiveris's "Piano"
    default for a braced, unnamed SA/TB hymn layout rather than a real accompaniment."""
    return bool(units) and all(
        _is_default_name(staves[0].partName) and is_accompaniment(staves[0]) for staves in units)


def _best_name(names: list[str]) -> str:
    real = [n for n in names if n and not _is_default_name(n)]
    if not real:
        return next((n for n in names if n), "")
    # The most complete label wins: "Soprano 1" over "S1" over the misread "81".
    return max(real, key=lambda n: (sum(c.isalpha() for c in n), -real.index(n)))


def assemble(scores: list[stream.Score], title_hint: str | None = None) -> stream.Score:
    """Merge recognised pages/movements into one score with one part per voice group.

    The result carries ``omr_issues`` (the repairs made, as ``check.Issue``) and
    ``omr_locations`` (measure number -> (page, system) in the recognised pages).
    """
    segments = []
    for s in scores:
        segs = segments_of(s)
        if segs:
            segs[0].new_page = True  # each recognised file/movement starts on a new page
        segments.extend(segs)
    if not segments:
        raise _error("OMR output contains no measures")
    n_logical = max(len(seg.units) for seg in segments)
    refs = next(seg.units for seg in segments if len(seg.units) == n_logical)

    mappings = [align_units(seg.units, refs) for seg in segments]
    n_staves = [1] * n_logical
    names: list[list[str]] = [[] for _ in range(n_logical)]
    abbrs: list[list[str]] = [[] for _ in range(n_logical)]
    instruments: list[instrument.Instrument | None] = [None] * n_logical
    for seg, mapping in zip(segments, mappings):
        for u, k in zip(seg.units, mapping):
            n_staves[k] = max(n_staves[k], len(u.staves))
            names[k].append(u.name)
            abbrs[k].append(u.abbreviation)
            if instruments[k] is None:
                instruments[k] = u.staves[0].getInstrument(returnDefault=False)

    out_parts: list[list[stream.Part]] = []
    for k in range(n_logical):
        name = _best_name(names[k])
        abbr = min((a for a in abbrs[k] if a and not _is_default_name(a)), key=len, default=None)
        staves = []
        for s in range(n_staves[k]):
            p = stream.PartStaff() if n_staves[k] > 1 else stream.Part()
            p.id = f"V{k + 1}-Staff{s + 1}" if n_staves[k] > 1 else f"V{k + 1}"
            p.partName = name or None
            p.partAbbreviation = abbr
            if instruments[k] is not None:
                inst = copy.deepcopy(instruments[k])
                inst.partName = name or None
                p.insert(0, inst)
            staves.append(p)
        out_parts.append(staves)

    # Repairs, logged per (logical part, staff) and named once the parts have their names.
    log: list[tuple[int | None, int, int, str, str]] = []  # (bar, part, staff, kind, detail)
    locations: dict[int, tuple[int, int]] = {}
    offset = 0.0
    number = 0
    page = system = 0
    previous_source: list[list[int | None]] = [[None] * n for n in n_staves]
    carried: float | None = None  # bar length of a time signature OMR missed, until the next one
    for seg, mapping in zip(segments, mappings):
        page, system = (page + 1, 1) if seg.new_page else (page, system + 1)
        first = number + 1 - seg.measure_range.start  # measure index -> number

        def report(i, u, s, detail, kind="fixed", mapping=mapping, first=first):
            log.append((first + i, mapping[u], s, kind, detail))

        meters, carried = meters_of(seg, drop_misread_time_signatures(seg, report), carried)
        drop_unaligned_beside_bar_rests(seg, report)
        lengths = _nominal_lengths(seg, meters)
        repair_missing_dots(seg, lengths, report)
        repair_onsets(seg, lengths, report)
        for i, u, s in unrecognised_bars(seg):
            report(i, u, s, "nothing recognised in this bar (missed note?)", "suspect")
        slot_unit = {k: u for u, k in zip(seg.units, mapping)}
        for i, length in zip(seg.measure_range, lengths):
            number += 1
            locations[number] = (page, system)
            # A time signature holds for every staff, also those OMR didn't see it on
            # (or that are hidden in this system).
            if i in meters and (i == seg.measure_range.start or meters.get(i - 1) != meters[i]):
                bar_ts = _time_signature(meters[i])
            else:
                bar_ts = next((ts for _, _, mm in _bar_measures(seg, i)
                               for ts in mm.getElementsByClass(meter.TimeSignature)), None)
            for k in range(n_logical):
                u = slot_unit.get(k)
                for s, dest in enumerate(out_parts[k]):
                    src = u.staves[s] if u is not None and s < len(u.staves) else None
                    src_ms = _measures(src) if src is not None else []
                    if src is not None and i < len(src_ms):
                        filled = getattr(src_ms[i], "choirsplit_filled", None)
                        if filled:
                            log.append((number, k, s, "fixed", f"filled from a second reading at {filled} dpi"))
                        m = copy.deepcopy(src_ms[i])
                        if previous_source[k][s] != id(src):
                            _carry_context(src, src_ms[i], m)
                            previous_source[k][s] = id(src)
                        if clip_measure(m, length):
                            log.append((number, k, s, "suspect", "notes ran past the end of the bar and were cut "
                                        "(missed bar line?)"))
                    else:
                        m = _rest_measure(length)
                        previous_source[k][s] = None
                    if bar_ts is not None:
                        for ts in list(m.getElementsByClass(meter.TimeSignature)):
                            m.remove(ts)
                        m.insert(0, copy.deepcopy(bar_ts))
                    m.number = number
                    dest.insert(offset, m)
            offset += length

    result = stream.Score()
    md = copy.deepcopy(scores[0].metadata) if scores[0].metadata is not None else None
    hymn = _closed_score(out_parts)
    acc = [is_accompaniment(staves[0]) and not hymn for staves in out_parts]
    lines: dict[int, int] = {}
    slots = []  # (logical part, staff) of each part, in score order
    for k, staves in enumerate(out_parts):
        for s, p in enumerate(staves):
            if acc[k]:
                lines[id(p)] = 1  # piano & co. are played as written
            else:
                strays: list[int] = []
                drop_range_strays(p, below=k + 1 < len(acc) and acc[k + 1], above=k > 0 and acc[k - 1],
                                  log=strays)
                dropped: list[int] = []
                lines[id(p)] = tidy_voices(p, dropped)
                log.extend((n, k, s, "fixed", "dropped a note outside the voice's range next to the "
                            "accompaniment") for n in strays)
                log.extend((n, k, s, "fixed", "dropped stray notes beside a whole-bar rest") for n in dropped)
            result.insert(0, p)
            slots.append((k, s))
    name_unnamed_parts(result, lines)
    part_names = {}
    grouped: dict[int, list[stream.Part]] = {}
    for p, (k, s) in zip(result.parts, slots):
        part_names.setdefault((k, s), p.partName or f"Part {k + 1}")
        grouped.setdefault(k, []).append(p)
    for staves in grouped.values():
        if len(staves) > 1 and all(isinstance(p, stream.PartStaff) for p in staves):
            # Keeps a grand staff one part when the score is saved as MusicXML.
            result.insert(0, layout.StaffGroup(staves, name=staves[0].partName, symbol="brace"))
    for p, (k, s) in zip(result.parts, slots):
        if fix_tenor_octave(p):
            log.append((None, k, s, "fixed", "sung an octave lower (treble clef read without its small 8)"))
    if md is not None:
        result.metadata = md
    _fix_title(result, scores[0], title_hint)
    result.omr_issues = [
        Issue(bar, part_names.get((k, s), f"Part {k + 1}"), kind, detail,
              *(locations.get(bar, (None, None)) if bar is not None else (None, None)))
        for bar, k, s, kind, detail in sorted(log, key=lambda e: (e[0] or 0, e[1], e[2]))
        if not acc[k]]  # the accompaniment isn't sung: its clean-up doesn't need checking
    result.omr_locations = locations
    return result


# ---- voices


def _intervals(elements, base: float) -> list[tuple[float, float]]:
    return [(base + float(e.offset), base + float(e.offset) + float(e.duration.quarterLength))
            for e in elements if float(e.duration.quarterLength) > 0]


def _union_length(iv: list[tuple[float, float]]) -> float:
    total, end = 0.0, float("-inf")
    for a, b in sorted(iv):
        if b <= end:
            continue
        total += b - max(a, end)
        end = b
    return total


def _overlap_length(lines: list[list[tuple[float, float]]]) -> float:
    """Time during which at least two of the lines sound."""
    events = []
    for line in lines:
        for a, b in line:
            events += [(a, 1), (b, -1)]
    events.sort(key=lambda e: (e[0], e[1]))
    depth, last, total = 0, None, 0.0
    for t, d in events:
        if depth >= 2 and last is not None:
            total += t - last
        depth += d
        last = t
    return total


def _note_elements(m: stream.Measure):
    """Yield (voice id, offset in measure, element) for every note/chord of a measure."""
    for el in m.notes:
        yield "1", float(el.getOffsetBySite(m)), el
    for v in m.voices:
        base = float(v.getOffsetBySite(m))
        for el in v.notes:
            yield str(v.id), base + float(el.getOffsetBySite(v)), el


def _polyphony(measures: list[stream.Measure]) -> float:
    """Fraction of sounding time in which two or more voices sound together."""
    overlap = sounding = 0.0
    for m in measures:
        lines = [_intervals(v.notes, float(v.getOffsetBySite(m))) for v in m.voices if v.notes]
        if m.notes:
            lines.append(_intervals(m.notes, 0.0))
        if len(lines) >= 2:
            overlap += _overlap_length([_merge_line(line) for line in lines])
        sounding += _union_length([iv for line in lines for iv in line])
    return overlap / sounding if sounding > 0 else 0.0


def _chordal(measures: list[stream.Measure]) -> bool:
    els = [el for m in measures for _, _, el in _note_elements(m)]
    chords = sum(1 for el in els if len(el.pitches) >= 2)
    return chords > 0 and chords >= 0.3 * len(els)


def _flatten_voices(measures: list[stream.Measure]) -> None:
    for m in measures:
        for v in list(m.voices):
            base = float(v.getOffsetBySite(m))
            for el in list(v.notes):
                off = base + float(el.getOffsetBySite(v))
                v.remove(el)
                m.insert(off, el)
            m.remove(v)


def simplify_voices(part: stream.Part) -> bool:
    """Collapse OMR voice splits that aren't real polyphony. Returns True if flattened."""
    measures = _measures(part)
    if _polyphony(measures) >= POLYPHONY_THRESHOLD:
        return False  # rest-only voices stay: they tell revoice_two_lines who is resting
    _flatten_voices(measures)  # drops rest-only voices (and all rests in voices) too
    return True


def _has_notes(m: stream.Measure | None) -> bool:
    return m is not None and any(True for _ in _note_elements(m))


def drop_notes_under_bar_rests(part: stream.Part, log: list[int] | None = None) -> int:
    """Remove stray notes OMR put on a sung staff in a bar that shows a whole-bar rest.

    Audiveris sometimes reads a piano note head, ledger line or tie just below/above a
    resting voice staff as a note in a second voice next to the printed whole-bar rest
    (e.g. the bass "singing" in the piano introduction). A whole-bar rest (MusicXML
    ``<rest measure="yes">``) means the staff is silent, so on a single-line staff whose
    neighbouring bars are silent too, the other voice's notes are dropped. Real
    two-voice staves (one voice resting while the other sings) are left alone.
    Returns the number of bars cleaned; their measure numbers are appended to `log`.
    """
    measures = _measures(part)
    if _polyphony(measures) >= POLYPHONY_THRESHOLD:
        return 0
    cleaned = 0
    for k, m in enumerate(measures):
        voices = list(m.voices)
        if len(voices) < 2 or m.notes:
            continue
        resting = [v for v in voices if not v.notes and any(
            getattr(r, "fullMeasure", False) is True for r in v.getElementsByClass(note.Rest))]
        if not resting:
            continue
        prev_m = measures[k - 1] if k > 0 else None
        next_m = measures[k + 1] if k + 1 < len(measures) else None
        if _has_notes(prev_m) or _has_notes(next_m):
            continue
        bar_rest = copy.deepcopy(next(r for r in resting[0].getElementsByClass(note.Rest)
                                      if getattr(r, "fullMeasure", False) is True))
        for v in voices:
            m.remove(v)
        m.insert(0, bar_rest)
        cleaned += 1
        if log is not None:
            log.append(m.number)
    return cleaned


def drop_range_strays(part: stream.Part, below: bool, above: bool, log: list[int] | None = None) -> int:
    """Remove notes OMR took from a neighbouring accompaniment staff into a sung staff.

    A piano note head printed just below the bass staff (or above the soprano staff)
    can be read as a note of the voice. On a single-line staff named for its voice, a
    note is dropped when it lies outside that voice's range on the side of the
    accompaniment staff (`below` / `above`) *and* it is the only note of the bar or
    sounds at the same time as another note of the staff. Returns the number of bars
    cleaned; their measure numbers are appended to `log`.
    """
    rng = voice_range(part.partName or "") if (below or above) and not _is_default_name(part.partName) else None
    if rng is None:
        return 0
    measures = _measures(part)
    replaced = []  # (container, rest, note) so the change can be undone
    bars = []
    for m in measures:
        elements = [(off, el) for _, off, el in _note_elements(m)]
        bad = []
        for off, el in elements:
            ps = [p.ps for p in el.pitches]
            if not ((below and max(ps) < rng[0]) or (above and min(ps) > rng[1])):
                continue
            end = off + float(el.duration.quarterLength)
            others = [(o, e) for o, e in elements if e is not el]
            if not others or any(o < end - _EPS and o + float(e.duration.quarterLength) > off + _EPS
                                 for o, e in others):
                bad.append(el)
        for el in bad:
            site, rest = el.activeSite, note.Rest(quarterLength=el.duration.quarterLength)
            site.replace(el, rest)
            replaced.append((site, rest, el))
        if bad:
            bars.append(m.number)
    # Without the strays the staff must be a single line; a real two-voice staff keeps all.
    if replaced and _polyphony(measures) >= POLYPHONY_THRESHOLD:
        for site, rest, el in replaced:
            site.replace(rest, el)
        return 0
    if log is not None:
        log.extend(bars)
    return len(bars)


def tidy_voices(part: stream.Part, log: list[int] | None = None) -> int:
    """Clean up OMR voices on one staff; return how many sung lines it carries (1 or 2).

    * voice splits that never sound together are merged into one line;
    * a staff whose voices really overlap (S+A or T+B on one staff) is re-voiced into
      exactly two voices by pitch, whatever voice numbers the OMR engine used - engines
      put stray notes in voice 3 or write a shared-stem S/A interval as a chord in one
      voice, which would otherwise create extra tracks or silence the alto;
    * a single voice written mostly in two-note chords also counts as two lines (score.py
      splits those top/bottom when the staff is named for two voices).

    Measure numbers of bars where stray notes were dropped are appended to `log`.
    """
    drop_notes_under_bar_rests(part, log)
    if simplify_voices(part):
        return 2 if _chordal(_measures(part)) else 1
    revoice_two_lines(part)
    if _copied_line(part):
        _keep_one_line(part)
        return 1
    return 2


def _voice_events(measures: list[stream.Measure], vid: str) -> list[NoteEvent]:
    """The notes of voice `vid` as events on the part's quarter-note timeline."""
    events = []
    for m in measures:
        base = float(m.offset)
        for v_id, off, el in _note_elements(m):
            if v_id == vid:
                events += [NoteEvent(base + off, float(el.duration.quarterLength), p.ps) for p in el.pitches]
    return events


def _copied_line(part: stream.Part) -> bool:
    """True if the two voices of a re-voiced staff are one line written twice: OMR put a single
    line into two voices, which `revoice_two_lines` then copied into both."""
    measures = _measures(part)
    return _differing_share(_voice_events(measures, "1"), _voice_events(measures, "2")) < DUPLICATE_LINE_SHARE


def _keep_one_line(part: stream.Part) -> None:
    """Rebuild every measure from one of its two voices: voice 1, or voice 2 where 1 is empty."""
    for m in _measures(part):
        voices = {str(v.id): v for v in m.voices}
        keep = voices.get("1")
        if keep is None or not keep.notes:
            keep = voices.get("2", keep)
        base = float(keep.getOffsetBySite(m)) if keep is not None else 0.0
        for v in list(m.voices):
            m.remove(v)
        if keep is not None:
            for el in list(keep.notesAndRests):
                m.insert(base + float(el.offset), el)


def _median(values: list[float]) -> float:
    values = sorted(values)
    return values[len(values) // 2] if values else 0.0


def revoice_two_lines(part: stream.Part) -> None:
    """Rebuild every measure of a two-voice staff as Voice "1" (upper) + Voice "2" (lower)."""
    measures = _measures(part)
    by_id: dict[str, list[float]] = {}
    for m in measures:
        for vid, _, el in _note_elements(m):
            by_id.setdefault(vid, []).extend(p.ps for p in el.pitches)
    staff_median = _median([x for v in by_id.values() for x in v])
    upper_ids = {vid for vid, ps in by_id.items() if _median(ps) >= staff_median}

    for m in measures:
        events = []  # [start, end, pitch, tie, vid, role]
        rests = []  # (start, end, vid)
        for vid, off, el in _note_elements(m):
            end = off + float(el.duration.quarterLength)
            notes_ = sorted(el.notes, key=lambda n: n.pitch.ps) if len(el.pitches) > 1 else [el]
            for i, n in enumerate(notes_):
                role = None
                if len(notes_) > 1:
                    role = "L" if i == 0 else "U" if i == len(notes_) - 1 else "U" if i >= len(notes_) / 2 else "L"
                tie_ = n.tie if n.tie is not None else el.tie
                events.append([off, end, copy.deepcopy(n.pitch), tie_, vid, role])
        # The same pitch struck at the same moment twice (two voice numbers, or a stray
        # copy) is one note head; once deduplicated it is treated as a shared unison.
        seen: set[tuple[float, float]] = set()
        unique = []
        for ev in events:
            k = (round(ev[0], 4), ev[2].ps)
            if k not in seen:
                seen.add(k)
                unique.append(ev)
        events = unique
        for v in m.voices:
            base = float(v.getOffsetBySite(m))
            for r in v.getElementsByClass(note.Rest):
                start = base + float(r.getOffsetBySite(v))
                rests.append((start, start + float(r.duration.quarterLength), str(v.id)))
        for ev in events:
            if ev[5] is not None:
                continue
            start, end, pitch, _, vid, _ = ev
            others = [o for o in events if o is not ev and o[0] < end - _EPS and o[1] > start + _EPS
                      and (o[4] != vid or o[5] is not None)]
            higher = any(o[2].ps > pitch.ps for o in others)
            lower = any(o[2].ps < pitch.ps for o in others)
            if higher and not lower:
                ev[5] = "L"
            elif lower and not higher:
                ev[5] = "U"
            elif others or any(r[0] < end - _EPS and r[1] > start + _EPS and r[2] != vid for r in rests):
                ev[5] = "U" if vid in upper_ids else "L"  # unison or the other voice rests
            else:
                ev[5] = "B"  # one note head for both voices
        for el in list(m.notesAndRests):
            m.remove(el)
        for v in list(m.voices):
            m.remove(v)
        for vid, roles in (("1", ("U", "B")), ("2", ("L", "B"))):
            v = stream.Voice(id=vid)
            for start, end, pitch, tie_, _, role in events:
                if role in roles:
                    n = note.Note(copy.deepcopy(pitch), quarterLength=end - start)
                    if tie_ is not None:
                        n.tie = copy.deepcopy(tie_)
                    v.insert(start, n)
            m.insert(0, v)


def _merge_line(line: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping intervals within one line (chords) so they don't count as overlap."""
    out: list[list[float]] = []
    for a, b in sorted(line):
        if out and a <= out[-1][1] + _EPS:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


# ---- naming


VOICE_LAYOUTS = {
    1: ["Voice"],
    3: ["Soprano", "Alto", "Bass"],
    4: ["Soprano", "Alto", "Tenor", "Bass"],
    5: ["Soprano 1", "Soprano 2", "Alto", "Tenor", "Bass"],
    6: ["Soprano 1", "Soprano 2", "Alto", "Tenor", "Bass 1", "Bass 2"],
    7: ["Soprano 1", "Soprano 2", "Alto 1", "Alto 2", "Tenor", "Bass 1", "Bass 2"],
    8: ["Soprano 1", "Soprano 2", "Alto 1", "Alto 2", "Tenor 1", "Tenor 2", "Bass 1", "Bass 2"],
}


def _abbrev(name: str) -> str:
    """'Soprano 1' -> 'S1', 'Alto' -> 'A' (score.py expands these back when splitting a staff)."""
    words = name.split()
    return words[0][0].upper() + (words[1] if len(words) > 1 else "")


def _voice_count(part: stream.Part) -> int:
    ids = {str(v.id) for m in _measures(part) for v in m.voices if v.notes}
    if len(ids) <= 1 and _chordal(_measures(part)):
        return 2
    return max(len(ids), 1)


def name_unnamed_parts(score: stream.Score, lines: dict[int, int] | None = None) -> None:
    """Name the sung staves by layout when OMR recognised no voice names at all.

    Only applies when *every* sung part has an empty/default name ("Voice", "P1", ...);
    accompaniment parts are left alone. If the score consists only of unlabelled
    keyboard-like groups (Audiveris calls an unnamed braced staff pair "Piano"), it is a
    closed-score hymn: those staves become "S A" / "T B" voices instead of a piano.
    `lines` maps id(part) to the number of sung lines on that staff.
    """
    everything = list(score.parts)
    acc = [is_accompaniment(p) for p in everything]
    parts = everything if everything and all(acc) else [p for p, a in zip(everything, acc) if not a]
    unnamed = [i for i, p in enumerate(parts) if _is_default_name(p.partName)]
    if len(unnamed) == 1 < len(parts):
        _name_the_gap(parts, unnamed[0], lines)
    if not parts or len(unnamed) < len(parts):
        return
    counts = [(lines or {}).get(id(p)) or _voice_count(p) for p in parts]
    lines = counts
    total = sum(lines)
    if total in VOICE_LAYOUTS:
        names = VOICE_LAYOUTS[total]
    elif total == 2:
        signs = [_clef_sign(p, _measures(p)[0]) if _measures(p) else "?" for p in parts]
        names = ["Tenor", "Bass"] if all(s == "F" for s in signs) else ["Soprano", "Alto"]
        if len(parts) == 2 and signs[0] == "G" and signs[1] == "F":
            names = ["Soprano", "Bass"]
    else:
        names = [f"Voice {i + 1}" for i in range(total)]
    it = iter(names)
    for i, (p, n) in enumerate(zip(parts, lines)):
        mine = [next(it) for _ in range(n)]
        label = mine[0] if n == 1 else " ".join(_abbrev(x) for x in mine)
        # Separate parts (not one keyboard-like instrument), with a voice instrument.
        new = stream.Part()
        new.id = f"V{i + 1}"
        for el in list(p.elements):
            if isinstance(el, instrument.Instrument):
                continue
            new.insert(p.elementOffset(el), el)
        voc = instrument.Vocalist()
        voc.partName = label
        new.insert(0, voc)
        new.partName = label
        new.partAbbreviation = " ".join(_abbrev(x) for x in mine)
        score.replace(p, new)


def _name_the_gap(parts: list[stream.Part], i: int, lines: dict[int, int] | None) -> None:
    """Name the one unnamed sung staff `parts[i]` ("Voice" above Alto, Tenor and Bass: OCR
    missed the Soprano label) when the other names are the standard layout around it."""
    layout = VOICE_LAYOUTS.get(len(parts))
    if layout is None or any(((lines or {}).get(id(p)) or 1) != 1 for p in parts):
        return
    if all((p.partName or "").strip().casefold() == name.casefold()
           for j, (p, name) in enumerate(zip(parts, layout)) if j != i):
        part = parts[i]
        part.partName, part.partAbbreviation = layout[i], _abbrev(layout[i])
        for inst in part.getElementsByClass(instrument.Instrument):
            inst.partName = layout[i]


def is_tenor_name(name: str) -> bool:
    """'Tenor', 'Tenors', 'Ten. II', 'T', 'T1' - but not 'Treble' or 'Tutti'."""
    low = name.strip().lower()
    return bool(re.search(r"\btenors?\b|\bten\.", low)
                or re.fullmatch(r"t\.?\s*(\d+|i{1,3}|iv)?", low))


TENOR_WRITTEN_MEDIAN = 63  # MIDI; a tenor line read in plain treble clef sits around C5


def fix_tenor_octave(part: stream.Part) -> bool:
    """Sound tenor lines written in treble clef an octave lower.

    Choral tenor parts are printed in a treble clef with a small "8" below (or, in older
    editions, a plain treble clef that is still sung an octave lower). OMR engines often
    miss the little 8, which would put the tenor practice track an octave too high. Only
    parts named Tenor (T, Ten., Tenor 1, ...) in a G clef without octave change whose
    notes sit unusually high are moved.
    """
    if not is_tenor_name(part.partName or ""):
        return False
    clefs = list(part.recurse().getElementsByClass(clef.Clef))
    if not clefs or any(c.sign != "G" or c.octaveChange != 0 for c in clefs):
        return False
    pitches = sorted(p.ps for n in part.recurse().notes for p in n.pitches)
    if not pitches or pitches[len(pitches) // 2] < TENOR_WRITTEN_MEDIAN:
        return False
    for n in part.recurse().notes:
        for p in n.pitches:
            p.octave = p.implicitOctave - 1
    for c in clefs:
        site = c.activeSite
        if site is not None:
            site.replace(c, clef.Treble8vbClef())
    return True


def _fix_title(result: stream.Score, original: stream.Score, title_hint: str | None) -> None:
    md = result.metadata
    title = md.title if md is not None else None
    movement = md.movementName if md is not None else None
    if title and title != title_hint:
        return
    if movement and movement != title_hint:
        if md is not None and not title:
            md.title = movement
        return
    # Fall back to the largest text on the first page (that's normally the title).
    boxes = [tb for tb in original.getElementsByClass(text.TextBox)
             if (tb.page in (None, 1)) and re.search(r"[A-Za-z]{2}", tb.content or "")]
    if not boxes:
        return
    biggest = max(boxes, key=lambda tb: float(tb.style.fontSize or 0))
    if md is None:
        from music21 import metadata as m21md
        md = m21md.Metadata()
        result.metadata = md
    md.title = biggest.content.strip()


if __name__ == "__main__":  # debugging aid: python -m choirsplit.omr score.pdf
    sc = load(Path(sys.argv[1]))
    for p in sc.parts:
        print(p.id, p.partName, len(p.flatten().notes))
