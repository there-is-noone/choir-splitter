"""Unit tests for choirsplit.mscx_utils."""

import shutil
import subprocess
import zipfile
from fractions import Fraction

import pytest

from choirsplit import mscx_utils as m


# --- duration_ql -----------------------------------------------------------

@pytest.mark.parametrize(
    ("duration_type", "dots", "expected"),
    [
        ("long", 0, Fraction(16)),
        ("breve", 0, Fraction(8)),
        ("whole", 0, Fraction(4)),
        ("half", 0, Fraction(2)),
        ("quarter", 0, Fraction(1)),
        ("eighth", 0, Fraction(1, 2)),
        ("16th", 0, Fraction(1, 4)),
        ("32nd", 0, Fraction(1, 8)),
        ("64th", 0, Fraction(1, 16)),
        ("128th", 0, Fraction(1, 32)),
        ("256th", 0, Fraction(1, 64)),
        ("512th", 0, Fraction(1, 128)),
        ("1024th", 0, Fraction(1, 256)),
        ("quarter", 1, Fraction(3, 2)),
        ("quarter", 2, Fraction(7, 4)),
        ("quarter", 3, Fraction(15, 8)),
        ("half", 1, Fraction(3)),
        ("eighth", 1, Fraction(3, 4)),
        ("whole", 2, Fraction(7)),
    ],
)
def test_duration_ql_values(duration_type, dots, expected):
    assert m.duration_ql(duration_type, dots) == expected


def test_duration_ql_default_dots_is_zero():
    assert m.duration_ql("quarter") == Fraction(1)


@pytest.mark.parametrize("duration_type", ["measure", "bogus", ""])
def test_duration_ql_rejects_unknown_or_measure(duration_type):
    with pytest.raises(ValueError):
        m.duration_ql(duration_type)


def test_duration_ql_rejects_negative_dots():
    with pytest.raises(ValueError):
        m.duration_ql("quarter", -1)


# --- parse_fraction --------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3/8", Fraction(3, 8)),
        (" -1/4 ", Fraction(-1, 4)),
        ("2", Fraction(2)),
        ("0", Fraction(0)),
        ("1/64", Fraction(1, 64)),
        ("4/4", Fraction(1)),
    ],
)
def test_parse_fraction_values(text, expected):
    assert m.parse_fraction(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "a/b", "1/0", "3/", "/4", "1/2/3", "1.5", "3/-4"])
def test_parse_fraction_rejects_bad_input(text):
    with pytest.raises(ValueError):
        m.parse_fraction(text)


# --- fraction_to_ql --------------------------------------------------------

@pytest.mark.parametrize(
    ("fr", "expected"),
    [
        (Fraction(3, 8), Fraction(3, 2)),
        (Fraction(1), Fraction(4)),
        (Fraction(-1, 4), Fraction(-1)),
        (Fraction(0), Fraction(0)),
    ],
)
def test_fraction_to_ql(fr, expected):
    assert m.fraction_to_ql(fr) == expected


# --- tpc_to_name -----------------------------------------------------------

@pytest.mark.parametrize(
    ("tpc", "expected"),
    [
        (14, "C"),
        (15, "G"),
        (13, "F"),
        (16, "D"),
        (17, "A"),
        (18, "E"),
        (19, "B"),
        (20, "F#"),
        (21, "C#"),
        (12, "B-"),
        (6, "F-"),
        (27, "F##"),
        (0, "C--"),
        (26, "B#"),
        (33, "B##"),
        (-1, "F--"),
    ],
)
def test_tpc_to_name(tpc, expected):
    assert m.tpc_to_name(tpc) == expected


@pytest.mark.parametrize("tpc", [-2, -100, 34, 100])
def test_tpc_to_name_rejects_out_of_range(tpc):
    with pytest.raises(ValueError):
        m.tpc_to_name(tpc)


# --- tempo_to_bpm ----------------------------------------------------------

