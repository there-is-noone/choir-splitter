"""pipeline.process_file / process_tracks end to end on small generated scores."""

from __future__ import annotations

import numpy as np
import pytest

from audio_helpers import SR, band_level, make_score, mp3_duration, read_wav, wav_frames
from choirsplit import synth
from choirsplit.pipeline import Options, process_file, process_tracks, safe_filename
from choirsplit.score import NoteEvent, ScoreTracks, Track
from choirsplit.synth import midi_to_hz

EIGHT = ["Soprano 1", "Soprano 2", "Alto 1", "Alto 2", "Tenor 1", "Tenor 2", "Bass 1", "Bass 2"]
# One held note per voice; no voice's fundamental is within a semitone of another's harmonics.
EIGHT_PITCHES = [75, 71, 66, 64, 61, 57, 53, 50]
SATB = ["Soprano", "Alto", "Tenor", "Bass"]
TITLE8 = "Op. 5: Ave/Maria — Test"


@pytest.fixture(scope="module")
def scores(tmp_path_factory):
    d = tmp_path_factory.mktemp("scores")
    return {
        # 2 s: one whole note per voice at 120 bpm
        "eight": make_score(d / "eight.musicxml", [(n, [p]) for n, p in zip(EIGHT, EIGHT_PITCHES)],
                            ql=4.0, title=TITLE8),
        # 2 s: four quarter notes per voice + piano chords
        "satb": make_score(d / "satb_piano.musicxml",
                           [(n, [p, p + 2, p + 4, p]) for n, p in zip(SATB, (72, 65, 57, 48))],
                           piano=[[48, 55, 64]] * 4),
    }


def names(paths):
    return sorted(p.name for p in paths)


def wav_opts(**kw):
    return Options(fmt="wav", **kw)


# --- file sets -----------------------------------------------------------------------

def test_satb_both_mode_file_set(scores, tmp_path):
    st, written = process_file(scores["satb"], tmp_path, wav_opts())
    assert st.title == "satb_piano"  # no title in the score -> file stem
    expected = []
    for i, v in enumerate(SATB, start=1):
        expected += [f"satb_piano - {i:02d} {v}.wav", f"satb_piano - {i:02d} {v} (practice).wav"]
    expected.append("satb_piano - All voices.wav")
    assert names(written) == sorted(expected)
    assert names(tmp_path.iterdir()) == sorted(expected)
    assert [t.name for t in st.accompaniment] == ["Piano"]


@pytest.mark.parametrize("mode, per_voice", [("solo", 1), ("practice", 1), ("both", 2)])
@pytest.mark.parametrize("full_mix", [True, False])
def test_modes(scores, tmp_path, mode, per_voice, full_mix):
    _, written = process_file(scores["satb"], tmp_path, wav_opts(mode=mode, full_mix=full_mix))
    assert len(written) == 4 * per_voice + full_mix
    practice = [p for p in written if p.name.endswith("(practice).wav")]
    assert len(practice) == (4 if mode != "solo" else 0)
    assert any(p.name.endswith("All voices.wav") for p in written) == full_mix


def test_eight_voice_names_are_safe(scores, tmp_path):
    st, written = process_file(scores["eight"], tmp_path, wav_opts(mode="practice", full_mix=False))
    assert st.title == TITLE8
    assert [t.name for t in st.voices] == EIGHT
    prefix = "Op. 5_ Ave_Maria — Test"
    assert names(written) == sorted(f"{prefix} - {i:02d} {v} (practice).wav" for i, v in enumerate(EIGHT, 1))
    assert all(p.parent == tmp_path for p in written)


def test_invalid_mode(scores, tmp_path):
    with pytest.raises(ValueError, match="mode"):
        process_file(scores["satb"], tmp_path, wav_opts(mode="karaoke"))


# --- --only ----------------------------------------------------------------------------

def test_only_is_case_insensitive_and_keeps_numbering(scores, tmp_path):
    _, written = process_file(scores["eight"], tmp_path,
                              wav_opts(mode="solo", full_mix=False, only=["alto 2", "BASS 1"]))
    assert names(written) == [
        "Op. 5_ Ave_Maria — Test - 04 Alto 2.wav", "Op. 5_ Ave_Maria — Test - 07 Bass 1.wav"]


