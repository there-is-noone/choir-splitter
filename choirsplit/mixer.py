"""Combine mono voice renders into stereo solo, practice and full mixes."""

from __future__ import annotations

import numpy as np

PEAK_TARGET = 0.89  # about -1 dBFS


def pan_positions(count: int, width: float = 0.7) -> list[float]:
    """Spread `count` sources from left (-width) to right (+width), like a choir on stage."""
    if count <= 1:
        return [0.0] * count
    return [(-width + 2 * width * i / (count - 1)) for i in range(count)]


def _pan_gains(pan: float) -> tuple[float, float]:
    # Constant-power panning.
    angle = (pan + 1) * np.pi / 4
    return float(np.cos(angle)), float(np.sin(angle))


def mix(sources: list[np.ndarray], gains: list[float], pans: list[float]) -> np.ndarray:
    """Mix mono sources into a (n, 2) stereo float32 array."""
    if not sources:
        raise ValueError("nothing to mix")
    n = max(len(s) for s in sources)
    out = np.zeros((n, 2), dtype=np.float64)
    for src, gain, pan in zip(sources, gains, pans):
        left, right = _pan_gains(pan)
        out[: len(src), 0] += src * gain * left
        out[: len(src), 1] += src * gain * right
    return out.astype(np.float32)


def normalize(audio: np.ndarray, peak: float = PEAK_TARGET) -> np.ndarray:
    m = float(np.max(np.abs(audio))) if audio.size else 0.0
    if m == 0.0:
        return audio
    return (audio * (peak / m)).astype(np.float32)


def solo(source: np.ndarray) -> np.ndarray:
    return normalize(mix([source], [1.0], [0.0]))


def practice(
    sources: list[np.ndarray],
    target: int,
    *,
    others_gain: float = 0.3,
    accompaniment: list[np.ndarray] | None = None,
    accompaniment_gain: float = 0.35,
) -> np.ndarray:
    """Target voice loud and centred; the other voices quieter, spread across the stereo field."""
    accompaniment = accompaniment or []
    pans = pan_positions(len(sources))
    pans[target] = 0.0
    gains = [1.0 if i == target else others_gain for i in range(len(sources))]
    return normalize(mix(
        sources + accompaniment,
        gains + [accompaniment_gain] * len(accompaniment),
        pans + [0.0] * len(accompaniment),
    ))


def full(
    sources: list[np.ndarray],
    *,
    accompaniment: list[np.ndarray] | None = None,
    accompaniment_gain: float = 0.6,
) -> np.ndarray:
    accompaniment = accompaniment or []
    return normalize(mix(
        sources + accompaniment,
        [1.0] * len(sources) + [accompaniment_gain] * len(accompaniment),
        pan_positions(len(sources)) + [0.0] * len(accompaniment),
    ))
