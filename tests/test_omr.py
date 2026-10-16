"""Tests for PDF/image input via optical music recognition (choirsplit.omr).

The unit tests use fake engines (tiny Python scripts) and synthetic music21 scores, so
they are fast and always run. Tests marked ``omr`` run the real engine (Audiveris) on
the engraved fixtures in tests/fixtures/pdf/ and are skipped when none is installed
(see scripts/install_omr.sh).
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from music21 import clef, converter, instrument, layout, note, stream, text

from choirsplit import omr
from choirsplit.loaders import ScoreLoadError, load_score
from choirsplit.score import extract_tracks, is_accompaniment

FIXTURES = Path(__file__).parent / "fixtures" / "pdf"
SATB_PDF = FIXTURES / "bwv66_6_satb.pdf"
SATB_XML = FIXTURES / "bwv66_6_satb.musicxml"
OCTO_PDF = FIXTURES / "agnus_ssaattbb.pdf"
OCTO_XML = FIXTURES / "agnus_ssaattbb.musicxml"
HYMN_PDF = FIXTURES / "old100_mutopia.pdf"


# --------------------------------------------------------------------------- helpers


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No engine anywhere: empty PATH, empty CHOIRSPLIT_HOME, no system install, no cache."""
    for var in ("CHOIRSPLIT_OMR_CMD", "CHOIRSPLIT_AUDIVERIS", "CHOIRSPLIT_OMR_TIMEOUT", "TESSDATA_PREFIX"):
        monkeypatch.delenv(var, raising=False)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.setenv("CHOIRSPLIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHOIRSPLIT_OMR_CACHE", "0")
    monkeypatch.setattr(omr, "SYSTEM_LOCATIONS", [])
    return tmp_path


def make_exe(path: Path, body: str) -> Path:
    """Write an executable Python script (the fake OMR engine)."""
    if sys.platform == "win32":
        pytest.skip("fake engines are #! scripts, which Windows can't run directly")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def fake_audiveris(path: Path, outputs: dict[str, Path] | None = None, *, exit_code: int = 0,
                   log: Path | None = None, sleep: float = 0.0) -> Path:
    """A fake Audiveris launcher: parses `-batch -export -output DIR -- FILE` and copies
    the given MusicXML files into DIR under the given names ({stem} = input stem)."""
    outputs = {k: str(v) for k, v in (outputs or {}).items()}
    return make_exe(path, f"""
        import json, os, shutil, sys, time
        args = sys.argv[1:]
        if {str(log) if log else None!r}:
            with open({str(log) if log else ''!r}, "a") as fh:
                fh.write(json.dumps({{"args": args, "tess": os.environ.get("TESSDATA_PREFIX")}}) + "\\n")
        time.sleep({sleep})
        assert args[:2] == ["-batch", "-export"], args
        out = args[args.index("-output") + 1]
        src = args[args.index("--") + 1]
        stem = os.path.splitext(os.path.basename(src))[0]
        print("INFO  fake audiveris processing", src)
        print("WARN  something odd on sheet 1")
        for name, xml in {outputs!r}.items():
            shutil.copy(xml, os.path.join(out, name.format(stem=stem)))
        sys.exit({exit_code})
    """)


def write_xml(score: stream.Score, path: Path) -> Path:
    score.write("musicxml", fp=str(path))
    return path


def line(name, pitches, *, ts_measures=None, hidden_from=None, part_id=None, cls=stream.Part,
         clef_obj=None, systems=(0,)):
    """A part with one measure per entry of `pitches` (each a list of (pitch, quarters)).

    `systems` lists the measure indices that start a new system; `hidden_from` is the set
    of system-start indices at which this staff is hidden (Audiveris print-object="no").
    """
    p = cls()
    if part_id:
        p.id = part_id
    p.partName = name
    for i, spec in enumerate(pitches):
        m = stream.Measure(number=i + 1)
        if i == 0:
            m.insert(0, clef_obj or clef.TrebleClef())
            from music21 import meter
            m.insert(0, meter.TimeSignature("4/4"))
        if i in systems:
            m.insert(0, layout.SystemLayout(isNew=i > 0))
            if hidden_from and i in hidden_from:
                m.insert(0, layout.StaffLayout(hidden=True))
        for pitch, ql in spec:
            m.append(note.Rest(quarterLength=ql) if pitch == "r" else note.Note(pitch, quarterLength=ql))
        p.append(m)
    return p


def whole(pitch):
    return [(pitch, 4.0)]


