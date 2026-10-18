"""OMR clean-up repairs on Audiveris-like MusicXML (choirsplit.omr).

The inputs are small hand-written MusicXML files that mimic what Audiveris exports
(default-x positions, ``<rest measure="yes">`` whole-bar rests, voice numbers), parsed
and assembled exactly like real OMR output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from choirsplit import omr
from choirsplit.loaders import load_score
from choirsplit.score import extract_tracks

DIVISIONS = 4  # per quarter
TYPES = {1: "16th", 2: "eighth", 4: "quarter", 8: "half", 16: "whole"}


def _note(spec: str, voice: int = 1, staff: int | None = None) -> str:
    """'C5:4@20' = C5 for 4 divisions at x=20; '.' after the length marks a printed dot
    ('C5:6.@20'); 'r:4@40' is a rest; 'R' is a whole-bar rest; '+' joins chord notes."""
    staff_xml = f"<staff>{staff}</staff>" if staff else ""
    if spec == "R":
        return (f'<note><rest measure="yes"/><duration>{{bar}}</duration>'
                f"<voice>{voice}</voice>{staff_xml}</note>")
    pitches, _, rest = spec.partition(":")
    length, _, x = rest.partition("@")
    dotted = length.endswith(".")
    divs = int(length.rstrip("."))
    base = divs * 2 // 3 if dotted else divs
    x_attr = f' default-x="{x}"' if x else ""
    out = []
    for k, p in enumerate(pitches.split("+")):
        chord = "<chord/>" if k else ""
        if p == "r":
            body = "<rest/>"
        else:
            step, octave = p[0], p[-1]
            alter = {"#": 1, "-": -1}.get(p[1] if len(p) == 3 else "", 0)
            body = (f"<pitch><step>{step}</step>"
                    + (f"<alter>{alter}</alter>" if alter else "")
                    + f"<octave>{octave}</octave></pitch>")
        out.append(f"<note{x_attr}>{chord}{body}<duration>{divs}</duration><voice>{voice}</voice>"
                   f"<type>{TYPES[base]}</type>{'<dot/>' if dotted else ''}{staff_xml}</note>")
    return "".join(out)


def _measure(number: int, voices: list[str], *, staff: int | None = None) -> str:
    """One staff's content: each entry of `voices` is a space-separated note list."""
    parts = []
    for v, spec in enumerate(voices, start=1):
        if spec is None:
            continue
        if parts:
            parts.append("<backup><duration>{bar}</duration></backup>")
        parts.extend(_note(tok, v, staff) for tok in spec.split())
    return "".join(parts)


def audiveris_xml(parts: list[dict], beats: int = 4, beat_type: int = 4) -> str:
    """parts: {'name', 'clef' ('G'/'F'), 'bars': [[voice specs...], ...]} or, for a
    grand staff, {'name', 'staves': [{'clef', 'bars'}, {'clef', 'bars'}]}."""
    bar = DIVISIONS * beats * 4 // beat_type
    plist, bodies = [], []
    for i, part in enumerate(parts, start=1):
        pid = f"P{i}"
        plist.append(f'<score-part id="{pid}"><part-name>{part["name"]}</part-name></score-part>')
        staves = part.get("staves") or [{"clef": part["clef"], "bars": part["bars"]}]
        n_bars = len(staves[0]["bars"])
        measures = []
        for b in range(n_bars):
            attrs = ""
            if b == 0:
                clefs = "".join(
                    f'<clef{f" number={chr(34)}{s + 1}{chr(34)}" if len(staves) > 1 else ""}>'
                    f'<sign>{st["clef"]}</sign><line>{2 if st["clef"] == "G" else 4}</line></clef>'
                    for s, st in enumerate(staves))
                attrs = (f"<attributes><divisions>{DIVISIONS}</divisions><key><fifths>0</fifths></key>"
                         f"<time><beats>{beats}</beats><beat-type>{beat_type}</beat-type></time>"
                         + (f"<staves>{len(staves)}</staves>" if len(staves) > 1 else "")
                         + f"{clefs}</attributes>")
            content = []
            for s, st in enumerate(staves):
                if s:
                    content.append("<backup><duration>{bar}</duration></backup>")
                content.append(_measure(b + 1, st["bars"][b], staff=s + 1 if len(staves) > 1 else None))
            measures.append(f'<measure number="{b + 1}">{attrs}{"".join(content)}</measure>')
        bodies.append(f'<part id="{pid}">{"".join(measures)}</part>')
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<score-partwise version="4.0">'
           f'<part-list>{"".join(plist)}</part-list>{"".join(bodies)}</score-partwise>')
    return xml.replace("{bar}", str(bar))


