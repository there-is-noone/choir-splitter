"""Option validation: bad values fail early with a clear message, before any slow loading."""

from __future__ import annotations

import pytest

from audio_helpers import make_score
from choirsplit import cli
from choirsplit.audio_io import EncoderError
from choirsplit.cli import main
from choirsplit.pipeline import Options, load_tracks, process_tracks, safe_filename


@pytest.fixture(scope="module")
def score(tmp_path_factory):
    return make_score(tmp_path_factory.mktemp("val") / "s.musicxml",
                      [("Soprano", [72] * 4), ("Alto", [65] * 4)], title="T")


@pytest.mark.parametrize("opt", [
    {"bpm": float("nan")}, {"bpm": float("inf")}, {"bpm": 0}, {"bpm": -5},
    {"tempo_scale": float("nan")}, {"tempo_scale": float("inf")}, {"tempo_scale": 0},
    {"others_gain": float("nan")}, {"others_gain": 1.5}, {"others_gain": -0.1},
    {"transpose": 49}, {"transpose": -49}, {"fmt": "ogg"}, {"mode": "loud"}, {"title": "  "},
])
def test_bad_options_rejected(opt):
    with pytest.raises(ValueError):
        Options(**opt).validate()


@pytest.mark.parametrize("args", [
    ["--bpm", "nan"], ["--bpm", "inf"], ["--tempo-scale", "nan"], ["--tempo-scale", "inf"],
    ["--others-volume", "5"], ["--transpose", "100"],
])
def test_cli_rejects_bad_values_even_with_list(score, args, capsys):
    assert main([str(score), "--list", *args]) == 1
    assert "error" in capsys.readouterr().err


def test_cli_checks_ffmpeg_before_loading(score, monkeypatch, capsys):
    def no_ffmpeg():
        raise EncoderError("no ffmpeg")

    def must_not_load(*a, **k):
        raise AssertionError("score was loaded before the ffmpeg check")

    monkeypatch.setattr(cli, "require_ffmpeg", no_ffmpeg)
    monkeypatch.setattr(cli, "load_tracks", must_not_load)
    assert main([str(score)]) == 1
    assert "no ffmpeg" in capsys.readouterr().err


def test_transpose_out_of_midi_range_rejected(score, tmp_path):
    st = load_tracks(score, Options(fmt="wav"))
    st.tracks[0].notes[0].pitch = 100
    with pytest.raises(ValueError, match="outside the MIDI range"):
        process_tracks(st, tmp_path, Options(fmt="wav", transpose=40))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["CON", "nul", "Com1", "aux.txt", "LPT9"])
def test_windows_reserved_names_are_prefixed(name):
    assert safe_filename(name) == f"_{name}"


def test_normal_names_unchanged():
    assert safe_filename("console") == "console"
    assert safe_filename("Ünï 歌") == "Ünï 歌"
