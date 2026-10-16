"""Edge cases of the native MuseScore reader: ottava shifts, MS2 voltas, staff mapping.

Scores are tiny inline .mscx strings written to tmp_path.  Every note is written as MIDI
60 (C4) unless stated; <pitch> in MuseScore excludes the ottava, so the reader has to add
the shift of any 8va/8vb/15ma/15mb line covering the note.
"""

from __future__ import annotations

import pytest
from music21 import chord, note, spanner

from choirsplit import musescore
from choirsplit.loaders import load_score


@pytest.fixture(autouse=True)
def no_musescore_program(monkeypatch):
    monkeypatch.delenv(musescore.MODE_ENV, raising=False)
    monkeypatch.setattr(musescore, "find_musescore_cli", lambda: None)


# ------------------------------------------------------------------ builders

def _note(pitch=60, dur="quarter", extra=""):
    return (f"<Chord><durationType>{dur}</durationType>"
            f"<Note><pitch>{pitch}</pitch><tpc>14</tpc></Note>{extra}</Chord>")


def _rest(dur="quarter"):
    return f"<Rest><durationType>{dur}</durationType></Rest>"


def _loc(measures=0, fractions=None):
    inner = f"<measures>{measures}</measures>" if measures else ""
    if fractions:
        inner += f"<fractions>{fractions}</fractions>"
    return f"<location>{inner}</location>"


def _ottava3(subtype, measures=0, fractions=None):
    """MuseScore 3/4 start half of an ottava spanner (ends `measures`/`fractions` later)."""
    return (f'<Spanner type="Ottava"><Ottava><subtype>{subtype}</subtype></Ottava>'
            f"<next>{_loc(measures, fractions)}</next></Spanner>")


def _end3():
    return '<Spanner type="Ottava"><prev><location><measures>-1</measures></location></prev></Spanner>'


def _volta3(endings, measures=1):
    return (f'<Spanner type="Volta"><Volta><endings>{endings}</endings></Volta>'
            f"<next>{_loc(measures)}</next></Spanner>")


def _staff3(measures, staff_id=1):
    """measures: list of lists of voice strings (or one string) -> <Staff>."""
    out = [f'<Staff id="{staff_id}">']
    for m in measures:
        extra = ""
        if isinstance(m, tuple):
            m, extra = m
        voices = [m] if isinstance(m, str) else m
        out.append("<Measure>" + extra + "".join(f"<voice>{v}</voice>" for v in voices)
                   + "</Measure>")
    out.append("</Staff>")
    return "".join(out)


def _part(*staff_ids, name="Piano"):
    staves = "".join(
        f'<Staff{"" if i is None else f" id=\"{i}\""}><StaffType group="pitched"/></Staff>'
        for i in staff_ids)
    return (f"<Part>{staves}<trackName>{name}</trackName>"
            f"<Instrument><longName>{name}</longName><trackName>{name}</trackName>"
            f"</Instrument></Part>")


def _doc(parts, staves, version="3.01"):
    return (f'<?xml version="1.0" encoding="UTF-8"?><museScore version="{version}"><Score>'
            f"<Division>480</Division><metaTag name=\"workTitle\">T</metaTag>"
            f"{parts}{staves}</Score></museScore>")


def _load(tmp_path, xml, name="s.mscx"):
    f = tmp_path / name
    f.write_text(xml, encoding="utf-8")
    return load_score(f)


def _midis(part):
    out = []
    for n in part.recurse().notes:
        out.append(tuple(p.midi for p in n.pitches) if isinstance(n, chord.Chord) else n.pitch.midi)
    return out


def _one_staff(tmp_path, measures, version="3.01"):
    xml = _doc(_part(1), _staff3(measures), version)
    return _load(tmp_path, xml).parts[0]


# ------------------------------------------------------------------ MuseScore 3+ ottavas

@pytest.mark.parametrize("subtype,shift", [
    ("8va", 12), ("8vb", -12), ("15ma", 24), ("15mb", -24), ("22ma", 36), ("22mb", -36),
])
def test_v3_ottava_subtypes(tmp_path, subtype, shift):
    part = _one_staff(tmp_path, [_ottava3(subtype, 1) + _note() * 4, _note() * 4])
    assert _midis(part) == [60 + shift] * 4 + [60] * 4


