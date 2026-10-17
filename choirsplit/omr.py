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
from dataclasses import dataclass, field
from pathlib import Path

from music21 import clef, converter, instrument, layout, meter, note, stream, text

from . import platforms
from .score import is_accompaniment

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
_EPS = 1e-6


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
    keep = [ln for ln in text_.strip().splitlines() if ln.strip()][-lines:]
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
    if proc.returncode != 0:
        raise _error(f"{what} failed with exit code {proc.returncode}:\n{_tail(proc.stdout + proc.stderr)}")
    _run.last_output = proc.stdout + proc.stderr  # for error messages when nothing is exported


_run.last_output = ""


def run_engine(engine: Engine, path: Path, outdir: Path, timeout: float | None = None) -> list[Path]:
    """Run OMR on `path`, return the produced MusicXML files in score order."""
    timeout = default_timeout(path) if timeout is None else timeout
    outdir.mkdir(parents=True, exist_ok=True)
    if engine.name == "audiveris":
        argv = [*engine.argv, "-batch", "-export", "-output", str(outdir), "--", str(path)]
        _run(argv, timeout, engine.env, "Audiveris")
    else:
        if engine.per_page and path.suffix.lower() == ".pdf":
            pages = rasterize_pdf(path, outdir / "_pages")
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
    return files


# --------------------------------------------------------------------------- cache


def _cache_root() -> Path | None:
    env = os.environ.get("CHOIRSPLIT_OMR_CACHE")
    if env is not None and env.strip().lower() in {"0", "off", "no", "false", ""}:
        return None
    if env:
        return Path(env).expanduser()
    return platforms.cache_dir() / "omr"


def cache_key(path: Path, engine: Engine) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    h.update(engine.cache_tag.encode())
    return h.hexdigest()[:32]


def recognise(path: Path, engine: Engine) -> list[stream.Score]:
    """Run OMR on `path` (or reuse the cached result) and parse the MusicXML it produced."""
    root = _cache_root()
    entry = root / cache_key(path, engine) if root else None
    if entry is not None and (entry / "done").exists():
        files = collect_outputs(entry)
        if files:
            return parse_outputs(files)
    with tempfile.TemporaryDirectory(prefix="choirsplit-omr-") as tmp:
        files = run_engine(engine, path, Path(tmp) / "out")
        scores = parse_outputs(files)
        if entry is not None:
            try:
                _store(entry, files, f"{path.name}\n{engine.cache_tag}\n")
            except OSError as exc:  # a read-only cache must not break loading
                print(f"choirsplit: could not cache OMR result: {exc}", file=sys.stderr)
    return scores


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


def load(path: Path) -> stream.Score:
    path = Path(path)
    engine = find_engine()
    if engine is None:
        raise _error(f"cannot read {path.name}: " + install_hint())
    scores = recognise(path, engine)
    if not scores:
        raise _error(f"OMR found no music in {path.name}")
    return assemble(scores, title_hint=path.stem)


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
    for g in groups:
        for p in g:
            for i, m in enumerate(_measures(p)):
                if any(getattr(x, "isNew", False) for x in m.getElementsByClass((layout.SystemLayout, layout.PageLayout))):
                    starts.add(i)
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
            segments.append(Segment(units, range(a, b)))
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


def _nominal_lengths(seg: Segment) -> list[float]:
    """Length (quarters) of each measure of a segment: the most common across its staves."""
    out = []
    for i in seg.measure_range:
        lengths = []
        bar = None
        for u in seg.units:
            for p in u.staves:
                ms = _measures(p)
                if i < len(ms):
                    lengths.append(round(_content_length(ms[i]), 4))
                    if bar is None:
                        ts = ms[i].getContextByClass(meter.TimeSignature)
                        bar = float(ts.barDuration.quarterLength) if ts is not None else None
        lengths = [x for x in lengths if x > 0]
        if not lengths:
            out.append(bar or 4.0)
            continue
        counts = Counter(lengths).most_common()
        top = [v for v, c in counts if c == counts[0][1]]
        out.append(bar if bar is not None and any(abs(v - bar) < _EPS for v in top) else max(top))
    return out


def clip_measure(m: stream.Measure, length: float) -> None:
    """Drop or shorten notes/rests that run past the end of a `length`-quarter measure."""
    for el in list(m.recurse().notesAndRests):
        start = float(el.getOffsetInHierarchy(m))
        end = start + float(el.duration.quarterLength)
        if start >= length - _EPS:
            el.activeSite.remove(el)
        elif end > length + _EPS:
            el.duration.quarterLength = length - start


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


