"""Synthesizer: pitch, envelopes/clicks, lengths, summing, timbre."""

from __future__ import annotations

import numpy as np
import pytest

from audio_helpers import SR, band_level, peak_freq, read_wav
from choirsplit import synth
from choirsplit.pipeline import Options, process_tracks
from choirsplit.score import NoteEvent, ScoreTracks, Track
from choirsplit.synth import midi_to_hz, render_notes


def note(start, dur, pitch, vel=80):
    return NoteEvent(start, dur, pitch, vel)


def rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


# --- pitch ---------------------------------------------------------------------------

@pytest.mark.parametrize("midi, hz", [(69, 440.0), (48, 130.81), (40, 82.41), (84, 1046.50)],
                         ids=["A4", "C3", "E2", "C6"])
def test_midi_to_hz(midi, hz):
    assert midi_to_hz(midi) == pytest.approx(hz, abs=0.01)


@pytest.mark.parametrize("midi", [69, 48, 40, 84], ids=["A4", "C3", "E2", "C6"])
def test_piano_note_fundamental_exact(midi):
    out = render_notes([note(0, 1.5, midi)], 1.5, timbre="piano")
    assert peak_freq(out) == pytest.approx(midi_to_hz(midi), rel=0.002)


@pytest.mark.parametrize("midi", [69, 48, 40, 84], ids=["A4", "C3", "E2", "C6"])
def test_voice_note_fundamental_before_vibrato(midi):
    # Vibrato fades in after VIBRATO_DELAY, so the first 0.25 s is a steady pitch.
    out = render_notes([note(0, 1.5, midi)], 1.5)
    head = out[: int(synth.VIBRATO_DELAY * SR)]
    assert peak_freq(head, pad=32) == pytest.approx(midi_to_hz(midi), rel=0.004)


@pytest.mark.parametrize("midi", [69, 40, 84], ids=["A4", "E2", "C6"])
def test_voice_note_fundamental_with_vibrato(midi):
    out = render_notes([note(0, 2.0, midi)], 2.0)
    # +-0.18 semitones of vibrato is about +-1 %.
    assert peak_freq(out) == pytest.approx(midi_to_hz(midi), rel=0.015)


def test_microtonal_pitch():
    out = render_notes([note(0, 1.5, 69.5)], 1.5, timbre="piano")
    assert peak_freq(out) == pytest.approx(midi_to_hz(69.5), rel=0.002)


# --- clicks and envelopes ------------------------------------------------------------

