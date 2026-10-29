"""MuseScore (.mscx/.mscz) input: the native reader and the MuseScore program fallback."""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from music21 import stream

from choirsplit import musescore
from choirsplit.loaders import ScoreLoadError, load_score
from choirsplit.mscx_utils import find_musescore_cli
from choirsplit.score import extract_tracks

FIXTURES = Path(__file__).parent / "fixtures" / "musescore"
REALWORLD = Path(__file__).parent / "fixtures" / "realworld"
SATB = ["Soprano", "Alto", "Tenor", "Bass"]
EIGHT = [f"{v} {n}" for v in SATB for n in (1, 2)]


@pytest.fixture(autouse=True)
def no_musescore_program(monkeypatch):
    """Tests use the native reader unless they install a (fake) MuseScore themselves."""
    monkeypatch.delenv(musescore.MODE_ENV, raising=False)
    monkeypatch.setattr(musescore, "find_musescore_cli", lambda: None)


def tracks(name: str, folder: Path = FIXTURES, **kwargs):
    return extract_tracks(load_score(folder / name), **kwargs)


def lines(st):
    return {t.name: [(round(n.start, 4), n.pitch, round(n.duration, 4)) for n in t.notes] for t in st.tracks}


# --------------------------------------------------------------- hand-made choir scores


@pytest.mark.parametrize("name,voices,duration", [
    ("satb_open.mscx", SATB, 12.0),
    ("satb_open.mscz", SATB, 12.0),
    ("satb_closed.mscx", SATB, 12.0),
    ("ssaattbb_open.mscx", EIGHT, 8.0),
    ("ssaattbb_open.mscz", EIGHT, 8.0),
    ("ssaattbb_closed.mscx", EIGHT, 8.0),
    ("ssaattbb_closed.mscz", EIGHT, 8.0),
])
def test_choir_voices_and_length(name, voices, duration):
    st = tracks(name)
    assert [t.name for t in st.voices] == voices
    assert st.accompaniment == []
    assert st.duration == pytest.approx(duration)
    assert all(t.notes for t in st.voices)


@pytest.mark.parametrize("open_name,closed_name", [
    ("satb_open.mscx", "satb_closed.mscx"),
    ("ssaattbb_open.mscx", "ssaattbb_closed.mscx"),
])
def test_closed_score_matches_open_score(open_name, closed_name):
    assert lines(tracks(closed_name)) == lines(tracks(open_name))


def test_mscz_matches_mscx():
    assert lines(tracks("satb_open.mscz")) == lines(tracks("satb_open.mscx"))


def test_mscz_saved_by_musescore_4():
    """A real MuseScore 4.7 .mscz: container.xml lists style, thumbnail and settings files too."""
    with zipfile.ZipFile(FIXTURES / "satb_piano_ms47.mscz") as zf:
        assert "audiosettings.json" in zf.namelist()
    assert lines(tracks("satb_piano_ms47.mscz")) == lines(tracks("satb_piano.mscx"))


def test_mscz_ignores_excerpts(tmp_path):
    """Generated parts (Excerpts/...) must never be picked as the score."""
    f = tmp_path / "with_parts.mscz"
    full = (FIXTURES / "satb_open.mscx").read_bytes()
    part = (FIXTURES / "pickup.mscx").read_bytes()
    with zipfile.ZipFile(f, "w") as zf:
        zf.writestr("META-INF/container.xml",
                    '<container><rootfiles><rootfile full-path="Excerpts/Soprano/Soprano.mscx"/>'
                    '<rootfile full-path="score.mscx"/></rootfiles></container>')
        zf.writestr("Excerpts/Soprano/Soprano.mscx", part)
        zf.writestr("score.mscx", full)
    assert [t.name for t in tracks(f.name, tmp_path).voices] == SATB


def test_satb_pitches_and_tempo():
    st = tracks("satb_open.mscx")
    soprano = st.voices[0].notes
    # E5 E5 F5 G5 at 80 bpm: 0.75 s per quarter.
    assert [n.pitch for n in soprano[:4]] == [76, 76, 77, 79]
    assert [n.start for n in soprano[:4]] == pytest.approx([0, 0.75, 1.5, 2.25])
    assert st.voices[3].notes[2].pitch == 53  # F3 in the bass