def names_and_pitches(score):
    return [(p.partName, [n.pitch.nameWithOctave for n in p.flatten().notes]) for p in score.parts]


# --------------------------------------------------------------------------- engine discovery


def test_no_engine_gives_install_instructions(clean_env):
    assert omr.find_engine() is None
    pdf = clean_env / "song.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ScoreLoadError) as exc:
        load_score(pdf)
    msg = str(exc.value)
    assert "song.pdf" in msg
    assert "scripts/install_omr.sh" in msg
    assert "CHOIRSPLIT_AUDIVERIS" in msg and "CHOIRSPLIT_OMR_CMD" in msg
    assert "MusicXML" in msg


def test_engine_on_path(clean_env, monkeypatch):
    exe = fake_audiveris(clean_env / "empty-bin" / "Audiveris")
    eng = omr.find_engine()
    assert eng.name == "audiveris" and eng.argv == [str(exe)]


def test_engine_in_choirsplit_home_with_tessdata(clean_env):
    home = clean_env / "home"
    exe = fake_audiveris(home / "audiveris" / "bin" / "Audiveris")
    (home / "audiveris" / "lib" / "app").mkdir(parents=True)
    (home / "audiveris" / "lib" / "app" / "Audiveris.cfg").write_text(
        "[JavaOptions]\njava-options=-Djpackage.app-version=5.11.0\n")
    (home / "tessdata").mkdir()
    eng = omr.find_engine()
    assert eng.argv == [str(exe)]
    assert eng.version == "5.11.0"
    assert eng.env == {"TESSDATA_PREFIX": str(home / "tessdata")}


def test_existing_tessdata_prefix_is_respected(clean_env, monkeypatch):
    home = clean_env / "home"
    fake_audiveris(home / "audiveris" / "bin" / "Audiveris")
    (home / "tessdata").mkdir()
    monkeypatch.setenv("TESSDATA_PREFIX", "/somewhere/else")
    assert omr.find_engine().env == {}


def test_system_location_is_last_resort(clean_env, monkeypatch):
    exe = fake_audiveris(clean_env / "opt-audiveris" / "bin" / "Audiveris")
    monkeypatch.setattr(omr, "SYSTEM_LOCATIONS", [clean_env / "opt-audiveris"])
    assert omr.find_engine().argv == [str(exe)]
    on_path = fake_audiveris(clean_env / "empty-bin" / "audiveris")
    # PATH wins. Case-insensitive file systems (macOS) report it as "Audiveris".
    [found] = omr.find_engine().argv
    assert Path(found).parent == on_path.parent and Path(found).name.lower() == "audiveris"


def test_env_audiveris_beats_path(clean_env, monkeypatch):
    fake_audiveris(clean_env / "empty-bin" / "Audiveris")
    explicit = fake_audiveris(clean_env / "custom" / "bin" / "Audiveris")
    monkeypatch.setenv("CHOIRSPLIT_AUDIVERIS", str(clean_env / "custom"))  # install dir works too
    assert omr.find_engine().argv == [str(explicit)]
    monkeypatch.setenv("CHOIRSPLIT_AUDIVERIS", str(explicit))
    assert omr.find_engine().argv == [str(explicit)]


def test_env_audiveris_invalid_is_an_error(clean_env, monkeypatch):
    monkeypatch.setenv("CHOIRSPLIT_AUDIVERIS", str(clean_env / "nope"))
    with pytest.raises(ScoreLoadError, match="CHOIRSPLIT_AUDIVERIS"):
        omr.find_engine()


def test_custom_command_wins(clean_env, monkeypatch):
    fake_audiveris(clean_env / "empty-bin" / "Audiveris")
    monkeypatch.setenv("CHOIRSPLIT_OMR_CMD", "oemer -o {outdir} {image}")
    eng = omr.find_engine()
    assert eng.name == "custom" and eng.per_page
    assert eng.argv == ["oemer", "-o", "{outdir}", "{image}"]
    monkeypatch.setenv("CHOIRSPLIT_OMR_CMD", "my-omr --in {input} --out {outdir}")
    assert not omr.find_engine().per_page
    monkeypatch.setenv("CHOIRSPLIT_OMR_CMD", "my-omr")
    with pytest.raises(ScoreLoadError, match=r"\{input\}"):
        omr.find_engine()


# --------------------------------------------------------------------------- running engines


@pytest.fixture
def satb_xml(tmp_path, make_part, make_score):
    s = make_score(*[make_part(n, [f"{p} {p} {p} {p}"] * 2) for n, p in
                     zip(("Soprano", "Alto", "Tenor", "Bass"), ("C5", "E4", "G3", "C3"))])
    return write_xml(s, tmp_path / "satb.musicxml")


