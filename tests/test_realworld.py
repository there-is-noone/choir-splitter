"""Extraction checks on real choral scores (see tests/fixtures/realworld/SOURCES.md)."""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

import pytest
from music21 import chord, note

from choirsplit.loaders import ScoreLoadError, load_score
from choirsplit.score import extract_tracks

FIXTURES = Path(__file__).parent / "fixtures" / "realworld"
SATB = ["Soprano", "Alto", "Tenor", "Bass"]
DOUBLE_CHOIR = [f"{v} {n}" for n in (1, 2) for v in SATB]

# file -> (expected voice names in order, expected accompaniment names)
EXPECTED = {
    "bach_bwv66.6.mxl": (SATB, []),
    "bach_bwv1.6_horn.mxl": (SATB, ["Horn 2"]),
    "bach_bwv227.3_ssatb.mxl": (["Soprano 1", "Soprano 2", "Alto", "Tenor", "Bass"], []),
    "monteverdi_madrigal.3.1.mxl": (["Canto", "Quinto", "Alto", "Tenor", "Basso"], []),
    "aloha_oe_satb_piano.mxl": (SATB + ["Solo Voice"], ["Piano"]),
    "palestrina_agnus_02_8v.krn": (DOUBLE_CHOIR, []),
    "palestrina_credo_15_b_8v.krn": (DOUBLE_CHOIR, []),
    "josquin_mille_regrets.abc": (["Superius", "Contratenor", "Tenor", "Bassus"], []),
    "ComeAgain.mid": (["cantus", "altus", "tenor", "bassus"], []),
    "new_britain.mid": (SATB, []),
    "nicaea.mid": (SATB, []),
    "Wiegenlied.mid": (SATB, []),
    "GrabliedM.mid": (["Tenor 1", "Tenor 2", "Bass 1", "Bass 2"], []),
    "miserere-mei-deus.mid": ([f"Part {i}" for i in range(1, 10)], []),
    "SchubertF-D706-Psalm23.mid": ([f"Part {i}" for i in range(1, 5)], ["up", "down"]),
}

# MIDI files where two voices share one track (top/bottom split must keep the order).
SHARED_TRACK = ["new_britain.mid", "nicaea.mid", "Wiegenlied.mid", "GrabliedM.mid"]

_cache: dict[str, object] = {}


def tracks_for(name: str):
    if name not in _cache:
        score = load_score(FIXTURES / name)
        _cache[name] = (score, extract_tracks(score))
    return _cache[name]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_voice_names_and_accompaniment(name):
    voices, accompaniment = EXPECTED[name]
    _, st = tracks_for(name)
    assert [t.name for t in st.voices] == voices
    assert [t.name for t in st.accompaniment] == accompaniment


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_tracks_fit_the_shared_timeline(name):
    _, st = tracks_for(name)
    assert st.duration > 5
    for t in st.tracks:
        assert t.notes, t.name
        assert t.end <= st.duration + 1e-6
        assert all(n.duration > 0 and n.start >= 0 for n in t.notes)
        assert all(24 <= n.pitch <= 96 for n in t.notes), t.name


@pytest.mark.parametrize("name", [n for n in sorted(EXPECTED) if not n.endswith(".mid")]
                         + ["ComeAgain.mid", "miserere-mei-deus.mid"])
def test_open_score_voices_match_their_part(name):
    """One part per voice: the track must contain exactly the part's (tie-merged) pitches."""
    score, st = tracks_for(name)
    parts = [p for p in score.parts if p.partName not in {t.name for t in st.accompaniment}]
    if name == "aloha_oe_satb_piano.mxl":
        parts = parts[:5]
    assert len(parts) == len(st.voices)
    for part, track in zip(parts, st.voices):
        expected = [p.ps for el in part.stripTies().flatten().getElementsByClass((note.Note, chord.Chord))
                    if not el.duration.isGrace and type(el).__name__ != "ChordSymbol"
                    for p in sorted(el.pitches, key=lambda p: p.ps)]
        assert [n.pitch for n in track.notes] == expected, track.name


@pytest.mark.parametrize("name", SHARED_TRACK)
def test_shared_track_split_keeps_both_lines(name):
    _, st = tracks_for(name)
    upper, lower = st.voices[:2], st.voices[2:]
    for pair in (upper, lower):
        hi, lo = pair
        # Both lines get (nearly) a full part, not just the odd overlapping note.
        assert len(lo.notes) > 0.7 * len(hi.notes)
        assert statistics.mean(n.pitch for n in hi.notes) > statistics.mean(n.pitch for n in lo.notes)
        assert hi.end == pytest.approx(lo.end, abs=1.0)


@pytest.mark.parametrize("name,voices", [("Amazing_grace.mscz", 4), ("adeste.mscx", 4)])
def test_musescore_files(name, voices):
    try:
        score = load_score(FIXTURES / name)
    except ScoreLoadError as exc:
        pytest.skip(f"MuseScore input unavailable: {exc}")
    st = extract_tracks(score)
    assert len(st.voices) == voices
    if name == "Amazing_grace.mscz":
        assert [t.name for t in st.voices] == SATB


@pytest.mark.omr
def test_pdf_hymn():
    try:
        score = load_score(FIXTURES / "new_britain-a4.pdf")
    except ScoreLoadError as exc:
        pytest.skip(f"OMR unavailable: {exc}")
    st = extract_tracks(score)
    assert 2 <= len(st.voices) <= 4


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="needs ffmpeg/ffprobe")
def test_cli_renders_eight_voice_double_choir(tmp_path):
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "choirsplit", str(FIXTURES / "palestrina_credo_15_b_8v.krn"), "-o", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    files = sorted(out.glob("*.mp3"))
    practice = [f for f in files if f.stem.endswith("(practice)")]
    full = [f for f in files if f.stem.endswith("All voices")]
    solo = [f for f in files if f not in practice and f not in full]
    assert (len(solo), len(practice), len(full)) == (8, 8, 1)
    durations = [_ffprobe_duration(f) for f in files]
    assert max(durations) - min(durations) < 0.05
    # 48 quarters; the file has no tempo mark, so the 96 bpm default applies.
    assert durations[0] == pytest.approx(30.0, abs=0.2)
