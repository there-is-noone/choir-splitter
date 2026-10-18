"""Degenerate input: silence, zero-length notes, rests-only and single-voice scores."""

from __future__ import annotations

import numpy as np
import pytest
from music21 import metadata, note, stream

from choirsplit import synth
from choirsplit.cli import main
from choirsplit.score import NoteEvent


def test_render_no_notes_is_silence_of_exact_length():
    out = synth.render_notes([], 1.0)
    assert len(out) == synth.SAMPLE_RATE and not out.any()


def test_render_zero_duration_score_is_empty():
    assert len(synth.render_notes([NoteEvent(0.0, 0.5, 60)], 0.0)) == 0


def test_render_zero_length_note_is_finite():
    out = synth.render_notes([NoteEvent(0.1, 0.0, 60)], 1.0)
    assert np.isfinite(out).all() and len(out) == synth.SAMPLE_RATE


def test_render_note_past_the_end_is_ignored():
    assert not synth.render_notes([NoteEvent(5.0, 1.0, 60)], 1.0).any()


def _write(tmp_path, notes_by_part):
    s = stream.Score()
    s.metadata = metadata.Metadata(title="Edge")
    for name, make in notes_by_part:
        p = stream.Part()
        p.partName = name
        for el in make():
            p.append(el)
        s.insert(0, p)
    path = tmp_path / "edge.musicxml"
    s.write("musicxml", fp=str(path))
    return path


def test_rests_only_score_reports_no_voices(tmp_path, capsys):
    path = _write(tmp_path, [("Soprano", lambda: [note.Rest(quarterLength=4)])])
    assert main([str(path), "-o", str(tmp_path / "o"), "--format", "wav"]) == 1
    assert "no sung voices" in capsys.readouterr().err


def test_single_voice_score_writes_tracks(tmp_path, capsys):
    path = _write(tmp_path, [("Soprano", lambda: [note.Note(60, quarterLength=1) for _ in range(4)])])
    out = tmp_path / "o"
    assert main([str(path), "-o", str(out), "--format", "wav"]) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "Edge - 01 Soprano (practice).wav", "Edge - 01 Soprano.wav", "Edge - All voices.wav"]


def test_empty_score_is_a_clean_error(tmp_path, capsys):
    path = _write(tmp_path, [("Soprano", lambda: [])])
    try:
        rc = main([str(path), "-o", str(tmp_path / "o"), "--format", "wav"])
    except Exception as exc:  # pragma: no cover - the point of this test
        pytest.fail(f"unhandled {type(exc).__name__}: {exc}")
    assert rc == 1
    assert "error" in capsys.readouterr().err