def test_load_pdf_through_fake_audiveris(clean_env, satb_xml):
    log = clean_env / "calls.jsonl"
    home = clean_env / "home"
    fake_audiveris(home / "audiveris" / "bin" / "Audiveris", {"{stem}.musicxml": satb_xml}, log=log)
    (home / "tessdata").mkdir()
    pdf = clean_env / "Hymn.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    score = load_score(pdf)
    assert [p.partName for p in score.parts] == ["Soprano", "Alto", "Tenor", "Bass"]
    call = json.loads(log.read_text().splitlines()[0])
    assert call["args"][-1] == str(pdf) and call["args"][-2] == "--"
    assert call["tess"] == str(home / "tessdata")
    st = extract_tracks(score)
    assert [t.name for t in st.voices] == ["Soprano", "Alto", "Tenor", "Bass"]


def test_image_input_is_passed_straight_through(clean_env, satb_xml):
    log = clean_env / "calls.jsonl"
    fake_audiveris(clean_env / "empty-bin" / "Audiveris", {"{stem}.musicxml": satb_xml}, log=log)
    png = clean_env / "scan.png"
    png.write_bytes(b"\x89PNG\r\n")
    assert len(load_score(png).parts) == 4
    assert json.loads(log.read_text())["args"][-1] == str(png)


def test_movements_are_merged_in_natural_order(clean_env, tmp_path):
    xmls = {}
    for i, pitch in [(1, "C4"), (2, "D4"), (10, "E4")]:
        s = stream.Score([line("Soprano", [whole(pitch)])])
        xmls[f"{{stem}}.mvt{i}.mxl"] = write_xml(s, tmp_path / f"m{i}.musicxml")
    # .mxl files must be zipped MusicXML; write real ones with music21.
    for name, xml in list(xmls.items()):
        mxl = tmp_path / name.replace("{stem}", "x")
        converter.parse(str(xml)).write("mxl", fp=str(mxl))
        xmls[name] = mxl
    fake_audiveris(clean_env / "empty-bin" / "Audiveris", xmls)
    pdf = clean_env / "multi.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    score = load_score(pdf)
    assert names_and_pitches(score) == [("Soprano", ["C4", "D4", "E4"])]
    offsets = [m.offset for m in score.parts[0].getElementsByClass(stream.Measure)]
    assert offsets == [0.0, 4.0, 8.0]


def test_natural_sort_of_outputs(tmp_path):
    for name in ["b.mvt10.mxl", "b.mvt2.mxl", "b.mvt1.mxl", "b.omr", "b.log"]:
        (tmp_path / name).write_text("x")
    assert [p.name for p in omr.collect_outputs(tmp_path)] == ["b.mvt1.mxl", "b.mvt2.mxl", "b.mvt10.mxl"]


def test_engine_failure_reports_log_tail(clean_env):
    fake_audiveris(clean_env / "empty-bin" / "Audiveris", exit_code=3)
    pdf = clean_env / "bad.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ScoreLoadError) as exc:
        load_score(pdf)
    assert "exit code 3" in str(exc.value)
    assert "something odd on sheet 1" in str(exc.value)


def test_engine_success_without_output_is_an_error(clean_env):
    fake_audiveris(clean_env / "empty-bin" / "Audiveris", {})
    pdf = clean_env / "blank.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ScoreLoadError, match="no MusicXML") as exc:
        load_score(pdf)
    assert "something odd on sheet 1" in str(exc.value)


def test_engine_timeout(clean_env, monkeypatch):
    fake_audiveris(clean_env / "empty-bin" / "Audiveris", sleep=10)
    monkeypatch.setenv("CHOIRSPLIT_OMR_TIMEOUT", "0.5")
    pdf = clean_env / "slow.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ScoreLoadError, match="timed out.*CHOIRSPLIT_OMR_TIMEOUT"):
        load_score(pdf)


def test_default_timeout_scales_with_pages(monkeypatch, tmp_path):
    monkeypatch.delenv("CHOIRSPLIT_OMR_TIMEOUT", raising=False)
    monkeypatch.setattr(omr, "pdf_page_count", lambda p: 3)
    pdf = tmp_path / "a.pdf"
    assert omr.default_timeout(pdf) == 120 + 3 * 120
    assert omr.default_timeout(tmp_path / "a.png") == 240
    monkeypatch.setenv("CHOIRSPLIT_OMR_TIMEOUT", "abc")
    with pytest.raises(ScoreLoadError):
        omr.default_timeout(pdf)


