"""End-to-end: score file -> per-voice audio files."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from music21.exceptions21 import Music21Exception

from . import jobs, mixer, synth
from .audio_io import write_audio
from .check import Issue, check_score
from .loaders import OMR_SUFFIXES, ScoreLoadError, load_score
from .score import ScoreTracks, extract_tracks

MAX_TRANSPOSE = 48  # semitones either way
MIN_DPI, MAX_DPI = 100, 1200  # --omr-dpi limits
# Tempo limits: far beyond them a track is either silence-length nonsense or so long its
# audio no longer fits in memory (0.05x speed on a 4-minute, 8-voice piece needs ~7 GB).
MIN_SCALE, MAX_SCALE = 0.25, 4.0
MIN_BPM, MAX_BPM = 10.0, 400.0
BITRATE = re.compile(r"(\d{2,3})k")
MIN_KBPS, MAX_KBPS = 32, 320
MIN_DURATION = 0.1  # seconds; a shorter score is a broken tempo, not music
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

    def load_key(self) -> tuple:
        """The settings load_tracks uses; the tracks it returns are only valid for them."""
        return (self.bpm, self.tempo_scale, self.split_voices, self.split_chords,
                self.include_accompaniment, self.title, self.omr_dpi)

    def validate(self) -> None:
        """Raise ValueError for a setting that can't work, before any slow loading starts."""
        if self.mode not in ("solo", "practice", "both"):
            raise ValueError(f"unknown mode {self.mode!r}")
        if self.fmt not in ("mp3", "wav"):
            raise ValueError(f"unsupported output format: {self.fmt}")
        if isinstance(self.others_gain, bool) or not 0.0 <= self.others_gain <= 1.0:  # also NaN
            raise ValueError(f"others volume must be between 0 and 1, got {self.others_gain}")
        if self.bpm is not None and not (math.isfinite(self.bpm) and self.bpm > 0):
            raise ValueError(f"bpm must be a positive number, got {self.bpm}")
        if self.bpm is not None and not MIN_BPM <= self.bpm <= MAX_BPM:
            raise ValueError(f"bpm must be between {MIN_BPM:g} and {MAX_BPM:g}, got {self.bpm:g}")
        if not (math.isfinite(self.tempo_scale) and self.tempo_scale > 0):
            raise ValueError(f"tempo scale must be a positive number, got {self.tempo_scale}")
        if not MIN_SCALE <= self.tempo_scale <= MAX_SCALE:
            raise ValueError(f"tempo scale must be between {MIN_SCALE:g} and {MAX_SCALE:g}, "
                             f"got {self.tempo_scale:g}")
        m = BITRATE.fullmatch(self.bitrate) if isinstance(self.bitrate, str) else None
        if m is None or not MIN_KBPS <= int(m.group(1)) <= MAX_KBPS:
            raise ValueError(f"bitrate must be like 192k, between {MIN_KBPS}k and {MAX_KBPS}k, "
                             f"got {self.bitrate!r}")
        if not isinstance(self.only, (list, tuple)) or not all(isinstance(o, str) for o in self.only):
            raise ValueError(f"only must be a list of voice names, got {self.only!r}")
        if abs(self.transpose) > MAX_TRANSPOSE:
            raise ValueError(f"transpose must be within {MAX_TRANSPOSE} semitones, got {self.transpose}")
        if self.title is not None and not self.title.strip():
            raise ValueError("title must not be empty")
        if self.omr_dpi is not None and not MIN_DPI <= self.omr_dpi <= MAX_DPI:
            raise ValueError(f"OMR resolution must be between {MIN_DPI} and {MAX_DPI} dpi, got {self.omr_dpi}")


def safe_filename(name: str, max_bytes: int = 200) -> str:
    # Undecodable bytes in a file name (surrogates) become "?", and then "_".
    name = name.encode("utf-8", "replace").decode()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    # Most file systems cap names at 255 bytes; leave room for the extension.
    name = name.encode()[:max_bytes].decode(errors="ignore").strip(" .")
    name = name or "untitled"
    stem = name.split(".")[0].rstrip(" ")
    return f"_{name}" if _WINDOWS_RESERVED.fullmatch(stem) else name


def process_tracks(st: ScoreTracks, out_dir: Path, opts: Options) -> list[Path]:
    opts.validate()
    if st.load_key is not None and st.load_key != opts.load_key():
        raise ValueError("the score was loaded with other tempo, split or title settings; load it again")
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
        jobs.check_cancelled()
        jobs.progress(f"rendering {track.name}")
        notes = track.notes
        if opts.transpose:
            notes = [type(n)(n.start, n.duration, n.pitch + opts.transpose, n.velocity) for n in notes]
        return synth.render_notes(notes, st.duration, timbre=timbre, sample_rate=opts.sample_rate)

    voice_audio = [render(t, "voice") for t in voices]
    acc_audio = [render(t, "piano") for t in st.accompaniment]

    title = safe_filename(st.title, max_bytes=120)  # leaves room for " - 01 Voice (practice)"
    written: list[Path] = []

    def emit(stem: str, audio, track_title: str) -> None:
        jobs.check_cancelled()
        jobs.progress(f"writing {stem}.{opts.fmt}")
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
    export = None if opts.export_musicxml is None else Path(opts.export_musicxml)
    if export is not None and path.exists() and export.exists() and export.samefile(path):
        raise ValueError(f"exporting MusicXML to {export} would overwrite the score itself; "
                         "pick another name")
    jobs.progress(f"reading {path.name}")
    score = load_score(path, omr_dpi=opts.omr_dpi)
    jobs.check_cancelled()
    try:
        st = _tracks_of(score, path, opts, export)
    except Music21Exception as exc:  # odd notation music21 can't write or measure
        raise ScoreLoadError(f"could not process {path.name}: {exc}") from exc
    if st.voices and st.duration < MIN_DURATION:  # (no voices: process_tracks says so)
        raise ValueError(f"{path.name} lasts only {st.duration:.3f} s at this tempo; check the tempo settings")
    st.load_key = opts.load_key()
    return st


def _tracks_of(score, path: Path, opts: Options, export: Path | None) -> ScoreTracks:
    for n in score.recurse().notes:
        n.lyrics = []  # the tracks don't use them, and the exported score shouldn't carry them
    if export is not None:
        jobs.progress(f"saving {export.name}")
        export.parent.mkdir(parents=True, exist_ok=True)
        score.write("musicxml", fp=str(export))
    jobs.progress("checking the score")
    # Repairs OMR made, then the bars that still look wrong (for any input).
    suffix = path.suffix.lower()
    source = "scan" if suffix in OMR_SUFFIXES else "midi" if suffix in (".mid", ".midi") else "notation"
    issues = list(getattr(score, "omr_issues", [])) + check_score(
        score, getattr(score, "omr_locations", None), source)
    st = extract_tracks(
        score,
        bpm=opts.bpm,
        tempo_scale=opts.tempo_scale,
        split_voices=opts.split_voices,
        split_chords=opts.split_chords,
        include_accompaniment=opts.include_accompaniment,
    )
    st.issues = issues + [Issue(None, name, "fixed", "its second voice only repeats the first: one track")
                          for name in st.merged]
    st.recognised = hasattr(score, "omr_issues")
    if opts.title is not None:
        st.title = opts.title.strip()
    elif st.title == "Score":
        st.title = path.stem
    return st