def test_piano_is_accompaniment():
    st = tracks("satb_piano.mscx")
    assert [t.name for t in st.voices] == SATB
    assert [t.name for t in st.accompaniment] == ["Piano"]
    piano = st.accompaniment[0].notes
    assert min(n.pitch for n in piano) == 43 and max(n.pitch for n in piano) == 79  # both staves
    assert tracks("satb_piano.mscx", include_accompaniment=False).accompaniment == []


def test_piano_staves_are_partstaffs():
    score = load_score(FIXTURES / "satb_piano.mscx")
    piano = [p for p in score.parts if p.partName == "Piano"]
    assert [p.id for p in piano] == ["P5-Staff1", "P5-Staff2"]
    assert all(isinstance(p, stream.PartStaff) for p in piano)


def test_ties_tuplets_and_tempo_change():
    st = tracks("ties_tuplets_tempo.mscx")
    notes = [(round(n.start, 4), n.pitch, round(n.duration, 4)) for n in st.voices[0].notes]
    third = round(1 / 3, 4)
    assert notes == [
        (0.0, 72, 3.0),        # C5 half tied to a quarter
        (3.0, 74, 2.0),        # D5 tied across the barline
        (5.0, 76, third), (round(5 + 1 / 3, 4), 77, third), (round(5 + 2 / 3, 4), 79, third),
        (6.0, 81, 2.0),
        (8.0, 79, 0.75),       # 120 bpm from bar 3
        (8.75, 77, 0.25),
        (9.0, 76, 1.5),        # E5 half tied into the 3/4 bar
        (10.5, 74, 0.5), (11.0, 72, 0.5),
    ]
    assert st.duration == pytest.approx(11.5)


def test_tempo_word_after_the_opening_is_ignored(tmp_path):
    """'Allegro' in place of 'q = 120' at bar 3: the piece stays at its opening 60 bpm."""
    text = (FIXTURES / "ties_tuplets_tempo.mscx").read_text(encoding="utf-8")
    assert text.count("<text>♩ = 120</text>") == 1
    path = tmp_path / "words.mscx"
    path.write_text(text.replace("<text>♩ = 120</text>", "<text><b>Allegro</b></text>"), encoding="utf-8")
    st = tracks(path.name, folder=tmp_path)
    notes = [(round(n.start, 4), n.pitch, round(n.duration, 4)) for n in st.voices[0].notes]
    assert notes[6:8] == [(8.0, 79, 1.5), (9.5, 77, 0.5)]
    assert st.duration == pytest.approx(15.0)


def test_pickup_measure():
    score = load_score(FIXTURES / "pickup.mscx")
    measures = list(score.parts[0].getElementsByClass(stream.Measure))
    assert [m.offset for m in measures] == [0.0, 1.0, 4.0]
    st = extract_tracks(score)
    # 90 bpm: the downbeat of bar 2 comes one beat (0.667 s) in.
    assert [round(n.start, 3) for n in st.voices[0].notes[:2]] == [0.0, 0.667]
    assert st.duration == pytest.approx(7 * 60 / 90)


def test_repeat_is_played_twice():
    st = tracks("repeat.mscx")
    assert [n.pitch for n in st.voices[0].notes] == [72, 74, 72, 74, 76]
    assert st.duration == pytest.approx(10.0)


def test_da_capo_al_fine_with_repeat():
    # |: 1 2 (Fine) :| 3 4 (D.C. al Fine) -> 1 2 1 2 3 4 1 2, as MuseScore plays it.
    st = tracks("dc_al_fine.mscx")
    assert [n.pitch for n in st.voices[0].notes] == [72, 74, 72, 74, 76, 77, 72, 74]
    assert [n.pitch for n in st.voices[1].notes] == [69, 71, 69, 71, 72, 74, 69, 71]
    assert st.duration == pytest.approx(16.0)


