"""Windows and macOS support, simulated on any OS: per-platform directories, finding
Audiveris and MuseScore where their installers put them, command splitting, console output.

The platform choirsplit sees is faked (Python's own is left alone) and the install folders are recreated under tmp_path, so these
tests check the lookup logic everywhere; real Windows/macOS runs happen in CI.
"""

from __future__ import annotations

import io
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from choirsplit import cli, mscx_utils, musescore, omr, platforms
from choirsplit.loaders import ScoreLoadError

FIXTURE = Path(__file__).parent / "fixtures" / "musescore" / "satb_open.mscx"
CFG = "[JavaOptions]\njava-options=-Djpackage.app-version=5.11.0\n"


def set_platform(monkeypatch, name: str) -> None:
    """Make choirsplit believe it runs on `name` ('win32', 'darwin', 'linux')."""
    monkeypatch.setattr(platforms, "sys", SimpleNamespace(platform=name))


def fake_home(monkeypatch, path: Path) -> None:
    """Point Path.home() at `path` (HOME on POSIX, USERPROFILE on Windows)."""
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))


@pytest.fixture
def windows(monkeypatch, tmp_path):
    """Pretend to be Windows with Program Files and LOCALAPPDATA under tmp_path."""
    set_platform(monkeypatch, "win32")
    pf = tmp_path / "Program Files"
    pf.mkdir()
    monkeypatch.setenv("ProgramFiles", str(pf))
    for var in ("ProgramW6432", "ProgramFiles(x86)", "CHOIRSPLIT_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    return tmp_path


@pytest.fixture
def macos(monkeypatch, tmp_path):
    """Pretend to be macOS with the home directory under tmp_path."""
    set_platform(monkeypatch, "darwin")
    fake_home(monkeypatch, tmp_path / "home")
    monkeypatch.delenv("CHOIRSPLIT_HOME", raising=False)
    return tmp_path


@pytest.fixture
def no_engine_on_path(monkeypatch, tmp_path):
    for var in ("CHOIRSPLIT_OMR_CMD", "CHOIRSPLIT_AUDIVERIS", "TESSDATA_PREFIX"):
        monkeypatch.delenv(var, raising=False)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def touch_exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)  # Windows treats every file as executable
    return path


# --------------------------------------------------------------------------- directories


def test_windows_dirs(windows):
    local = windows / "AppData" / "Local"
    assert platforms.data_dir() == local / "choirsplit"
    assert platforms.cache_dir() == local / "choirsplit" / "Cache"
    assert platforms.program_dirs() == [windows / "Program Files", local / "Programs"]


def test_windows_program_dirs_deduplicated(windows, monkeypatch):
    monkeypatch.setenv("ProgramW6432", str(windows / "Program Files"))
    monkeypatch.setenv("ProgramFiles(x86)", str(windows / "Program Files (x86)"))
    assert platforms.program_dirs()[:2] == [windows / "Program Files", windows / "Program Files (x86)"]