def test_v3_ottava_unknown_subtype_is_ignored(tmp_path):
    part = _one_staff(tmp_path, [_ottava3("bogus", 1) + _note() * 4])
    assert _midis(part) == [60] * 4


def test_v3_ottava_spans_several_measures(tmp_path):
    part = _one_staff(tmp_path, [
        _ottava3("8va", 3) + _note() * 4, _note() * 4, _note() * 4 + _end3(), _note() * 4,
    ])
    assert _midis(part) == [72] * 12 + [60] * 4


def test_v3_ottava_starting_mid_measure(tmp_path):
    # starts on beat 3 and ends at the barline (relative location: 0 measures, +1/2 note)
    start = _ottava3("8va", 0, "1/2")
    part = _one_staff(tmp_path, [_note() * 2 + start + _note() * 2, _note() * 4])
    assert _midis(part) == [60, 60, 72, 72] + [60] * 4


def test_v3_location_element_moves_the_ottava_start(tmp_path):
    # <location> is relative to the previous element: 2 quarters of notes + 1/4 note = beat 4
    start = f"<location><fractions>1/4</fractions></location>{_ottava3('8va', 0, '1/4')}"
    part = _one_staff(tmp_path, [_note() * 2 + start + _note()])
    assert _midis(part) == [60, 60, 72]


def test_v3_ottava_ending_mid_measure(tmp_path):
    # starts at the barline, ends on beat 3 of the next measure (relative fractions 1/2)
    part = _one_staff(tmp_path, [_ottava3("8va", 1, "1/2") + _note() * 4, _note() * 4])
    assert _midis(part) == [72] * 4 + [72, 72, 60, 60]


def test_v3_ottava_ending_mid_measure_with_negative_fraction(tmp_path):
    # starts on beat 3, ends on beat 2 of the next measure: measures=1, fractions=-1/4
    start = _ottava3("8vb", 1, "-1/4")
    part = _one_staff(tmp_path, [_note() * 2 + start + _note() * 2, _note() * 4])
    assert _midis(part) == [60, 60, 48, 48, 48, 60, 60, 60]


def test_v3_ottava_within_one_measure(tmp_path):
    start = _ottava3("15ma", 0, "1/4")  # one quarter long
    part = _one_staff(tmp_path, [_note() + start + _note() * 2 + _note()])
    # the line starts after the first note (cursor 1) and lasts 1/4 whole = 1 quarter
    assert _midis(part) == [60, 84, 60, 60]


def test_v3_ottava_covers_other_voices(tmp_path):
    # the line sits in voice 1 but shifts the voice 2 notes under it too
    part = _one_staff(tmp_path, [[_ottava3("8va", 1) + _note() * 4, _note(55) * 4],
                                 [_note() * 4, _note(55) * 4]])
    assert sorted(_midis(part.getElementsByClass("Measure")[0].voices[1])) == [67] * 4
    assert _midis(part.getElementsByClass("Measure")[1].voices[1]) == [55] * 4


def test_v3_ottava_in_second_voice_shifts_first_voice(tmp_path):
    part = _one_staff(tmp_path, [[_note() * 4, _ottava3("8va", 1) + _note(55) * 4]])
    v = part.getElementsByClass("Measure")[0].voices
    assert _midis(v[0]) == [72] * 4
    assert _midis(v[1]) == [67] * 4


def test_v3_ottava_shifts_chords_and_leaves_rests(tmp_path):
    ch = ("<Chord><durationType>quarter</durationType>"
          "<Note><pitch>60</pitch><tpc>14</tpc></Note>"
          "<Note><pitch>64</pitch><tpc>18</tpc></Note></Chord>")
    part = _one_staff(tmp_path, [_ottava3("8vb", 1) + ch + _rest() + ch + _rest()])
    assert _midis(part) == [(48, 52), (48, 52)]
    assert [r.quarterLength for r in part.recurse().getElementsByClass(note.Rest)] == [1.0, 1.0]


def test_v3_overlapping_ottavas_add_up(tmp_path):
    part = _one_staff(tmp_path, [_ottava3("8va", 1) + _ottava3("8va", 1) + _note() * 4])
    assert _midis(part) == [84] * 4


