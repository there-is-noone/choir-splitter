from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from music21 import converter, stream

from . import jobs, platforms
from .pdf import (
    NO_STAVES,
    TOO_LARGE_IMAGE,
    default_timeout,
    fitting_dpi,
    natural_key,
    pdf_page_count,
    rasterize_pdf,
)
from .types import MUSICXML_SUFFIXES, Engine, EngineError, _error

AUDIVERIS_PDF_RESOLUTION = "org.audiveris.omr.image.ImageLoading.pdfResolution"
SECOND_READING_DPI, AUDIVERIS_DEFAULT_DPI = 250, 300
SHEET = re.compile(r"\[[^\]#]*#(\d+)\]")
_NOISE = re.compile(
    r"^\s*(Estimating resolution as \d+|at [\w$.<>/]+\(.*\)|\.\.\. \d+ more)\s*$"
)
_LAUNCHERS = (
    "bin/Audiveris",
    "Audiveris",
    "bin/audiveris",
    "Audiveris.exe",
    "Contents/MacOS/Audiveris",
)


def _system_locations() -> list[Path]:
    if platforms.is_windows():
        return [d / "Audiveris" for d in platforms.program_dirs()]
    if platforms.is_macos():
        return [d / "Audiveris.app" for d in platforms.program_dirs()]
    return [Path("/opt/audiveris")]


SYSTEM_LOCATIONS = _system_locations()


def reset_dir(p: Path) -> None:
    shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)


def data_home() -> Path:
    return platforms.data_dir()


def install_hint() -> str:
    return (
        "PDF and image scores need an optical music recognition engine (Audiveris), and none was found.\n"
        "  Windows: the 'windowsConsole' .msi installer, macOS: the .dmg, both from\n"
        "      https://github.com/Audiveris/audiveris/releases\n"
        "  Linux: scripts/install_omr.sh (no root needed, about 210 MB)\n"
        "Or point CHOIRSPLIT_AUDIVERIS at an existing Audiveris program or install folder, "
        "or set CHOIRSPLIT_OMR_CMD to another OMR command such as 'oemer -o {outdir} {image}'.\n"
        "Alternatively export MusicXML from your notation program and use that file."
    )


def _audiveris_from(path: Path) -> Path | None:
    if path.is_dir():
        for cand in (path / rel for rel in _LAUNCHERS):
            if cand.is_file() and os.access(cand, os.X_OK):
                return cand
        return None
    return path if path.is_file() and os.access(path, os.X_OK) else None


def _audiveris_version(exe: Path) -> str:
    here = exe.resolve().parent
    for cfg in (
        p / "Audiveris.cfg"
        for p in (here.parent / "lib" / "app", here / "app", here.parent / "app")
    ):
        try:
            if cfg.is_file() and (
                m := re.search(
                    r"app-version=([\w.\-]+)", cfg.read_text("utf-8", errors="replace")
                )
            ):
                return m.group(1)
        except OSError:
            continue
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
    if not platforms.is_windows():
        return shlex.split(command)
    tokens = shlex.split(command, posix=False)
    return [
        t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t
        for t in tokens
    ]


def find_engine() -> Engine | None:
    if custom := os.environ.get("CHOIRSPLIT_OMR_CMD", "").strip():
        tokens = split_command(custom)
        joined = " ".join(tokens)
        if "{image}" not in joined and "{input}" not in joined:
            raise _error(
                "CHOIRSPLIT_OMR_CMD must contain {input} or {image} (and usually {outdir})"
            )
        return Engine("custom", tokens, per_page="{image}" in joined)

    if explicit := os.environ.get("CHOIRSPLIT_AUDIVERIS", "").strip():
        if (exe := _audiveris_from(Path(explicit).expanduser())) is None:
            raise _error(
                f"CHOIRSPLIT_AUDIVERIS={explicit} is not an Audiveris launcher or install directory"
            )
        return _audiveris_engine(exe)

    for name in ("Audiveris", "audiveris"):
        if found := shutil.which(name):
            return _audiveris_engine(Path(found))

    for base in (data_home() / "audiveris", *SYSTEM_LOCATIONS):
        if exe := _audiveris_from(base):
            return _audiveris_engine(exe)
    return None