@pytest.mark.skipif(not shutil.which("pdfinfo"), reason="needs pdfinfo")
def test_pdf_page_count():
    assert omr.pdf_page_count(OCTO_PDF) == 3
    assert omr.pdf_page_count(SATB_PDF) == 1


def test_image_only_engine_runs_once_per_page_in_order(clean_env, monkeypatch, tmp_path):
    """CHOIRSPLIT_OMR_CMD with {image}: PDF pages are rasterised and recognised one by one."""
    page_xml = []
    for i, pitch in enumerate(["C4", "D4", "E4"], 1):
        s = stream.Score([line("Alto", [whole(pitch)]), line("Bass", [whole(pitch.replace("4", "2"))],
                                                                clef_obj=clef.BassClef())])
        page_xml.append(write_xml(s, tmp_path / f"page{i}.musicxml"))
    mapping = {f"page-{i}.png": str(x) for i, x in enumerate(page_xml, 1)}
    script = make_exe(tmp_path / "fake-oemer", f"""
        import os, shutil, sys
        out, image = sys.argv[sys.argv.index("-o") + 1], sys.argv[-1]
        shutil.copy({mapping!r}[os.path.basename(image)], os.path.join(out, "result.musicxml"))
    """)

    def fake_rasterize(path, outdir, dpi=300):
        outdir.mkdir(parents=True, exist_ok=True)
        pages = []
        for i in (3, 1, 2):  # creation order must not matter
            (outdir / f"page-{i}.png").write_bytes(b"png")
        for i in (1, 2, 3):
            pages.append(outdir / f"page-{i}.png")
        return pages

    monkeypatch.setattr(omr, "rasterize_pdf", fake_rasterize)
    monkeypatch.setenv("CHOIRSPLIT_OMR_CMD", f"{script} -o {{outdir}} {{image}}")
    pdf = clean_env / "pages.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    score = load_score(pdf)
    assert names_and_pitches(score) == [("Alto", ["C4", "D4", "E4"]), ("Bass", ["C2", "D2", "E2"])]


@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs pdftoppm")
def test_rasterize_pdf_renders_every_page(tmp_path):
    pages = omr.rasterize_pdf(OCTO_PDF, tmp_path / "pages", dpi=20)
    assert len(pages) == 3
    assert [p.name for p in pages] == sorted(p.name for p in pages)
    assert all(p.read_bytes()[:4] == b"\x89PNG" for p in pages)


def test_results_are_cached_by_file_hash(clean_env, monkeypatch, satb_xml):
    log = clean_env / "calls.jsonl"
    fake_audiveris(clean_env / "empty-bin" / "Audiveris", {"{stem}.musicxml": satb_xml}, log=log)
    monkeypatch.setenv("CHOIRSPLIT_OMR_CACHE", str(clean_env / "cache"))
    pdf = clean_env / "c.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% one\n")
    load_score(pdf)
    load_score(pdf)
    assert len(log.read_text().splitlines()) == 1
    pdf.write_bytes(b"%PDF-1.4\n% changed\n")
    load_score(pdf)
    assert len(log.read_text().splitlines()) == 2
    monkeypatch.setenv("CHOIRSPLIT_OMR_CACHE", "0")
    load_score(pdf)
    assert len(log.read_text().splitlines()) == 3


# --------------------------------------------------------------------------- post-processing


def audiveris_split_score():
    """What Audiveris makes of a two-system SA score whose later systems say 'S'/'A':
    four parts, each visible in only one system (hidden staves elsewhere)."""
    systems = (0, 2)
    full = [line("Soprano", [whole("C5"), whole("D5"), whole("r"), whole("r")], systems=systems, hidden_from={2}),
            line("Alto", [whole("A4"), whole("B4"), whole("r"), whole("r")], systems=systems, hidden_from={2})]
    short = [line("S", [whole("r"), whole("r"), whole("E5"), whole("F5")], systems=systems, hidden_from={0}),
             line("A", [whole("r"), whole("r"), whole("C5"), whole("D5")], systems=systems, hidden_from={0})]
    # Audiveris lists the parts it created later first; order must come from the systems.
    return stream.Score(short + full)


