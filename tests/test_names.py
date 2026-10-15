"""Light coverage of the naming helpers: _split_names, _unique_names, _staff_names, _meaningful_title."""

from __future__ import annotations

import pytest

from choirsplit.score import Track, _meaningful_title, _split_names, _staff_names, _unique_names


@pytest.mark.parametrize("name, count, expected", [
    ("Soprano Alto", 2, ["Soprano", "Alto"]),
    ("Soprano/Alto", 2, ["Soprano", "Alto"]),
    ("Soprano and Alto", 2, ["Soprano", "Alto"]),
    ("Soprano And Alto", 2, ["Soprano", "Alto"]),
    ("Tenor & Bass", 2, ["Tenor", "Bass"]),
    ("S.A.", 2, ["Soprano", "Alto"]),
    ("SA", 2, ["Soprano", "Alto"]),
    ("TB", 2, ["Tenor", "Bass"]),
    ("SATB", 4, ["Soprano", "Alto", "Tenor", "Bass"]),
    ("S A T B", 4, ["Soprano", "Alto", "Tenor", "Bass"]),
    ("SSA", 3, ["Soprano", "Soprano", "Alto"]),  # made unique later by _unique_names
    ("Women", 2, ["Soprano", "Alto"]),
    ("women", 2, ["Soprano", "Alto"]),
    ("Men", 2, ["Tenor", "Bass"]),
    ("Upper voices", 2, ["Soprano", "Alto"]),
    ("Soprano 1 2", 2, ["Soprano 1", "Soprano 2"]),
    ("Soprano 1/2", 2, ["Soprano 1", "Soprano 2"]),
    ("Tenor I/II", 2, ["Tenor I", "Tenor II"]),
    ("Tenor I II", 2, ["Tenor I", "Tenor II"]),
    ("Bass 1 2 3", 3, ["Bass 1", "Bass 2", "Bass 3"]),
    ("S1 S2", 2, ["Soprano 1", "Soprano 2"]),
    ("T1/T2", 2, ["Tenor 1", "Tenor 2"]),
    ("TI TII", 2, ["Tenor I", "Tenor II"]),
    ("Sop", 2, ["Soprano 1", "Soprano 2"]),
    ("B", 2, ["Bass 1", "Bass 2"]),
    ("Bass", 2, ["Bass 1", "Bass 2"]),
    ("Tenor I", 2, ["Tenor I 1", "Tenor I 2"]),  # not ["Tenor", "I"]
    ("", 2, ["Voice 1", "Voice 2"]),
    ("   ", 3, ["Voice 1", "Voice 2", "Voice 3"]),
])
def test_split_names(name, count, expected):
    assert _split_names(name, count) == expected


@pytest.mark.parametrize("name, count", [("Choir", 5), ("Soprano Alto", 3), ("X", 8), ("Tenor I/II", 4)])
def test_split_names_always_returns_count_distinct_names(name, count):
    names = _split_names(name, count)
    assert len(names) == count and len(set(names)) == count


@pytest.mark.parametrize("name, staves, expected", [
    ("Soprano", 1, ["Soprano"]),
    ("Soprano", 2, ["Soprano 1", "Soprano 2"]),
    ("SATB", 2, ["Soprano Alto", "Tenor Bass"]),
    ("Choir", 2, ["Soprano Alto", "Tenor Bass"]),
    ("Mixed Chorus", 2, ["Soprano Alto", "Tenor Bass"]),
    ("Choir", 3, ["Choir 1", "Choir 2", "Choir 3"]),
])
def test_staff_names(name, staves, expected):
    assert _staff_names(name, staves) == expected


@pytest.mark.parametrize("names, expected", [
    (["Soprano", "Alto"], ["Soprano", "Alto"]),
    (["Soprano", "Alto", "Soprano", "Alto"], ["Soprano 1", "Alto 1", "Soprano 2", "Alto 2"]),
    (["Bass", "Bass", "Bass"], ["Bass 1", "Bass 2", "Bass 3"]),
    # an existing "Soprano 1" must not be duplicated
    (["Soprano", "Soprano", "Soprano 1"], ["Soprano 2", "Soprano 3", "Soprano 1"]),
    (["Soprano 1", "Soprano", "Soprano"], ["Soprano 1", "Soprano 2", "Soprano 3"]),
])
def test_unique_names(names, expected):
    tracks = [Track(n) for n in names]
    _unique_names(tracks)
    assert [t.name for t in tracks] == expected
    assert len({t.name for t in tracks}) == len(tracks)


@pytest.mark.parametrize("title, ok", [
    ("Ave verum corpus", True),
    ("Jesu, meine Freude", True),
    ("Psalm 23", True),
    (None, False),
    ("", False),
    ("   ", False),
    ("Untitled", False),
    ("untitled score", False),
    ("Title", False),
    ("music21 Fragment", False),
    ("bwv66.6.mxl", False),
    ("song.MusicXML", False),
    ("piece.mid", False),
    ("piece.midi", False),
    ("scan.pdf", False),
    ("anthem.mscz", False),
    ("Requiem in D minor, K. 626", True),
])
def test_meaningful_title(title, ok):
    assert _meaningful_title(title) is ok
