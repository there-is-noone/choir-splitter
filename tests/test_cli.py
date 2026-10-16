"""Command line: option handling, file names, durations, error reporting."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from audio_helpers import SR, ffprobe, make_score, mp3_duration, wav_frames
from choirsplit.cli import main

TITLE = "Test Hymn"
SATB = ["Soprano", "Alto", "Tenor", "Bass"]
EIGHT = ["Soprano 1", "Soprano 2", "Alto 1", "Alto 2", "Tenor 1", "Tenor 2", "Bass 1", "Bass 2"]


@pytest.fixture(scope="module")
def scores(tmp_path_factory):
    """Two-measure (8 quarter notes, 4 s at 120 bpm) scores of three kinds."""
    d = tmp_path_factory.mktemp("cli_scores")
    satb = make_score(
        d / "satb.musicxml",
        [("Soprano", [72] * 8), ("Alto", [65] * 8), ("Tenor", [57] * 8), ("Bass", [48] * 8)],
        bpm=120, title=TITLE,
    )
    eight = make_score(
        d / "eight.musicxml",
        [(n, [p] * 8) for n, p in zip(EIGHT, [75, 71, 66, 64, 61, 57, 53, 50])],
        bpm=120, title=TITLE,
    )
    satb_piano = make_score(
        d / "satb_piano.musicxml",
        [("Soprano", [72] * 8), ("Alto", [65] * 8), ("Tenor", [57] * 8), ("Bass", [48] * 8)],
        bpm=120, title=TITLE, piano=[[48, 55, 64]] * 8,
    )
    return {"satb": satb, "eight": eight, "satb_piano": satb_piano}


def run(args, capsys):
    rc = main([str(a) for a in args])
    out, err = capsys.readouterr()
    return rc, out, err


def names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


# --- file names -----------------------------------------------------------------------

@pytest.mark.parametrize("mode, full_mix, expected", [
    ("solo", True, [f"{TITLE} - All voices.wav"]
     + [f"{TITLE} - {i:02d} {v}.wav" for i, v in enumerate(SATB, 1)]),
    ("solo", False, [f"{TITLE} - {i:02d} {v}.wav" for i, v in enumerate(SATB, 1)]),
    ("practice", True, [f"{TITLE} - All voices.wav"]
     + [f"{TITLE} - {i:02d} {v} (practice).wav" for i, v in enumerate(SATB, 1)]),
    ("practice", False, [f"{TITLE} - {i:02d} {v} (practice).wav" for i, v in enumerate(SATB, 1)]),
    ("both", True, [f"{TITLE} - All voices.wav"] + sorted(
        [f"{TITLE} - {i:02d} {v}.wav" for i, v in enumerate(SATB, 1)]
        + [f"{TITLE} - {i:02d} {v} (practice).wav" for i, v in enumerate(SATB, 1)])),
    ("both", False, sorted(
        [f"{TITLE} - {i:02d} {v}.wav" for i, v in enumerate(SATB, 1)]
        + [f"{TITLE} - {i:02d} {v} (practice).wav" for i, v in enumerate(SATB, 1)])),
], ids=["solo", "solo-no-full", "practice", "practice-no-full", "both", "both-no-full"])
def test_expected_file_names_per_mode(scores, tmp_path, capsys, mode, full_mix, expected):
    out = tmp_path / "out"
    args = [scores["satb"], "-o", out, "--format", "wav", "--mode", mode]
    if not full_mix:
        args.append("--no-full-mix")
    rc, stdout, _ = run(args, capsys)
    assert rc == 0
    assert names(out) == sorted(expected)
    assert stdout.count("  wrote ") == len(expected)


def test_satb_piano_writes_no_extra_files(scores, tmp_path, capsys):
    out = tmp_path / "out"
    rc, _, _ = run([scores["satb_piano"], "-o", out, "--format", "wav"], capsys)
    assert rc == 0
    assert len(names(out)) == 9  # 4 solo + 4 practice + all voices; piano is only mixed in


# --- --list ---------------------------------------------------------------------------

def test_list_prints_voices_and_writes_nothing(scores, tmp_path, capsys):
    score = tmp_path / "hymn.musicxml"
    score.write_bytes(scores["satb"].read_bytes())
    out = tmp_path / "out"
    rc, stdout, _ = run([score, "-o", out, "--list"], capsys)
    assert rc == 0
    assert stdout.startswith(f"{TITLE} (4.0s)")
    for i, v in enumerate(SATB, 1):
        assert f"{i:2d}. {v} [" in stdout
    assert "wrote" not in stdout
    assert not out.exists()
    # without -o the default "<stem>_tracks" dir must not be created either
    rc, _, _ = run([score, "--list"], capsys)
    assert rc == 0
    assert not (tmp_path / "hymn_tracks").exists()
    assert names(tmp_path) == ["hymn.musicxml"]


def test_list_from_module_entry_point(scores):
    proc = subprocess.run(
        [sys.executable, "-m", "choirsplit", "--list", str(scores["eight"])],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"{TITLE} (4.0s)" in proc.stdout
    for v in EIGHT:
        assert v in proc.stdout


# --- --only ---------------------------------------------------------------------------

def test_only_is_case_insensitive(scores, tmp_path, capsys):
    out = tmp_path / "out"
    rc, _, _ = run([scores["eight"], "-o", out, "--format", "wav", "--mode", "solo",
                    "--no-full-mix", "--only", "alto 2", "--only", "BASS 1"], capsys)
    assert rc == 0
    assert names(out) == [f"{TITLE} - 04 Alto 2.wav", f"{TITLE} - 07 Bass 1.wav"]


def test_only_unknown_voice_exits_1(scores, tmp_path, capsys):
    rc, _, err = run([scores["satb"], "-o", tmp_path / "out", "--format", "wav",
                      "--only", "Sopran"], capsys)
    assert rc == 1
    assert "error:" in err and "Sopran" in err and "Soprano" in err


# --- tempo and length -----------------------------------------------------------------

def _solo_frames(scores, tmp_path, capsys, name, *extra):
    out = tmp_path / name
    rc, _, _ = run([scores["satb"], "-o", out, "--format", "wav", "--mode", "solo",
                    "--no-full-mix", "--only", "Soprano", *extra], capsys)
    assert rc == 0
    [path] = list(out.iterdir())
    return wav_frames(path)


def test_bpm_60_doubles_length_of_120(scores, tmp_path, capsys):
    f120 = _solo_frames(scores, tmp_path, capsys, "b120", "--bpm", "120")
    f60 = _solo_frames(scores, tmp_path, capsys, "b60", "--bpm", "60")
    assert f120 == round(4.0 * SR)
    assert f60 == 2 * f120


def test_tempo_scale_half_doubles_length(scores, tmp_path, capsys):
    f1 = _solo_frames(scores, tmp_path, capsys, "t1")
    fh = _solo_frames(scores, tmp_path, capsys, "th", "--tempo-scale", "0.5")
    assert fh == 2 * f1


def test_all_files_same_sample_count(scores, tmp_path, capsys):
    out = tmp_path / "out"
    rc, _, _ = run([scores["eight"], "-o", out, "--format", "wav", "--mode", "both",
                    "--tempo-scale", "0.8"], capsys)
    assert rc == 0
    paths = list(out.iterdir())
    assert len(paths) == 17  # 8 solo + 8 practice + all voices
    counts = {wav_frames(p) for p in paths}
    assert counts == {round(4.0 / 0.8 * SR)}


# --- output directory -----------------------------------------------------------------

def test_default_output_dir_is_next_to_score(tmp_path, capsys, scores):
    score = tmp_path / "hymn.musicxml"
    score.write_bytes(scores["satb"].read_bytes())
    rc, _, _ = run([score, "--format", "wav", "--mode", "solo", "--only", "Bass", "--no-full-mix"],
                   capsys)
    assert rc == 0
    assert names(tmp_path / "hymn_tracks") == ["Test Hymn - 04 Bass.wav"]


# --- errors ---------------------------------------------------------------------------

def test_missing_file_exits_1(tmp_path, capsys):
    rc, _, err = run([tmp_path / "nope.musicxml", "-o", tmp_path / "out", "--format", "wav"], capsys)
    assert rc == 1
    assert "error:" in err and "not found" in err
    assert not (tmp_path / "out").exists()


def test_unsupported_suffix_exits_1(scores, tmp_path, capsys):
    # A valid score under a suffix the loader does not know must be refused, not parsed.
    txt = tmp_path / "hymn.txt"
    txt.write_bytes(scores["satb"].read_bytes())
    rc, _, err = run([txt, "-o", tmp_path / "out", "--format", "wav"], capsys)
    assert rc == 1
    assert "error:" in err
    assert not (tmp_path / "out").exists()


def test_garbage_musicxml_exits_1(tmp_path, capsys):
    bad = tmp_path / "bad.musicxml"
    bad.write_text("this is not xml <<<")
    rc, _, err = run([bad, "-o", tmp_path / "out", "--format", "wav"], capsys)
    assert rc == 1
    assert "error:" in err and "bad.musicxml" in err


@pytest.mark.parametrize("bad", ["1.5", "-0.1"])
def test_others_volume_out_of_range_exits_1(scores, tmp_path, capsys, bad):
    rc, _, err = run([scores["satb"], "-o", tmp_path / "out", "--format", "wav",
                      "--others-volume", bad], capsys)
    assert rc == 1
    assert "error:" in err and "others" in err


def test_tempo_scale_zero_exits_1(scores, tmp_path, capsys):
    rc, _, err = run([scores["satb"], "-o", tmp_path / "out", "--format", "wav",
                      "--tempo-scale", "0"], capsys)
    assert rc == 1
    assert "error:" in err


# --- MP3 ------------------------------------------------------------------------------

def test_mp3_run_duration_and_title_tag(scores, tmp_path, capsys):
    out = tmp_path / "out"
    rc, _, _ = run([scores["satb"], "-o", out, "--mode", "practice", "--only", "Alto",
                    "--no-full-mix"], capsys)
    assert rc == 0
    [path] = list(out.iterdir())
    assert path.name == f"{TITLE} - 02 Alto (practice).mp3"
    assert mp3_duration(path) == pytest.approx(4.0, abs=0.1)
    tags = {k.lower(): v for k, v in ffprobe(path)["format"]["tags"].items()}
    assert tags["title"] == "Alto (practice)"
    assert tags["album"] == TITLE


def test_title_option_renames_files_and_album_tag(scores, tmp_path, capsys):
    out = tmp_path / "out"
    rc, stdout, _ = run([scores["satb"], "-o", out, "--mode", "solo", "--only", "Bass",
                         "--title", "  Ave Verum: take 2 "], capsys)
    assert rc == 0
    assert stdout.startswith("Ave Verum: take 2:")
    # ':' is not allowed in file names on every system, so it becomes '_' there only.
    assert names(out) == ["Ave Verum_ take 2 - 04 Bass.mp3", "Ave Verum_ take 2 - All voices.mp3"]
    tags = {k.lower(): v for k, v in ffprobe(out / "Ave Verum_ take 2 - 04 Bass.mp3")["format"]["tags"].items()}
    assert tags["album"] == "Ave Verum: take 2"
    assert tags["title"] == "Bass"


def test_title_option_shows_in_list(scores, capsys):
    rc, stdout, _ = run([scores["satb"], "--list", "--title", "Ave Verum"], capsys)
    assert rc == 0
    assert stdout.splitlines()[0].startswith("Ave Verum (")


def test_empty_title_exits_1(scores, tmp_path, capsys):
    rc, _, err = run([scores["satb"], "-o", tmp_path / "out", "--title", "  "], capsys)
    assert rc == 1
    assert "title" in err
    assert not (tmp_path / "out").exists()
