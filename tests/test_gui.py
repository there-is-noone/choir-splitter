"""Desktop app logic, without a display: the window's settings as pipeline Options,
dropped files, the worker thread, remembered settings, start-up checks and shortcuts.
The Tk window itself is checked by hand."""

from __future__ import annotations

import io
import json
import queue
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from audio_helpers import make_score
from choirsplit import gui, platforms, shortcut
from choirsplit.check import Issue
from choirsplit.pipeline import Options

SATB = ["Soprano", "Alto", "Tenor", "Bass"]


def set_platform(monkeypatch, name: str) -> None:
    monkeypatch.setattr(platforms, "sys", SimpleNamespace(platform=name))


@pytest.fixture(scope="module")
def satb(tmp_path_factory):
    d = tmp_path_factory.mktemp("gui")
    return make_score(d / "Ave.musicxml", [(n, [p] * 4) for n, p in zip(SATB, (72, 65, 57, 48))])


@pytest.fixture
def home(monkeypatch, tmp_path):
    """choirsplit's data directory (settings, log) under tmp_path."""
    monkeypatch.setenv("CHOIRSPLIT_HOME", str(tmp_path / "data"))
    return tmp_path / "data"


# --------------------------------------------------------------------------- settings -> Options


def test_default_window_settings_are_the_command_line_defaults(satb):
    score, out_dir, opts = gui.to_options(gui.GuiState(score=str(satb)))
    assert score == satb and out_dir == satb.with_name("Ave_tracks")
    assert opts == Options()


def test_every_control_reaches_the_options(satb, tmp_path):
    state = gui.GuiState(
        score=str(satb), output=str(tmp_path / "out"), mode="practice", others_volume=45, speed=80,
        full_mix=False, accompaniment=False, voices=SATB, chosen=["Alto", "Bass"], title=" Ave ",
        transpose=-2, fixed_tempo=True, bpm=72.5, fmt="wav", bitrate="256k", split_voices=False,
        split_chords=False, export_musicxml=True, omr_dpi=400)
    _, out_dir, opts = gui.to_options(state)
    assert out_dir == tmp_path / "out"
    assert opts == Options(
        mode="practice", full_mix=False, fmt="wav", bitrate="256k", bpm=72.5, tempo_scale=0.8,
        transpose=-2, others_gain=0.45, include_accompaniment=False, split_voices=False,
        split_chords=False, only=["Alto", "Bass"], title="Ave",
        export_musicxml=tmp_path / "out" / "Ave.musicxml", omr_dpi=400)


def test_voices_all_ticked_means_no_restriction_and_none_ticked_is_refused(satb):
    state = gui.GuiState(score=str(satb), voices=SATB, chosen=list(SATB))
    assert gui.to_options(state)[2].only == []
    state.chosen = []
    with pytest.raises(ValueError, match="pick at least one voice"):
        gui.to_options(state)
    assert gui.to_options(state, need_voices=False)[2].only == []  # listing voices again is fine


def test_the_tempo_only_counts_when_fixed(satb):
    assert gui.to_options(gui.GuiState(score=str(satb), bpm=5))[2].bpm is None
    with pytest.raises(ValueError, match="bpm must be between"):
        gui.to_options(gui.GuiState(score=str(satb), fixed_tempo=True, bpm=5))


def test_no_score_yet():
    with pytest.raises(ValueError, match="choose a score"):
        gui.to_options(gui.GuiState())


# --------------------------------------------------------------------------- dropped files


