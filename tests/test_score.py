"""Voice extraction from music21 scores: timing, ties, tempo, repeats, closed scores, accompaniment.

Most fixtures are written to MusicXML and re-parsed through ``loaders.load_score`` so the
real MusicXML round trip is exercised. ``bpm=60`` is used wherever possible so that
one quarter note == one second.
"""

from __future__ import annotations

import os
import statistics

import pytest
from music21 import bar, chord, corpus, instrument, note, pitch, spanner, stream, tempo, tie

from choirsplit.score import (
    DEFAULT_VELOCITY,
    NoteEvent,
    ScoreTracks,
    TempoMap,
    Track,
    extract_tracks,
    is_accompaniment,
)
from conftest import build_part, build_score, overlaps, pitches_of, onsets_of, track_summary

P = {n: pitch.Pitch(n).ps for n in (
    "C3", "D3", "E3", "F3", "G3", "A3", "B3",
    "C4", "D4", "E4", "F4", "G4", "A4", "B4",
    "C5", "D5", "E5", "F5", "G5", "A5", "B5", "C6",
)}


def by_name(st: ScoreTracks) -> dict[str, Track]:
    return {t.name: t for t in st.tracks}


def expected_end(score: stream.Score, **kw) -> float:
    """Tempo-map end of the (repeat-expanded) score, mirroring extract_tracks."""
    try:
        expanded = score.expandRepeats()
        if expanded is not None and len(expanded.parts) == len(score.parts):
            score = expanded
    except Exception:
        pass
    return TempoMap(score, **kw).seconds(float(score.highestTime))


# =========================================================================== Bach chorales

#: Pure four-part chorales from the music21 corpus (parts named Soprano/Alto/Tenor/Bass,
#: no instrumental doubling parts). Pinned and sorted so the sweep is deterministic.
CHORALES = [
    "bach/bwv10.7", "bach/bwv101.7", "bach/bwv102.7", "bach/bwv103.6", "bach/bwv104.6",
    "bach/bwv108.6", "bach/bwv11.6", "bach/bwv110.7", "bach/bwv111.6", "bach/bwv113.8",
    "bach/bwv114.7", "bach/bwv115.6", "bach/bwv116.6", "bach/bwv117.4", "bach/bwv119.9",
    "bach/bwv120.6", "bach/bwv121.6", "bach/bwv122.6", "bach/bwv123.6", "bach/bwv125.6",
    "bach/bwv126.6", "bach/bwv127.5", "bach/bwv13.6", "bach/bwv133.6", "bach/bwv135.6",
    "bach/bwv139.6", "bach/bwv14.5", "bach/bwv140.7", "bach/bwv144.3", "bach/bwv145.5",
    "bach/bwv153.1", "bach/bwv26.6", "bach/bwv40.8",
]

SATB = ["Soprano", "Alto", "Tenor", "Bass"]


def check_satb(st: ScoreTracks, score: stream.Score) -> None:
    assert [t.name for t in st.tracks] == SATB
    assert all(t.kind == "voice" for t in st.tracks)
    means = [statistics.mean(pitches_of(t)) for t in st.tracks]
    assert means[0] > means[1] > means[2] > means[3], means
    lo = {"Soprano": (55, 84), "Alto": (50, 79), "Tenor": (43, 72), "Bass": (33, 67)}
    for t in st.tracks:
        assert overlaps(t) == 0, t.name
        low, high = lo[t.name]
        assert low <= min(pitches_of(t)) and max(pitches_of(t)) <= high, t.name
        assert all(n.duration > 0 for n in t.notes)
        assert t.notes == sorted(t.notes, key=lambda e: (e.start, e.pitch))
    assert st.duration == pytest.approx(expected_end(score))
    assert max(t.end for t in st.tracks) <= st.duration + 1e-9


def test_bwv66_6_in_memory():
    score = corpus.parse("bach/bwv66.6")
    st = extract_tracks(score)
    check_satb(st, score)
    assert [len(t.notes) for t in st.tracks] == [36, 42, 44, 41]
    # the corpus file has <sound tempo="96">, so 36 quarters last 22.5 s
    assert st.duration == pytest.approx(36 * 60 / 96)
    # first soprano note is the C#5 pickup
    assert st.tracks[0].notes[0].pitch == pitch.Pitch("C#5").ps
    assert st.tracks[0].notes[0].start == 0.0


def test_bwv66_6_musicxml_round_trip(write_and_load):
    original = extract_tracks(corpus.parse("bach/bwv66.6"))
    loaded = write_and_load(corpus.parse("bach/bwv66.6"))
    st = extract_tracks(loaded)
    check_satb(st, loaded)
    # identical notes; music21's writer pads the incomplete last bar, so duration may grow
    assert track_summary(st) == track_summary(original)


def test_bwv66_6_midi_round_trip(write_and_load):
    original = extract_tracks(corpus.parse("bach/bwv66.6"))
    loaded = write_and_load(corpus.parse("bach/bwv66.6"), fmt="midi")
    st = extract_tracks(loaded)
    check_satb(st, loaded)
    for a, b in zip(st.tracks, original.tracks):
        assert pitches_of(a) == pitches_of(b)
        assert onsets_of(a) == pytest.approx(onsets_of(b), abs=0.01)


