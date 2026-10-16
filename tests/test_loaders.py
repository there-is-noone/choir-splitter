"""loaders.load_score: formats, Opus / bare-Part handling, MIDI input and error reporting."""

from __future__ import annotations

import shutil
import statistics

import pytest
from music21 import converter, corpus, instrument, note, stream

from choirsplit.loaders import ScoreLoadError, load_score, supported_suffixes
from choirsplit.score import extract_tracks
from conftest import build_part, build_score, name_tracks_for_midi, onsets_of, pitches_of, satb_parts, write_score

SATB = ["Soprano", "Alto", "Tenor", "Bass"]


# --------------------------------------------------------------------------- errors


def test_missing_file(tmp_path):
    with pytest.raises(ScoreLoadError, match="not found"):
        load_score(tmp_path / "nope.musicxml")


@pytest.mark.parametrize("suffix", [".musicxml", ".xml", ".mxl", ".mid", ".midi", ".abc", ".krn", ".txt"])
@pytest.mark.parametrize("content", [
    b"this is not music \x00\x01\x02 at all\n" * 5,
    b"",
    b'<?xml version="1.0"?><foo/>',
], ids=["garbage", "empty", "wrong-xml"])
def test_garbage_files_raise_score_load_error(tmp_path, suffix, content):
    path = tmp_path / f"bad{suffix}"
    path.write_bytes(content)
    with pytest.raises(ScoreLoadError):
        load_score(path)


def test_truncated_musicxml(tmp_path):
    good = write_score(build_score(*satb_parts()), tmp_path, "good")
    bad = tmp_path / "truncated.musicxml"
    bad.write_bytes(good.read_bytes()[:400])
    with pytest.raises(ScoreLoadError, match="truncated.musicxml"):
        load_score(bad)


def test_directory_is_not_a_score(tmp_path):
    d = tmp_path / "folder.musicxml"
    d.mkdir()
    with pytest.raises(ScoreLoadError):
        load_score(d)


def test_parse_error_is_chained(tmp_path, monkeypatch):
    path = tmp_path / "x.musicxml"
    path.write_text("whatever")

    def boom(*a, **k):
        raise ValueError("kaputt")

    monkeypatch.setattr(converter, "parse", boom)
    with pytest.raises(ScoreLoadError, match="kaputt") as info:
        load_score(path)
    assert isinstance(info.value.__cause__, ValueError)


def test_supported_suffixes():
    s = supported_suffixes()
    assert {".musicxml", ".mxl", ".xml", ".mid", ".midi", ".abc", ".mscz", ".mscx", ".pdf", ".png"} <= s
    assert all(x.startswith(".") and x == x.lower() for x in s)


# --------------------------------------------------------------------------- dispatch / stream types


def _fake_parse(monkeypatch, result):
    monkeypatch.setattr(converter, "parse", lambda *a, **k: result)


def test_bare_part_is_wrapped_in_a_score(tmp_path, monkeypatch):
    part = build_part("Soprano", ["C5:4"])
    _fake_parse(monkeypatch, part)
    (tmp_path / "p.musicxml").write_text("-")
    score = load_score(tmp_path / "p.musicxml")
    assert isinstance(score, stream.Score)
    assert list(score.parts) == [part]
    assert [t.name for t in extract_tracks(score).tracks] == ["Soprano"]


def test_non_score_stream_is_rejected(tmp_path, monkeypatch):
    s = stream.Stream([note.Note("C4")])
    _fake_parse(monkeypatch, s)
    (tmp_path / "p.musicxml").write_text("-")
    with pytest.raises(ScoreLoadError, match="did not parse into a score"):
        load_score(tmp_path / "p.musicxml")


def test_empty_opus_is_rejected(tmp_path, monkeypatch):
    _fake_parse(monkeypatch, stream.Opus())
    (tmp_path / "p.abc").write_text("-")
    with pytest.raises(ScoreLoadError, match="no scores"):
        load_score(tmp_path / "p.abc")


@pytest.mark.parametrize("suffix, module", [(".mscz", "musescore"), (".mscx", "musescore"),
                                            (".pdf", "omr"), (".PNG", "omr")])