@pytest.mark.parametrize(
    ("qps", "expected"),
    [(2.0, 120.0), (1.5, 90.0), (0.5, 30.0), (1, 60.0)],
)
def test_tempo_to_bpm(qps, expected):
    assert m.tempo_to_bpm(qps) == pytest.approx(expected)


@pytest.mark.parametrize("qps", [0, 0.0, -1.0, float("nan")])
def test_tempo_to_bpm_rejects_non_positive(qps):
    with pytest.raises(ValueError):
        m.tempo_to_bpm(qps)


# --- read_mscx_bytes -------------------------------------------------------

def _container(*rootfiles):
    body = "".join(f'<rootfile full-path="{r}"/>' for r in rootfiles)
    return f'<?xml version="1.0" encoding="UTF-8"?><container><rootfiles>{body}</rootfiles></container>'.encode()


def _make_mscz(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_read_mscx_plain_file(tmp_path):
    p = tmp_path / "score.mscx"
    p.write_bytes(b"<museScore/>")
    assert m.read_mscx_bytes(p) == b"<museScore/>"


def test_read_mscx_accepts_str_path(tmp_path):
    p = tmp_path / "score.mscx"
    p.write_bytes(b"<x/>")
    assert m.read_mscx_bytes(str(p)) == b"<x/>"


@pytest.mark.parametrize("name", ["score.xml", "score.mxl", "score.txt", "score"])
def test_read_mscx_rejects_other_suffixes(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"<x/>")
    with pytest.raises(ValueError):
        m.read_mscx_bytes(p)


def test_read_mscz_uses_container_rootfile(tmp_path):
    p = _make_mscz(
        tmp_path / "a.mscz",
        {
            "META-INF/container.xml": _container("score.mscx"),
            "score.mscx": b"SCORE",
            "Excerpts/part.mscx": b"EXCERPT",
        },
    )
    assert m.read_mscx_bytes(p) == b"SCORE"


def test_read_mscz_container_points_to_non_root_entry(tmp_path):
    p = _make_mscz(
        tmp_path / "a.mscz",
        {
            "META-INF/container.xml": _container("sub/main.mscx"),
            "score.mscx": b"ROOT",
            "sub/main.mscx": b"MAIN",
        },
    )
    assert m.read_mscx_bytes(p) == b"MAIN"


def test_read_mscz_without_container_falls_back(tmp_path):
    p = _make_mscz(
        tmp_path / "a.mscz",
        {"score.mscx": b"SCORE", "Excerpts/part.mscx": b"EXCERPT"},
    )
    assert m.read_mscx_bytes(p) == b"SCORE"


def test_read_mscz_ignores_excerpts_entries(tmp_path):
    p = _make_mscz(
        tmp_path / "a.mscz",
        {"Excerpts/part.mscx": b"EXCERPT", "score.mscx": b"SCORE"},
    )
    assert m.read_mscx_bytes(p) == b"SCORE"


def test_read_mscz_prefers_root_level_over_nested(tmp_path):
    p = _make_mscz(
        tmp_path / "a.mscz",
        {"sub/nested.mscx": b"NESTED", "root.mscx": b"ROOT"},
    )
    assert m.read_mscx_bytes(p) == b"ROOT"


def test_read_mscz_container_names_missing_entry_falls_back(tmp_path):
    p = _make_mscz(
        tmp_path / "a.mscz",
        {"META-INF/container.xml": _container("gone.mscx"), "score.mscx": b"SCORE"},
    )
    assert m.read_mscx_bytes(p) == b"SCORE"


def test_read_mscz_container_names_non_mscx_falls_back(tmp_path):
    p = _make_mscz(
        tmp_path / "a.mscz",
        {
            "META-INF/container.xml": _container("score.xml"),
            "score.xml": b"XML",
            "score.mscx": b"SCORE",
        },
    )
    assert m.read_mscx_bytes(p) == b"SCORE"


def test_read_mscz_malformed_container_falls_back(tmp_path):
    p = _make_mscz(
        tmp_path / "a.mscz",
        {"META-INF/container.xml": b"<container><rootfiles", "score.mscx": b"SCORE"},
    )
    assert m.read_mscx_bytes(p) == b"SCORE"


def test_read_mscz_only_excerpts_raises(tmp_path):
    p = _make_mscz(tmp_path / "a.mscz", {"Excerpts/part.mscx": b"EXCERPT"})
    with pytest.raises(ValueError):
        m.read_mscx_bytes(p)


def test_read_mscz_without_mscx_raises(tmp_path):
    p = _make_mscz(tmp_path / "a.mscz", {"readme.txt": b"hello"})
    with pytest.raises(ValueError, match="no .mscx"):
        m.read_mscx_bytes(p)


def test_read_mscz_bad_zip_raises(tmp_path):
    p = tmp_path / "bad.mscz"
    p.write_bytes(b"this is not a zip archive")
    with pytest.raises(ValueError, match="zip"):
        m.read_mscx_bytes(p)


def test_read_mscz_refuses_huge_entry(tmp_path):
    p = tmp_path / "huge.mscz"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("score.mscx", bytes(50 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="too large"):
        m.read_mscx_bytes(p)


# --- find_musescore_cli ----------------------------------------------------

_CLI_ENV = "CHOIRSPLIT_MUSESCORE"


def _no_subprocess(*args, **kwargs):
    raise AssertionError("subprocess.run must not be called")


@pytest.fixture
def clean_cli_env(monkeypatch):
    monkeypatch.delenv(_CLI_ENV, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setattr(subprocess, "run", _no_subprocess)
    return monkeypatch


def test_cli_none_when_nothing_found(clean_cli_env):
    assert m.find_musescore_cli() is None


def test_cli_env_existing_file_wins(clean_cli_env, tmp_path):
    exe = tmp_path / "my-musescore"
    exe.write_text("#!/bin/sh\n")
    clean_cli_env.setenv(_CLI_ENV, str(exe))
    clean_cli_env.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/mscore")
    assert m.find_musescore_cli() == [str(exe)]


def test_cli_env_name_resolved_via_which(clean_cli_env):
    clean_cli_env.setenv(_CLI_ENV, "custom-ms")
    clean_cli_env.setattr(
        shutil, "which", lambda name, *a, **k: "/opt/bin/custom-ms" if name == "custom-ms" else None
    )
    assert m.find_musescore_cli() == ["/opt/bin/custom-ms"]


def test_cli_env_unresolvable_falls_through_to_names(clean_cli_env, tmp_path):
    clean_cli_env.setenv(_CLI_ENV, str(tmp_path / "missing"))
    clean_cli_env.setattr(
        shutil, "which", lambda name, *a, **k: "/usr/bin/mscore" if name == "mscore" else None
    )
    assert m.find_musescore_cli() == ["/usr/bin/mscore"]


def test_cli_blank_env_is_ignored(clean_cli_env):
    clean_cli_env.setenv(_CLI_ENV, "   ")
    clean_cli_env.setattr(
        shutil, "which", lambda name, *a, **k: "/usr/bin/musescore" if name == "musescore" else None
    )
    assert m.find_musescore_cli() == ["/usr/bin/musescore"]


@pytest.mark.parametrize(
    "name",
    ["mscore", "mscore4portable", "musescore", "musescore4", "MuseScore4", "mscore3", "MuseScore3"],
)
def test_cli_finds_each_known_name(clean_cli_env, name):
    clean_cli_env.setattr(
        shutil, "which", lambda n, *a, **k: f"/usr/bin/{n}" if n == name else None
    )
    assert m.find_musescore_cli() == [f"/usr/bin/{name}"]


def test_cli_name_order_prefers_mscore(clean_cli_env):
    clean_cli_env.setattr(
        shutil,
        "which",
        lambda n, *a, **k: f"/usr/bin/{n}" if n in ("musescore", "mscore") else None,
    )
    assert m.find_musescore_cli() == ["/usr/bin/mscore"]


def test_cli_flatpak_used_when_installed(clean_cli_env):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    clean_cli_env.setattr(
        shutil, "which", lambda n, *a, **k: "/usr/bin/flatpak" if n == "flatpak" else None
    )
    clean_cli_env.setattr(subprocess, "run", fake_run)
    assert m.find_musescore_cli() == ["flatpak", "run", "org.musescore.MuseScore"]
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ["flatpak", "info", "org.musescore.MuseScore"]
    assert kwargs.get("timeout") == 10
    assert kwargs.get("capture_output") is True


def test_cli_flatpak_not_installed_returns_none(clean_cli_env):
    clean_cli_env.setattr(
        shutil, "which", lambda n, *a, **k: "/usr/bin/flatpak" if n == "flatpak" else None
    )
    clean_cli_env.setattr(
        subprocess, "run", lambda args, **k: subprocess.CompletedProcess(args, 1)
    )
    assert m.find_musescore_cli() is None


def test_cli_flatpak_exception_returns_none(clean_cli_env):
    def boom(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 10)

    clean_cli_env.setattr(
        shutil, "which", lambda n, *a, **k: "/usr/bin/flatpak" if n == "flatpak" else None
    )
    clean_cli_env.setattr(subprocess, "run", boom)
    assert m.find_musescore_cli() is None


def test_cli_flatpak_skipped_without_flatpak_binary(clean_cli_env):
    # clean_cli_env already makes subprocess.run raise if it is called.
    assert m.find_musescore_cli() is None


def test_cli_binary_beats_flatpak(clean_cli_env):
    clean_cli_env.setattr(
        shutil,
        "which",
        lambda n, *a, **k: {"mscore": "/usr/bin/mscore", "flatpak": "/usr/bin/flatpak"}.get(n),
    )
    assert m.find_musescore_cli() == ["/usr/bin/mscore"]


# --- instrument_kind -------------------------------------------------------

@pytest.mark.parametrize(
    ("instrument_id", "names", "expected"),
    [
        ("keyboard.piano", [], "piano"),
        ("keyboard.organ.pipe", [], "organ"),
        ("keyboard.organ", [], "organ"),
        ("keyboard.harpsichord", [], "keyboard"),
        ("keyboard.celesta", [], "keyboard"),
        ("keyboard.synth", [], "keyboard"),
        ("keyboard.clavichord", [], "keyboard"),
        ("voice.soprano", [], "voice"),
        ("voice.tenor", [], "voice"),
        ("voice.choir", [], "voice"),
        ("drum.group.set", [], "percussion"),
        ("perc.timpani", [], "percussion"),
        ("piano", [], "piano"),
        ("soprano", [], "voice"),
        ("harmonium", [], "organ"),
        ("pno", [], "piano"),
        ("KEYBOARD.PIANO", [], "piano"),
        ("Voice.Soprano", [], "voice"),
        (None, ["Piano"], "piano"),
        (None, ["Mezzo-Soprano"], "voice"),
        ("", [None, "", "Alto Voice"], "voice"),
        ("keyboard.piano", ["Pipe Organ"], "organ"),
        ("voice.tenor", ["Drum"], "voice"),
        ("voice.soprano", ["Synth"], "voice"),
        ("keyboard.synth", ["Choir Synth"], "keyboard"),
        ("drum.group", ["Choir Drums"], "percussion"),
        ("pluck.guitar", ["Electric Bass"], "other"),
        ("pluck.bass", [], "other"),
        ("strings.contrabass", ["Bass"], "other"),
        ("wind.flutes.flute", ["Flute"], "other"),
        (None, [], "other"),
        ("", [], "other"),
    ],
)
def test_instrument_kind(instrument_id, names, expected):
    assert m.instrument_kind(instrument_id, names) == expected