def test_dropped_files_keep_only_scores(satb, tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("x")
    upper = tmp_path / "SCAN.PDF"
    upper.write_bytes(b"%PDF")
    spaced = tmp_path / "my score.mxl"
    spaced.write_bytes(b"PK")
    items = [str(other), str(tmp_path), "https://example.org/x.pdf", str(upper),
             spaced.as_uri(), str(tmp_path / "gone.musicxml"), str(satb)]
    assert gui.dropped_files(items) == [upper, spaced, satb]


# --------------------------------------------------------------------------- the worker


def run(worker: gui.Worker, kind: str, state: gui.GuiState, timeout: float = 60):
    score, out_dir, opts = gui.to_options(state, need_voices=kind == "make")
    worker.start(kind, score, out_dir, opts)
    worker.thread.join(timeout)
    assert not worker.busy
    events = []
    while True:
        try:
            events.append(worker.events.get_nowait())
        except queue.Empty:
            return events


def test_worker_finds_voices_checks_and_makes_tracks(satb, tmp_path):
    w = gui.Worker()
    state = gui.GuiState(score=str(satb), output=str(tmp_path), fmt="wav", mode="solo", full_mix=False)
    events = run(w, "voices", state)
    assert events[0] == ("stage", "reading Ave.musicxml")
    assert events[-1][0] == "voices" and events[-1][3] == SATB

    events = run(w, "check", state)
    assert events[-1][0] == "check" and all(isinstance(i, Issue) for i in events[-1][2])
    assert ("stage", "reading Ave.musicxml") not in events  # the loaded score was reused

    state.voices, state.chosen = SATB, ["Tenor"]
    events = run(w, "make", state)
    kind, folder, files, suspects, recognised = events[-1]
    assert (kind, folder, recognised) == ("done", tmp_path, False)
    assert [f.name for f in files] == ["Ave - 03 Tenor.wav"]
    assert ("stage", "rendering Tenor") in events


def test_worker_reloads_when_load_settings_change(satb, tmp_path):
    w = gui.Worker()
    run(w, "voices", gui.GuiState(score=str(satb)))
    events = run(w, "voices", gui.GuiState(score=str(satb), speed=50))
    assert ("stage", "reading Ave.musicxml") in events
    assert events[-1][2] == pytest.approx(4.0)  # 2 s at half speed


def test_worker_reports_errors_and_stops_on_cancel(satb, tmp_path, monkeypatch, home):
    w = gui.Worker()
    events = run(w, "voices", gui.GuiState(score=str(tmp_path / "missing.musicxml")))
    assert events[-1][0] == "error" and "file not found" in events[-1][1]

    class CancelOnFirstStage(queue.Queue):
        def put(self, item, *a, **k):
            if item[0] == "stage":
                w.cancel()
            super().put(item, *a, **k)

    w.events = CancelOnFirstStage()
    events = run(w, "make", gui.GuiState(score=str(satb), output=str(tmp_path / "out"), fmt="wav"))
    assert events[-1] == ("cancelled",)
    assert not any((tmp_path / "out").glob("*"))

    def boom(*a, **k):
        raise KeyError("odd")

    monkeypatch.setattr("choirsplit.pipeline.load_tracks", boom)
    events = run(gui.Worker(), "voices", gui.GuiState(score=str(satb)))
    assert events[-1][0] == "error" and "KeyError" in events[-1][1] and "gui.log" in events[-1][1]
    assert "KeyError: 'odd'" in (home / "gui.log").read_text()


def test_one_job_at_a_time(satb):
    w = gui.Worker()
    w.thread = SimpleNamespace(is_alive=lambda: True)
    with pytest.raises(RuntimeError, match="already running"):
        w.start("voices", satb, satb.parent, Options())


# --------------------------------------------------------------------------- start-up


def test_missing_console_streams_go_to_the_log(monkeypatch, home):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    gui.ensure_streams()
    sys.stderr.write("music21 warning\n")
    sys.stderr.flush()
    assert (home / "gui.log").read_text() == "music21 warning\n"


def test_settings_are_remembered_but_not_the_score(home):
    state = gui.GuiState(score="x.pdf", mode="solo", speed=75, fmt="wav", omr_dpi=600, title="T")
    gui.save_settings(state)
    saved = json.loads((home / "gui.json").read_text())
    assert "score" not in saved and "title" not in saved
    fresh = gui.GuiState()
    gui.load_settings(fresh)
    assert (fresh.score, fresh.mode, fresh.speed, fresh.fmt, fresh.omr_dpi, fresh.title) == \
        ("", "solo", 75, "wav", 600, "")


def test_damaged_settings_are_ignored(home):
    home.mkdir(parents=True)
    (home / "gui.json").write_text('{"mode": 3, "speed": "fast", "omr_dpi": 123, "fmt": "wav"}')
    state = gui.GuiState()
    gui.load_settings(state)
    assert (state.mode, state.speed, state.omr_dpi, state.fmt) == ("both", 100, None, "wav")
    (home / "gui.json").write_text("not json")
    gui.load_settings(gui.GuiState())


@pytest.mark.parametrize("plat, tk, env, allowed", [
    ("linux", 9.0, "", False), ("linux", 8.6, "", True), ("win32", 9.0, "", True),
    ("darwin", 9.0, "", True), ("linux", 9.0, "1", True), ("win32", 8.6, "0", False),
])
def test_drag_and_drop_is_off_where_tkdnd_crashes(monkeypatch, plat, tk, env, allowed):
    set_platform(monkeypatch, plat)
    monkeypatch.setenv("CHOIRSPLIT_DND", env)
    assert gui.dnd_allowed(tk) is allowed


@pytest.mark.parametrize("plat, hint", [
    ("linux", "python3-tkinter"), ("darwin", "brew install python-tk@"), ("win32", "tcl/tk and IDLE"),
])
def test_missing_tk_says_how_to_get_it(monkeypatch, plat, hint):
    set_platform(monkeypatch, plat)
    assert hint in gui.tk_hint()


# --------------------------------------------------------------------------- operating system


def test_no_console_window_on_windows_only(monkeypatch):
    set_platform(monkeypatch, "win32")
    assert platforms.no_console() == {"creationflags": 0x08000000}
    set_platform(monkeypatch, "linux")
    assert platforms.no_console() == {}


@pytest.mark.parametrize("plat, which, command", [
    ("darwin", None, "open"), ("linux", "/usr/bin/xdg-open", "xdg-open"), ("linux", None, "gio"),
])
def test_open_folder_uses_the_systems_file_manager(monkeypatch, tmp_path, plat, which, command):
    set_platform(monkeypatch, plat)
    monkeypatch.setattr(platforms.shutil, "which", lambda name: which)
    seen = []
    monkeypatch.setattr(platforms.subprocess, "Popen", lambda cmd, **k: seen.append(cmd))
    platforms.open_folder(tmp_path)
    assert seen[0][0] == command and seen[0][-1] == str(tmp_path)


def test_open_folder_on_windows(monkeypatch, tmp_path):
    set_platform(monkeypatch, "win32")
    seen = []
    monkeypatch.setattr(platforms.os, "startfile", seen.append, raising=False)
    platforms.open_folder(tmp_path)
    assert seen == [str(tmp_path)]


def test_linux_shortcut_carries_the_terminals_settings(monkeypatch, tmp_path):
    set_platform(monkeypatch, "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("CHOIRSPLIT_AUDIVERIS", "/opt/my audiveris")
    monkeypatch.setenv("PATH", "/usr/bin:/home/me/bin")
    entry = shortcut.install_shortcut(home=tmp_path)
    assert entry == tmp_path / "share" / "applications" / "choirsplit.desktop"
    text = entry.read_text()
    exec_line = next(line for line in text.splitlines() if line.startswith("Exec="))
    assert exec_line.startswith("Exec=env ") and exec_line.endswith("-m choirsplit.gui %f")
    assert '"CHOIRSPLIT_AUDIVERIS=/opt/my audiveris"' in exec_line
    assert "PATH=/usr/bin:/home/me/bin" in exec_line
    assert "StartupWMClass=choirsplit" in text and f"Icon={shortcut.ICON}" in text
    assert shortcut.ICON.is_file()


def test_desktop_quoting():
    assert shortcut._desktop_quote("plain") == "plain"
    assert shortcut._desktop_quote('a "b" $c 50%') == '"a \\"b\\" \\$c 50%%"'


def test_mac_shortcut_is_an_app_bundle(monkeypatch, tmp_path):
    set_platform(monkeypatch, "darwin")
    monkeypatch.setenv("CHOIRSPLIT_HOME", "/Users/me/choir data")
    app = shortcut.install_shortcut(home=tmp_path)
    assert app == tmp_path / "Applications" / "Choir Split.app"
    launcher = app / "Contents" / "MacOS" / "choirsplit"
    script = launcher.read_text()
    assert "export CHOIRSPLIT_HOME='/Users/me/choir data'\n" in script
    assert script.rstrip().endswith('-m choirsplit.gui "$@"') and launcher.stat().st_mode & 0o111
    import plistlib
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "choirsplit"


def test_windows_shortcut_goes_through_powershell(monkeypatch, tmp_path):
    set_platform(monkeypatch, "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    seen = {}

    def fake_run(cmd, env=None, **k):
        seen["cmd"], seen["env"] = cmd, env
        Path(env["CS_LINK"]).write_bytes(b"lnk")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(shortcut.subprocess, "run", fake_run)
    link = shortcut.install_shortcut(home=tmp_path)
    assert link == tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Choir Split.lnk"
    assert seen["cmd"][0] == "powershell" and "-m choirsplit.gui" in seen["cmd"][-1]


def test_main_installs_the_shortcut_without_tk(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(shortcut, "install_shortcut", lambda: tmp_path / "x.desktop")
    assert gui.main(["--install-shortcut"]) == 0
    assert "added" in capsys.readouterr().out


def test_main_without_tk_explains(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "tkinter", None)  # import tkinter raises ImportError
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert gui.main([]) == 1
    assert "needs Tk" in sys.stderr.getvalue()