def test_musescore_and_omr_files_are_dispatched(tmp_path, monkeypatch, suffix, module):
    import importlib

    mod = importlib.import_module(f"choirsplit.{module}")
    seen = []
    sentinel = build_score(*satb_parts())
    monkeypatch.setattr(mod, "load", lambda p, *a, **k: seen.append(p) or sentinel)
    path = tmp_path / f"in{suffix}"
    path.write_bytes(b"x")
    assert load_score(path) is sentinel
    assert seen == [path]


# --------------------------------------------------------------------------- real formats


def test_musicxml_str_path_and_uppercase_suffix(tmp_path):
    path = write_score(build_score(*satb_parts()), tmp_path, "satb")
    upper = tmp_path / "SATB.MUSICXML"
    shutil.copy(path, upper)
    for p in (str(path), upper):
        score = load_score(p)
        assert [x.partName for x in score.parts] == SATB


def test_compressed_mxl(tmp_path):
    path = write_score(build_score(*satb_parts(), bpm=60), tmp_path, "satb", fmt="mxl")
    st = extract_tracks(load_score(path))
    assert [t.name for t in st.tracks] == SATB
    assert st.duration == pytest.approx(8.0)


def test_corpus_humdrum_file(tmp_path):
    src = corpus.getWork("bach/bwv277")
    if isinstance(src, list):
        src = next(p for p in src if str(p).endswith(".krn"))
    dst = tmp_path / "bwv277.krn"
    shutil.copy(src, dst)
    st = extract_tracks(load_score(dst))
    assert len(st.voices) == 4
    means = [sum(pitches_of(t)) / len(t.notes) for t in st.voices]
    assert means == sorted(means, reverse=True)


ABC_TWO_TUNES = """X:1
T:First tune
M:4/4
L:1/4
K:C
C D E F|G4|]

X:2
T:Second tune
M:4/4
L:1/4
K:C
c B A G|F4|]
"""


def test_abc_opus_takes_the_first_score(tmp_path):
    path = tmp_path / "book.abc"
    path.write_text(ABC_TWO_TUNES)
    assert isinstance(converter.parse(str(path)), stream.Opus)
    score = load_score(path)
    assert isinstance(score, stream.Score)
    st = extract_tracks(score)
    assert st.title == "First tune"
    assert len(st.tracks) == 1
    assert pitches_of(st.tracks[0])[:4] == [60.0, 62.0, 64.0, 65.0]


ABC_ONE_TUNE_PER_VOICE = """X:1
T:Motet
T:Superius
M:4/4
L:1/4
K:C
c d e f|g4|]

X:2
T:Motet
T:Bassus
M:4/4
L:1/4
K:C
C, D, E, F,|G,4|]
"""


def test_abc_one_tune_per_voice_is_merged(tmp_path):
    path = tmp_path / "motet.abc"
    path.write_text(ABC_ONE_TUNE_PER_VOICE)
    st = extract_tracks(load_score(path))
    assert [t.name for t in st.voices] == ["Superius", "Bassus"]
    assert onsets_of(st.voices[0]) == onsets_of(st.voices[1])


def test_abc_single_tune(tmp_path):
    path = tmp_path / "tune.abc"
    path.write_text(ABC_TWO_TUNES.split("\n\n")[0] + "\n")
    score = load_score(path)
    assert isinstance(score, stream.Score) and len(score.parts) == 1


# --------------------------------------------------------------------------- MIDI


def test_midi_tracks_named_per_voice(tmp_path):
    score = name_tracks_for_midi(build_score(*satb_parts(), bpm=60))
    path = write_score(score, tmp_path, "satb", fmt="midi")
    st = extract_tracks(load_score(path))
    assert [t.name for t in st.tracks] == SATB
    assert all(t.kind == "voice" for t in st.tracks)
    assert [t.notes[0].pitch for t in st.tracks] == [72.0, 64.0, 55.0, 48.0]
    for t in st.tracks:
        assert onsets_of(t) == pytest.approx([float(i) for i in range(8)], abs=0.01)
    assert st.duration == pytest.approx(8.0, abs=0.05)


def test_midi_voices_with_piano_program(tmp_path):
    """Many MIDI files play every voice with program 0 (piano); the track names decide."""
    parts = satb_parts()
    for p in parts:
        inst = instrument.Piano()
        inst.instrumentName = p.partName
        p.insert(0, inst)
    path = write_score(build_score(*parts, bpm=60), tmp_path, "pianos", fmt="midi")
    st = extract_tracks(load_score(path))
    assert [(t.name, t.kind) for t in st.tracks] == [(n, "voice") for n in SATB]