def test_only_unknown_voice_is_an_error(scores, tmp_path):
    with pytest.raises(ValueError, match="Sopran") as exc:
        process_file(scores["satb"], tmp_path / "out", wav_opts(only=["Sopran"]))
    assert "Soprano" in str(exc.value)  # lists the available voices
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


# --- durations -------------------------------------------------------------------------

def test_all_files_same_length_wav(scores, tmp_path):
    st, written = process_file(scores["satb"], tmp_path, wav_opts())
    assert st.duration == pytest.approx(2.0)
    assert {wav_frames(p) for p in written} == {round(2.0 * SR)}


@pytest.mark.parametrize("bpm, scale, seconds", [
    (None, 1.0, 2.0), (60, 1.0, 4.0), (None, 0.5, 4.0), (90, 0.75, 4 * 60 / 90 / 0.75), (240, 1.0, 1.0),
])
def test_bpm_and_tempo_scale_exact(scores, tmp_path, bpm, scale, seconds):
    st, written = process_file(scores["satb"], tmp_path,
                               wav_opts(bpm=bpm, tempo_scale=scale, mode="solo", only=["Bass"]))
    assert st.duration == pytest.approx(seconds)
    assert {wav_frames(p) for p in written} == {round(seconds * SR)}


def test_mp3_files_equal_duration(scores, tmp_path):
    # ffmpeg/ffprobe start-up is slow (~0.3 s each), so a practice track and the full mix
    # (different mixes, same length) stand in for the set; the WAV tests cover every file.
    _, written = process_file(scores["satb"], tmp_path, Options(bpm=80, mode="practice", only=["Bass"]))
    assert len(written) == 2
    durations = {mp3_duration(p) for p in written}
    assert len(durations) == 1
    assert durations.pop() == pytest.approx(3.0, abs=0.05)
    assert all(p.suffix == ".mp3" for p in written)


@pytest.mark.parametrize("bad", [{"bpm": 0}, {"bpm": -10}, {"tempo_scale": 0}, {"tempo_scale": -1}])
def test_invalid_tempo(scores, tmp_path, bad):
    with pytest.raises(ValueError):
        process_file(scores["satb"], tmp_path, wav_opts(**bad))


# --- mixing behaviour ------------------------------------------------------------------

def test_eight_voice_practice_target_dominates(scores, tmp_path):
    _, written = process_file(scores["eight"], tmp_path, wav_opts(mode="practice", full_mix=False))
    assert len(written) == 8
    freqs = [midi_to_hz(p) for p in EIGHT_PITCHES]
    for target, path in enumerate(sorted(written)):
        audio, _ = read_wav(path)
        levels = [band_level(audio, f, rel=0.025) for f in freqs]
        others = [lv for i, lv in enumerate(levels) if i != target]
        # gain 1.0 vs 0.3 -> ~3.3x; leave room for vibrato smearing and panning
        assert levels[target] > 2.5 * max(others), (path.name, levels)
        # target centred
        tl = band_level(audio[:, 0], freqs[target], rel=0.025)
        tr = band_level(audio[:, 1], freqs[target], rel=0.025)
        assert tl == pytest.approx(tr, rel=0.02)
        assert np.abs(audio).max() < 0.9


@pytest.mark.parametrize("others_gain", [0.0, 0.1, 0.5])
def test_others_volume(scores, tmp_path, others_gain):
    _, written = process_file(scores["eight"], tmp_path,
                              wav_opts(mode="practice", full_mix=False, only=["Alto 1"],
                                       others_gain=others_gain))
    audio, _ = read_wav(written[0])
    freqs = [midi_to_hz(p) for p in EIGHT_PITCHES]
    target = band_level(audio, freqs[2], rel=0.025)  # both channels: independent of panning
    assert target > 0
    for i in (0, 4, 7):
        assert band_level(audio, freqs[i], rel=0.025) / target == pytest.approx(others_gain, abs=0.02)


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_others_volume_out_of_range(scores, tmp_path, bad):
    with pytest.raises(ValueError, match="others"):
        process_file(scores["satb"], tmp_path, wav_opts(others_gain=bad))