def test_parts_split_by_name_are_recollated_by_position():
    merged = omr.assemble([audiveris_split_score()])
    assert names_and_pitches(merged) == [
        ("Soprano", ["C5", "D5", "E5", "F5"]),
        ("Alto", ["A4", "B4", "C5", "D5"]),
    ]
    st = extract_tracks(merged)
    assert [t.name for t in st.voices] == ["Soprano", "Alto"]
    starts = [n.start for n in st.voices[0].notes]
    bar = starts[1]
    assert bar > 0 and starts == pytest.approx([0, bar, 2 * bar, 3 * bar])
    assert [n.start for n in st.voices[1].notes] == pytest.approx(starts)


def test_ocr_misread_names_do_not_win():
    systems = (0, 1)
    s = stream.Score([
        line("Soprano 1", [whole("C5"), whole("r")], systems=systems, hidden_from={1}),
        line("81", [whole("r"), whole("D5")], systems=systems, hidden_from={0}),
    ])
    merged = omr.assemble([s])
    assert names_and_pitches(merged) == [("Soprano 1", ["C5", "D5"])]


def test_system_with_fewer_staves_is_matched_by_name_and_clef():
    """A system where Tenor is hidden (empty staff): the others must keep their lines."""
    systems = (0, 1)
    s = stream.Score([
        line("Soprano", [whole("C5"), whole("D5")], systems=systems),
        line("Tenor", [whole("C4"), whole("r")], systems=systems, hidden_from={1}),
        line("Bass", [whole("C3"), whole("D3")], systems=systems, clef_obj=clef.BassClef()),
    ])
    merged = omr.assemble([s])
    got = dict(names_and_pitches(merged))
    assert got == {"Soprano": ["C5", "D5"], "Tenor": ["C4"], "Bass": ["C3", "D3"]}


def test_align_units_prefers_matching_names():
    def unit(name, clefs=("G",)):
        return omr.Unit([stream.Part()], name, "", clefs, omr.name_key(name))

    refs = [unit("Soprano"), unit("Alto"), unit("Tenor"), unit("Bass", ("F",))]
    assert omr.align_units([unit("A"), unit("B", ("F",))], refs) == [1, 3]
    assert omr.align_units([unit("S"), unit("T")], refs) == [0, 2]
    assert omr.align_units(refs, refs) == [0, 1, 2, 3]


@pytest.mark.parametrize("name,key", [
    ("Soprano 1", "s1"), ("S1", "s1"), ("81", "s1"), ("Sop. II", "s2"), ("Tenor", "t"),
    ("B2", "b2"), ("Alto I", "a1"), ("Voice", ""), ("P3", ""), ("", ""),
])
def test_name_key(name, key):
    assert omr.name_key(name) == key


def test_movements_concatenate_on_one_timeline():
    a = stream.Score([line("Soprano", [whole("C5")]), line("Bass", [whole("C3")], clef_obj=clef.BassClef())])
    b = stream.Score([line("Soprano", [whole("D5"), whole("E5")]),
                      line("Bass", [whole("D3"), whole("E3")], clef_obj=clef.BassClef())])
    merged = omr.assemble([a, b])
    assert names_and_pitches(merged) == [("Soprano", ["C5", "D5", "E5"]), ("Bass", ["C3", "D3", "E3"])]
    for p in merged.parts:
        assert [m.offset for m in p.getElementsByClass(stream.Measure)] == [0.0, 4.0, 8.0]
        assert [m.number for m in p.getElementsByClass(stream.Measure)] == [1, 2, 3]


def test_misread_rhythm_does_not_shift_later_measures():
    """One over-full measure (a misread half note as whole) is clipped to the bar."""
    s = stream.Score([
        line("Soprano", [[("C5", 2), ("D5", 4)], whole("E5")]),  # 6 quarters in a 4/4 bar
        line("Alto", [[("A4", 2), ("B4", 2)], whole("C5")]),
        line("Bass", [[("C3", 2), ("D3", 2)], whole("E3")], clef_obj=clef.BassClef()),
    ])
    merged = omr.assemble([s])
    sop = extract_tracks(merged).voices[0]
    spq = sop.notes[0].duration / 2  # seconds per quarter, whatever the default tempo
    got = [(round(n.start / spq, 3), round(n.duration / spq, 3)) for n in sop.notes]
    assert got == [(0.0, 2.0), (2.0, 2.0), (4.0, 4.0)]