def collect_outputs(outdir: Path) -> list[Path]:
    files = [
        p
        for p in outdir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in MUSICXML_SUFFIXES
        and not p.name.startswith(".")
        and p.parent.name != "META-INF"
    ]
    return sorted(
        files,
        key=lambda p: (natural_key(str(p.parent.relative_to(outdir))), natural_key(p)),
    )


def _tail(text_: str, lines: int = 15) -> str:
    keep = [
        ln for ln in text_.strip().splitlines() if ln.strip() and not _NOISE.match(ln)
    ][-lines:]
    return "\n".join("    " + ln for ln in keep)


def _run(
    argv: list[str],
    timeout: float,
    env: dict[str, str],
    what: str,
    pages: int | None = None,
) -> str:
    full_env = {**os.environ, **env}
    seen = set()

    def on_line(line: str) -> None:
        if (m := SHEET.search(line)) and m.group(1) not in seen:
            seen.add(m.group(1))
            jobs.progress(
                f"reading page {m.group(1)}" + (f" of {pages}" if pages else "")
            )

    try:
        proc = jobs.run_process(argv, timeout=timeout, env=full_env, on_line=on_line)
    except subprocess.TimeoutExpired:
        raise _error(
            f"{what} timed out after {timeout:.0f} s (set CHOIRSPLIT_OMR_TIMEOUT to allow more time)"
        ) from None
    except OSError as exc:
        raise _error(f"could not run {what} ({argv[0]}): {exc}") from exc
    output = proc.stderr + proc.stdout
    if proc.returncode != 0:
        raise EngineError(
            f"{what} failed with exit code {proc.returncode}:\n{_tail(output)}", output
        )
    return output


def _run_audiveris(
    engine: Engine, path: Path, outdir: Path, timeout: float, dpi: int | None
) -> tuple[int | None, str]:
    is_pdf = path.suffix.lower() == ".pdf"
    pages = pdf_page_count(path) if is_pdf else None
    skipped: set[int] = set()
    resized = False
    while True:
        options = (
            ["-constant", f"{AUDIVERIS_PDF_RESOLUTION}={dpi}"]
            if dpi is not None and is_pdf
            else []
        )
        if skipped:
            options += [
                "-sheets",
                *_sheet_ranges(sorted(set(range(1, pages + 1)) - skipped)),
            ]
        argv = [
            *engine.argv,
            "-batch",
            "-export",
            *options,
            "-output",
            str(outdir),
            "--",
            str(path),
        ]
        try:
            output, failure = _run(argv, timeout, engine.env, "Audiveris", pages), None
        except EngineError as exc:
            output, failure = exc.output, exc
        smaller = fitting_dpi(output, dpi)
        if smaller is None:
            if failure is None:
                return dpi, output
            staffless = {int(n) for n in NO_STAVES.findall(output)} - skipped
            if (
                not staffless
                or not pages
                or not set(range(1, pages + 1)) - skipped - staffless
            ):
                raise failure
            skipped |= staffless
            jobs.report(
                f"{path.name}: no staves found on page{'s' if len(skipped) > 1 else ''} "
                f"{', '.join(map(str, sorted(skipped)))}; reading the other pages"
            )
            reset_dir(outdir)
            continue
        if not is_pdf or resized or smaller < 50:
            m = TOO_LARGE_IMAGE.search(output)
            raise _error(
                f"{path.name} is too large for Audiveris ({m.group(1)} pixels, at most "
                f"{m.group(2)}); scale it down and try again"
            )
        jobs.report(
            f"the pages of {path.name} are too large for Audiveris at {dpi or AUDIVERIS_DEFAULT_DPI} dpi; reading them at {smaller} dpi"
        )
        dpi, resized = smaller, True
        reset_dir(outdir)


