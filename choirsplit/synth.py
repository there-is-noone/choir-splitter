"""Small numpy synthesizer: a vowel-like sung tone and a plucked/piano-like tone."""

from __future__ import annotations

import numpy as np

from .score import NoteEvent

SAMPLE_RATE = 44100

# Relative harmonic amplitudes. The voice profile approximates an open "ah"/"oo".
VOICE_HARMONICS = (1.0, 0.55, 0.32, 0.18, 0.12, 0.07, 0.04, 0.02)
PIANO_HARMONICS = (1.0, 0.45, 0.25, 0.12, 0.08, 0.05)

ATTACK = 0.03
RELEASE = 0.08
VIBRATO_RATE = 5.2  # Hz
VIBRATO_DEPTH = 0.18  # semitones
VIBRATO_DELAY = 0.25  # seconds before vibrato fades in


def midi_to_hz(pitch: float) -> float:
    return 440.0 * 2.0 ** ((pitch - 69.0) / 12.0)


def _voice_note(freq: float, n_samples: int, sr: int) -> np.ndarray:
    t = np.arange(n_samples) / sr
    vib_env = np.clip((t - VIBRATO_DELAY) / 0.3, 0.0, 1.0)
    semis = VIBRATO_DEPTH * vib_env * np.sin(2 * np.pi * VIBRATO_RATE * t)
    inst_freq = freq * 2.0 ** (semis / 12.0)
    phase = 2 * np.pi * np.cumsum(inst_freq) / sr
    out = np.zeros(n_samples)
    for k, amp in enumerate(VOICE_HARMONICS, start=1):
        if freq * k >= sr / 2 * 0.9:
            break
        out += amp * np.sin(k * phase)
    return out


def _piano_note(freq: float, n_samples: int, sr: int) -> np.ndarray:
    t = np.arange(n_samples) / sr
    out = np.zeros(n_samples)
    for k, amp in enumerate(PIANO_HARMONICS, start=1):
        if freq * k >= sr / 2 * 0.9:
            break
        out += amp * np.exp(-t * (1.2 + 0.8 * k)) * np.sin(2 * np.pi * freq * k * t)
    return out


def _envelope(n_body: int, n_total: int, sr: int) -> np.ndarray:
    env = np.ones(n_total)
    n_att = min(int(ATTACK * sr), n_body)
    if n_att > 0:
        env[:n_att] = np.linspace(0.0, 1.0, n_att, endpoint=False)
    n_rel = n_total - n_body
    if n_rel > 0:
        env[n_body:] = np.linspace(1.0, 0.0, n_rel)
    return env


def render_notes(
    notes: list[NoteEvent],
    duration: float,
    *,
    timbre: str = "voice",
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Render note events to a mono float32 buffer exactly `duration` seconds long."""
    total = int(round(duration * sample_rate))
    out = np.zeros(total + int(RELEASE * sample_rate) + 1, dtype=np.float64)
    gen = _voice_note if timbre == "voice" else _piano_note
    for ev in notes:
        start = int(round(ev.start * sample_rate))
        if start >= total:
            continue
        # Leave a tiny gap so repeated notes are audibly re-articulated.
        body = max(int((ev.duration - 0.02) * sample_rate), int(0.02 * sample_rate))
        n_total = body + int(RELEASE * sample_rate)
        wave = gen(midi_to_hz(ev.pitch), n_total, sample_rate)
        wave *= _envelope(body, n_total, sample_rate) * (ev.velocity / 127.0)
        out[start:start + n_total] += wave[: len(out) - start]
    out = out[:total]
    return (out * 0.25).astype(np.float32)
