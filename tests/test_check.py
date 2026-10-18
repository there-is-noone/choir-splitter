"""Suspect-bar report (choirsplit.check) and the --check / --export-musicxml workflow."""

from __future__ import annotations

import pytest
from conftest import build_part, build_piano, build_score, write_score
from music21 import corpus
from test_omr_repairs import audiveris_xml

from choirsplit.check import Issue, check_score, format_issues, voice_range, voice_types
from choirsplit.cli import main
from choirsplit.pipeline import Options, load_tracks


def kinds(issues):
    return [(i.bar, i.voice, i.detail.split(" (")[0]) for i in issues]


@pytest.mark.parametrize("name, expected", [
    ("Soprano 1", ["soprano"]), ("S A", ["soprano", "alto"]), ("Contralto", ["alto"]),
    ("Baixo", ["bass"]), ("Men", ["tenor", "bass"]), ("T.", ["tenor"]), ("Piano", []),
    ("Voice", []), ("", []),
])
def test_voice_types_from_staff_names(name, expected):
    assert voice_types(name) == expected


def test_voice_range_of_a_two_voice_staff_spans_both():
    assert voice_range("S A") == (voice_range("Alto")[0], voice_range("Soprano")[1])
    assert voice_range("Voice") is None


def test_short_line_is_reported():
    """The alto lost a dot: its notes stop half a beat before the others."""
    score = build_score(
        build_part("Soprano", ["C5:1 D5:1 E5:2", "F5:4"]),
        build_part("Alto", ["A4:1 B4:1 C5:1.5", "C5:4"]),
        build_part("Tenor", ["E4:1 F4:1 G4:2", "A4:4"]),
    )
    assert kinds(check_score(score)) == [(1, "Alto", "notes fill 3.5 of 4 beats")]


def test_voice_with_a_later_entry_or_a_rest_is_not_short():
    score = build_score(
        build_part("Soprano", ["C5:2 r:1 E5:1"]),
        build_part("Alto", [["r:2 C5:2"]]),
        build_part("Tenor", ["E4:4"]),
    )
    assert check_score(score) == []


def test_note_outside_the_voice_range_is_reported():
    score = build_score(build_part("Bass", ["C3 D3 E5 C3"]), build_part("Tenor", ["C4 D4 E4 C4"]))
    found = kinds(check_score(score))
    assert (1, "Bass", "E5 is outside the usual range") in found
    assert {voice for _, voice, _ in found} == {"Bass"}  # (it also leaps there and back)


def test_rare_big_leap_is_reported_but_leaping_lines_are_not():
    steps = ["C5 D5 E5 F5"] * 6
    odd = build_part("Soprano", steps[:3] + ["C5 D5 C4 F5"] + steps[3:])  # D5 -> C4 -> F5
    assert kinds(check_score(build_score(odd))) == [(4, "Soprano", "leap of 14 semitones"),
                                                     (4, "Soprano", "leap of 17 semitones")]
    leaper = build_part("Soprano", ["C4 C6 C4 C6"] * 6)
    assert check_score(build_score(leaper)) == []


def test_bass_octave_leaps_at_cadences_are_normal():
    bass = build_part("Bass", ["G3 F3 E3 D3", "G3 G2:3", "C3 D3 E3 F3", "G3 G2:3"])
    assert check_score(build_score(bass)) == []


def test_out_of_step_voice_in_homophony_is_reported():
    """Three voices move together; in bar 2 the alto's second note is a misread length."""
    together = "C5:1 D5:1 E5:1 F5:1"
    alto = ["A4:1 B4:1 C5:1 D5:1", "A4:1.5 B4:0.5 C5:1 D5:1", "A4:1 B4:1 C5:1 D5:1"]
    score = build_score(
        build_part("Soprano", [together] * 3), build_part("Alto", alto),
        build_part("Tenor", ["E4:1 F4:1 G4:1 A4:1"] * 3), build_part("Bass", ["C3:1 D3:1 E3:1 F3:1"] * 3),
    )
    assert kinds(check_score(score)) == [(2, "Alto", "rhythm differs from the other voices")]


def test_independent_counterpoint_is_not_out_of_step():
    score = build_score(
        build_part("Soprano", ["C5:1 D5:1 E5:1 F5:1"] * 3),
        build_part("Alto", ["A4:1.5 B4:0.5 C5:1 D5:1", "A4:1 B4:1 C5:1 D5:1", "A4:1.5 B4:0.5 C5:1 D5:1"]),
        build_part("Tenor", ["E4:1 F4:1 G4:1 A4:1"] * 3), build_part("Bass", ["C3:1 D3:1 E3:1 F3:1"] * 3),
    )
    assert check_score(score) == []