def assemble_xml(tmp_path: Path, xml: str):
    path = tmp_path / "omr.musicxml"
    path.write_text(xml, encoding="utf-8")
    return omr.assemble(omr.parse_outputs([path]))


def rhythm(part) -> list[list[tuple[str, float, float]]]:
    """Per bar: (pitch, offset in bar, quarters) of each note."""
    out = []
    for m in omr._measures(part):
        out.append([(n.pitches[0].nameWithOctave, float(n.getOffsetInHierarchy(m)),
                     float(n.duration.quarterLength)) for n in m.recurse().notes])
    return out


def by_name(score):
    return {p.partName: p for p in score.parts}


# --------------------------------------------------------------------------- stray notes


def satb_piano(bass_bars, tenor_bars=None):
    """SATB + piano in 3/4, three bars; voices rest except as given."""
    rest = [["R"]] * 3
    piano_rh = [["C6+E6:4@20 C6+E6:4@60 C6+E6:4@100"], ["A5+C6:8@20 G5:4@100"], ["C6:12.@20"]]
    piano_lh = [["C3:4@20 E3:4@60 G3:4@100"], ["F2:12.@20"], ["C3:12.@20"]]
    return [
        {"name": "Soprano", "clef": "G", "bars": rest},
        {"name": "Alto", "clef": "G", "bars": rest},
        {"name": "Tenor", "clef": "G", "bars": tenor_bars or rest},
        {"name": "Bass", "clef": "F", "bars": bass_bars},
        {"name": "Piano", "staves": [{"clef": "G", "bars": piano_rh}, {"clef": "F", "bars": piano_lh}]},
    ]


def test_stray_note_next_to_a_whole_bar_rest_is_dropped(tmp_path):
    """Trisha's Lullaby, bar 2: Audiveris printed the bass's whole-bar rest in voice 1 and
    read a piano tie/ledger line below the staff as an F2 in voice 2. The bass must not
    sing during the piano introduction; the piano keeps every note."""
    bass = [["R"], ["R", "F2:12.@14"], ["R"]]
    merged = assemble_xml(tmp_path, audiveris_xml(satb_piano(bass), beats=3))
    st = extract_tracks(merged)
    assert "Bass" not in [t.name for t in st.voices]
    assert not list(by_name(merged)["Bass"].recurse().notes)
    assert len(st.accompaniment[0].notes) == 6 + 3 + 1 + 3 + 1 + 1


def test_cue_notes_beside_a_whole_bar_rest_are_dropped_while_the_staff_sings(tmp_path):
    """The Lord of the Rings suite, pages 5 and 7: the resting bass staff prints the
    cello's part in small cue notes ("Vc.") under its whole-bar rests, between bars the
    bass sings. Nothing the choir sings lines up with them: the bar stays silent."""
    bass = [["C3:12.@20"], ["R", "F2:4@37 C3:4@71 F3:4@103"], ["G2:12.@20"]]
    merged = assemble_xml(tmp_path, audiveris_xml(satb_piano(bass), beats=3))
    assert [n[0] for bar in rhythm(by_name(merged)["Bass"]) for n in bar] == ["C3", "G2"]
    assert [(i.bar, i.voice, i.detail.split(" (")[0]) for i in merged.omr_issues] == [
        (2, "Bass", "dropped notes beside a whole-bar rest")]


