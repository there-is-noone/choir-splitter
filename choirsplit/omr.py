"""PDF / image input via optical music recognition. Not implemented yet."""

from __future__ import annotations

from pathlib import Path

from music21 import stream


def load(path: Path) -> stream.Score:
    from .loaders import ScoreLoadError

    raise ScoreLoadError(
        f"PDF/image scores are not supported yet ({path.name}); export MusicXML instead"
    )