def test_v3_opposite_ottavas_cancel(tmp_path):
    part = _one_staff(tmp_path, [_ottava3("8va", 1) + _ottava3("8vb", 1) + _note() * 4])
    assert _midis(part) == [60] * 4


def test_v3_ottava_without_end_runs_to_the_end(tmp_path):
    part = _one_staff(tmp_path, [_note() * 4, _ottava3("8va", 99) + _note() * 4, _note() * 4])
    assert _midis(part) == [60] * 4 + [72] * 8


def test_v3_closing_half_alone_does_nothing(tmp_path):
    part = _one_staff(tmp_path, [_end3() + _note() * 4])
    assert _midis(part) == [60] * 4


def test_v3_ottava_without_next_is_ignored(tmp_path):
    xml = '<Spanner type="Ottava"><Ottava><subtype>8va</subtype></Ottava></Spanner>'
    part = _one_staff(tmp_path, [xml + _note() * 4])
    assert _midis(part) == [60] * 4


def test_v3_ottava_does_not_leak_to_next_staff(tmp_path):
    xml = _doc(_part(1, 2),
               _staff3([_ottava3("8va", 1) + _note() * 4], 1)
               + _staff3([_note() * 4], 2))
    score = _load(tmp_path, xml)
    assert [_midis(p) for p in score.parts] == [[72] * 4, [60] * 4]


def test_v3_ottava_in_tuplet_and_after_triplet(tmp_path):
    trip = ("<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes>"
            "<baseNote>eighth</baseNote></Tuplet>"
            + _note(60, "eighth") * 3 + "<endTuplet/>")
    # ottava starts after the triplet (1 quarter in) and runs to the barline
    part = _one_staff(tmp_path, [trip + _ottava3("8va", 1) + _note() * 3])
    assert _midis(part) == [60, 60, 60, 72, 72, 72]


def test_ottava_final_measure_end_beyond_score(tmp_path):
    part = _one_staff(tmp_path, [_note() * 4, _ottava3("8va", 5) + _note() * 4])
    assert _midis(part) == [60] * 4 + [72] * 4


def test_v4_ottava_with_text_noise_children(tmp_path):
    sp = ('<Spanner type="Ottava"><Ottava><subtype>8va</subtype><placement>above</placement>'
          '<lineWidth>0.15</lineWidth></Ottava><next><location><measures>1</measures>'
          '</location></next></Spanner>')
    part = _one_staff(tmp_path, [sp + _note() * 4, _note() * 4], "4.00")
    assert _midis(part) == [72] * 4 + [60] * 4


# -------------------------------------------------------------- MuseScore 2 ottavas

def _legacy_chord(pitch=60, dur="quarter"):
    return _note(pitch, dur)


def _measure2(content, tick=None):
    t = f"<tick>{tick}</tick>" if tick is not None else ""
    return f"<Measure>{t}{content}</Measure>"


def _doc2(measures_xml, subtype_xml=None):
    staff = '<Staff id="1">' + measures_xml + "</Staff>"
    return _doc(_part(1), staff, "2.06")


def _o2(sid, subtype="8va"):
    return f'<Ottava id="{sid}"><subtype>{subtype}</subtype></Ottava>'


def _end2(sid):
    return f'<endSpanner id="{sid}"/>'


def _tick(t):
    return f"<tick>{t}</tick>"


def test_v2_ottava_with_end_spanner(tmp_path):
    xml = _doc2(
        _measure2(_tick(0) + _o2(1) + _legacy_chord() * 4, 0)
        + _measure2(_tick(1920) + _end2(1) + _legacy_chord() * 4, 1920))
    part = _load(tmp_path, xml).parts[0]
    assert _midis(part) == [72] * 4 + [60] * 4


def test_v2_ottava_ending_mid_measure(tmp_path):
    xml = _doc2(
        _measure2(_o2(1) + _legacy_chord() * 4)
        + _measure2(_legacy_chord() * 2 + _tick(2880) + _end2(1) + _legacy_chord() * 2, 1920))
    part = _load(tmp_path, xml).parts[0]
    assert _midis(part) == [72] * 6 + [60] * 2