def _sheet_ranges(numbers: list[int]) -> list[str]:
    runs: list[list[int]] = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return [f"{a}-{b}" if b > a else str(a) for a, b in runs]


def run_engine(
    engine: Engine,
    path: Path,
    outdir: Path,
    timeout: float | None = None,
    dpi: int | None = None,
) -> tuple[list[Path], int | None]:
    timeout = default_timeout(path) if timeout is None else timeout
    outdir.mkdir(parents=True, exist_ok=True)
    if engine.name == "audiveris":
        dpi, output = _run_audiveris(engine, path, outdir, timeout, dpi)
    else:
        output = ""
        pages = (
            rasterize_pdf(path, outdir / "_pages", dpi=dpi or 300)
            if (engine.per_page and path.suffix.lower() == ".pdf")
            else [path]
        )
        if not pages:
            raise _error(f"{path.name}: no pages could be rendered")
        per_page_timeout = timeout / max(len(pages), 1) if len(pages) > 1 else timeout
        for i, page in enumerate(pages, 1):
            page_out = outdir / f"page-{i:04d}" if len(pages) > 1 else outdir
            page_out.mkdir(parents=True, exist_ok=True)
            argv = [
                t.replace("{input}", str(page))
                .replace("{image}", str(page))
                .replace("{outdir}", str(page_out))
                for t in engine.argv
            ]
            jobs.progress(f"reading page {i} of {len(pages)}")
            output = _run(
                argv,
                max(per_page_timeout, 60.0),
                engine.env,
                f"OMR command ({engine.argv[0]})",
            )
        shutil.rmtree(outdir / "_pages", ignore_errors=True)
    if not (files := collect_outputs(outdir)):
        raise _error(
            f"OMR produced no MusicXML for {path.name}; the scan may be too blurry, skewed or "
            f"low-resolution (300 dpi recommended).\nEngine output:\n{_tail(output)}"
        )
    return files, dpi


def _cache_root() -> Path | None:
    if (
        env := os.environ.get("CHOIRSPLIT_OMR_CACHE")
    ) is not None and env.strip().lower() in {"0", "off", "no", "false", ""}:
        return None
    return Path(env).expanduser() if env else platforms.cache_dir() / "omr"


def cache_key(path: Path, engine: Engine, dpi: int | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    h.update(engine.cache_tag.encode())
    if dpi is not None:
        h.update(f"|dpi={dpi}".encode())
    return h.hexdigest()[:32]


def _store(entry: Path, files: list[Path], stamp: str) -> None:
    staging = entry.with_name(entry.name + ".tmp")
    reset_dir(staging)
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


def recognise(
    path: Path, engine: Engine, dpi: int | None = None
) -> tuple[list[stream.Score], int | None]:
    root = _cache_root()
    entry = root / cache_key(path, engine, dpi) if root else None
    if (
        entry is not None
        and (entry / "done").exists()
        and (files := collect_outputs(entry))
    ):
        stamp = (entry / "done").read_text("utf-8").split("\n")
        read_at = stamp[2] if len(stamp) > 2 else ""
        return parse_outputs(files), int(read_at) if read_at.isdigit() else dpi
    with tempfile.TemporaryDirectory(prefix="choirsplit-omr-") as tmp:
        files, read_at = run_engine(engine, path, Path(tmp) / "out", dpi=dpi)
        scores = parse_outputs(files)
        if entry is not None:
            try:
                _store(
                    entry, files, f"{path.name}\n{engine.cache_tag}\n{read_at or ''}\n"
                )
            except (OSError, UnicodeError) as exc:
                jobs.report(f"could not cache OMR result: {exc}")
    return scores, read_at