@pytest.mark.parametrize("name", CHORALES)
def test_chorale_sweep(name):
    score = corpus.parse(name)
    st = extract_tracks(score)
    check_satb(st, score)


@pytest.mark.slow
def test_all_plain_satb_chorales(request):
    """Every plain SATB chorale in the corpus (~2 min). Run with ``-m slow``."""
    if "slow" not in (request.config.getoption("markexpr") or "") and not os.environ.get("CHOIRSPLIT_SLOW"):
        pytest.skip("slow corpus sweep: run with -m slow or CHOIRSPLIT_SLOW=1")
    checked = 0
    for path in sorted(corpus.getComposer("bach"), key=str):
        if not str(path).endswith((".mxl", ".xml")):
            continue
        score = corpus.parse(path)
        if not isinstance(score, stream.Score):
            continue
        if [p.partName for p in score.parts] != SATB:
            continue
        check_satb(extract_tracks(score), score)
        checked += 1
    assert checked > 300


def test_voice_named_parts_with_piano_playback_are_voices():
    """bwv52.6 gives every staff (even the voices) a piano sound and 'Pno' abbreviation."""
    st = extract_tracks(corpus.parse("bach/bwv52.6"))
    assert [t.name for t in st.voices] == SATB
    # the horn parts really are instruments with a piano sound -> accompaniment
    assert [t.name for t in st.accompaniment] == ["Horn 1", "Horn 2"]


def test_continuo_is_accompaniment():
    st = extract_tracks(corpus.parse("bach/bwv248.9-s"))
    assert [t.name for t in st.voices] == SATB
    assert [t.name for t in st.accompaniment] == ["Continuo"]


# =========================================================================== timing basics


def test_open_satb_round_trip(make_score, load_tracks):
    from conftest import satb_parts

    st = load_tracks(make_score(*satb_parts(), bpm=60))
    assert [t.name for t in st.tracks] == SATB
    assert pitches_of(st.tracks[0]) == [P["C5"]] * 8
    assert pitches_of(st.tracks[3]) == [P["C3"]] * 8
    for t in st.tracks:
        assert onsets_of(t) == [float(i) for i in range(8)]
        assert all(n.duration == pytest.approx(1.0) for n in t.notes)
        assert all(n.velocity == DEFAULT_VELOCITY for n in t.notes)
    assert st.duration == pytest.approx(8.0)