def test_v2_ottava_without_end_spanner_runs_to_the_end(tmp_path):
    xml = _doc2(_measure2(_legacy_chord() * 4) + _measure2(_o2(1, "8vb") + _legacy_chord() * 4, 1920)
                + _measure2(_legacy_chord() * 4, 3840))
    part = _load(tmp_path, xml).parts[0]
    assert _midis(part) == [60] * 4 + [48] * 8


def test_v2_end_spanner_with_unknown_id_is_ignored(tmp_path):
    xml = _doc2(_measure2(_o2(1) + _legacy_chord() * 2 + _end2(7) + _legacy_chord() * 2))
    assert _midis(_load(tmp_path, xml).parts[0]) == [72] * 4


@pytest.mark.parametrize("subtype,shift", [
    ("8va", 12), ("8vb", -12), ("15ma", 24), ("15mb", -24),
    # numeric codes follow the current enum order (0 8va, 1 8vb, 2 15ma, 3 15mb)
    ("0", 12), ("1", -12), ("2", 24), ("3", -24), ("99", 0),
])
def test_v2_ottava_subtypes_including_numeric(tmp_path, subtype, shift):
    xml = _doc2(_measure2(_o2(1, subtype) + _legacy_chord() * 4))
    assert _midis(_load(tmp_path, xml).parts[0]) == [60 + shift] * 4


def test_v2_two_ottavas_in_a_row(tmp_path):
    xml = _doc2(
        _measure2(_o2(1, "8va") + _legacy_chord() * 2 + _tick(960) + _end2(1)
                  + _o2(2, "8vb") + _legacy_chord() * 2)
        + _measure2(_end2(2) + _legacy_chord() * 4, 1920))
    assert _midis(_load(tmp_path, xml).parts[0]) == [72, 72, 48, 48, 60, 60, 60, 60]


def test_v2_ottava_state_does_not_leak_between_staves(tmp_path):
    staff1 = '<Staff id="1">' + _measure2(_o2(1) + _legacy_chord() * 4) + "</Staff>"
    staff2 = '<Staff id="2">' + _measure2(_legacy_chord() * 4) + "</Staff>"
    xml = _doc(_part(1, 2), staff1 + staff2, "2.06")
    score = _load(tmp_path, xml)
    assert [_midis(p) for p in score.parts] == [[72] * 4, [60] * 4]


# ------------------------------------------------------------------ voltas

def _volta2(sid, endings):
    return f'<Volta id="{sid}"><endings>{endings}</endings></Volta>'


def _volta_numbers(part):
    return [(b.number, len(b.getSpannedElements()))
            for b in part.recurse().getElementsByClass(spanner.RepeatBracket)]


def _repeat_measures(open_vs, close_vs, rest=""):
    return ""


def test_v2_volta_closed_by_end_spanner_at_barline(tmp_path):
    # measure 2 = ending 1 (with the repeat), measure 3 = ending 2
    m = lambda c: _legacy_chord() * 4 + c
    xml = _doc2(
        _measure2(m(""))
        + _measure2(_volta2(1, "1") + m("") + "<endRepeat>2</endRepeat>", 1920)
        + _measure2(_end2(1) + _volta2(2, "2") + m(""), 3840)
        + _measure2(_end2(2) + m(""), 5760))
    part = _load(tmp_path, xml).parts[0]
    assert _volta_numbers(part) == [("1", 1), ("2", 1)]


def test_v2_volta_spanning_two_measures(tmp_path):
    m = _legacy_chord() * 4
    xml = _doc2(
        _measure2(m)
        + _measure2(_volta2(1, "1, 2") + m, 1920)
        + _measure2(m, 3840)
        + _measure2(_end2(1) + m, 5760))
    assert _volta_numbers(_load(tmp_path, xml).parts[0]) == [("1, 2", 2)]


def test_v2_volta_end_spanner_inside_last_measure(tmp_path):
    # the line ends at the end of the last measure (tick after the notes, not at a barline)
    m = _legacy_chord() * 4
    xml = _doc2(_measure2(m) + _measure2(_volta2(1, "1") + m + _end2(1), 1920))
    assert _volta_numbers(_load(tmp_path, xml).parts[0]) == [("1", 1)]


def test_v2_volta_without_end_spanner_covers_one_measure(tmp_path):
    m = _legacy_chord() * 4
    xml = _doc2(_measure2(m) + _measure2(_volta2(1, "1") + m, 1920) + _measure2(m, 3840))
    assert _volta_numbers(_load(tmp_path, xml).parts[0]) == [("1", 1)]


