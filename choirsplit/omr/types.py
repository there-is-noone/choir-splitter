from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from music21 import stream

from .loaders import ScoreLoadError

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
MUSICXML_SUFFIXES = {".mxl", ".musicxml", ".xml"}
CACHE_VERSION = "1"


class EngineError(ScoreLoadError):
    """An OMR run failed; carries its output, which decides whether to read again."""

    def __init__(self, message: str, output: str):
        super().__init__(message)
        self.output = output


def _error(msg: str) -> Exception:
    return ScoreLoadError(msg)


@dataclass
class Engine:
    name: str
    argv: list[str]
    version: str = "unknown"
    per_page: bool = False
    env: dict[str, str] = field(default_factory=dict)

    @property
    def cache_tag(self) -> str:
        return f"{self.name}|{self.version}|{' '.join(self.argv)}|{CACHE_VERSION}"


@dataclass
class Unit:
    """One instrument/voice group on one system: one Part, or the PartStaffs of one part."""

    staves: list[stream.Part]
    name: str
    abbreviation: str
    clefs: tuple[str, ...]
    key: str


@dataclass
class Segment:
    """A run of measures (one system, or a whole score without layout info)."""

    units: list[Unit]
    measure_range: range
    new_page: bool = False


Report = Callable[[int, int, int, str], None]
