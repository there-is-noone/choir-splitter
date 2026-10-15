"""Shared helpers for the audio/CLI tests (kept out of conftest.py on purpose)."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 44100


def peak_freq(x: np.ndarray, sr: int = SR, fmin: float = 20.0, pad: int = 8) -> float:
    """Frequency of the strongest spectral peak (Hann window, zero-padded, parabolic interp)."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x) * pad
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), n))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    spec[freqs < fmin] = 0
    k = int(np.argmax(spec))
    if 0 < k < len(spec) - 1:
        a, b, c = np.log(spec[k - 1 : k + 2] + 1e-30)
        k = k + 0.5 * (a - c) / (a - 2 * b + c)
    return k * sr / n


def band_level(x: np.ndarray, freq: float, sr: int = SR, rel: float = 0.03) -> float:
    """Spectral energy (Hann, root of summed |X|^2) within +-rel of `freq`.

    Energy rather than peak height, so vibrato (which smears wider at higher pitches)
    doesn't bias comparisons between voices. A 2-D (n, channels) input sums the channels'
    energies, i.e. the level independent of panning.
    """
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        return float(np.sqrt(sum(band_level(x[:, c], freq, sr, rel) ** 2 for c in range(x.shape[1]))))
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    sel = (freqs >= freq * (1 - rel)) & (freqs <= freq * (1 + rel))
    return float(np.sqrt(np.sum(spec[sel] ** 2)))


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Return (float array shaped (n, channels), sample rate)."""
    with wave.open(str(path), "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        assert sw == 2
        data = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float64) / 32767
    return data.reshape(-1, ch), sr


def wav_frames(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        return w.getnframes()


def ffprobe(path: Path) -> dict:
    exe = shutil.which("ffprobe")
    assert exe, "ffprobe not installed"
    out = subprocess.run(
        [exe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, check=True, text=True,
    ).stdout
    return json.loads(out)


def mp3_duration(path: Path) -> float:
    return float(ffprobe(path)["format"]["duration"])


def make_score(path: Path, parts: list[tuple[str, list[int]]], *, bpm: float = 120,
               ql: float = 1.0, title: str | None = None, piano: list[list[int]] | None = None) -> Path:
    """Write a simple MusicXML score: one Part per (name, [midi, ...]) with notes of length `ql`.

    `piano` adds a two-handed accompaniment part (list of chords, one per beat).
    """
    from music21 import chord, instrument, meter, metadata, note, stream, tempo

    sc = stream.Score()
    if title is not None:
        sc.metadata = metadata.Metadata(title=title)
    for i, (name, pitches) in enumerate(parts):
        p = stream.Part()
        p.partName = name
        p.id = f"P{i + 1}"
        p.insert(0, instrument.Vocalist())
        p.partName = name
        if i == 0:
            p.insert(0, tempo.MetronomeMark(number=bpm))
        p.insert(0, meter.TimeSignature("4/4"))
        for m in pitches:
            p.append(note.Note(m, quarterLength=ql))
        sc.insert(0, p)
    if piano is not None:
        p = stream.Part()
        p.id = "PIANO"
        p.insert(0, instrument.Piano())
        p.partName = "Piano"
        p.insert(0, meter.TimeSignature("4/4"))
        for ch in piano:
            p.append(chord.Chord(ch, quarterLength=ql))
        sc.insert(0, p)
    path = Path(path)
    sc.write("musicxml", fp=str(path))
    return path
