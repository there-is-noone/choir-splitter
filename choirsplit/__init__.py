"""choirsplit: turn choir scores into separate audio tracks for each voice."""

from .pipeline import Options, process_file
from .score import NoteEvent, ScoreTracks, Track, extract_tracks

__all__ = ["NoteEvent", "Options", "ScoreTracks", "Track", "extract_tracks", "process_file"]
__version__ = "0.1.0"
