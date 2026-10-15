"""Load any supported score file into a music21 Score."""

from __future__ import annotations

from pathlib import Path

from music21 import converter, stream

MUSIC21_SUFFIXES = {
    ".musicxml", ".mxl", ".xml", ".mid", ".midi", ".krn", ".abc", ".mei", ".capx", ".md",
}
MUSESCORE_SUFFIXES = {".mscz", ".mscx"}
OMR_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


class ScoreLoadError(RuntimeError):
    pass


def supported_suffixes() -> set[str]:
    return MUSIC21_SUFFIXES | MUSESCORE_SUFFIXES | OMR_SUFFIXES


def load_score(path: str | Path) -> stream.Score:
    path = Path(path)
    if not path.exists():
        raise ScoreLoadError(f"file not found: {path}")
    suffix = path.suffix.lower()
    if suffix in MUSESCORE_SUFFIXES:
        from . import musescore
        return musescore.load(path)
    if suffix in OMR_SUFFIXES:
        from . import omr
        return omr.load(path)
    try:
        parsed = converter.parse(str(path))
    except Exception as exc:
        raise ScoreLoadError(f"could not parse {path.name}: {exc}") from exc
    if isinstance(parsed, stream.Opus):
        if not parsed.scores:
            raise ScoreLoadError(f"{path.name} contains no scores")
        parsed = parsed.scores[0]
    if isinstance(parsed, stream.Part):
        score = stream.Score()
        score.insert(0, parsed)
        parsed = score
    if not isinstance(parsed, stream.Score):
        raise ScoreLoadError(f"{path.name} did not parse into a score")
    return parsed