def test_rests_are_silent_and_trailing_rests_count(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Soprano", ["r C5 r:2", "D5 r:3"]), bpm=60))
    assert track_summary(st) == {"Soprano": [(1.0, 1.0, P["C5"]), (4.0, 1.0, P["D5"])]}
    assert st.duration == pytest.approx(8.0)


def test_dotted_notes(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Alto", ["C5:1.5 D5:0.5 E5:2", "F5:3 G5:0.75 A5:0.25"]), bpm=60))
    assert track_summary(st)["Alto"] == [
        (0.0, 1.5, P["C5"]), (1.5, 0.5, P["D5"]), (2.0, 2.0, P["E5"]),
        (4.0, 3.0, P["F5"]), (7.0, 0.75, P["G5"]), (7.75, 0.25, P["A5"]),
    ]


def test_triplets(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Tenor", ["C4:1/3 D4:1/3 E4:1/3 F4:2/3 G4:2/3 A4:2/3 B4:1"]), bpm=60))
    notes = by_name(st)["Tenor"].notes
    assert [n.start for n in notes] == pytest.approx([0, 1 / 3, 2 / 3, 1, 5 / 3, 7 / 3, 3])
    assert [n.duration for n in notes] == pytest.approx([1 / 3] * 3 + [2 / 3] * 3 + [1])


def test_grace_notes_are_ignored(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Soprano", ["gD5 C5:2 gE5 gF5 D5:2"]), bpm=60))
    assert track_summary(st)["Soprano"] == [(0.0, 2.0, P["C5"]), (2.0, 2.0, P["D5"])]


def test_pickup_measure(make_part, make_score, load_tracks):
    parts = [
        make_part("Soprano", ["G4", "C5:4", "D5:3"], first_number=0),
        make_part("Bass", ["G3", "C3:4", "G3:3"], first_number=0),
    ]
    st = load_tracks(make_score(*parts, bpm=60))
    assert onsets_of(by_name(st)["Soprano"]) == [0.0, 1.0, 5.0]
    assert onsets_of(by_name(st)["Bass"]) == [0.0, 1.0, 5.0]
    assert st.tracks[0].end == pytest.approx(8.0)


def test_voices_share_one_timeline(make_part, make_score, load_tracks):
    """A voice that enters late is still placed at its absolute time."""
    parts = [
        make_part("Soprano", ["C5:4", "D5:4", "E5:4"]),
        make_part("Alto", ["r:4", "r:4", "r:2 G4:2"]),
    ]
    st = load_tracks(make_score(*parts, bpm=60))
    assert track_summary(st)["Alto"] == [(10.0, 2.0, P["G4"])]
    assert st.duration == pytest.approx(12.0)


def test_velocity_is_taken_from_the_note():
    p = build_part("Soprano", ["C5 D5:3"])
    first = p.recurse().notes.first()
    first.volume.velocity = 110
    st = extract_tracks(build_score(p, bpm=60))
    assert [n.velocity for n in st.tracks[0].notes] == [110, DEFAULT_VELOCITY]


def test_microtones_survive():
    p = build_part("Soprano", ["C5:4"])
    n = p.recurse().notes.first()
    n.pitch.microtone = 50
    st = extract_tracks(build_score(p, bpm=60))
    assert st.tracks[0].notes[0].pitch == pytest.approx(72.5)


def test_unpitched_notes_are_skipped():
    p = build_part("Soprano", ["C5:4"])
    m = p.getElementsByClass(stream.Measure).first()
    drum = note.Unpitched(quarterLength=1)
    m.insert(0, drum)
    st = extract_tracks(build_score(p, bpm=60))
    assert pitches_of(st.tracks[0]) == [P["C5"]]


def test_part_without_measures():
    p = stream.Part()
    p.partName = "Soprano"
    p.append([note.Note("C5"), note.Rest(), note.Note("E5", quarterLength=2)])
    st = extract_tracks(build_score(p, bpm=60))
    assert track_summary(st) == {"Soprano": [(0.0, 1.0, P["C5"]), (2.0, 2.0, P["E5"])]}


def test_empty_parts_are_dropped(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Soprano", ["C5:4"]), make_part("Alto", ["r:4"]), bpm=60))
    assert [t.name for t in st.tracks] == ["Soprano"]


def test_unnamed_parts_get_placeholder_names():
    s = stream.Score()
    for pit in ("C5", "C4"):
        p = build_part(None, [f"{pit}:4"])
        s.insert(0, p)
    st = extract_tracks(s)
    assert [t.name for t in st.tracks] == ["Part 1", "Part 2"]


# =========================================================================== ties


def test_tie_across_barline(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Soprano", ["C5:2 C5:2~", "C5:4"]), bpm=60))
    assert track_summary(st)["Soprano"] == [(0.0, 2.0, P["C5"]), (2.0, 6.0, P["C5"])]


def test_tie_chain_with_continue(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Bass", ["C3:4~", "C3:4~", "C3:2 D3:2"]), bpm=60))
    assert track_summary(st)["Bass"] == [(0.0, 10.0, P["C3"]), (10.0, 2.0, P["D3"])]


def test_tie_within_measure_and_repeated_notes(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Alto", ["E4:1~ E4:1 E4:1 E4:1"]), bpm=60))
    assert track_summary(st)["Alto"] == [(0.0, 2.0, P["E4"]), (2.0, 1.0, P["E4"]), (3.0, 1.0, P["E4"])]


def test_ties_inside_chords_hymnal(make_part, make_score, load_tracks):
    """S and A as chords in one voice, both tied over the barline."""
    part = make_part("Soprano Alto", ["C5+E4:2 C5+E4:2~", "C5+E4:2 D5+F4:2"])
    st = load_tracks(make_score(part, bpm=60))
    assert track_summary(st) == {
        "Soprano": [(0.0, 2.0, P["C5"]), (2.0, 4.0, P["C5"]), (6.0, 2.0, P["D5"])],
        "Alto": [(0.0, 2.0, P["E4"]), (2.0, 4.0, P["E4"]), (6.0, 2.0, P["F4"])],
    }


def test_partial_chord_tie_only_merges_tied_pitch(make_score, load_tracks):
    """In a chord, only the tied note is held; the other re-attacks."""
    p = build_part("Piano", ["C4+E4:2 C4+G4:2"], instrument=instrument.Piano())
    first_chord = p.recurse().getElementsByClass(chord.Chord).first()
    first_chord.notes[0].tie = tie.Tie("start")  # C4 held
    second = p.recurse().getElementsByClass(chord.Chord).last()
    second.notes[0].tie = tie.Tie("stop")
    st = load_tracks(make_score(p, bpm=60))
    notes = sorted(track_summary(st)["Piano"])
    assert notes == [(0.0, 2.0, P["E4"]), (0.0, 4.0, P["C4"]), (2.0, 2.0, P["G4"])]


def test_tie_stop_without_start_is_a_new_note():
    p = build_part("Soprano", ["C5:2 C5:2"])
    last = p.recurse().notes.last()
    last.tie = tie.Tie("stop")
    st = extract_tracks(build_score(p, bpm=60))
    assert track_summary(st)["Soprano"] == [(0.0, 2.0, P["C5"]), (2.0, 2.0, P["C5"])]


def test_tie_to_a_different_pitch_does_not_merge():
    p = build_part("Soprano", ["C5:2 D5:2"])
    ns = list(p.recurse().notes)
    T = tie.Tie
    ns[0].tie, ns[1].tie = T("start"), T("stop")
    st = extract_tracks(build_score(p, bpm=60))
    assert track_summary(st)["Soprano"] == [(0.0, 2.0, P["C5"]), (2.0, 2.0, P["D5"])]


# =========================================================================== tempo


def test_no_tempo_defaults_to_96(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Soprano", ["C5:4", "D5:4"])))
    assert st.tracks[0].notes[1].start == pytest.approx(4 * 60 / 96)
    assert st.duration == pytest.approx(8 * 60 / 96)


def test_no_tempo_in_memory_defaults_to_96():
    tm = TempoMap(build_score(build_part("Soprano", ["C5:4"])))
    assert tm.seconds(4.0) == pytest.approx(2.5)


def test_tempo_change_between_measures(make_part, make_score, load_tracks):
    p = make_part("Soprano", ["C5:4", "D5:4", "E5:4"])
    p.getElementsByClass(stream.Measure)[1].insert(0, tempo.MetronomeMark(number=120))
    p.getElementsByClass(stream.Measure)[2].insert(0, tempo.MetronomeMark(number=30))
    st = load_tracks(make_score(p, bpm=60))
    assert track_summary(st)["Soprano"] == [(0.0, 4.0, P["C5"]), (4.0, 2.0, P["D5"]), (6.0, 8.0, P["E5"])]
    assert st.duration == pytest.approx(14.0)


def test_tempo_change_mid_note_splits_its_duration(make_part, make_score, load_tracks):
    """A held note across a tempo change lasts part-old-tempo, part-new-tempo."""
    p = make_part("Soprano", ["C5:4~", "C5:4"])
    p.getElementsByClass(stream.Measure)[1].insert(0, tempo.MetronomeMark(number=120))
    st = load_tracks(make_score(p, bpm=60))
    assert track_summary(st)["Soprano"] == [(0.0, 6.0, P["C5"])]


def test_tempo_change_inside_a_measure(make_part, make_score, load_tracks):
    p = make_part("Soprano", ["C5 D5 E5 F5"])
    p.getElementsByClass(stream.Measure)[0].insert(2, tempo.MetronomeMark(number=120))
    st = load_tracks(make_score(p, bpm=60))
    assert onsets_of(st.tracks[0]) == [0.0, 1.0, 2.0, 2.5]
    assert st.duration == pytest.approx(3.0)


def test_tempo_with_dotted_quarter_referent(make_part, make_score, load_tracks):
    p = make_part("Soprano", ["C5:1.5 D5:1.5", "E5:3"], ts="6/8")
    mm = tempo.MetronomeMark(number=60, referent=1.5)  # dotted quarter = 60
    p.getElementsByClass(stream.Measure)[0].insert(0, mm)
    st = load_tracks(make_score(p))
    assert track_summary(st)["Soprano"] == [(0.0, 1.0, P["C5"]), (1.0, 1.0, P["D5"]), (2.0, 2.0, P["E5"])]


def test_first_tempo_mark_after_pickup_applies_from_the_start(make_part, make_score, load_tracks):
    """A mark printed at bar 1 after an anacrusis also governs the pickup."""
    p = make_part("Soprano", ["G4", "C5:4"], first_number=0)
    p.getElementsByClass(stream.Measure)[1].insert(0, tempo.MetronomeMark(number=60))
    st = load_tracks(make_score(p))
    assert track_summary(st)["Soprano"] == [(0.0, 1.0, P["G4"]), (1.0, 4.0, P["C5"])]


def test_duplicate_marks_in_every_part_are_harmless(make_part, make_score, load_tracks):
    parts = [make_part(n, ["C5:4", "C5:4"]) for n in ("Soprano", "Alto")]
    for p in parts:
        p.getElementsByClass(stream.Measure)[0].insert(0, tempo.MetronomeMark(number=60))
        p.getElementsByClass(stream.Measure)[1].insert(0, tempo.MetronomeMark(number=120))
    st = load_tracks(make_score(*parts))
    assert st.duration == pytest.approx(6.0)
    assert onsets_of(st.tracks[1]) == [0.0, 4.0]


def test_text_tempo_indication(make_part, make_score, load_tracks):
    """'Adagio' with no number still yields a usable tempo (music21 infers one)."""
    p = make_part("Soprano", ["C5:4"])
    p.getElementsByClass(stream.Measure)[0].insert(0, tempo.MetronomeMark("adagio"))
    st = extract_tracks(make_score(p))
    assert 0 < st.duration < 60


def test_bpm_override_ignores_score_tempo(make_part, make_score, load_tracks):
    p = make_part("Soprano", ["C5:4", "D5:4"])
    p.getElementsByClass(stream.Measure)[1].insert(0, tempo.MetronomeMark(number=30))
    st = load_tracks(make_score(p, bpm=60), bpm=120)
    assert track_summary(st)["Soprano"] == [(0.0, 2.0, P["C5"]), (2.0, 2.0, P["D5"])]
    assert st.duration == pytest.approx(4.0)


@pytest.mark.parametrize("scale, expected", [(1.0, 4.0), (0.5, 8.0), (2.0, 2.0), (0.8, 5.0)])
def test_tempo_scale(make_part, make_score, scale, expected):
    st = extract_tracks(make_score(make_part("Soprano", ["C5:4"]), bpm=60), tempo_scale=scale)
    assert st.duration == pytest.approx(expected)
    assert st.tracks[0].notes[0].duration == pytest.approx(expected)


def test_tempo_scale_combines_with_bpm(make_part, make_score):
    st = extract_tracks(make_score(make_part("Soprano", ["C5:4"])), bpm=120, tempo_scale=0.5)
    assert st.duration == pytest.approx(4.0)


@pytest.mark.parametrize("kwargs", [{"tempo_scale": 0}, {"tempo_scale": -1}, {"bpm": 0}, {"bpm": -60}])
def test_invalid_tempo_arguments(make_part, make_score, kwargs):
    with pytest.raises(ValueError):
        extract_tracks(make_score(make_part("Soprano", ["C5:4"])), **kwargs)


def test_tempo_map_segments():
    s = build_score(build_part("Soprano", ["C5:4", "C5:4", "C5:4"]))
    ms = s.parts[0].getElementsByClass(stream.Measure)
    ms[0].insert(0, tempo.MetronomeMark(number=60))
    ms[1].insert(0, tempo.MetronomeMark(number=120))
    ms[2].insert(0, tempo.MetronomeMark(number=240))
    tm = TempoMap(s)
    assert [tm.seconds(q) for q in (0, 2, 4, 6, 8, 10, 12)] == pytest.approx([0, 2, 4, 5, 6, 6.5, 7])


# =========================================================================== repeats


def _with_barline(part, index, barline, side="right"):
    m = part.getElementsByClass(stream.Measure)[index]
    if side == "right":
        m.rightBarline = barline
    else:
        m.leftBarline = barline
    return part


def test_simple_repeat_is_expanded(make_part, make_score, load_tracks):
    p = _with_barline(make_part("Soprano", ["C5:4", "D5:4"]), 1, bar.Repeat(direction="end"))
    st = load_tracks(make_score(p, bpm=60))
    assert pitches_of(st.tracks[0]) == [P["C5"], P["D5"], P["C5"], P["D5"]]
    assert onsets_of(st.tracks[0]) == [0.0, 4.0, 8.0, 12.0]
    assert st.duration == pytest.approx(16.0)


def test_repeat_section_in_the_middle(make_part, make_score, load_tracks):
    parts = []
    for name, pits in (("Soprano", ("C5", "D5", "E5", "F5")), ("Bass", ("C3", "D3", "E3", "F3"))):
        p = make_part(name, [f"{x}:4" for x in pits])
        _with_barline(p, 1, bar.Repeat(direction="start"), side="left")
        _with_barline(p, 2, bar.Repeat(direction="end"))
        parts.append(p)
    st = load_tracks(make_score(*parts, bpm=60))
    assert pitches_of(by_name(st)["Soprano"]) == [P[x] for x in ("C5", "D5", "E5", "D5", "E5", "F5")]
    assert pitches_of(by_name(st)["Bass"]) == [P[x] for x in ("C3", "D3", "E3", "D3", "E3", "F3")]
    assert st.duration == pytest.approx(24.0)


def test_volta_endings(make_part, make_score, load_tracks):
    p = make_part("Soprano", ["C5:4", "D5:4", "E5:4"])
    ms = p.getElementsByClass(stream.Measure)
    ms[1].rightBarline = bar.Repeat(direction="end")
    p.insert(0, spanner.RepeatBracket(ms[1], number=1))
    p.insert(0, spanner.RepeatBracket(ms[2], number=2))
    st = load_tracks(make_score(p, bpm=60))
    assert pitches_of(st.tracks[0]) == [P["C5"], P["D5"], P["C5"], P["E5"]]


def test_repeat_with_tempo_change_is_timed_per_pass(make_part, make_score, load_tracks):
    p = make_part("Soprano", ["C5:4", "D5:4"])
    p.getElementsByClass(stream.Measure)[1].insert(0, tempo.MetronomeMark(number=120))
    _with_barline(p, 1, bar.Repeat(direction="end"))
    st = load_tracks(make_score(p, bpm=60))
    # the 60 bpm mark is repeated with bar 1, so each pass is 4 s + 2 s
    assert onsets_of(st.tracks[0]) == [0.0, 4.0, 6.0, 10.0]
    assert st.duration == pytest.approx(12.0)


@pytest.mark.parametrize("layout_", ["start_only", "two_starts", "end_on_first", "double_end"])
def test_malformed_repeats_do_not_crash(make_part, make_score, load_tracks, layout_):
    p = make_part("Soprano", ["C5:4", "D5:4", "E5:4"])
    if layout_ == "start_only":
        _with_barline(p, 1, bar.Repeat(direction="start"), side="left")
    elif layout_ == "two_starts":
        _with_barline(p, 0, bar.Repeat(direction="start"), side="left")
        _with_barline(p, 1, bar.Repeat(direction="start"), side="left")
    elif layout_ == "end_on_first":
        _with_barline(p, 0, bar.Repeat(direction="end"))
        _with_barline(p, 1, bar.Repeat(direction="start"), side="left")
    else:
        _with_barline(p, 0, bar.Repeat(direction="end"))
        _with_barline(p, 2, bar.Repeat(direction="end"))
    st = load_tracks(make_score(p, bpm=60))
    ps = pitches_of(st.tracks[0])
    assert {P["C5"], P["D5"], P["E5"]} <= set(ps)
    assert overlaps(st.tracks[0]) == 0
    assert st.duration >= 12.0


def test_unexpandable_repeat_in_memory_falls_back_to_written_order(monkeypatch):
    s = build_score(build_part("Soprano", ["C5:4", "D5:4"]), bpm=60)

    def boom(self, *a, **k):
        raise RuntimeError("cannot expand")

    monkeypatch.setattr(stream.Score, "expandRepeats", boom)
    st = extract_tracks(s)
    assert pitches_of(st.tracks[0]) == [P["C5"], P["D5"]]


# =========================================================================== accompaniment


def test_piano_two_staves_become_one_accompaniment_track(make_part, make_piano, make_score, load_tracks):
    score = make_score(
        make_part("Soprano", ["C5:4", "D5:4"]),
        make_part("Alto", ["A4:4", "B4:4"]),
        make_piano(["E4+G4:4", "F4+A4:4"], ["C3:4", "D3:4"]),
        bpm=60,
    )
    st = load_tracks(score)
    assert [t.name for t in st.voices] == ["Soprano", "Alto"]
    assert [(t.name, t.kind) for t in st.accompaniment] == [("Piano", "accompaniment")]
    acc = st.accompaniment[0]
    assert sorted(pitches_of(acc)) == sorted([P["E4"], P["G4"], P["F4"], P["A4"], P["C3"], P["D3"]])
    assert sorted(set(onsets_of(acc))) == [0.0, 4.0]

    st2 = load_tracks(score, include_accompaniment=False)
    assert [t.name for t in st2.tracks] == ["Soprano", "Alto"]
    assert st2.accompaniment == []


def test_piano_with_two_voices_per_staff_is_merged(make_part, make_piano, make_score, load_tracks):
    score = make_score(
        make_part("Soprano", ["C5:4"]),
        make_piano([("G4:4", "E4:2 D4:2")], [("C3:4", "C2:4")]),
        bpm=60,
    )
    st = load_tracks(score)
    assert len(st.accompaniment) == 1
    assert sorted(pitches_of(st.accompaniment[0])) == sorted(P[x] if x in P else pitch.Pitch(x).ps
                                                             for x in ("G4", "E4", "D4", "C3", "C2"))


def test_organ_three_staves_one_track(make_part, make_score, load_tracks):
    manuals_and_pedal = [
        build_part("Organ", ["E4+G4:4"], instrument=instrument.Organ(), cls=stream.PartStaff),
        build_part("Organ", ["C4:4"], cls=stream.PartStaff),
        build_part("Organ", ["C3:4"], cls=stream.PartStaff),
    ]
    st = load_tracks(make_score(make_part("Bass", ["C3:4"]), manuals_and_pedal, bpm=60))
    assert [t.name for t in st.voices] == ["Bass"]
    assert [t.name for t in st.accompaniment] == ["Organ"]
    assert sorted(pitches_of(st.accompaniment[0])) == [P["C3"], P["C4"], P["E4"], P["G4"]]


@pytest.mark.parametrize("name, inst, expected", [
    ("Piano", None, "accompaniment"),
    ("Pno.", None, "accompaniment"),
    ("Organ", None, "accompaniment"),
    ("Accompaniment", None, "accompaniment"),
    ("Rehearsal reduction", None, "accompaniment"),
    ("Continuo", None, "accompaniment"),
    ("Harpsichord", None, "accompaniment"),
    ("Rehearsal", instrument.Piano(), "accompaniment"),
    ("Upper", instrument.Organ(), "accompaniment"),
    ("Harp", instrument.Harp(), "accompaniment"),
    ("Guitar", instrument.AcousticGuitar(), "accompaniment"),
    ("Soprano", None, "voice"),
    ("Soprano", instrument.Piano(), "voice"),      # piano playback sound on a sung part
    ("Bass", instrument.Piano(), "voice"),
    ("Alto", instrument.Soprano(), "voice"),
    ("Choir", instrument.Choir(), "voice"),
    ("Melody", None, "voice"),
])
def test_accompaniment_detection_round_trip(make_part, make_score, load_tracks, name, inst, expected):
    score = make_score(make_part("Tenor", ["C4:4"]), make_part(name, ["E4:4"], instrument=inst), bpm=60)
    st = load_tracks(score)
    kinds = {t.name: t.kind for t in st.tracks}
    other = [t for t in st.tracks if t.name != "Tenor"]
    assert kinds["Tenor"] == "voice"
    assert len(other) == 1 and other[0].kind == expected, kinds


def test_is_accompaniment_by_class_without_name():
    p = build_part(None, ["C4:4"], instrument=instrument.Piano())
    assert is_accompaniment(p)
    p = build_part(None, ["C4:4"], instrument=instrument.Vocalist())
    assert not is_accompaniment(p)


def test_single_staff_piano_is_accompaniment(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Soprano", ["C5:4"]), make_part("Piano", ["C4+E4+G4:4"]), bpm=60))
    assert [t.name for t in st.accompaniment] == ["Piano"]
    assert len(st.accompaniment[0].notes) == 3


# =========================================================================== closed scores


def test_sa_and_tb_closed_score(make_part, make_score, load_tracks):
    women = make_part("Soprano Alto", [("E5 F5 G5:2", "C5 D5 E5:2"), ("A5:4", "F5:4")])
    men = make_part("Tenor Bass", [("G3 A3 B3:2", "C3 D3 E3:2"), ("C4:4", "F3:4")])
    st = load_tracks(make_score(women, men, bpm=60))
    assert [t.name for t in st.tracks] == SATB
    s, a, t, b = st.tracks
    assert pitches_of(s) == [P[x] for x in ("E5", "F5", "G5", "A5")]
    assert pitches_of(a) == [P[x] for x in ("C5", "D5", "E5", "F5")]
    assert pitches_of(t) == [P[x] for x in ("G3", "A3", "B3", "C4")]
    assert pitches_of(b) == [P[x] for x in ("C3", "D3", "E3", "F3")]
    for tr in st.tracks:
        assert onsets_of(tr) == [0.0, 1.0, 2.0, 4.0]
        assert overlaps(tr) == 0


@pytest.mark.parametrize("upper, lower, names", [
    ("Women", "Men", SATB),
    ("S A", "T B", SATB),
    ("SA", "TB", SATB),
    ("S/A", "T/B", SATB),
    ("Soprano & Alto", "Tenor & Bass", SATB),
    ("Sopran/Alt", "Tenor/Bass", ["Sopran", "Alto", "Tenor", "Bass"]),
])
def test_closed_score_staff_names(make_part, make_score, load_tracks, upper, lower, names):
    score = make_score(
        make_part(upper, [("E5:4", "C5:4")]),
        make_part(lower, [("G3:4", "C3:4")]),
        bpm=60,
    )
    st = load_tracks(score)
    assert [t.name for t in st.tracks] == names
    assert [t.notes[0].pitch for t in st.tracks] == [P["E5"], P["C5"], P["G3"], P["C3"]]


def test_split_voices_false_keeps_one_track_per_staff(make_part, make_score, load_tracks):
    women = make_part("Women", [("E5:2 F5:2", "C5:4")])
    st = load_tracks(make_score(women, bpm=60), split_voices=False, split_chords=False)
    assert [t.name for t in st.tracks] == ["Women"]
    assert sorted(pitches_of(st.tracks[0])) == [P["C5"], P["E5"], P["F5"]]


def test_voice_two_drops_out(make_part, make_score, load_tracks):
    """Voice 2 only in bars 1 and 3: alto notes keep their absolute times."""
    women = make_part("Women", [("E5:4", "C5:4"), "F5:4", ("G5:2 A5:2", "B4:4")])
    st = load_tracks(make_score(women, bpm=60))
    assert track_summary(st) == {
        "Soprano": [(0.0, 4.0, P["E5"]), (4.0, 4.0, P["F5"]), (8.0, 2.0, P["G5"]), (10.0, 2.0, P["A5"])],
        "Alto": [(0.0, 4.0, P["C5"]), (8.0, 4.0, P["B4"])],
    }
    assert st.duration == pytest.approx(12.0)


def test_voice_two_with_explicit_rests_in_voice_one(make_part, make_score, load_tracks):
    """Realistic export: voice 1 rests while voice 2 sings."""
    women = make_part("Women", [("E5:4", "C5:4"), ("r:4", "A4:4"), ("F5:4", "r:4")])
    st = load_tracks(make_score(women, bpm=60))
    assert track_summary(st) == {
        "Soprano": [(0.0, 4.0, P["E5"]), (8.0, 4.0, P["F5"])],
        "Alto": [(0.0, 4.0, P["C5"]), (4.0, 4.0, P["A4"])],
    }


@pytest.mark.xfail(strict=True, reason=(
    "music21's MusicXML importer drops the <voice> number when a measure holds a single "
    "voice, so a bar containing only voice 2 is indistinguishable from voice 1"))
def test_measure_with_only_voice_two(make_part, make_score, load_tracks):
    women = make_part("Women", [("E5:4", "C5:4"), ("r:4", "A4:4")])
    # remove voice 1 completely from bar 2
    m2 = women.getElementsByClass(stream.Measure)[1]
    m2.remove(m2.voices[0])
    st = load_tracks(make_score(women, bpm=60))
    assert pitches_of(by_name(st)["Alto"]) == [P["C5"], P["A4"]]


def test_closed_score_voice_two_only_in_memory(make_part, make_score):
    """In memory the Voice id survives, so a bar with only voice 2 is assigned correctly."""
    women = make_part("Women", [("E5:4", "C5:4"), ("r:4", "A4:4")])
    m2 = women.getElementsByClass(stream.Measure)[1]
    m2.remove(m2.voices[0])
    st = extract_tracks(make_score(women, bpm=60))
    assert pitches_of(by_name(st)["Alto"]) == [P["C5"], P["A4"]]
    assert pitches_of(by_name(st)["Soprano"]) == [P["E5"]]


def test_hymnal_chords_split_top_and_bottom(make_part, make_score, load_tracks):
    part = make_part("Women", ["E5+C5 F5+A4 G5:2", "C6+E5 B5+G5 C6+C6:2"])
    st = load_tracks(make_score(part, bpm=60))
    assert [t.name for t in st.tracks] == ["Soprano", "Alto"]
    s, a = st.tracks
    # unisons (single G5 note, or a C6+C6 chord) go to both voices
    assert pitches_of(s) == [P["E5"], P["F5"], P["G5"], P["C6"], P["B5"], P["C6"]]
    assert pitches_of(a) == [P["C5"], P["A4"], P["G5"], P["E5"], P["G5"], P["C6"]]
    assert onsets_of(s) == onsets_of(a) == [0.0, 1.0, 2.0, 4.0, 5.0, 6.0]
    assert overlaps(s) == overlaps(a) == 0


def test_hymnal_tb_on_bass_clef_staff(make_part, make_score, load_tracks):
    upper = make_part("Soprano Alto", ["E5+C5:2 D5+B4:2"])
    lower = make_part("Tenor Bass", ["G3+C3:2 G3+G2:2"])
    st = load_tracks(make_score(upper, lower, bpm=60))
    assert [t.name for t in st.tracks] == SATB
    assert [pitches_of(t) for t in st.tracks] == [
        [P["E5"], P["D5"]], [P["C5"], P["B4"]], [P["G3"], P["G3"]], [P["C3"], pitch.Pitch("G2").ps],
    ]


def test_hymnal_split_chords_false(make_part, make_score, load_tracks):
    part = make_part("Women", ["E5+C5:2 F5+A4:2"])
    st = load_tracks(make_score(part, bpm=60), split_chords=False)
    assert [t.name for t in st.tracks] == ["Women"]
    assert len(st.tracks[0].notes) == 4


def test_occasional_divisi_chord_in_named_voice_stays_one_track(make_part, make_score, load_tracks):
    """A 'Soprano' part with one divisi chord is not a two-voice staff."""
    part = make_part("Soprano", ["C5 D5 E5 F5", "G5 A5 B5 C6+A5"])
    st = load_tracks(make_score(part, bpm=60))
    assert [t.name for t in st.tracks] == ["Soprano"]
    assert len(st.tracks[0].notes) == 9


def test_mixed_chords_and_voices_on_closed_staff(make_part, make_score, load_tracks):
    """Common closed-score engraving: S+A share chords, and split into voices only where
    the rhythm differs. The alto must keep the lower chord notes."""
    women = make_part("Women", ["E5+C5:2 F5+A4:2", ("G5:4", "B4:2 C5:2"), "C6+A5:4"])
    st = load_tracks(make_score(women, bpm=60))
    assert track_summary(st) == {
        "Soprano": [(0.0, 2.0, P["E5"]), (2.0, 2.0, P["F5"]), (4.0, 4.0, P["G5"]), (8.0, 4.0, P["C6"])],
        "Alto": [(0.0, 2.0, P["C5"]), (2.0, 2.0, P["A4"]), (4.0, 2.0, P["B4"]), (6.0, 2.0, P["C5"]),
                 (8.0, 4.0, P["A5"])],
    }


# =========================================================================== titles


def test_title_from_metadata(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Soprano", ["C5:4"]), title="Ave verum corpus"))
    assert st.title == "Ave verum corpus"


def test_title_falls_back_to_score(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Soprano", ["C5:4"])))
    assert st.title == "Score"


def test_title_argument_wins(make_part, make_score, load_tracks):
    st = load_tracks(make_score(make_part("Soprano", ["C5:4"]), title="Ave"), title="Custom")
    assert st.title == "Custom"


def test_movement_name_used_when_title_missing(make_part, make_score):
    s = make_score(make_part("Soprano", ["C5:4"]), title="")
    s.metadata.movementName = "Kyrie"
    assert extract_tracks(s).title == "Kyrie"


# =========================================================================== data classes


def test_track_and_scoretracks_helpers():
    t = Track("Soprano", notes=[NoteEvent(1.0, 2.0, 60.0), NoteEvent(0.0, 0.5, 62.0)])
    acc = Track("Piano", "accompaniment", [NoteEvent(0, 4, 48)])
    st = ScoreTracks("x", [t, acc], 4.0)
    assert t.end == 3.0
    assert Track("empty").end == 0.0
    assert st.voices == [t] and st.accompaniment == [acc]


def test_repeat_with_piano_keeps_staves_grouped(make_part, make_piano, make_score, load_tracks):
    sop = _with_barline(make_part("Soprano", ["C5:4", "D5:4"]), 1, bar.Repeat(direction="end"))
    rh, lh = make_piano(["E4:4", "F4:4"], ["C3:4", "D3:4"])
    for staff in (rh, lh):
        _with_barline(staff, 1, bar.Repeat(direction="end"))
    st = load_tracks(make_score(sop, [rh, lh], bpm=60))
    assert [(t.name, t.kind) for t in st.tracks] == [("Soprano", "voice"), ("Piano", "accompaniment")]
    assert len(st.accompaniment[0].notes) == 8
    assert st.duration == pytest.approx(16.0)
