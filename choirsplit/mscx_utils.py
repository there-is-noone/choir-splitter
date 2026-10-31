"""Pure helper functions for the MuseScore (.mscz/.mscx) parser.

Covers duration and fraction arithmetic, tonal pitch class (tpc) to note
names, tempo conversion, reading score XML out of .mscz/.mscx files without
extracting to disk, locating a MuseScore CLI, and classifying instruments.
"""

import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from fractions import Fraction
from pathlib import Path

from . import platforms

_DURATION_BASE: dict[str, Fraction] = {
    "long": Fraction(16),
    "breve": Fraction(8),
    "whole": Fraction(4),
    "half": Fraction(2),
    "quarter": Fraction(1),
    "eighth": Fraction(1, 2),
    "16th": Fraction(1, 4),
    "32nd": Fraction(1, 8),
    "64th": Fraction(1, 16),
    "128th": Fraction(1, 32),
    "256th": Fraction(1, 64),
    "512th": Fraction(1, 128),
    "1024th": Fraction(1, 256),
}

_FRACTION_RE = re.compile(r"(-?[0-9]+)(?:/([0-9]+))?")

_TPC_STEPS = "FCGDAEB"
_TPC_MIN = -1  # MuseScore range: -1 (F double-flat) .. 33 (B double-sharp)
_TPC_MAX = 33

_MAX_ENTRY_BYTES = 50 * 1024 * 1024

_MUSESCORE_NAMES = (
    "mscore",
    "mscore4portable",
    "musescore",
    "musescore4",
    "MuseScore4",
    "mscore3",
    "MuseScore3",
)
_FLATPAK_APP_ID = "org.musescore.MuseScore"

_VOICE_WORDS = (
    "voice",
    "soprano",
    "alto",
    "tenor",
    "bass",
    "baritone",
    "mezzo",
    "contralto",
    "choir",
    "chorus",
    "treble",
    "countertenor",
    "vocal",
)


def duration_ql(duration_type: str, dots: int = 0) -> Fraction:
    """Return the length in quarter notes of a MuseScore durationType with dots."""
    if duration_type == "measure":
        raise ValueError("'measure' has no fixed duration")
    try:
        base = _DURATION_BASE[duration_type]
    except KeyError:
        raise ValueError(f"unknown durationType: {duration_type!r}") from None
    if dots < 0:
        raise ValueError(f"dots must be non-negative, got {dots}")
    return base * (2 - Fraction(1, 2**dots))


def parse_fraction(text: str) -> Fraction:
    """Parse a whole-note fraction such as '3/8', '-1/4' or '2'."""
    match = _FRACTION_RE.fullmatch(text.strip()) if isinstance(text, str) else None
    if match is None:
        raise ValueError(f"not a fraction: {text!r}")
    numerator = int(match.group(1))
    denominator = int(match.group(2)) if match.group(2) is not None else 1
    if denominator == 0:
        raise ValueError(f"zero denominator in fraction: {text!r}")
    return Fraction(numerator, denominator)


def fraction_to_ql(fr: Fraction) -> Fraction:
    """Convert a whole-note fraction to quarter lengths."""
    return Fraction(fr) * 4


def tpc_to_name(tpc: int) -> str:
    """Convert a MuseScore tonal pitch class to a music21 note name without octave."""
    if tpc < _TPC_MIN or tpc > _TPC_MAX:
        raise ValueError(f"tpc out of range {_TPC_MIN}..{_TPC_MAX}: {tpc}")
    step = _TPC_STEPS[(tpc + 1) % 7]
    alter = (tpc + 1) // 7 - 2
    if alter == 0:
        return step
    if alter < 0:
        return step + "-" * -alter
    return step + "#" * alter


def tempo_to_bpm(quarters_per_second: float) -> float:
    """Convert quarter notes per second to beats (quarter notes) per minute."""
    if not quarters_per_second > 0:
        raise ValueError(f"tempo must be positive, got {quarters_per_second}")
    return float(quarters_per_second) * 60.0