def test_spurious_voices_are_merged():
    """Audiveris: whole-bar rest in voice 1, the actual notes in voice 2 -> one line."""
    p = stream.Part()
    p.partName = "Soprano"
    for i, (v1, v2) in enumerate([("r", "C5"), ("D5", None), (None, "E5")]):
        m = stream.Measure(number=i + 1)
        for vid, pitch in (("1", v1), ("2", v2)):
            if pitch is None:
                continue
            v = stream.Voice(id=vid)
            v.append(note.Rest(quarterLength=4) if pitch == "r" else note.Note(pitch, quarterLength=4))
            m.insert(0, v)
        p.append(m)
    assert omr.simplify_voices(p) is True
    assert all(not m.voices for m in p.getElementsByClass(stream.Measure))
    assert [n.pitch.nameWithOctave for n in p.flatten().notes] == ["C5", "D5", "E5"]
    st = extract_tracks(stream.Score([p]))
    assert [t.name for t in st.voices] == ["Soprano"]


def test_real_two_voice_staff_is_kept(make_part):
    p = make_part("Soprano Alto", [("C5 D5 E5 F5", "A4 B4 C5 D5")] * 3)
    assert omr.tidy_voices(p) == 2
    st = extract_tracks(stream.Score([p]))
    assert [t.name for t in st.voices] == ["Soprano", "Alto"]
    assert [n.pitch for n in st.voices[1].notes][:4] == [69, 71, 72, 74]


def test_two_voice_staff_is_revoiced_by_pitch(make_part):
    """Hymn-style OMR output: stray voice 3, a shared-stem interval written as a chord in
    voice 1, and a unison note head: the result is exactly an upper and a lower line."""
    p = make_part("Soprano Alto", [
        ("C5 D5 E5 F5", "A4 B4 C5 D5"),
        ("G5+E5 A5+F5 G5:2",),            # shared stems as chords in voice 1; G5 unison
        ("C5 D5 E5:2", "A4 B4 r:2", "C5:2"),  # stray voice 3 duplicating C5; alto rests
    ])
    assert omr.tidy_voices(p) == 2
    for m in p.getElementsByClass(stream.Measure):
        assert sorted(str(v.id) for v in m.voices) == ["1", "2"]
    st = extract_tracks(stream.Score([p]))
    sop, alto = st.voices
    assert [t.name for t in st.voices] == ["Soprano", "Alto"]
    assert [int(n.pitch) for n in sop.notes] == [72, 74, 76, 77, 79, 81, 79, 72, 74, 76]
    assert [int(n.pitch) for n in alto.notes] == [69, 71, 72, 74, 76, 77, 79, 69, 71]


def test_revoicing_respects_a_resting_voice(make_part):
    """Staggered entries: in bar 2 the alto rests (whole-bar rest in voice 2), so the
    soprano's lone notes must not be doubled into the alto track."""
    p = make_part("Soprano Alto", [
        ("C5 D5 E5 F5", "A4 B4 C5 D5"),
        ("G5 A5 G5:2", "r:4"),
        ("C5 D5 E5 F5", "A4 B4 C5 D5"),
    ])
    assert omr.tidy_voices(p) == 2
    sop, alto = extract_tracks(stream.Score([p])).voices
    assert [int(n.pitch) for n in sop.notes] == [72, 74, 76, 77, 79, 81, 79, 72, 74, 76, 77]
    assert [int(n.pitch) for n in alto.notes] == [69, 71, 72, 74, 69, 71, 72, 74]


def test_chordal_single_voice_counts_as_two_lines(make_part):
    p = make_part("Voice", ["C5+A4 D5+B4 E5+C5 F5"] * 2)
    assert omr.tidy_voices(p) == 2


def test_unnamed_open_score_gets_satb_names():
    s = stream.Score([line("Voice", [whole(p)]) for p in ("C5", "E4", "G3", "C3")])
    merged = omr.assemble([s])
    assert [p.partName for p in merged.parts] == ["Soprano", "Alto", "Tenor", "Bass"]


def test_unnamed_eight_staves_get_ssaattbb_names():
    s = stream.Score([line("", [whole("C4")]) for _ in range(8)])
    merged = omr.assemble([s])
    assert [p.partName for p in merged.parts] == [
        "Soprano 1", "Soprano 2", "Alto 1", "Alto 2", "Tenor 1", "Tenor 2", "Bass 1", "Bass 2"]


def test_unnamed_closed_score_hymn_is_not_a_piano(make_part):
    """Audiveris names an unlabelled braced two-staff group 'Piano' (a keyboard part);
    with no other parts in the score it is really S/A + T/B on two staves."""
    upper = make_part("Piano", [("C5 D5 E5 F5", "A4 B4 C5 D5")] * 2, cls=stream.PartStaff,
                      part_id="P1-Staff1", instrument=instrument.Piano())
    lower = make_part("Piano", [("E3 F3 G3 A3", "C3 D3 E3 F3")] * 2, cls=stream.PartStaff,
                      part_id="P1-Staff2")
    assert is_accompaniment(upper)
    merged = omr.assemble([stream.Score([upper, lower])])
    assert not any(is_accompaniment(p) for p in merged.parts)
    st = extract_tracks(merged)
    assert [t.name for t in st.voices] == ["Soprano", "Alto", "Tenor", "Bass"]
    assert not st.accompaniment


