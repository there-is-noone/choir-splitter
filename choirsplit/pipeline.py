"""End-to-end: score file -> per-voice audio files."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import mixer, synth
from .audio_io import write_audio
from .check import check_score
from .loaders import load_score
from .score import ScoreTracks, extract_tracks

MAX_TRANSPOSE = 48  # semitones either way
MIN_DPI, MAX_DPI = 100, 1200  # --omr-dpi limits
# Device names Windows refuses as file names, with or without an extension.
_WINDOWS_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])")


@dataclass
class Options:
    mode: str = "both"  # "solo", "practice" or "both"
    full_mix: bool = True
    fmt: str = "mp3"
    bitrate: str = "192k"
    bpm: float | None = None
    tempo_scale: float = 1.0
    transpose: int = 0
    others_gain: float = 0.3
    include_accompaniment: bool = True
    split_voices: bool = True
    split_chords: bool = True
    sample_rate: int = synth.SAMPLE_RATE
    only: list[str] = field(default_factory=list)  # restrict to these voice names
    title: str | None = None  # song title for file names and tags (default: from the score)
    export_musicxml: Path | None = None  # also write the (cleaned) score here as MusicXML
    omr_dpi: int | None = None  # resolution PDFs are read at by OMR (default: the engine's)

    def validate(self) -> None:
        """Raise ValueError for a setting that can't work, before any slow loading starts."""
        if self.mode not in ("solo", "practice", "both"):
            raise ValueError(f"unknown mode {self.mode!r}")
        if self.fmt not in ("mp3", "wav"):
            raise ValueError(f"unsupported output format: {self.fmt}")
        if not 0.0 <= self.others_gain <= 1.0:  # also rejects NaN
            raise ValueError(f"others volume must be between 0 and 1, got {self.others_gain}")
        if self.bpm is not None and not (math.isfinite(self.bpm) and self.bpm > 0):
            raise ValueError(f"bpm must be a positive number, got {self.bpm}")
        if not (math.isfinite(self.tempo_scale) and self.tempo_scale > 0):
            raise ValueError(f"tempo scale must be a positive number, got {self.tempo_scale}")
        if abs(self.transpose) > MAX_TRANSPOSE:
            raise ValueError(f"transpose must be within {MAX_TRANSPOSE} semitones, got {self.transpose}")
        if self.title is not None and not self.title.strip():
            raise ValueError("title must not be empty")
        if self.omr_dpi is not None and not MIN_DPI <= self.omr_dpi <= MAX_DPI:
            raise ValueError(f"OMR resolution must be between {MIN_DPI} and {MAX_DPI} dpi, got {self.omr_dpi}")


def safe_filename(name: str, max_bytes: int = 200) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    # Most file systems cap names at 255 bytes; leave room for the extension.
    name = name.encode()[:max_bytes].decode(errors="ignore").strip(" .")
    name = name or "untitled"
    stem = name.split(".")[0].rstrip(" ")
    return f"_{name}" if _WINDOWS_RESERVED.fullmatch(stem) else name


def process_tracks(st: ScoreTracks, out_dir: Path, opts: Options) -> list[Path]:
    opts.validate()
    voices = st.voices
    if not voices:
        raise ValueError("no sung voices found in the score")
    if opts.transpose:
        pitches = [n.pitch + opts.transpose for t in st.tracks for n in t.notes]
        if pitches and not (0 <= min(pitches) and max(pitches) <= 127):
            raise ValueError(f"transposing by {opts.transpose} moves notes outside the MIDI range 0-127")
    only = {o.strip().lower() for o in opts.only}
    unknown = [o for o in opts.only if o.strip().lower() not in {t.name.lower() for t in voices}]
    if unknown:
        raise ValueError(
            f"no voice named {', '.join(repr(o) for o in unknown)}; "
            f"available: {', '.join(t.name for t in voices)}"
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    def render(track, timbre):
        notes = track.notes
        if opts.transpose:
            notes = [type(n)(n.start, n.duration, n.pitch + opts.transpose, n.velocity) for n in notes]
        return synth.render_notes(notes, st.duration, timbre=timbre, sample_rate=opts.sample_rate)

    voice_audio = [render(t, "voice") for t in voices]
    acc_audio = [render(t, "piano") for t in st.accompaniment]

    title = safe_filename(st.title, max_bytes=120)  # leaves room for " - 01 Voice (practice)"
    written: list[Path] = []

    def emit(stem: str, audio, track_title: str) -> None:
        written.append(write_audio(
            out_dir / safe_filename(stem), audio, opts.sample_rate, opts.fmt,
            **({"bitrate": opts.bitrate, "metadata": {"title": track_title, "album": st.title}}
               if opts.fmt == "mp3" else {}),
        ))

    for i, track in enumerate(voices):
        if only and track.name.lower() not in only:
            continue
        prefix = f"{title} - {i + 1:02d} {track.name}"
        if opts.mode in ("solo", "both"):
            emit(prefix, mixer.solo(voice_audio[i]), track.name)
        if opts.mode in ("practice", "both"):
            emit(f"{prefix} (practice)", mixer.practice(
                voice_audio, i, others_gain=opts.others_gain, accompaniment=acc_audio,
            ), f"{track.name} (practice)")
    if opts.full_mix:
        emit(f"{title} - All voices", mixer.full(voice_audio, accompaniment=acc_audio), "All voices")
    return written


def process_file(path: str | Path, out_dir: str | Path, opts: Options | None = None) -> tuple[ScoreTracks, list[Path]]:
    opts = opts or Options()
    st = load_tracks(path, opts)
    return st, process_tracks(st, Path(out_dir), opts)


def load_tracks(path: str | Path, opts: Options | None = None) -> ScoreTracks:
    """Load a score file and split it into tracks as `opts` asks (shared by the CLI)."""
    opts = opts or Options()
    opts.validate()
    path = Path(path)
    score = load_score(path, omr_dpi=opts.omr_dpi)
    if opts.export_musicxml is not None:
        export = Path(opts.export_musicxml)
        export.parent.mkdir(parents=True, exist_ok=True)
        score.write("musicxml", fp=str(export))
    # Repairs OMR made, then the bars that still look wrong (for any input).
    issues = list(getattr(score, "omr_issues", [])) + check_score(score, getattr(score, "omr_locations", None))
    st = extract_tracks(
        score,
        bpm=opts.bpm,
        tempo_scale=opts.tempo_scale,
        split_voices=opts.split_voices,
        split_chords=opts.split_chords,
        include_accompaniment=opts.include_accompaniment,
    )
    st.issues = issues
    st.recognised = hasattr(score, "omr_issues")
    if opts.title is not None:
        st.title = opts.title.strip()
    elif st.title == "Score":
        st.title = path.stem
    return st