def test_da_capo_al_fine_play_repeats_repeats_again():
    # Measured with MuseScore 4.7: "play repeats" replays the repeat bar after the jump and
    # only stops at Fine once it is done: 1 2 1 2 3 4 1 2 1 2.
    st = tracks("dc_al_fine_repeats.mscx")
    assert [n.pitch for n in st.voices[0].notes] == [72, 74, 72, 74, 76, 77, 72, 74, 72, 74]


@pytest.mark.parametrize("name,pitches", [
    # bars: C D | E (1.) :| F (2.) | G (D.C.); MuseScore 4.7's MIDI export, measured
    ("dc_voltas.mscx", [72, 74, 76, 72, 74, 77, 79, 72, 74, 77, 79]),  # after D.C.: last ending only
    ("dc_voltas_repeats.mscx", [72, 74, 76, 72, 74, 77, 79, 72, 74, 76, 72, 74, 77, 79]),  # all again
])
def test_da_capo_with_voltas(name, pitches):
    assert [n.pitch for n in tracks(name).voices[0].notes] == pitches


def test_dal_segno_al_coda():
    # 1 2(Segno) 3(To Coda) 4(D.S. al Coda) 5(Coda) -> 1 2 3 4 2 3 5.
    st = tracks("ds_al_coda.mscx")
    assert [n.pitch for n in st.voices[0].notes] == [72, 74, 76, 77, 74, 76, 79]
    assert [n.start for n in st.voices[0].notes] == pytest.approx([0, 2, 4, 6, 8, 10, 12])


def _order(n, start=(), end=None, voltas=(), markers=None, jumps=None):
    start_rep = [i in start for i in range(n)]
    end_rep = [(end or {}).get(i, 0) for i in range(n)]
    marks = [set((markers or {}).get(i, ())) for i in range(n)]
    return musescore._play_order(n, start_rep, end_rep, [musescore._Volta(*v) for v in voltas],
                                 marks, jumps or {})


def test_play_order_voltas():
    # |: 0 [1. 1 :|] [2. 2] 3
    assert _order(4, {0}, {1: 2}, [(1, 1, [1]), (2, 1, [2])]) == [0, 1, 0, 2, 3]


def test_play_order_jump_after_voltas_takes_last_ending():
    jump = {3: musescore._Jump("start", "end", "", False)}
    assert _order(4, {0}, {1: 2}, [(1, 1, [1]), (2, 1, [2])], jumps=jump) == [0, 1, 0, 2, 3, 0, 2, 3]
    jump = {3: musescore._Jump("start", "end", "", True)}  # "play repeats" ticked
    assert _order(4, {0}, {1: 2}, [(1, 1, [1]), (2, 1, [2])], jumps=jump) == [0, 1, 0, 2, 3] * 2


def test_play_order_bad_input_never_hangs():
    assert _order(3, jumps={2: musescore._Jump("segno", "fine", "", False)}) == [0, 1, 2]  # no segno
    assert len(_order(2, {0}, {1: 10 ** 9})) < 1000


def test_title_and_names():
    score = load_score(FIXTURES / "satb_open.mscx")
    assert score.metadata.title == "Open SATB Hymn"
    assert [p.partName for p in score.parts] == SATB
    assert tracks("satb_open.mscx").title == "Open SATB Hymn"


# ------------------------------------------------------------ MuseScore's own files


def test_closed_score_template_voice_names():
    score = load_score(FIXTURES / "04-SATB_Closed_Score.mscx")
    assert [p.partName for p in score.parts] == ["Soprano/Alto", "Tenor/Bass"]
    assert score.metadata.title == "SATB Closed Score"


def test_satb_piano_template():
    score = load_score(FIXTURES / "03-SATB_+_Piano.mscx")
    assert [p.partName for p in score.parts] == SATB + ["Piano", "Piano"]
    assert [p.id for p in score.parts][-2:] == ["P5-Staff1", "P5-Staff2"]