@pytest.mark.parametrize("timbre", ["voice", "piano"])
def test_envelope_starts_and_ends_at_zero(timbre):
    out = render_notes([note(0.5, 1.0, 69)], 2.0, timbre=timbre)
    start = int(0.5 * SR)
    assert np.all(out[:start] == 0)
    assert abs(out[start]) < 1e-6
    # first millisecond of the attack is tiny compared with the note
    assert np.abs(out[start : start + SR // 1000]).max() < 0.05 * np.abs(out).max()
    # release finished well before the buffer end: silence afterwards
    end = int((0.5 + 1.0 + synth.RELEASE) * SR) + 2
    assert np.all(out[end:] == 0)


@pytest.mark.parametrize("timbre", ["voice", "piano"])
def test_no_jumps_at_note_boundaries(timbre):
    notes = [note(0.0, 0.5, 60), note(0.5, 0.5, 64), note(1.0, 0.5, 67), note(1.5, 0.5, 72)]
    out = render_notes(notes, 2.2, timbre=timbre).astype(np.float64)
    d = np.abs(np.diff(out))
    steady = render_notes([note(0, 2.0, 72)], 2.0, timbre=timbre).astype(np.float64)
    limit = 1.5 * np.abs(np.diff(steady)).max()  # natural slope of the highest note
    for t in (0.0, 0.5, 1.0, 1.5, 2.0):
        i = int(t * SR)
        window = d[max(i - 200, 0) : i + 200]
        assert window.max() < limit, f"jump near {t}s"


@pytest.mark.parametrize("timbre", ["voice", "piano"])
@pytest.mark.parametrize("start, dur", [
    (0.0, 1.0),    # note ends exactly at the end of the buffer (the usual last note)
    (0.5, 2.0),    # note runs past the end
    (0.99, 0.5),   # note starts 10 ms before the end
], ids=["ends-at-end", "past-end", "starts-near-end"])
def test_no_click_at_buffer_end(timbre, start, dur):
    out = render_notes([note(start, dur, 69)], 1.0, timbre=timbre)
    assert len(out) == SR
    full_level = np.abs(render_notes([note(0, 0.5, 69)], 0.5, timbre=timbre)).max()
    assert abs(out[-1]) < 0.01 * full_level
    ms = SR // 1000
    assert np.abs(out[-ms:]).max() < 0.15 * full_level


def test_short_note_still_has_attack():
    out = render_notes([note(0.2, 0.01, 69)], 0.5)
    i = int(0.2 * SR)
    assert abs(out[i]) < 1e-6
    assert np.abs(out).max() > 0


# --- lengths and edge cases ----------------------------------------------------------

@pytest.mark.parametrize("duration", [0.0, 0.001, 1.0, 2.345678, 10.0])
@pytest.mark.parametrize("sr", [22050, 44100, 48000])
def test_exact_output_length(duration, sr):
    out = render_notes([note(0, duration, 60)], duration, sample_rate=sr)
    assert len(out) == round(duration * sr)
    assert out.dtype == np.float32


def test_empty_note_list_is_silence():
    out = render_notes([], 3.0)
    assert len(out) == 3 * SR
    assert not np.any(out)


def test_notes_past_the_end_are_ignored_or_clipped():
    notes = [note(5.0, 1.0, 60), note(1.0, 1.0, 60), note(0.5, 10.0, 64)]
    out = render_notes(notes, 1.0)
    assert len(out) == SR
    assert np.all(np.isfinite(out))
    # The note at 5 s contributes nothing; same as rendering without it.
    ref = render_notes(notes[1:], 1.0)
    np.testing.assert_array_equal(out, ref)


def test_negative_start_is_safe():
    out = render_notes([note(-0.5, 1.0, 69)], 1.0)
    assert len(out) == SR
    assert np.all(np.isfinite(out))
    # the tail of the note sounds at the beginning, nothing is written at the end
    assert np.abs(out[: SR // 4]).max() > 0
    assert np.abs(out[int(0.7 * SR):]).max() == 0


def test_zero_and_tiny_durations_render():
    out = render_notes([note(0.1, 0.0, 69), note(0.3, 1e-4, 72)], 0.6)
    assert np.all(np.isfinite(out)) and np.abs(out).max() > 0


# --- summing, timbre, velocity -------------------------------------------------------

def test_overlapping_notes_sum():
    a, b = note(0.1, 1.0, 60), note(0.4, 1.0, 67)
    both = render_notes([a, b], 2.0, timbre="piano").astype(np.float64)
    sep = render_notes([a], 2.0, timbre="piano").astype(np.float64) + render_notes([b], 2.0, timbre="piano")
    np.testing.assert_allclose(both, sep, atol=1e-6)


def test_chord_contains_all_pitches():
    chord = [note(0, 1.5, p) for p in (60, 64, 67)]
    out = render_notes(chord, 1.5)
    levels = [band_level(out, midi_to_hz(p), rel=0.015) for p in (60, 64, 67)]
    off = band_level(out, midi_to_hz(62), rel=0.01)
    assert min(levels) > 10 * off


def test_piano_decays_voice_sustains():
    piano = render_notes([note(0, 3.0, 57)], 3.0, timbre="piano")
    voice = render_notes([note(0, 3.0, 57)], 3.0, timbre="voice")
    first, last = slice(int(0.1 * SR), int(0.5 * SR)), slice(int(2.3 * SR), int(2.7 * SR))
    assert rms(piano[last]) < 0.3 * rms(piano[first])
    assert rms(voice[last]) == pytest.approx(rms(voice[first]), rel=0.2)


def test_timbres_differ():
    a = render_notes([note(0, 1.0, 60)], 1.0, timbre="voice")
    b = render_notes([note(0, 1.0, 60)], 1.0, timbre="piano")
    assert not np.allclose(a, b)


@pytest.mark.parametrize("timbre", ["voice", "piano"])
def test_harmonics_above_nyquist_dropped(timbre):
    sr = 8000
    # Top note of a piano: 4186 Hz is already above Nyquist (4000) -> only safe partials.
    for midi in (100, 108):
        out = render_notes([note(0, 0.5, midi)], 0.5, timbre=timbre, sample_rate=sr)
        assert np.all(np.isfinite(out))
        spec = np.abs(np.fft.rfft(out * np.hanning(len(out))))
        freqs = np.fft.rfftfreq(len(out), 1 / sr)
        f0 = midi_to_hz(midi)
        allowed = [k * f0 for k in range(1, 9) if k * f0 < sr / 2 * 0.9]
        mask = np.ones_like(spec, dtype=bool)
        for f in allowed:
            mask &= np.abs(freqs - f) > f * 0.03 + 20
        # No energy aliased to other frequencies.
        assert spec[mask].max() < 0.02 * max(spec.max(), 1e-9) + 1e-6


def test_high_harmonic_count_drops_for_high_notes():
    sr = SR
    low = render_notes([note(0, 0.5, 60)], 0.5, sample_rate=sr)
    high = render_notes([note(0, 0.5, 120)], 0.5, sample_rate=sr)  # 8372 Hz: only 2 partials fit
    f0 = midi_to_hz(120)
    assert band_level(high, f0) > 0
    alias3 = abs((3 * f0 + sr / 2) % sr - sr / 2)  # where a 3rd harmonic would fold back to
    assert band_level(high, alias3) < 1e-3 * band_level(high, f0)
    assert np.all(np.isfinite(low))


@pytest.mark.parametrize("timbre", ["voice", "piano"])
def test_velocity_scales_linearly(timbre):
    loud = render_notes([note(0, 1.0, 69, 100)], 1.0, timbre=timbre)
    soft = render_notes([note(0, 1.0, 69, 50)], 1.0, timbre=timbre)
    np.testing.assert_allclose(soft.astype(np.float64) * 2, loud, atol=1e-6)
    silent = render_notes([note(0, 1.0, 69, 0)], 1.0, timbre=timbre)
    assert not np.any(silent)


def test_deterministic():
    notes = [note(0, 1.0, 60), note(0.5, 1.0, 64)]
    np.testing.assert_array_equal(render_notes(notes, 2.0), render_notes(notes, 2.0))


# --- transposition through the pipeline ---------------------------------------------

@pytest.mark.parametrize("semitones", [0, 12, -12, 7, -5])
def test_pipeline_transpose_shifts_peak(tmp_path, semitones):
    st = ScoreTracks("T", [Track("Soprano", "voice", [note(0, 1.5, 69)])], 1.5)
    opts = Options(mode="solo", full_mix=False, fmt="wav", transpose=semitones)
    [path] = process_tracks(st, tmp_path, opts)
    audio, sr = read_wav(path)
    head = audio[: int(synth.VIBRATO_DELAY * sr), 0]
    assert peak_freq(head, sr, pad=32) == pytest.approx(midi_to_hz(69 + semitones), rel=0.005)
    # the score itself is not modified
    assert st.tracks[0].notes[0].pitch == 69