def _repair_line(line: list, ref: list[tuple[float | None, float, float, bool]], length: float) -> bool:
    """Add the dot OMR missed to notes of an under-full line, if the line's note heads
    line up with the onsets of the complete lines in the same system and adding a dot
    (and nothing else) to some notes makes every note start where its head is printed.

    Lengthening the *last* note is only done when the complete lines confirm it: at
    least two of their notes start and end with it and nothing starts during the
    added time. Otherwise the gap is more likely a rest OMR missed (a half note and
    an unrecognised quarter rest, not a dotted half)."""
    if any(e.duration.tuplets or e.duration.isGrace for e in line):
        return False
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
    return True


def repair_missing_dots(seg: Segment, lengths: list[float]) -> int:
    """Restore augmentation dots OMR missed (a dotted quarter read as a quarter).

    A missed dot leaves a line short of the bar and pulls its later notes early. Within
    one system, notes struck together are printed at the same x position on every
    staff, so the complete lines of the bar tell where each note of the short line
    really starts. Only lines where every note then lines up and the only differences
    are single missing dots are changed. Returns the number of lines repaired.
    """
    repaired = 0
    for i, length in zip(seg.measure_range, lengths):
        measures = [ms[i] for u in seg.units for p in u.staves
                    for ms in [_measures(p)] if i < len(ms)]
        ref = None
        for m in measures:
            for line in _lines(m):
                if sum(float(e.duration.quarterLength) for e in line) < length - _EPS:
                    if ref is None:
                        ref = _complete_lines(measures, length)
                    repaired += _repair_line(line, ref, length)
    return repaired


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
    """Merge recognised pages/movements into one score with one part per voice group."""
    segments = [seg for s in scores for seg in segments_of(s)]
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

    offset = 0.0
    number = 0
    previous_source: list[list[int | None]] = [[None] * n for n in n_staves]
    for seg_index, (seg, mapping) in enumerate(zip(segments, mappings)):
        lengths = _nominal_lengths(seg)
        repair_missing_dots(seg, lengths)
        slot_unit = {k: u for u, k in zip(seg.units, mapping)}
        for local, (i, length) in enumerate(zip(seg.measure_range, lengths)):
            number += 1
            for k in range(n_logical):
                u = slot_unit.get(k)
                for s, dest in enumerate(out_parts[k]):
                    src = u.staves[s] if u is not None and s < len(u.staves) else None
                    src_ms = _measures(src) if src is not None else []
                    if src is not None and i < len(src_ms):
                        m = copy.deepcopy(src_ms[i])
                        if previous_source[k][s] != id(src):
                            _carry_context(src, src_ms[i], m)
                            previous_source[k][s] = id(src)
                        clip_measure(m, length)
                    else:
                        m = _rest_measure(length)
                        previous_source[k][s] = None
                    m.number = number
                    dest.insert(offset, m)
            offset += length

    result = stream.Score()
    md = copy.deepcopy(scores[0].metadata) if scores[0].metadata is not None else None
    hymn = _closed_score(out_parts)
    lines: dict[int, int] = {}
    for staves in out_parts:
        for p in staves:
            if is_accompaniment(p) and not hymn:
                lines[id(p)] = 1  # piano & co. are played as written
            else:
                lines[id(p)] = tidy_voices(p)
            result.insert(0, p)
    name_unnamed_parts(result, lines)
    for p in result.parts:
        fix_tenor_octave(p)
    if md is not None:
        result.metadata = md
    _fix_title(result, scores[0], title_hint)
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


def drop_notes_under_bar_rests(part: stream.Part) -> int:
    """Remove stray notes OMR put on a sung staff in a bar that shows a whole-bar rest.

    Audiveris sometimes reads a piano note head, ledger line or tie just below/above a
    resting voice staff as a note in a second voice next to the printed whole-bar rest
    (e.g. the bass "singing" in the piano introduction). A whole-bar rest (MusicXML
    ``<rest measure="yes">``) means the staff is silent, so on a single-line staff whose
    neighbouring bars are silent too, the other voice's notes are dropped. Real
    two-voice staves (one voice resting while the other sings) are left alone.
    Returns the number of bars cleaned.
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
    return cleaned


def tidy_voices(part: stream.Part) -> int:
    """Clean up OMR voices on one staff; return how many sung lines it carries (1 or 2).

    * voice splits that never sound together are merged into one line;
    * a staff whose voices really overlap (S+A or T+B on one staff) is re-voiced into
      exactly two voices by pitch, whatever voice numbers the OMR engine used - engines
      put stray notes in voice 3 or write a shared-stem S/A interval as a chord in one
      voice, which would otherwise create extra tracks or silence the alto;
    * a single voice written mostly in two-note chords also counts as two lines (score.py
      splits those top/bottom when the staff is named for two voices).
    """
    drop_notes_under_bar_rests(part)
    if simplify_voices(part):
        return 2 if _chordal(_measures(part)) else 1
    revoice_two_lines(part)
    return 2


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
    if not parts or any(not _is_default_name(p.partName) for p in parts):
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