def test_divisi_voice_singing_with_the_choir_beside_a_bar_rest_is_kept(tmp_path):
    """Tenor 1 sings while tenor 2 (same staff) shows a whole-bar rest: its notes start
    where the bass's do, so they are sung, not cue notes."""
    tenor = [["C4:12.@20"], ["R", "D4:4@20 E4:4@60 F4:4@100"], ["C4:12.@20"]]
    bass = [["C3:12.@20"], ["B2:4@20 C3:4@60 D3:4@100"], ["C3:12.@20"]]
    merged = assemble_xml(tmp_path, audiveris_xml(satb_piano(bass, tenor_bars=tenor), beats=3))
    sung = [n[0] for bar in rhythm(by_name(merged)["Tenor"]) for n in bar]
    assert len(sung) == 5 and sung[0] == sung[-1]


def test_two_voice_staff_with_a_resting_voice_keeps_its_notes(tmp_path):
    """S+A on one staff, alto has a whole-bar rest in bar 2: real polyphony, keep it."""
    sa = [["C5:4@20 D5:4@60 E5:4@100", "A4:4@20 B4:4@60 C5:4@100"],
          ["F5:4@20 G5:4@60 A5:4@100", "R"],
          ["C5:4@20 D5:4@60 E5:4@100", "A4:4@20 B4:4@60 C5:4@100"]]
    tb = [["C4:4@20 D4:4@60 E4:4@100", "A3:4@20 B3:4@60 C3:4@100"]] * 3
    parts = [{"name": "Women", "clef": "G", "bars": sa}, {"name": "Men", "clef": "F", "bars": tb}]
    st = extract_tracks(assemble_xml(tmp_path, audiveris_xml(parts, beats=3)))
    sop = next(t for t in st.voices if t.name == "Soprano")
    assert [int(n.pitch) for n in sop.notes][3:6] == [77, 79, 81]


# --------------------------------------------------------------------------- missed dots


def six_eight(alto_bars, *, soprano=None, tenor=None, bass=None):
    return [
        {"name": "Soprano", "clef": "G", "bars": soprano or [["F4:3.@24 G4:1@66 A4:2@88 G4:6.@122"]]},
        {"name": "Alto", "clef": "G", "bars": alto_bars},
        {"name": "Tenor", "clef": "G", "bars": tenor or [["A4:4@24 F4:2@88 G4:6.@122"]]},
        {"name": "Bass", "clef": "F", "bars": bass or [["F3:4@24 F3:2@88 E3:6.@122"]]},
    ]


def test_missed_dot_on_the_last_note_is_restored(tmp_path):
    """Trisha's Lullaby (6/8), alto bar 3: 'C q, C e, Bb q.' was read without the dot;
    the other three voices all hold a dotted quarter from the same beat."""
    merged = assemble_xml(tmp_path, audiveris_xml(six_eight([["C4:4@24 C4:2@88 B3:4@122"]]),
                                                  beats=6, beat_type=8))
    assert rhythm(by_name(merged)["Alto"]) == [[("C4", 0.0, 1.0), ("C4", 1.0, 0.5), ("B3", 1.5, 1.5)]]
    voices = {t.name: t for t in extract_tracks(merged).voices}
    end = [voices[n].notes[-1].start + voices[n].notes[-1].duration for n in ("Alto", "Soprano")]
    assert end[0] == pytest.approx(end[1])


def test_missed_dots_inside_the_bar_are_restored(tmp_path):
    """Alto 'Bb e. C s D e Bb q.' read as 'e s e q': both dots come back, so the D and
    the Bb sound with the soprano's F and G instead of early."""
    merged = assemble_xml(tmp_path, audiveris_xml(
        six_eight([["B3:2@19 C4:1@59 D4:2@85 B3:4@119"]],
                  soprano=[["D5:3.@19 E5:1@59 F5:2@85 G5:6.@119"]],
                  tenor=[["F4:4@19 A4:2@85 G4:6.@119"]], bass=[["B2:4@19 B2:2@85 E3:6.@119"]]),
        beats=6, beat_type=8))
    assert rhythm(by_name(merged)["Alto"]) == [
        [("B3", 0.0, 0.75), ("C4", 0.75, 0.25), ("D4", 1.0, 0.5), ("B3", 1.5, 1.5)]]