def test_v2_volta_without_numbers_is_dropped(tmp_path):
    m = _legacy_chord() * 4
    xml = _doc2(_measure2(_volta2(1, "") + m) + _measure2(_end2(1) + m, 1920))
    assert _volta_numbers(_load(tmp_path, xml).parts[0]) == []


def test_volta_repeated_on_every_staff_is_not_duplicated(tmp_path):
    m = _legacy_chord() * 4
    staves = "".join(
        f'<Staff id="{i}">' + _measure2(m) + _measure2(_volta2(1, "1") + m, 1920)
        + _measure2(_end2(1) + m, 3840) + "</Staff>" for i in (1, 2))
    xml = _doc(_part(1, 2), staves, "2.06")
    score = _load(tmp_path, xml)
    for p in score.parts:
        assert _volta_numbers(p) == [("1", 1)]


def test_v3_volta_repeated_on_every_staff_is_not_duplicated(tmp_path):
    staves = "".join(
        _staff3([_note() * 4, (_volta3("1") + _note() * 4), _note() * 4], i) for i in (1, 2))
    score = _load(tmp_path, _doc(_part(1, 2), staves))
    for p in score.parts:
        assert _volta_numbers(p) == [("1", 1)]


def test_v3_distinct_voltas_are_both_kept(tmp_path):
    staff = _staff3([_note() * 4, _volta3("1") + _note() * 4, _volta3("2") + _note() * 4])
    part = _load(tmp_path, _doc(_part(1), staff)).parts[0]
    assert _volta_numbers(part) == [("1", 1), ("2", 1)]


def test_v3_volta_ending_part_way_into_a_measure_covers_it(tmp_path):
    sp = ('<Spanner type="Volta"><Volta><endings>1</endings></Volta><next><location>'
          "<measures>1</measures><fractions>1/4</fractions></location></next></Spanner>")
    staff = _staff3([_note() * 4, sp + _note() * 4, _note() * 4, _note() * 4])
    part = _load(tmp_path, _doc(_part(1), staff)).parts[0]
    assert _volta_numbers(part) == [("1", 2)]


# ------------------------------------------------------------------ staff mapping

def test_staves_are_mapped_by_position_without_ids(tmp_path):
    # MuseScore 5 part staves carry no id: the n-th part <Staff> is the n-th score <Staff>.
    parts = _part(None, name="Soprano") + _part(None, name="Alto")
    staves = _staff3([_note(72) * 4], 1) + _staff3([_note(67) * 4], 2)
    score = _load(tmp_path, _doc(parts, staves, "5.00"))
    assert [p.partName for p in score.parts] == ["Soprano", "Alto"]
    assert [_midis(p) for p in score.parts] == [[72] * 4, [67] * 4]


def test_mapping_ignores_ids_even_when_they_disagree(tmp_path):
    parts = _part(7, name="Soprano") + _part(3, name="Alto")
    staves = _staff3([_note(72) * 4], 1) + _staff3([_note(67) * 4], 2)
    score = _load(tmp_path, _doc(parts, staves))
    assert [_midis(p) for p in score.parts] == [[72] * 4, [67] * 4]


def test_two_staff_part_becomes_two_partstaves(tmp_path):
    parts = _part(None, None, name="Piano") + _part(None, name="Bass")
    staves = (_staff3([_note(72) * 4], 1) + _staff3([_note(48) * 4], 2)
              + _staff3([_note(40) * 4], 3))
    score = _load(tmp_path, _doc(parts, staves, "5.00"))
    assert [p.id for p in score.parts] == ["P1-Staff1", "P1-Staff2", "P2"]
    assert [_midis(p) for p in score.parts] == [[72] * 4, [48] * 4, [40] * 4]


