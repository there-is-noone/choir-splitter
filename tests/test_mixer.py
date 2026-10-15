"""Mixer: pan law, pan positions, practice balance, normalisation."""

from __future__ import annotations

import numpy as np
import pytest

from audio_helpers import SR
from choirsplit import mixer
from choirsplit.mixer import PEAK_TARGET, full, mix, normalize, pan_positions, practice, solo
from choirsplit.score import NoteEvent
from choirsplit.synth import render_notes

N = 4410  # 0.1 s; frequencies below are whole numbers of cycles in this window


def sine(cycles: int, n: int = N, amp: float = 0.5) -> np.ndarray:
    return (amp * np.sin(2 * np.pi * cycles * np.arange(n) / n)).astype(np.float32)


def bin_mag(x: np.ndarray, cycles: int) -> float:
    return float(np.abs(np.fft.rfft(np.asarray(x, dtype=np.float64)))[cycles])


def level(stereo: np.ndarray, cycles: int) -> tuple[float, float, float]:
    """(left, right, combined power level) of the sine with `cycles` cycles."""
    left, right = bin_mag(stereo[:, 0], cycles), bin_mag(stereo[:, 1], cycles)
    return left, right, float(np.hypot(left, right))


# --- pan law -------------------------------------------------------------------------

@pytest.mark.parametrize("pan", np.linspace(-1, 1, 21))
def test_pan_law_constant_power(pan):
    left, right = mixer._pan_gains(pan)
    assert left ** 2 + right ** 2 == pytest.approx(1.0)
    assert left >= -1e-12 and right >= -1e-12


def test_pan_law_extremes_and_centre():
    assert mixer._pan_gains(-1) == pytest.approx((1.0, 0.0), abs=1e-12)
    assert mixer._pan_gains(1) == pytest.approx((0.0, 1.0), abs=1e-12)
    left, right = mixer._pan_gains(0)
    assert left == pytest.approx(right) == pytest.approx(np.sqrt(0.5))


def test_pan_law_mirror():
    for p in np.linspace(0, 1, 11):
        l1, r1 = mixer._pan_gains(p)
        l2, r2 = mixer._pan_gains(-p)
        assert (l1, r1) == pytest.approx((r2, l2))


# --- pan positions -------------------------------------------------------------------

@pytest.mark.parametrize("count", range(0, 13))
def test_pan_positions_symmetric_and_bounded(count):
    pans = pan_positions(count)
    assert len(pans) == count
    assert all(-1.0 <= p <= 1.0 for p in pans)
    assert pans == pytest.approx([-p for p in reversed(pans)], abs=1e-12)
    assert pans == sorted(pans)
    if count == 1:
        assert pans == [0.0]
    if count >= 2:
        assert len(set(np.round(pans, 9))) == count  # all distinct


@pytest.mark.parametrize("width", [0.0, 0.5, 1.0])
def test_pan_positions_width(width):
    pans = pan_positions(8, width)
    assert min(pans) == pytest.approx(-width) and max(pans) == pytest.approx(width)


# --- mix -----------------------------------------------------------------------------

