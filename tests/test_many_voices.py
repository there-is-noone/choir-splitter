"""Five to twelve voices: open staves, two voices per staff, double/triple choir, SSA/TTBB/SAB.

Each voice gets a distinct pitch and its own rhythm pattern so a voice routed to the wrong
track (or merged into a neighbour) is caught by the pitch/onset checks.
"""

from __future__ import annotations

import pytest
from music21 import pitch, stream

from choirsplit.score import extract_tracks
from conftest import build_part, build_score, name_tracks_for_midi, onsets_of, overlaps, pitches_of

PITCHES = ["A5", "F5", "D5", "B4", "G4", "E4", "C4", "A3", "F3", "D3", "B2", "G2"]
#: one bar each, distinct rhythms: whole, two halves, four quarters, dotted half + quarter
RHYTHMS = [["4"], ["2", "2"], ["1", "1", "1", "1"], ["3", "1"]]


def line(index: int, bars: int = 2) -> list[str]:
    """Measures for voice `index`: its own pitch, its own rhythm pattern."""
    p = PITCHES[index]
    rhythm = RHYTHMS[index % len(RHYTHMS)]
    return [" ".join(f"{p}:{d}" for d in rhythm)] * bars


def expected_onsets(index: int, bars: int = 2) -> list[float]:
    rhythm = [float(d) for d in RHYTHMS[index % len(RHYTHMS)]]
    out, t = [], 0.0
    for _ in range(bars):
        for d in rhythm:
            out.append(t)
            t += d
    return out


def check_voice(track, index: int, bars: int = 2) -> None:
    assert track.kind == "voice"
    assert set(pitches_of(track)) == {pitch.Pitch(PITCHES[index]).ps}, track.name
    assert onsets_of(track) == expected_onsets(index, bars), track.name
    assert overlaps(track) == 0


def open_score(names, bars=2, **kw):
    return build_score(*[build_part(n, line(i, bars)) for i, n in enumerate(names)], bpm=60, **kw)


def closed_score(staff_names, voices_per_staff=2, bars=2):
    parts, index = [], 0
    for name in staff_names:
        voices = []
        for _ in range(voices_per_staff):
            voices.append(line(index, bars))
            index += 1
        parts.append(build_part(name, [tuple(v[b] for v in voices) for b in range(bars)]))
    return build_score(*parts, bpm=60)


def check_all(st, names, bars=2):
    assert [t.name for t in st.tracks] == names
    assert len(set(names)) == len(names)
    for i, t in enumerate(st.tracks):
        check_voice(t, i, bars)
    assert st.duration == pytest.approx(4.0 * bars)


SSAATTBB = ["Soprano 1", "Soprano 2", "Alto 1", "Alto 2", "Tenor 1", "Tenor 2", "Bass 1", "Bass 2"]


# --------------------------------------------------------------------------- open scores


@pytest.mark.parametrize("names", [
    ["Soprano", "Alto", "Tenor", "Bass", "Baritone"],                       # 5
    ["Soprano 1", "Soprano 2", "Alto", "Tenor", "Bass"],                     # 5 (SSATB)
    ["Soprano 1", "Soprano 2", "Alto", "Tenor", "Bass 1", "Bass 2"],         # 6
    ["Soprano 1", "Soprano 2", "Alto 1", "Alto 2", "Tenor", "Bass 1", "Bass 2"],  # 7
    SSAATTBB,                                                                # 8
], ids=lambda n: f"{len(n)}v")
def test_open_staves(load_tracks, names):
    check_all(load_tracks(open_score(names)), names)


def test_ssaattbb_eight_staves_midi(load_tracks):
    """MIDI file with one named track per voice."""
    st = load_tracks(name_tracks_for_midi(open_score(SSAATTBB)), fmt="midi")
    assert [t.name for t in st.tracks] == SSAATTBB
    for i, t in enumerate(st.tracks):
        assert set(pitches_of(t)) == {pitch.Pitch(PITCHES[i]).ps}
        assert onsets_of(t) == pytest.approx(expected_onsets(i), abs=0.01)


def test_ssa(load_tracks):
    names = ["Soprano 1", "Soprano 2", "Alto"]
    check_all(load_tracks(open_score(names)), names)


def test_ttbb_open(load_tracks):
    names = ["Tenor 1", "Tenor 2", "Bass 1", "Bass 2"]
    check_all(load_tracks(open_score(names)), names)


def test_sab(load_tracks):
    names = ["Soprano", "Alto", "Baritone"]
    check_all(load_tracks(open_score(names)), names)


# --------------------------------------------------------------------------- two voices per staff


@pytest.mark.parametrize("staves", [
    ["Soprano 1 2", "Alto 1 2", "Tenor 1 2", "Bass 1 2"],
    ["Soprano 1/2", "Alto 1/2", "Tenor 1/2", "Bass 1/2"],
    ["S1 S2", "A1 A2", "T1 T2", "B1 B2"],
    ["Soprano", "Alto", "Tenor", "Bass"],
])
def test_ssaattbb_four_staves_two_voices(load_tracks, staves):
    check_all(load_tracks(closed_score(staves)), SSAATTBB)


def test_roman_numerals(load_tracks):
    st = load_tracks(closed_score(["Soprano I/II", "Alto I/II", "Tenor I/II", "Bass I/II"]))
    check_all(st, [f"{v} {n}" for v in ("Soprano", "Alto", "Tenor", "Bass") for n in ("I", "II")])


