"""`choir-split-ui --install-shortcut`: start the desktop app from the Start menu (Windows),
~/Applications (macOS) or the desktop's app menu (Linux), without a terminal.

A program started that way doesn't see variables set in shell start-up files, so the
shortcut carries this terminal's PATH and CHOIRSPLIT_* settings (where Audiveris is,
for example) along on macOS and Linux. Windows keeps them in the user's environment.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from . import platforms

NAME = "Choir Split"
ICON = Path(__file__).with_name("icon.png")


def carried_env() -> dict[str, str]:
    """The settings a shortcut must pass on: PATH (for Homebrew's or a custom Audiveris,
    pdfinfo) and every CHOIRSPLIT_* variable."""
    env = {k: v for k, v in os.environ.items() if k.startswith("CHOIRSPLIT_")}
    if os.environ.get("PATH"):
        env["PATH"] = os.environ["PATH"]
    return env


def _python(windowed: bool) -> str:
    exe = Path(sys.executable)
    if windowed and platforms.is_windows():
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return str(exe)


def _desktop_quote(arg: str) -> str:
    """Quote an Exec argument the way the freedesktop Desktop Entry spec wants."""
    if not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`='):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'.replace("%", "%%")


def desktop_entry(python: str, env: dict[str, str]) -> str:
    argv = (["env", *(f"{k}={v}" for k, v in sorted(env.items()))] if env else []) + \
        [python, "-m", "choirsplit.gui"]
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={NAME}",
        "Comment=Turn a choir score into practice tracks for every voice",
        "Exec=" + " ".join(_desktop_quote(a) for a in argv) + " %f",
        f"Icon={ICON}",
        "Terminal=false",
        "Categories=AudioVideo;Audio;Music;Education;",
        "StartupWMClass=choirsplit",
        "",
    ])


def mac_launcher(python: str, env: dict[str, str]) -> str:
    exports = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in sorted(env.items()))
    return f'#!/bin/sh\n{exports}exec {shlex.quote(python)} -m choirsplit.gui "$@"\n'


def install_shortcut(home: Path | None = None) -> Path:
    """Add the shortcut for this system and return where it went."""
    home = home or Path.home()
    python = _python(windowed=True)
    if platforms.is_windows():
        programs = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") \
            / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        programs.mkdir(parents=True, exist_ok=True)
        link = programs / f"{NAME}.lnk"
        script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CS_LINK); "
                  "$s.TargetPath = $env:CS_TARGET; $s.Arguments = '-m choirsplit.gui'; "
                  "$s.WorkingDirectory = $env:USERPROFILE; $s.Description = $env:CS_NAME; $s.Save()")
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "CS_LINK": str(link), "CS_TARGET": python, "CS_NAME": NAME},
            capture_output=True, text=True, timeout=60, **platforms.no_console())
        if proc.returncode != 0 or not link.exists():
            raise RuntimeError(f"PowerShell could not create the shortcut: {proc.stderr.strip()}")
        return link
    env = carried_env()
    if platforms.is_macos():
        app = home / "Applications" / f"{NAME}.app"
        contents = app / "Contents"
        (contents / "MacOS").mkdir(parents=True, exist_ok=True)
        launcher = contents / "MacOS" / "choirsplit"
        launcher.write_text(mac_launcher(python, env), encoding="utf-8")
        launcher.chmod(0o755)
        with open(contents / "Info.plist", "wb") as fh:
            plistlib.dump({
                "CFBundleName": NAME, "CFBundleDisplayName": NAME,
                "CFBundleIdentifier": "org.choirsplit.ui", "CFBundleExecutable": "choirsplit",
                "CFBundlePackageType": "APPL", "CFBundleVersion": "1",
                "LSMinimumSystemVersion": "10.13", "NSHighResolutionCapable": True,
            }, fh)
        return app
    apps = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / "choirsplit.desktop"
    entry.write_text(desktop_entry(python, env), encoding="utf-8")
    entry.chmod(0o755)
    return entry
