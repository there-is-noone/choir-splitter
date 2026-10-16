"""Where things live on Linux, macOS and Windows: choirsplit's own data and cache
directories, and the folders installers put programs (Audiveris, MuseScore) in."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def data_dir() -> Path:
    """choirsplit's data directory (the user-local Audiveris install, OCR data).

    ``CHOIRSPLIT_HOME`` overrides it. Defaults: ``~/.local/share/choirsplit`` (Linux),
    ``~/Library/Application Support/choirsplit`` (macOS), ``%LOCALAPPDATA%\\choirsplit`` (Windows).
    """
    env = os.environ.get("CHOIRSPLIT_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    if is_windows():
        return _local_appdata() / "choirsplit"
    if is_macos():
        return Path.home() / "Library" / "Application Support" / "choirsplit"
    return Path.home() / ".local" / "share" / "choirsplit"


def cache_dir() -> Path:
    """choirsplit's cache directory: ``$XDG_CACHE_HOME/choirsplit`` or ``~/.cache/choirsplit``
    (Linux), ``~/Library/Caches/choirsplit`` (macOS), ``%LOCALAPPDATA%\\choirsplit\\Cache`` (Windows)."""
    if is_windows():
        return _local_appdata() / "choirsplit" / "Cache"
    if is_macos():
        return Path.home() / "Library" / "Caches" / "choirsplit"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "choirsplit"


def program_dirs() -> list[Path]:
    """Folders installers put applications in (empty on Linux, where PATH is enough)."""
    if is_windows():
        dirs = [os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"),
                os.environ.get("ProgramFiles(x86)")]
        found = [Path(d) for d in dict.fromkeys(d for d in dirs if d)]
        return found + [_local_appdata() / "Programs"]
    if is_macos():
        return [Path("/Applications"), Path.home() / "Applications"]
    return []