def test_named_voices_with_unlabelled_piano_keep_the_piano(make_part):
    sop = make_part("Soprano", ["C5 D5 E5 F5"])
    upper = make_part("Piano", ["C4 D4 E4 F4"], cls=stream.PartStaff, part_id="P2-Staff1",
                      instrument=instrument.Piano())
    lower = make_part("Piano", ["C3 D3 E3 F3"], cls=stream.PartStaff, part_id="P2-Staff2")
    merged = omr.assemble([stream.Score([sop, upper, lower])])
    st = extract_tracks(merged)
    assert [t.name for t in st.voices] == ["Soprano"]
    assert [t.name for t in st.accompaniment] == ["Piano"]


def test_unnamed_voices_with_unlabelled_piano(make_part):
    """No names recognised at all (e.g. no OCR data): four 'Voice' staves plus Audiveris's
    default 'Piano' group -> SATB plus a piano accompaniment, not eight voices."""
    voices = [make_part("Voice", [f"{p} {p} {p} {p}"] * 2) for p in ("C5", "E4", "G3", "C3")]
    upper = make_part("Piano", [("C5 D5 E5 F5", "C4 D4 E4 F4")] * 2, cls=stream.PartStaff,
                      part_id="P5-Staff1", instrument=instrument.Piano())
    lower = make_part("Piano", [("C3 D3 E3 F3", "C2 D2 E2 F2")] * 2, cls=stream.PartStaff,
                      part_id="P5-Staff2")
    st = extract_tracks(omr.assemble([stream.Score(voices + [upper, lower])]))
    assert [t.name for t in st.voices] == ["Soprano", "Alto", "Tenor", "Bass"]
    assert [t.name for t in st.accompaniment] == ["Piano"]
    assert len(st.accompaniment[0].notes) == 4 * 4 * 2  # every piano note kept


@pytest.mark.parametrize("name,is_tenor", [
    ("Tenor", True), ("Tenors", True), ("Tenor 2", True), ("Ten. II", True), ("T", True),
    ("T1", True), ("Treble", False), ("Tutti", False), ("Alto", False), ("Soprano", False),
])
def test_is_tenor_name(name, is_tenor):
    assert omr.is_tenor_name(name) is is_tenor


def test_tenor_in_plain_treble_clef_sounds_an_octave_lower():
    tenor = line("Tenor", [[("C5", 2), ("E5", 2)]])  # OMR missed the little 8 under the clef
    assert omr.fix_tenor_octave(tenor)
    assert [n.pitch.nameWithOctave for n in tenor.flatten().notes] == ["C4", "E4"]
    already = line("Tenor", [whole("C4")], clef_obj=clef.Treble8vbClef())
    assert not omr.fix_tenor_octave(already)
    alto = line("Alto", [whole("C5")])
    assert not omr.fix_tenor_octave(alto)
    bass_clef_tenor = line("Tenor", [whole("C4")], clef_obj=clef.BassClef())
    assert not omr.fix_tenor_octave(bass_clef_tenor)
    for name in ("Treble", "Tutti"):
        assert not omr.fix_tenor_octave(line(name, [whole("C5")]))


def test_title_falls_back_to_largest_credit_text():
    s = stream.Score([line("Soprano", [whole("C5")])])
    from music21 import metadata
    s.metadata = metadata.Metadata()
    s.metadata.movementName = "scan_0001"  # Audiveris uses the file name
    big = text.TextBox("Ave verum corpus")
    big.style.fontSize = 20
    small = text.TextBox("W. A. Mozart")
    small.style.fontSize = 11
    s.insert(0, big)
    s.insert(0, small)
    merged = omr.assemble([s], title_hint="scan_0001")
    assert merged.metadata.title == "Ave verum corpus"


# --------------------------------------------------------------------------- real engine

ENGINE = None
try:
    ENGINE = omr.find_engine()
except ScoreLoadError:
    pass

@pytest.fixture(scope="module")
def omr_cache(tmp_path_factory):
    """Recognise each fixture once per test session (Audiveris takes ~20 s per page)."""
    old = os.environ.get("CHOIRSPLIT_OMR_CACHE")
    os.environ["CHOIRSPLIT_OMR_CACHE"] = str(tmp_path_factory.mktemp("omr-cache"))
    yield
    if old is None:
        os.environ.pop("CHOIRSPLIT_OMR_CACHE", None)
    else:
        os.environ["CHOIRSPLIT_OMR_CACHE"] = old