def test_mix_shape_and_dtype():
    out = mix([sine(10), sine(20, n=N // 2)], [1.0, 1.0], [0.0, 0.0])
    assert out.shape == (N, 2) and out.dtype == np.float32
    # shorter source is zero-padded
    np.testing.assert_allclose(out[N // 2:, 0], sine(10)[N // 2:] * np.sqrt(0.5), atol=1e-6)


def test_mix_empty_raises():
    with pytest.raises(ValueError):
        mix([], [], [])


def test_mix_hard_pans():
    out = mix([sine(10), sine(20)], [1.0, 1.0], [-1.0, 1.0])
    assert level(out, 10)[1] < 1e-3 * level(out, 10)[0]
    assert level(out, 20)[0] < 1e-3 * level(out, 20)[1]


# --- normalize -----------------------------------------------------------------------

@pytest.mark.parametrize("amp", [1e-4, 0.3, 1.0, 7.5])
def test_normalize_hits_peak_target(amp):
    x = np.stack([sine(10, amp=amp), sine(13, amp=amp / 2)], axis=1)
    y = normalize(x)
    assert np.abs(y).max() == pytest.approx(PEAK_TARGET, rel=1e-5)
    assert np.abs(y).max() < 1.0
    assert y.dtype == np.float32


def test_normalize_silence_stays_silent():
    x = np.zeros((1000, 2), dtype=np.float32)
    y = normalize(x)
    assert not np.any(y) and np.all(np.isfinite(y))
    assert y.shape == x.shape
    assert normalize(np.zeros((0, 2), dtype=np.float32)).shape == (0, 2)


def test_silent_sources_through_every_mix():
    silent = [np.zeros(N, dtype=np.float32) for _ in range(4)]
    for out in (solo(silent[0]), practice(silent, 1), full(silent),
                practice(silent, 0, accompaniment=[silent[0]])):
        assert np.all(np.isfinite(out)) and not np.any(out)
        assert out.shape == (N, 2) and out.dtype == np.float32


# --- solo / practice / full ----------------------------------------------------------

def test_solo_is_centred_and_normalized():
    out = solo(sine(10))
    np.testing.assert_array_equal(out[:, 0], out[:, 1])
    assert np.abs(out).max() == pytest.approx(PEAK_TARGET, rel=1e-5)


@pytest.mark.parametrize("count", [1, 2, 4, 8, 12])
@pytest.mark.parametrize("others_gain", [0.0, 0.2, 0.3, 0.5, 1.0])
def test_practice_balance(count, others_gain):
    cycles = [10 + 7 * i for i in range(count)]
    sources = [sine(c) for c in cycles]
    for target in range(count):
        out = practice(sources, target, others_gain=others_gain)
        assert np.abs(out).max() <= PEAK_TARGET + 1e-6
        tl, tr, t = level(out, cycles[target])
        assert tl == pytest.approx(tr, rel=1e-4)  # target centred
        for i, c in enumerate(cycles):
            if i == target:
                continue
            _, _, o = level(out, c)
            assert o == pytest.approx(others_gain * t, rel=1e-3, abs=1e-6 * t)


def test_practice_others_keep_their_stage_positions():
    cycles = [10, 17, 24, 31]
    out = practice([sine(c) for c in cycles], 1)
    pans = pan_positions(4)
    for i, c in enumerate(cycles):
        if i == 1:
            continue
        left, right, _ = level(out, c)
        exp_l, exp_r = mixer._pan_gains(pans[i])
        assert left / right == pytest.approx(exp_l / exp_r, rel=1e-3)


def test_practice_accompaniment_level():
    voices = [sine(10), sine(17)]
    acc = [sine(31)]
    out = practice(voices, 0, accompaniment=acc, accompaniment_gain=0.35)
    _, _, t = level(out, 10)
    al, ar, a = level(out, 31)
    assert a == pytest.approx(0.35 * t, rel=1e-3)
    assert al == pytest.approx(ar, rel=1e-4)
    out2 = practice(voices, 0, accompaniment=None)
    assert level(out2, 31)[2] < 1e-6 * level(out2, 10)[2]


def test_practice_does_not_mutate_inputs():
    voices = [sine(10), sine(17)]
    acc = [sine(31)]
    copy = [v.copy() for v in voices]
    practice(voices, 1, accompaniment=acc)
    assert len(voices) == 2 and len(acc) == 1
    for a, b in zip(voices, copy):
        np.testing.assert_array_equal(a, b)


def test_full_mix_equal_levels_spread():
    cycles = [10 + 7 * i for i in range(8)]
    out = full([sine(c) for c in cycles], accompaniment=[sine(80)], accompaniment_gain=0.6)
    levels = [level(out, c)[2] for c in cycles]
    assert levels == pytest.approx([levels[0]] * 8, rel=1e-3)
    assert level(out, 80)[2] == pytest.approx(0.6 * levels[0], rel=1e-3)
    # leftmost voice is left, rightmost is right
    assert level(out, cycles[0])[0] > level(out, cycles[0])[1]
    assert level(out, cycles[-1])[1] > level(out, cycles[-1])[0]


def test_eight_rendered_voices_never_clip():
    pitches = [79, 76, 72, 69, 64, 60, 55, 48]
    dur = 1.0
    voices = [render_notes([NoteEvent(0, dur, p, 127)], dur) for p in pitches]
    piano = [render_notes([NoteEvent(0, dur, p, 127) for p in (48, 52, 55, 60)], dur, timbre="piano")]
    outs = [full(voices, accompaniment=piano), solo(voices[0])]
    outs += [practice(voices, i, accompaniment=piano) for i in range(8)]
    for out in outs:
        assert np.all(np.isfinite(out))
        assert np.abs(out).max() == pytest.approx(PEAK_TARGET, rel=1e-5)
        assert out.shape == (int(dur * SR), 2)