def test_missing_rest_is_not_mistaken_for_a_missing_dot(tmp_path):
    """Trisha's Lullaby, tenor bar 72: 'Bb half, quarter rest' with the rest unrecognised.
    The bar is short, but the piano moves on beat 3, so the half note stays a half."""
    tenor = [["R"], ["B3:8@20"], ["R"]]
    bass = [["R"], ["R"], ["R"]]
    merged = assemble_xml(tmp_path, audiveris_xml(satb_piano(bass, tenor_bars=tenor), beats=3))
    assert rhythm(by_name(merged)["Tenor"])[1] == [("B3", 0.0, 2.0)]


def test_correct_bars_and_unpositioned_notes_are_untouched(tmp_path):
    """Without x positions there is no evidence where the notes belong: no change."""
    merged = assemble_xml(tmp_path, audiveris_xml(six_eight([["C4:4 C4:2 B3:4"]]), beats=6, beat_type=8))
    assert rhythm(by_name(merged)["Alto"]) == [[("C4", 0.0, 1.0), ("C4", 1.0, 0.5), ("B3", 1.5, 1.0)]]
    for name in ("Soprano", "Tenor", "Bass"):
        assert sum(n[2] for n in rhythm(by_name(merged)[name])[0]) == 3.0


def test_ambiguous_alignment_is_left_alone(tmp_path):
    """Two different onsets printed at nearly the same x: don't guess."""
    merged = assemble_xml(tmp_path, audiveris_xml(
        six_eight([["C4:4@24 C4:2@88 B3:4@122"]], tenor=[["A4:4@24 F4:2@88 G4:2@120 G4:4@124"]]),
        beats=6, beat_type=8))
    assert rhythm(by_name(merged)["Alto"])[0][-1] == ("B3", 1.5, 1.0)


# --------------------------------------------------------------------------- missed rests


def missed_rest_parts(short):
    """4/4: soprano and alto print 'half, eighth rest, three eighths'; `short` is the
    third staff's bar."""
    return [
        {"name": "Soprano", "clef": "G", "bars": [["B4:8@16 r:2@89 B4:2@122 B4:2@161 B4:2@193"]]},
        {"name": "Alto", "clef": "G", "bars": [["D4:8@18 r:2@89 B3:2@122 D4:2@162 E4:2@194"]]},
        {"name": "Soprano 2", "clef": "G", "bars": [[short]]},
    ]


def test_missed_rest_in_mid_bar_is_restored(tmp_path):
    """The Lord of the Rings suite, bar 26: the eighth rest after the half note was not
    recognised, so the three eighths came in half a beat early. Their note heads line up
    with the other staves' eighths, which puts them back on beats 2.5, 3 and 3.5."""
    merged = assemble_xml(tmp_path, audiveris_xml(missed_rest_parts("F4:8@17 B4:2@122 B4:2@161 B4:2@194")))
    assert rhythm(by_name(merged)["Soprano 2"]) == [
        [("F4", 0.0, 2.0), ("B4", 2.5, 0.5), ("B4", 3.0, 0.5), ("B4", 3.5, 0.5)]]
    assert [(i.bar, i.voice, i.kind) for i in merged.omr_issues] == [(1, "Soprano 2", "fixed")]
    assert "moved 3 notes" in merged.omr_issues[0].detail


def test_notes_are_never_moved_earlier(tmp_path):
    """A note head lining up with an *earlier* onset contradicts the printed lengths: the
    bar is left alone (and reported by the check instead)."""
    parts = [
        {"name": "Soprano", "clef": "G", "bars": [["B4:4@16 C5:6.@60 D5:6.@122"]]},
        {"name": "Alto", "clef": "G", "bars": [["D4:4@16 E4:6.@60 F4:6.@122"]]},
        {"name": "Soprano 2", "clef": "G", "bars": [["F4:8@17 G4:2@60 A4:2@122"]]},
    ]
    merged = assemble_xml(tmp_path, audiveris_xml(parts))
    assert rhythm(by_name(merged)["Soprano 2"]) == [[("F4", 0.0, 2.0), ("G4", 2.0, 0.5), ("A4", 2.5, 0.5)]]
    assert merged.omr_issues == []