def test_accompaniment_is_not_checked():
    score = build_score(build_part("Soprano", ["C5:4"]), build_piano(["C2 C7:3"], ["C1:1"]))
    assert check_score(score) == []


@pytest.mark.slow
@pytest.mark.parametrize("work", ["bach/bwv66.6", "bach/bwv10.7", "bach/bwv117.4", "bach/bwv153.9",
                                  "bach/bwv157.5", "bach/bwv269", "bach/bwv277"])
def test_clean_chorales_have_no_suspects(work):
    assert check_score(corpus.parse(work)) == []


def test_issue_locations_and_formatting():
    issues = [Issue(37, "Alto", "suspect", "notes fill 2.5 of 3 beats", 3, 2),
              Issue(None, "Tenor", "fixed", "sung an octave lower")]
    assert format_issues(issues) == [
        "bar 37 (p3, s2)  Alto   suspect: notes fill 2.5 of 3 beats",
        "all bars         Tenor  fixed: sung an octave lower",
    ]
    locs = {1: (4, 1)}
    score = build_score(build_part("Soprano", ["C5:1 D5:1"]), build_part("Alto", ["A4:2 B4:2"]),
                        build_part("Tenor", ["E4:2 F4:2"]))
    assert [i.location() for i in check_score(score, locs)] == ["bar 1 (p4, s1)"]


# --------------------------------------------------------------------------- workflow


@pytest.fixture
def short_alto(tmp_path):
    """A MusicXML file whose alto bar 1 is half a beat short (music21 itself would fill
    the gap with a rest when writing, so the file is written by hand)."""
    xml = audiveris_xml([
        {"name": "Soprano", "clef": "G", "bars": [["C5:4 D5:4 E5:8"], ["F5:16"]]},
        {"name": "Alto", "clef": "G", "bars": [["A4:4 B4:4 C5:6."], ["C5:16"]]},
        {"name": "Tenor", "clef": "G", "bars": [["E4:4 F4:4 G4:8"], ["A4:16"]]},
        {"name": "Piano", "staves": [{"clef": "G", "bars": [["C5:16"], ["C5:16"]]},
                                     {"clef": "F", "bars": [["C3:16"], ["C3:16"]]}]},
    ])
    path = tmp_path / "short_alto.musicxml"
    path.write_text(xml, encoding="utf-8")
    return path


def test_check_lists_suspect_bars(short_alto, capsys):
    assert main([str(short_alto), "--check"]) == 0
    out = capsys.readouterr().out
    assert "Please check (1):" in out
    assert "bar 1  Alto  suspect: notes fill 3.5 of 4 beats" in out
    assert "--export-musicxml" not in out  # the hint is for recognised PDFs only


def test_check_on_a_clean_score(tmp_path, capsys):
    path = write_score(build_score(build_part("Soprano", ["C5:4"]), build_part("Alto", ["A4:4"])),
                       tmp_path, "clean")
    assert main([str(path), "--check", "--list"]) == 0
    out = capsys.readouterr().out
    assert "No problems found." in out and "1. Soprano [voice]" in out


def test_export_musicxml_round_trips(short_alto, tmp_path, capsys):
    out = tmp_path / "fix" / "cleaned.musicxml"
    assert main([str(short_alto), "--list", "--export-musicxml", str(out)]) == 0
    assert f"saved {out}" in capsys.readouterr().out
    first = load_tracks(short_alto)
    again = load_tracks(out)
    assert [(t.name, t.kind) for t in again.tracks] == [(t.name, t.kind) for t in first.tracks]
    for a, b in zip(first.tracks, again.tracks):
        assert [(n.start, n.duration, n.pitch) for n in a.notes] == pytest.approx(
            [(n.start, n.duration, n.pitch) for n in b.notes])


def test_loaded_tracks_carry_the_issues(short_alto):
    st = load_tracks(short_alto)
    assert not st.recognised
    assert [(i.bar, i.voice, i.kind) for i in st.issues] == [(1, "Alto", "suspect")]


@pytest.mark.parametrize("dpi", [99, 1201, 0, -300])
def test_omr_dpi_is_validated(dpi):
    with pytest.raises(ValueError, match="dpi"):
        Options(omr_dpi=dpi).validate()
    Options(omr_dpi=400).validate()