def test_windows_without_localappdata_uses_home(windows, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA")
    assert platforms.data_dir() == Path.home() / "AppData" / "Local" / "choirsplit"


def test_macos_dirs(macos):
    home = macos / "home"
    assert platforms.data_dir() == home / "Library" / "Application Support" / "choirsplit"
    assert platforms.cache_dir() == home / "Library" / "Caches" / "choirsplit"
    assert platforms.program_dirs() == [Path("/Applications"), home / "Applications"]


def test_linux_dirs_unchanged(monkeypatch, tmp_path):
    set_platform(monkeypatch, "linux")
    fake_home(monkeypatch, tmp_path)
    monkeypatch.delenv("CHOIRSPLIT_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert platforms.data_dir() == tmp_path / ".local" / "share" / "choirsplit"
    assert platforms.cache_dir() == tmp_path / ".cache" / "choirsplit"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert platforms.cache_dir() == tmp_path / "xdg" / "choirsplit"
    assert platforms.program_dirs() == []


@pytest.mark.parametrize("plat", ["linux", "darwin", "win32"])
def test_choirsplit_home_overrides_everywhere(monkeypatch, tmp_path, plat):
    set_platform(monkeypatch, plat)
    monkeypatch.setenv("CHOIRSPLIT_HOME", str(tmp_path / "mine"))
    assert platforms.data_dir() == tmp_path / "mine"
    assert omr.data_home() == tmp_path / "mine"


def test_omr_cache_follows_platform(windows, monkeypatch):
    monkeypatch.delenv("CHOIRSPLIT_OMR_CACHE", raising=False)
    assert omr._cache_root() == windows / "AppData" / "Local" / "choirsplit" / "Cache" / "omr"


# --------------------------------------------------------------------------- Audiveris


def test_audiveris_found_in_program_files(windows, no_engine_on_path, monkeypatch):
    root = windows / "Program Files" / "Audiveris"
    exe = touch_exe(root / "Audiveris.exe")
    (root / "app").mkdir()
    (root / "app" / "Audiveris.cfg").write_text(CFG)
    monkeypatch.setattr(omr, "SYSTEM_LOCATIONS", omr._system_locations())
    eng = omr.find_engine()
    assert eng.argv == [str(exe)]
    assert eng.version == "5.11.0"


def test_audiveris_found_in_user_applications_on_macos(macos, no_engine_on_path, monkeypatch):
    bundle = macos / "home" / "Applications" / "Audiveris.app"
    exe = touch_exe(bundle / "Contents" / "MacOS" / "Audiveris")
    (bundle / "Contents" / "app").mkdir()
    (bundle / "Contents" / "app" / "Audiveris.cfg").write_text(CFG)
    monkeypatch.setattr(omr, "SYSTEM_LOCATIONS", omr._system_locations())
    eng = omr.find_engine()
    assert eng.argv == [str(exe)]
    assert eng.version == "5.11.0"


@pytest.mark.parametrize("given", ["bundle", "exe"])
def test_audiveris_env_accepts_app_bundle_or_exe(macos, no_engine_on_path, monkeypatch, given):
    bundle = macos / "Audiveris.app"
    exe = touch_exe(bundle / "Contents" / "MacOS" / "Audiveris")
    monkeypatch.setenv("CHOIRSPLIT_AUDIVERIS", str(bundle if given == "bundle" else exe))
    assert omr.find_engine().argv == [str(exe)]


def test_audiveris_in_choirsplit_data_dir_on_windows(windows, no_engine_on_path, monkeypatch):
    exe = touch_exe(windows / "AppData" / "Local" / "choirsplit" / "audiveris" / "Audiveris.exe")
    monkeypatch.setattr(omr, "SYSTEM_LOCATIONS", [])
    assert omr.find_engine().argv == [str(exe)]


def test_system_locations_per_platform(monkeypatch, tmp_path):
    set_platform(monkeypatch, "linux")
    assert omr._system_locations() == [Path("/opt/audiveris")]
    set_platform(monkeypatch, "darwin")
    fake_home(monkeypatch, tmp_path)
    assert omr._system_locations() == [Path("/Applications/Audiveris.app"),
                                       tmp_path / "Applications" / "Audiveris.app"]


def test_install_hint_covers_every_os():
    hint = omr.install_hint()
    for word in ("Windows", "macOS", "Linux", ".msi", ".dmg", "scripts/install_omr.sh",
                 "CHOIRSPLIT_AUDIVERIS", "CHOIRSPLIT_OMR_CMD", "MusicXML"):
        assert word in hint


# --------------------------------------------------------------------------- command splitting


def test_split_command_keeps_windows_paths(monkeypatch):
    set_platform(monkeypatch, "win32")
    cmd = r'"C:\Program Files\oemer\oemer.exe" -o {outdir} C:\scores\{image}'
    assert omr.split_command(cmd) == [r"C:\Program Files\oemer\oemer.exe", "-o", "{outdir}",
                                      r"C:\scores\{image}"]


def test_split_command_posix_unchanged(monkeypatch):
    set_platform(monkeypatch, "linux")
    assert omr.split_command("oemer -o '{outdir}' my\\ dir/{image}") == [
        "oemer", "-o", "{outdir}", "my dir/{image}"]


def test_custom_command_with_windows_path(monkeypatch):
    set_platform(monkeypatch, "win32")
    monkeypatch.setenv("CHOIRSPLIT_OMR_CMD", r'C:\tools\omr.exe {input} {outdir}')
    assert omr.find_engine().argv == [r"C:\tools\omr.exe", "{input}", "{outdir}"]


# --------------------------------------------------------------------------- MuseScore


@pytest.fixture
def no_musescore_on_path(monkeypatch):
    monkeypatch.delenv("CHOIRSPLIT_MUSESCORE", raising=False)
    monkeypatch.setattr(mscx_utils.shutil, "which", lambda name, *a, **k: None)


@pytest.mark.parametrize("version", [4, 3])
def test_musescore_found_in_program_files(windows, no_musescore_on_path, version):
    exe = touch_exe(windows / "Program Files" / f"MuseScore {version}" / "bin" / f"MuseScore{version}.exe")
    assert mscx_utils.find_musescore_cli() == [str(exe)]


def test_musescore_4_preferred_over_3(windows, no_musescore_on_path):
    touch_exe(windows / "Program Files" / "MuseScore 3" / "bin" / "MuseScore3.exe")
    ms4 = touch_exe(windows / "Program Files" / "MuseScore 4" / "bin" / "MuseScore4.exe")
    assert mscx_utils.find_musescore_cli() == [str(ms4)]


def test_musescore_found_in_applications_on_macos(macos, no_musescore_on_path):
    exe = touch_exe(macos / "home" / "Applications" / "MuseScore 4.app" / "Contents" / "MacOS" / "mscore")
    assert mscx_utils.find_musescore_cli() == [str(exe)]


def test_musescore_not_found_on_windows(windows, no_musescore_on_path):
    assert mscx_utils.find_musescore_cli() is None


@pytest.mark.parametrize("plat, offscreen", [("linux", True), ("win32", False), ("darwin", False)])
def test_offscreen_qt_only_on_linux(monkeypatch, plat, offscreen):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs["env"])
        raise OSError("not really running MuseScore")

    set_platform(monkeypatch, plat)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(musescore, "find_musescore_cli", lambda: ["mscore"])
    monkeypatch.setattr(musescore.subprocess, "run", fake_run)
    with pytest.raises(ScoreLoadError):
        musescore.load_with_cli(FIXTURE)
    assert (seen.get("QT_QPA_PLATFORM") == "offscreen") is offscreen


# --------------------------------------------------------------------------- console output


def test_cli_output_survives_a_legacy_code_page(monkeypatch):
    """A Windows console or pipe in cp1252 can't show 'Ł' or '♫'; printing must not crash."""
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    rc = cli.main([str(FIXTURE), "--list", "--title", "Łódź ♫"])
    out.flush()
    assert rc == 0
    assert raw.getvalue().decode("cp1252").startswith("?ód? ? (")


def test_omr_cache_stamp_is_utf8(tmp_path, monkeypatch):
    src = tmp_path / "x.musicxml"
    src.write_text("<score-partwise/>", encoding="utf-8")
    entry = tmp_path / "cache" / "abc"
    omr._store(entry, [src], "Zdrowaś Maryjo.pdf\ntag\n")
    assert (entry / "done").read_bytes().decode("utf-8").startswith("Zdrowaś")