def test_women_men_closed_satb(load_tracks):
    check_all(load_tracks(closed_score(["Women", "Men"])), ["Soprano", "Alto", "Tenor", "Bass"])


def test_ttbb_two_staves(load_tracks):
    st = load_tracks(closed_score(["Tenor I II", "Bass I II"]))
    check_all(st, ["Tenor I", "Tenor II", "Bass I", "Bass II"])


def test_ssa_on_one_staff_three_voices(load_tracks):
    st = load_tracks(closed_score(["SSA"], voices_per_staff=3))
    check_all(st, ["Soprano 1", "Soprano 2", "Alto"])


def test_sab_closed(load_tracks):
    score = build_score(
        build_part("Soprano Alto", [(line(0)[b], line(1)[b]) for b in range(2)]),
        build_part("Baritone", line(2)),
        bpm=60,
    )
    check_all(load_tracks(score), ["Soprano", "Alto", "Baritone"])


def test_mixed_open_and_closed(load_tracks):
    """S1/S2 on one staff, then A, T, B on their own staves: 5 voices."""
    score = build_score(
        build_part("Soprano 1 2", [(line(0)[b], line(1)[b]) for b in range(2)]),
        build_part("Alto", line(2)), build_part("Tenor", line(3)), build_part("Bass", line(4)),
        bpm=60,
    )
    check_all(load_tracks(score), ["Soprano 1", "Soprano 2", "Alto", "Tenor", "Bass"])


def test_choir_grand_staff_in_one_part(load_tracks):
    """One MusicXML part "Choir" with two staves, two voices each (closed SATB)."""
    staves = [
        build_part("Choir", [(line(0)[b], line(1)[b]) for b in range(2)], cls=stream.PartStaff),
        build_part("Choir", [(line(2)[b], line(3)[b]) for b in range(2)], cls=stream.PartStaff),
    ]
    st = load_tracks(build_score(staves, bpm=60))
    check_all(st, ["Soprano", "Alto", "Tenor", "Bass"])


def test_soprano_split_over_two_staves_of_one_part(load_tracks):
    staves = [build_part("Soprano", line(0), cls=stream.PartStaff),
              build_part("Soprano", line(1), cls=stream.PartStaff)]
    st = load_tracks(build_score(staves, build_part("Alto", line(2)), bpm=60))
    check_all(st, ["Soprano 1", "Soprano 2", "Alto"])


# --------------------------------------------------------------------------- double / triple choir


def test_double_choir_duplicate_names(load_tracks):
    names = ["Soprano", "Alto", "Tenor", "Bass"] * 2
    st = load_tracks(open_score(names))
    check_all(st, ["Soprano 1", "Alto 1", "Tenor 1", "Bass 1", "Soprano 2", "Alto 2", "Tenor 2", "Bass 2"])


def test_double_choir_prefixed_names(load_tracks):
    names = [f"Choir {c} {v}" for c in (1, 2) for v in ("Soprano", "Alto", "Tenor", "Bass")]
    check_all(load_tracks(open_score(names)), names)


def test_double_choir_closed(load_tracks):
    st = load_tracks(closed_score(["Women", "Men", "Women", "Men"]))
    check_all(st, ["Soprano 1", "Alto 1", "Tenor 1", "Bass 1", "Soprano 2", "Alto 2", "Tenor 2", "Bass 2"])


def test_double_choir_with_organ(load_tracks):
    from conftest import build_piano
    from music21 import instrument

    names = ["Soprano", "Alto", "Tenor", "Bass"] * 2
    parts = [build_part(n, line(i)) for i, n in enumerate(names)]
    organ = build_piano(["C4+E4:4"] * 2, ["C3:4"] * 2, name="Organ", instrument=instrument.Organ())
    st = load_tracks(build_score(*parts, organ, bpm=60))
    assert len(st.voices) == 8 and [t.name for t in st.accompaniment] == ["Organ"]
    for i, t in enumerate(st.voices):
        check_voice(t, i)


# --------------------------------------------------------------------------- 9 to 12 voices


@pytest.mark.parametrize("count", [9, 10, 11, 12])
def test_many_open_staves(load_tracks, count):
    names = [f"Voice {i + 1}" for i in range(count)]
    check_all(load_tracks(open_score(names)), names)


def test_triple_choir_twelve_staves(load_tracks):
    names = ["Soprano", "Alto", "Tenor", "Bass"] * 3
    st = load_tracks(open_score(names))
    expected = [f"{v} {c}" for c in (1, 2, 3) for v in ("Soprano", "Alto", "Tenor", "Bass")]
    check_all(st, expected)


def test_twelve_voices_on_six_staves(load_tracks):
    staves = ["Soprano 1 2", "Mezzo 1 2", "Alto 1 2", "Tenor 1 2", "Baritone 1 2", "Bass 1 2"]
    st = load_tracks(closed_score(staves))
    expected = [f"{v} {n}" for v in ("Soprano", "Mezzo", "Alto", "Tenor", "Baritone", "Bass") for n in (1, 2)]
    check_all(st, expected)


def test_twelve_voices_in_memory_without_round_trip():
    names = [f"Part {i}" for i in range(12)]
    st = extract_tracks(open_score(names))
    check_all(st, names)