def test_retiming_needs_x_positions(tmp_path):
    merged = assemble_xml(tmp_path, audiveris_xml(missed_rest_parts("F4:8 B4:2 B4:2 B4:2")))
    assert rhythm(by_name(merged)["Soprano 2"])[0][1] == ("B4", 2.0, 0.5)


# --------------------------------------------------------------------------- piano notes in a voice


def test_piano_note_below_the_bass_is_dropped(tmp_path):
    """A piano note head under the bass staff read as a second bass voice (E1, far below
    any bass) sounding with the bass's own note, or alone in a bar the bass rests."""
    bass = [["C3:12.@20"], ["D3:12.@20", "E1:12.@20"], ["F1:4@20 r:8@60"]]
    merged = assemble_xml(tmp_path, audiveris_xml(satb_piano(bass), beats=3))
    assert [n[0] for bar in rhythm(by_name(merged)["Bass"]) for n in bar] == ["C3", "D3"]
    assert [(i.bar, i.voice) for i in merged.omr_issues] == [(2, "Bass"), (3, "Bass")]
    assert all("outside the voice's range" in i.detail for i in merged.omr_issues)


def test_low_bass_notes_that_are_sung_are_kept(tmp_path):
    """D2 is low but in range; G1 is below it but neither alone nor sounding with
    another bass note, so there is no sign it belongs to the piano."""
    bass = [["D2:12.@20"], ["C3:4@20 G1:8@60"], ["C3:12.@20"]]
    merged = assemble_xml(tmp_path, audiveris_xml(satb_piano(bass), beats=3))
    assert [n[0] for bar in rhythm(by_name(merged)["Bass"]) for n in bar] == ["D2", "C3", "G1", "C3"]


def test_low_notes_on_a_staff_not_next_to_the_piano_are_kept(tmp_path):
    tenor = [["C3:12.@20"], ["D3:12.@20", "C1:12.@20"], ["C3:12.@20"]]
    merged = assemble_xml(tmp_path, audiveris_xml(satb_piano([["R"]] * 3, tenor_bars=tenor), beats=3))
    assert "C1" in [n[0] for bar in rhythm(by_name(merged)["Tenor"]) for n in bar]


# --------------------------------------------------------------------------- repair log


def test_repairs_are_logged_with_voice_and_place(tmp_path):
    merged = assemble_xml(tmp_path, audiveris_xml(six_eight([["C4:4@24 C4:2@88 B3:4@122"]]),
                                                  beats=6, beat_type=8))
    tenor, issue = merged.omr_issues  # (the treble-clef tenor is also sung an octave lower)
    assert (tenor.bar, tenor.voice) == (None, "Tenor")
    assert (issue.bar, issue.voice, issue.kind, issue.detail) == (1, "Alto", "fixed", "restored 1 missed dot")
    assert (issue.page, issue.system) == (1, 1)
    assert merged.omr_locations == {1: (1, 1)}


def test_stray_note_and_tenor_octave_are_logged(tmp_path):
    tenor = [["C5:12.@20"], ["D5:12.@20"], ["E5:12.@20"]]
    bass = [["R"], ["R", "F2:12.@14"], ["R"]]
    merged = assemble_xml(tmp_path, audiveris_xml(satb_piano(bass, tenor_bars=tenor), beats=3))
    assert [(i.bar, i.voice, i.detail.split(" (")[0]) for i in merged.omr_issues] == [
        (None, "Tenor", "sung an octave lower"),
        (2, "Bass", "dropped stray notes beside a whole-bar rest"),
    ]


def test_grand_staff_stays_one_part_when_saved(tmp_path):
    merged = assemble_xml(tmp_path, audiveris_xml(satb_piano([["C3:12.@20"]] * 3), beats=3))
    out = tmp_path / "saved.musicxml"
    merged.write("musicxml", fp=str(out))
    st = extract_tracks(load_score(out))
    assert [t.name for t in st.accompaniment] == ["Piano"]
    assert [t.name for t in st.voices] == ["Bass"]