def test_skipped_percussion_and_linked_staves_keep_positions(tmp_path):
    perc = ('<Part><Staff><StaffType group="percussion"/></Staff><trackName>Drums</trackName>'
            "<Instrument><longName>Drums</longName></Instrument></Part>")
    linked = ('<Part><Staff><StaffType group="pitched"/><linkedTo>1</linkedTo></Staff>'
              "<Staff><StaffType group=\"pitched\"/></Staff><trackName>Guitar</trackName>"
              "<Instrument><longName>Guitar</longName></Instrument></Part>")
    parts = perc + linked + _part(None, name="Bass")
    staves = (_staff3([_note(36) * 4], 1) + _staff3([_note(50) * 4], 2)
              + _staff3([_note(52) * 4], 3) + _staff3([_note(40) * 4], 4))
    score = _load(tmp_path, _doc(parts, staves, "5.00"))
    assert [p.partName for p in score.parts] == ["Guitar", "Bass"]
    assert [_midis(p) for p in score.parts] == [[52] * 4, [40] * 4]


def test_fewer_score_staves_than_part_staves(tmp_path):
    # the second part points to a staff that is not there: it is skipped, nothing crashes
    parts = _part(1, name="Soprano") + _part(2, name="Alto")
    score = _load(tmp_path, _doc(parts, _staff3([_note(72) * 4], 1)))
    assert [p.partName for p in score.parts] == ["Soprano"]


def test_part_with_more_staves_than_score_has(tmp_path):
    parts = _part(1, 2, name="Piano")
    score = _load(tmp_path, _doc(parts, _staff3([_note(72) * 4], 1)))
    assert len(score.parts) == 1
    assert _midis(score.parts[0]) == [72] * 4


def test_more_score_staves_than_parts_are_ignored(tmp_path):
    staves = _staff3([_note(72) * 4], 1) + _staff3([_note(67) * 4], 2)
    score = _load(tmp_path, _doc(_part(1, name="Soprano"), staves))
    assert len(score.parts) == 1
    assert _midis(score.parts[0]) == [72] * 4


def test_staves_with_different_measure_counts(tmp_path):
    staves = (_staff3([_note() * 4, _volta3("1") + _note() * 4, _note() * 4, _note() * 4], 1)
              + _staff3([_note() * 4, _note() * 4], 2))
    score = _load(tmp_path, _doc(_part(1, 2), staves))
    counts = [len(p.getElementsByClass("Measure")) for p in score.parts]
    assert counts == [4, 2]
    # the volta (found on staff 1) must not break the short staff
    assert _volta_numbers(score.parts[0]) == [("1", 1)]


def test_staves_with_different_counts_and_end_repeat(tmp_path):
    rep = "<endRepeat>2</endRepeat>"
    staves = (_staff3([_note() * 4, (_note() * 4, rep), _note() * 4], 1)
              + _staff3([_note() * 4], 2))
    score = _load(tmp_path, _doc(_part(1, 2), staves))
    assert [len(p.getElementsByClass("Measure")) for p in score.parts] == [3, 1]


def test_ottava_on_short_staff_with_more_measures_elsewhere(tmp_path):
    staves = (_staff3([_note() * 4, _note() * 4, _note() * 4], 1)
              + _staff3([_ottava3("8va", 5) + _note() * 4], 2))
    score = _load(tmp_path, _doc(_part(1, 2), staves))
    assert _midis(score.parts[0]) == [60] * 12
    assert _midis(score.parts[1]) == [72] * 4


def test_nested_score_element_is_not_read_as_staves(tmp_path):
    # MuseScore 3 .mscx files with excerpts embed whole <Score> elements; only the direct
    # <Staff> children of the main score may be used.
    nested = ("<Score>" + _part(1, name="Excerpt") + _staff3([_note(30) * 4], 1) + "</Score>")
    xml = _doc(_part(1, name="Soprano"), _staff3([_note(72) * 4], 1) + nested)
    score = _load(tmp_path, xml)
    assert [p.partName for p in score.parts] == ["Soprano"]
    assert _midis(score.parts[0]) == [72] * 4


def test_v3_ottava_across_a_short_pickup_measure(tmp_path):
    xml = _doc(_part(1), "<Staff id=\"1\"><Measure len=\"1/4\"><voice>"
               + _ottava3("8va", 2) + _note() + "</voice></Measure><Measure><voice>"
               + _note() * 4 + "</voice></Measure><Measure><voice>" + _note() * 4
               + "</voice></Measure></Staff>")
    part = _load(tmp_path, xml).parts[0]
    assert _midis(part) == [72] + [72] * 4 + [60] * 4