# Expected values agree with MuseScore 4.7's MusicXML export of the same files (the 5.00
# file is newer than MuseScore 4.7 can read; its values are from the native reader).
@pytest.mark.parametrize("name,count,end,duration", [
    ("testTie.mscx", 13, 6.25, 7.5),                        # 3.01 ties
    ("tuplets.mscx", 54, 11.875, 12.5),                     # 2.06 tuplets
    ("repeat_with_2_voltas.mscx", 28, 17.5, 17.5),          # 4.00 repeats + voltas
    ("pickup-measure-test.mscx", 28, 20.0, 20.0),           # 3.01 pickup
    ("tempo_changes_during_notes.mscx", 6, 93.6, 93.6),     # 4.20 tempo changes
    ("testOrnamentsTrillsOttava.mscx", 30, 37.5, 37.5),     # 3.01 ottava lines
    ("testTuplets_Example_8.5_MBC2015.mscx", 29, 4.375, 5.0),  # 5.00
])
def test_musescore_test_files(name, count, end, duration):
    st = tracks(name)
    notes = [n for t in st.tracks for n in t.notes]
    assert len(notes) == count
    assert max(t.end for t in st.tracks) == pytest.approx(end, abs=0.01)
    assert st.duration == pytest.approx(duration, abs=0.01)


def test_ottava_lines_shift_pitch():
    # Written B6 under a "15mb" line sounds B4.
    notes = tracks("testOrnamentsTrillsOttava.mscx").tracks[0].notes
    assert notes[0].pitch == 71


def test_several_voices_on_piano_staves():
    st = tracks("voices.mscx")
    assert [t.name for t in st.tracks] == ["Piano", "Trombone 1", "Trombone 2"]
    assert [len(t.notes) for t in st.tracks] == [28, 3, 17]


def test_musescore2_closed_hymn():
    """adeste.mscx (MuseScore 2.0.2): two "Voice" staves holding S+A and T+B as chords."""
    st = tracks("adeste.mscx", REALWORLD)
    assert [t.name for t in st.voices] == SATB
    for upper, lower in (st.voices[:2], st.voices[2:]):
        assert len(upper.notes) == len(lower.notes)
        assert all(u.pitch >= l.pitch for u, l in zip(upper.notes, lower.notes))


# ------------------------------------------------------------------- errors and modes


def test_musescore_1_files_are_refused(tmp_path):
    f = tmp_path / "old.mscx"
    f.write_text('<?xml version="1.0"?><museScore version="1.14"><Score/></museScore>')
    with pytest.raises(ScoreLoadError, match="MuseScore 1.14"):
        load_score(f)


@pytest.mark.parametrize("content", [b"not xml at all", b"<html></html>", b""])
def test_garbage_mscx(tmp_path, content):
    f = tmp_path / "bad.mscx"
    f.write_bytes(content)
    with pytest.raises(ScoreLoadError):
        load_score(f)


def test_garbage_mscz(tmp_path):
    f = tmp_path / "bad.mscz"
    f.write_bytes(b"PK\x03\x04 broken zip")
    with pytest.raises(ScoreLoadError):
        load_score(f)


def test_mscz_without_score(tmp_path):
    f = tmp_path / "empty.mscz"
    with zipfile.ZipFile(f, "w") as zf:
        zf.writestr("readme.txt", "nothing here")
    with pytest.raises(ScoreLoadError):
        load_score(f)


def test_percussion_only_score(tmp_path):
    f = tmp_path / "drums.mscx"
    f.write_text("""<museScore version="4.20"><Score><Division>480</Division>
      <Part><Staff id="1"><StaffType group="percussion"/></Staff><trackName>Drumset</trackName>
      <Instrument id="drumset"><instrumentId>drum.group.set</instrumentId></Instrument></Part>
      <Staff id="1"><Measure><voice><Rest><durationType>measure</durationType>
      <duration>4/4</duration></Rest></voice></Measure></Staff></Score></museScore>""")
    with pytest.raises(ScoreLoadError, match="no pitched staves"):
        load_score(f)