def pitch_accuracy(truth: stream.Score, recognised: stream.Score) -> tuple[float, list[float]]:
    """Share of ground-truth pitches found, in order, in the matching recognised part."""
    per_part, hit, total = [], 0, 0
    for t, r in zip(truth.parts, recognised.parts):
        a = [p.ps for n in t.flatten().notes for p in n.pitches]
        b = [p.ps for n in r.flatten().notes for p in n.pitches]
        k = sum(blk.size for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
        per_part.append(k / len(a))
        hit += k
        total += len(a)
    return hit / total, per_part


@pytest.mark.usefixtures("omr_cache")
@pytest.mark.omr
@pytest.mark.slow
@pytest.mark.skipif(ENGINE is None, reason="no OMR engine installed (scripts/install_omr.sh)")
def test_real_satb_chorale():
    score = load_score(SATB_PDF)
    st = extract_tracks(score)
    assert [t.name for t in st.voices] == ["Soprano", "Alto", "Tenor", "Bass"]
    medians = [sorted(n.pitch for n in t.notes)[len(t.notes) // 2] for t in st.voices]
    assert medians == sorted(medians, reverse=True)
    assert 67 <= medians[0] <= 79 and 38 <= medians[3] <= 55  # soprano ~G4-G5, bass ~D2-G3
    acc, per_part = pitch_accuracy(converter.parse(str(SATB_XML)), score)
    assert acc >= 0.8, per_part
    lengths = {round(t.end, 1) for t in st.voices}
    assert len(lengths) == 1, lengths  # all voices end together


@pytest.mark.usefixtures("omr_cache")
@pytest.mark.omr
@pytest.mark.slow
@pytest.mark.skipif(ENGINE is None, reason="no OMR engine installed (scripts/install_omr.sh)")
def test_real_ssaattbb_multipage():
    score = load_score(OCTO_PDF)
    st = extract_tracks(score)
    assert [t.name for t in st.voices] == [
        "Soprano 1", "Soprano 2", "Alto 1", "Alto 2", "Tenor 1", "Tenor 2", "Bass 1", "Bass 2"]
    acc, per_part = pitch_accuracy(converter.parse(str(OCTO_XML)), score)
    assert acc >= 0.8, per_part
    measures = {len(p.getElementsByClass(stream.Measure)) for p in score.parts}
    assert measures == {30}
    assert st.title == "Agnus Dei II"


@pytest.mark.usefixtures("omr_cache")
@pytest.mark.omr
@pytest.mark.slow
@pytest.mark.skipif(ENGINE is None, reason="no OMR engine installed (scripts/install_omr.sh)")
@pytest.mark.skipif(not HYMN_PDF.exists(), reason="hymn fixture missing")
def test_real_closed_score_hymn():
    """A real public-domain hymn (Mutopia): S/A and T/B share staves, no part names."""
    st = extract_tracks(load_score(HYMN_PDF))
    assert [t.name for t in st.voices] == ["Soprano", "Alto", "Tenor", "Bass"]
    assert not st.accompaniment
    medians = [sorted(n.pitch for n in t.notes)[len(t.notes) // 2] for t in st.voices]
    assert medians == sorted(medians, reverse=True)


@pytest.mark.usefixtures("omr_cache")
@pytest.mark.omr
@pytest.mark.slow
@pytest.mark.skipif(ENGINE is None, reason="no OMR engine installed (scripts/install_omr.sh)")
@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_real_cli_end_to_end(tmp_path):
    out = tmp_path / "out"
    cli = Path(sys.executable).with_name("choir-split")
    cmd = [str(cli)] if cli.exists() else [sys.executable, "-m", "choirsplit"]
    proc = subprocess.run([*cmd, str(SATB_PDF), "-o", str(out)], capture_output=True, text=True,
                          env=os.environ.copy(), timeout=900)
    assert proc.returncode == 0, proc.stderr
    mp3s = sorted(out.glob("*.mp3"))
    assert len(mp3s) == 4 * 2 + 1, [p.name for p in mp3s]  # solo + practice per voice, full mix
    durations = []
    for f in mp3s:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", str(f)], capture_output=True, text=True)
        durations.append(float(probe.stdout))
    assert max(durations) - min(durations) < 0.1, durations
    assert min(durations) > 10
