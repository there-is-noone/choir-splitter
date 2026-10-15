"""MuseScore (.mscz / .mscx) input. Not implemented yet."""

from __future__ import annotations

from pathlib import Path

from music21 import stream


def load(path: Path) -> stream.Score:
    from .loaders import ScoreLoadError

    raise ScoreLoadError(
        f"MuseScore files are not supported yet ({path.name}); export MusicXML from MuseScore instead"
    )