def test_unknown_mode():
    with pytest.raises(ScoreLoadError, match="unknown MuseScore mode"):
        musescore.load(FIXTURES / "satb_open.mscx", mode="magic")


def test_cli_mode_without_musescore(monkeypatch):
    monkeypatch.setenv(musescore.MODE_ENV, "cli")
    with pytest.raises(ScoreLoadError, match="MuseScore program not found"):
        load_score(FIXTURES / "satb_open.mscx")


def fake_musescore(tmp_path: Path, musicxml: Path | None, exit_code: int = 0) -> list[str]:
    """A stand-in MuseScore that 'converts' by copying a ready MusicXML file."""
    script = tmp_path / "fake_mscore.py"
    script.write_text(
        "import shutil, sys\n"
        "out = sys.argv[sys.argv.index('-o') + 1]\n"
        f"src = {str(musicxml) if musicxml else ''!r}\n"
        "if src:\n    shutil.copy(src, out)\n"
        "print('fake MuseScore says hi', file=sys.stderr)\n"
        f"sys.exit({exit_code})\n")
    return [sys.executable, str(script)]


@pytest.fixture
def satb_musicxml(tmp_path):
    out = tmp_path / "satb.musicxml"
    musescore.parse_native(FIXTURES / "satb_open.mscx").write("musicxml", fp=str(out))
    return out


def test_cli_mode_converts_with_musescore(monkeypatch, tmp_path, satb_musicxml):
    monkeypatch.setattr(musescore, "find_musescore_cli", lambda: fake_musescore(tmp_path, satb_musicxml))
    score = musescore.load(FIXTURES / "satb_open.mscx", mode="cli")
    assert [t.name for t in extract_tracks(score).voices] == SATB


def test_auto_mode_falls_back_to_musescore(monkeypatch, tmp_path, satb_musicxml):
    broken = tmp_path / "broken.mscx"
    broken.write_text("<museScore version='4.20'><Score><Part>")  # truncated
    monkeypatch.setattr(musescore, "find_musescore_cli", lambda: fake_musescore(tmp_path, satb_musicxml))
    assert [t.name for t in extract_tracks(load_score(broken)).voices] == SATB
    with pytest.raises(ScoreLoadError):
        musescore.load(broken, mode="native")


def test_failed_conversion_reports_musescore_output(monkeypatch, tmp_path):
    monkeypatch.setattr(musescore, "find_musescore_cli", lambda: fake_musescore(tmp_path, None, 3))
    with pytest.raises(ScoreLoadError, match="exit 3.*fake MuseScore says hi"):
        musescore.load(FIXTURES / "satb_open.mscx", mode="cli")


def test_auto_mode_reports_both_failures(monkeypatch, tmp_path):
    broken = tmp_path / "broken.mscx"
    broken.write_text("<museScore")
    monkeypatch.setattr(musescore, "find_musescore_cli", lambda: fake_musescore(tmp_path, None, 1))
    with pytest.raises(ScoreLoadError, match="not valid MuseScore XML.*conversion also failed"):
        load_score(broken)


# ---------------------------------------------------------------- command line + real MuseScore


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_cli_writes_eight_voice_tracks(tmp_path):
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "choirsplit", str(FIXTURES / "ssaattbb_closed.mscz"), "-o", str(out),
         "--mode", "solo", "--no-full-mix"],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    assert sorted(f.stem for f in out.glob("*.mp3")) == [f"Eight Voices - {i:02d} {v}" for i, v in enumerate(EIGHT, 1)]


@pytest.mark.musescore
def test_real_musescore_matches_native(monkeypatch):
    monkeypatch.undo()  # use the real program lookup
    if find_musescore_cli() is None:
        pytest.skip("MuseScore not installed (set CHOIRSPLIT_MUSESCORE)")
    for name in ("satb_closed.mscx", "ssaattbb_open.mscz", "ties_tuplets_tempo.mscx"):
        via_cli = extract_tracks(musescore.load(FIXTURES / name, mode="cli"))
        native = extract_tracks(musescore.load(FIXTURES / name, mode="native"))
        assert lines(via_cli) == lines(native), name