def read_mscx_bytes(path) -> bytes:
    """Return the score XML bytes from a .mscx file or a .mscz archive.

    Never extracts to disk. For .mscz the score is chosen from
    META-INF/container.xml, falling back to the first .mscx entry outside
    Excerpts/ (root-level entries preferred).
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".mscx":
        return p.read_bytes()
    if suffix != ".mscz":
        raise ValueError(f"expected a .mscz or .mscx file, got {p.suffix!r}")

    try:
        zf = zipfile.ZipFile(p)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a valid .mscz (zip) archive: {p}") from exc
    with zf:
        try:
            name = _pick_mscx_entry(zf)
            info = zf.getinfo(name)
            if info.file_size > _MAX_ENTRY_BYTES:
                raise ValueError(
                    f"entry {name!r} is too large ({info.file_size} bytes); "
                    f"limit is {_MAX_ENTRY_BYTES} bytes"
                )
            return zf.read(name)
        except zipfile.BadZipFile as exc:
            raise ValueError(f"corrupt entry in .mscz archive {p}: {exc}") from exc


def _container_rootfiles(zf: zipfile.ZipFile) -> list[str]:
    """Return rootfile full-path values declared in META-INF/container.xml."""
    try:
        data = zf.read("META-INF/container.xml")
    except KeyError:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    paths = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "rootfile":
            full_path = element.get("full-path")
            if full_path:
                paths.append(full_path)
    return paths


def _pick_mscx_entry(zf: zipfile.ZipFile) -> str:
    """Choose the score entry name inside an open .mscz archive."""
    names = zf.namelist()
    for full_path in _container_rootfiles(zf):
        if (full_path.lower().endswith(".mscx") and full_path in names
                and not full_path.startswith("Excerpts/")):
            return full_path
    candidates = [
        n for n in names if n.lower().endswith(".mscx") and not n.startswith("Excerpts/")
    ]
    if not candidates:
        raise ValueError("no .mscx score found in .mscz archive")
    # Stable sort: root-level entries first, archive order otherwise.
    candidates.sort(key=lambda n: "/" in n)
    return candidates[0]


def _musescore_install_paths() -> list[Path]:
    """Where the MuseScore 4 and 3 installers put the program on Windows and macOS."""
    if platforms.is_windows():
        return [d / f"MuseScore {v}" / "bin" / f"MuseScore{v}.exe"
                for v in (4, 3) for d in platforms.program_dirs()]
    if platforms.is_macos():
        return [d / f"MuseScore {v}.app" / "Contents" / "MacOS" / "mscore"
                for v in (4, 3) for d in platforms.program_dirs()]
    return []


def find_musescore_cli() -> list[str] | None:
    """Return the command prefix that runs MuseScore, or None if not found.

    Order: CHOIRSPLIT_MUSESCORE env var (file or PATH name), well-known
    executable names on PATH, the installers' default locations on Windows and
    macOS, then the flatpak MuseScore app.
    """
    env_value = os.environ.get("CHOIRSPLIT_MUSESCORE", "").strip()
    if env_value:
        if os.path.isfile(env_value):
            return [env_value]
        found = shutil.which(env_value)
        if found:
            return [found]

    for name in _MUSESCORE_NAMES:
        found = shutil.which(name)
        if found:
            return [found]

    for exe in _musescore_install_paths():
        if exe.is_file():
            return [str(exe)]

    if shutil.which("flatpak"):
        try:
            result = subprocess.run(
                ["flatpak", "info", _FLATPAK_APP_ID],
                capture_output=True,
                timeout=10,
                check=False,
                **platforms.no_console(),
            )
        except Exception:
            return None
        if result.returncode == 0:
            return ["flatpak", "run", _FLATPAK_APP_ID]
    return None


def instrument_kind(instrument_id: str | None, names: list[str]) -> str:
    """Classify a MuseScore instrument as piano, organ, keyboard, voice, percussion or other."""
    iid = (instrument_id or "").lower()
    text = " ".join([iid] + [n for n in names if n]).lower()

    def has(*words: str) -> bool:
        return any(w in text for w in words)

    # Pluck and string instruments are never voices, even if named "bass".
    excluded_from_voice = iid.startswith(("pluck.", "strings."))

    def is_voice() -> bool:
        return not excluded_from_voice and has(*_VOICE_WORDS)

    if has("organ", "harmonium"):
        return "organ"
    if has("piano", "pno"):
        return "piano"
    if iid.startswith("voice.") and is_voice():
        return "voice"
    if has("keyboard", "harpsichord", "celesta", "clavi", "synth"):
        return "keyboard"
    if has("drum", "percussion") or iid.startswith(("drum.", "perc")):
        return "percussion"
    if is_voice():
        return "voice"
    return "other"
