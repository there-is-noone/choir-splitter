"""End-to-end: score file -> per-voice audio files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import mixer, synth
from .audio_io import write_audio
from .loaders import load_score
from .score import ScoreTracks, extract_tracks


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


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name or "untitled"


def process_tracks(st: ScoreTracks, out_dir: Path, opts: Options) -> list[Path]:
    if opts.mode not in ("solo", "practice", "both"):
        raise ValueError(f"unknown mode {opts.mode!r}")
    voices = st.voices
    if not voices:
        raise ValueError("no sung voices found in the score")
    out_dir.mkdir(parents=True, exist_ok=True)

    def render(track, timbre):
        notes = track.notes
        if opts.transpose:
            notes = [type(n)(n.start, n.duration, n.pitch + opts.transpose, n.velocity) for n in notes]
        return synth.render_notes(notes, st.duration, timbre=timbre, sample_rate=opts.sample_rate)

    voice_audio = [render(t, "voice") for t in voices]
    acc_audio = [render(t, "piano") for t in st.accompaniment]

    title = safe_filename(st.title)
    written: list[Path] = []
    only = {o.lower() for o in opts.only}

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
    path = Path(path)
    score = load_score(path)
    st = extract_tracks(
        score,
        bpm=opts.bpm,
        tempo_scale=opts.tempo_scale,
        split_voices=opts.split_voices,
        split_chords=opts.split_chords,
        include_accompaniment=opts.include_accompaniment,
    )
    if st.title == "Score":
        st.title = path.stem
    return st, process_tracks(st, Path(out_dir), opts)