def test_no_accompaniment(scores, tmp_path):
    piano_f = midi_to_hz(55)  # G3: only the piano plays it
    with_acc, _ = process_file(scores["satb"], tmp_path / "a", wav_opts(mode="practice", only=["Soprano"]))
    without, _ = process_file(scores["satb"], tmp_path / "b",
                              wav_opts(mode="practice", only=["Soprano"], include_accompaniment=False))
    assert [t.name for t in with_acc.accompaniment] == ["Piano"]
    assert without.accompaniment == []
    for name in ("satb_piano - 01 Soprano (practice).wav", "satb_piano - All voices.wav"):
        a, _ = read_wav(tmp_path / "a" / name)
        b, _ = read_wav(tmp_path / "b" / name)
        assert band_level(a[:, 0], piano_f, rel=0.01) > 20 * band_level(b[:, 0], piano_f, rel=0.01)


def test_solo_track_contains_only_that_voice(scores, tmp_path):
    _, written = process_file(scores["eight"], tmp_path, wav_opts(mode="solo", full_mix=False, only=["Tenor 2"]))
    audio, _ = read_wav(written[0])
    np.testing.assert_array_equal(audio[:, 0], audio[:, 1])
    freqs = [midi_to_hz(p) for p in EIGHT_PITCHES]
    levels = [band_level(audio[:, 0], f, rel=0.025) for f in freqs]
    assert all(levels[5] > 50 * lv for i, lv in enumerate(levels) if i != 5)


def test_transpose_option(scores, tmp_path):
    from audio_helpers import peak_freq

    _, written = process_file(scores["eight"], tmp_path,
                              wav_opts(mode="solo", full_mix=False, only=["Bass 2"], transpose=-3))
    audio, sr = read_wav(written[0])
    head = audio[: int(synth.VIBRATO_DELAY * sr), 0]
    assert peak_freq(head, sr, pad=32) == pytest.approx(midi_to_hz(EIGHT_PITCHES[7] - 3), rel=0.005)


def test_mp3_metadata_from_pipeline(scores, tmp_path):
    from audio_helpers import ffprobe

    _, written = process_file(scores["eight"], tmp_path, Options(mode="practice", only=["Soprano 2"]))
    tags = {p.name: ffprobe(p)["format"]["tags"] for p in written}
    practice = tags["Op. 5_ Ave_Maria — Test - 02 Soprano 2 (practice).mp3"]
    assert practice["title"] == "Soprano 2 (practice)" and practice["album"] == TITLE8
    assert tags["Op. 5_ Ave_Maria — Test - All voices.mp3"]["title"] == "All voices"


def test_bitrate_option(scores, tmp_path):
    from audio_helpers import ffprobe

    _, written = process_file(scores["satb"], tmp_path, Options(mode="solo", full_mix=False,
                                                                only=["Alto"], bitrate="96k"))
    assert int(ffprobe(written[0])["streams"][0]["bit_rate"]) == 96000


# --- process_tracks directly --------------------------------------------------------

def test_long_title_is_shortened_but_suffixes_survive(tmp_path):
    st = ScoreTracks("Ein sehr langer Titel ž " * 20, [
        Track("Soprano", "voice", [NoteEvent(0, 0.2, 72)]),
        Track("Alto", "voice", [NoteEvent(0, 0.2, 67)]),
    ], 0.2)
    written = process_tracks(st, tmp_path, wav_opts())
    assert len(written) == 5 and len(set(written)) == 5
    assert all(len(p.name.encode()) < 255 for p in written)
    assert sum(p.name.endswith("(practice).wav") for p in written) == 2


def test_no_voices_is_an_error(tmp_path):
    st = ScoreTracks("x", [Track("Piano", "accompaniment", [NoteEvent(0, 1, 60)])], 1.0)
    with pytest.raises(ValueError, match="no sung voices"):
        process_tracks(st, tmp_path, wav_opts())


def test_single_voice_score(tmp_path):
    st = ScoreTracks("Solo", [Track("Voice", "voice", [NoteEvent(0, 0.5, 60)])], 0.5)
    written = process_tracks(st, tmp_path, wav_opts())
    assert names(written) == ["Solo - 01 Voice (practice).wav", "Solo - 01 Voice.wav", "Solo - All voices.wav"]


def test_output_dir_created(scores, tmp_path):
    out = tmp_path / "a" / "b" / "c"
    process_file(scores["satb"], out, wav_opts(mode="solo", only=["Tenor"], full_mix=False))
    assert names(out.iterdir()) == ["satb_piano - 03 Tenor.wav"]


def test_safe_filename_examples():
    assert safe_filename("Op. 5: Ave/Maria — Sopran 1") == "Op. 5_ Ave_Maria — Sopran 1"
    assert safe_filename(" .hidden. ") == "hidden"