def test_midi_tempo_change(tmp_path):
    from music21 import tempo

    p = build_part("Soprano", ["C5:4", "D5:4"])
    p.getElementsByClass(stream.Measure)[1].insert(0, tempo.MetronomeMark(number=120))
    path = write_score(name_tracks_for_midi(build_score(p, bpm=60)), tmp_path, "t", fmt="midi")
    st = extract_tracks(load_score(path))
    assert onsets_of(st.tracks[0]) == pytest.approx([0.0, 4.0], abs=0.01)
    assert st.tracks[0].notes[1].duration == pytest.approx(2.0, abs=0.02)


def test_midi_with_accompaniment_track(tmp_path):
    parts = satb_parts()
    pno = build_part("Piano", ["C4+E4+G4:4"] * 2, instrument=instrument.Piano())
    score = name_tracks_for_midi(build_score(*parts, pno, bpm=60))
    path = write_score(score, tmp_path, "acc", fmt="midi")
    st = extract_tracks(load_score(path))
    assert [t.name for t in st.voices] == SATB
    assert [t.name for t in st.accompaniment] == ["Piano"]
    assert extract_tracks(load_score(path), include_accompaniment=False).accompaniment == []


def test_midi_chorale_matches_musicxml(tmp_path):
    xml = write_score(corpus.parse("bach/bwv66.6"), tmp_path, "c")
    mid = write_score(corpus.parse("bach/bwv66.6"), tmp_path, "c", fmt="midi")
    a, b = extract_tracks(load_score(xml)), extract_tracks(load_score(mid))
    assert [t.name for t in a.tracks] == [t.name for t in b.tracks] == SATB
    for x, y in zip(a.tracks, b.tracks):
        assert pitches_of(x) == pitches_of(y)


@pytest.mark.parametrize("name", ["Soprano", "Tenor", "B", "Women"])
@pytest.mark.parametrize("fmt", ["memory", "midi"])
def test_legato_overlaps_in_one_line_do_not_split_it(tmp_path, name, fmt):
    """Performance MIDI often holds each note into the next; that is still one line."""
    p = stream.Part()
    p.partName = name
    for i, n in enumerate(["C5", "D5", "E5", "D5", "C5", "B4", "C5", "E5"]):
        p.insert(i, note.Note(n, quarterLength=1.25))
    score = name_tracks_for_midi(build_score(p, bpm=60))
    if fmt == "midi":
        score = load_score(write_score(score, tmp_path, "legato", fmt="midi"))
    st = extract_tracks(score)
    assert [(t.name, len(t.notes)) for t in st.tracks] == [(name, 8)]


@pytest.mark.parametrize("women, men, counts", [
    (("E5 F5 G5:2", "C5 D5 E5:2"), ("G3 A3 B3:2", "C3 D3 E3:2"), [3, 3, 3, 3]),   # same rhythm
    (("E5:2 F5:2", "C5 D5 E5 F5"), ("G3:4", "C3 D3 E3 F3"), [2, 4, 1, 4]),        # own rhythms
    ("E5+C5 F5+D5 G5+E5:2", "G3+C3 A3+D3 B3+E3:2", [3, 3, 3, 3]),                 # chords
], ids=["same-rhythm", "own-rhythm", "chords"])
def test_midi_closed_score_tracks_are_split(tmp_path, women, men, counts):
    """A MIDI 'Women' track carries S+A and 'Men' carries T+B (common in hymn MIDI files)."""
    score = name_tracks_for_midi(build_score(build_part("Women", [women] * 2),
                                             build_part("Men", [men] * 2), bpm=60))
    st = extract_tracks(load_score(write_score(score, tmp_path, "closed", fmt="midi")))
    assert [t.name for t in st.tracks] == SATB
    assert [len(t.notes) for t in st.tracks] == [2 * c for c in counts]
    s, a, t, b = st.tracks
    assert statistics.mean(pitches_of(s)) > statistics.mean(pitches_of(a))
    assert min(pitches_of(t)) > max(pitches_of(b))
    for tr in st.tracks:
        ns = sorted(tr.notes, key=lambda e: e.start)
        assert all(y.start >= x.start + x.duration - 1e-3 for x, y in zip(ns, ns[1:])), tr.name
