"""Desktop window: drop a score, pick the options, make the tracks (`choir-split-ui`).

Tkinter, so it runs wherever Python does (Windows, macOS, Linux). The optional `gui` extra
adds a modern theme (sv-ttk, with darkdetect to follow the system's light or dark mode)
and dragging files in (tkinterdnd2). The tracks are made in this process, on one worker
thread; the window only reads the worker's events, as Tk must stay on its own thread.

The logic (the window's settings as pipeline Options, dropped files, the worker) is kept
free of tkinter so it can be tested without a display.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import jobs, platforms

APP_NAME = "Choir Split"
BITRATES = ("128k", "192k", "256k", "320k")
# "PDF reading detail": the resolution scanned pages are read at (None: Audiveris's 300).
READING_DETAIL = {"Standard": None, "High (400 dpi)": 400, "Very high (600 dpi)": 600}
MODE_HINTS = {
    "solo": "Solo: each voice on its own.",
    "practice": "Practice: your voice loud, the others quiet.",
    "both": "Both: a solo and a practice track for every voice.",
}
# Settings remembered between sessions (not the score, its folder or its voices).
REMEMBERED = (
    "mode",
    "others_volume",
    "speed",
    "full_mix",
    "accompaniment",
    "fmt",
    "bitrate",
    "split_voices",
    "split_chords",
    "export_musicxml",
    "omr_dpi",
)


@dataclass
class GuiState:
    """Everything the window lets the user set, in the window's own terms."""

    score: str = ""
    output: str = ""  # "" = <score name>_tracks beside the score
    mode: str = "both"
    others_volume: int = 30  # percent
    speed: int = 100  # percent
    full_mix: bool = True
    accompaniment: bool = True
    voices: list[str] = field(
        default_factory=list
    )  # found by "Find voices" ([]: not looked)
    chosen: list[str] = field(default_factory=list)  # the ticked ones
    title: str = ""
    transpose: int = 0
    fixed_tempo: bool = False
    bpm: float = 100.0
    fmt: str = "mp3"
    bitrate: str = "192k"
    split_voices: bool = True
    split_chords: bool = True
    export_musicxml: bool = False
    omr_dpi: int | None = None


def to_options(state: GuiState, need_voices: bool = True):
    """(score, output folder, pipeline.Options) for `state`; ValueError if it can't work.
    `need_voices`: also refuse a voice list with nothing ticked (not when listing them)."""
    from .cli import output_paths
    from .pipeline import Options

    if not state.score:
        raise ValueError("choose a score first")
    score = Path(state.score)
    if need_voices and state.voices and not state.chosen:
        raise ValueError("pick at least one voice")
    only = [] if set(state.chosen) >= set(state.voices) else list(state.chosen)
    out_dir, export = output_paths(
        score,
        Path(state.output) if state.output else None,
        Path() if state.export_musicxml else None,
    )
    opts = Options(
        mode=state.mode,
        full_mix=state.full_mix,
        fmt=state.fmt,
        bitrate=state.bitrate,
        bpm=float(state.bpm) if state.fixed_tempo else None,
        tempo_scale=state.speed / 100,
        transpose=int(state.transpose),
        others_gain=state.others_volume / 100,
        include_accompaniment=state.accompaniment,
        split_voices=state.split_voices,
        split_chords=state.split_chords,
        only=only,
        title=state.title.strip() or None,
        export_musicxml=export,
        omr_dpi=state.omr_dpi,
    )
    opts.validate()
    return score, out_dir, opts


def supported(path: Path) -> bool:
    from .loaders import supported_suffixes

    return path.suffix.lower() in supported_suffixes()


def dropped_files(items: list[str]) -> list[Path]:
    """The score files among dropped items (Tk's split list of paths; some desktops send
    file:// URIs). Web links, folders and other files are left out."""
    found = []
    for item in items:
        if item.startswith("file:"):
            item = unquote(urlparse(item).path)
            if platforms.is_windows() and item[:1] == "/" and item[2:3] == ":":
                item = item[1:]  # /C:/Users/... -> C:/Users/...
        p = Path(item)
        if p.is_file() and supported(p):
            found.append(p)
    return found


def log_path() -> Path:
    return platforms.data_dir() / "gui.log"


def ensure_streams() -> None:
    """Under pythonw (Windows, no console) sys.stdout and sys.stderr are None, and music21
    writes warnings to sys.stderr directly; send both to the log file instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log_path().parent.mkdir(parents=True, exist_ok=True)
        sink = open(log_path(), "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        sink = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


def load_settings(state: GuiState) -> None:
    try:
        saved = json.loads(
            (platforms.data_dir() / "gui.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return
    if not isinstance(saved, dict):
        return
    types = {f.name: type(getattr(state, f.name)) for f in fields(state)}
    for name in REMEMBERED:
        value = saved.get(name)
        if name == "omr_dpi" and value in READING_DETAIL.values():
            state.omr_dpi = value
        elif value is not None and isinstance(value, types[name]):
            setattr(state, name, value)


def save_settings(state: GuiState) -> None:
    try:
        target = platforms.data_dir() / "gui.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {k: v for k, v in asdict(state).items() if k in REMEMBERED}, indent=1
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


class Worker:
    """Runs one job at a time on a background thread. The window reads `events`:
    ("stage", text), ("voices", title, seconds, names), ("check", recognised, issues),
    ("done", folder, files, suspect bars, recognised), ("error", text), ("cancelled",),
    ("engines", PDF reading available)."""

    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        self.job: jobs.Job | None = None
        self.thread: threading.Thread | None = None
        self._loaded = (
            None  # (key, ScoreTracks): a PDF is read once for Find, Check and Make
        )

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, kind: str, score: Path, out_dir: Path, opts) -> None:
        if self.busy:
            raise RuntimeError("a job is already running")
        self.job = jobs.Job(progress=lambda text: self.events.put(("stage", text)))
        self.thread = threading.Thread(
            target=self._run,
            args=(kind, score, out_dir, opts, self.job),
            name="choirsplit-job",
            daemon=True,
        )
        self.thread.start()

    def cancel(self) -> None:
        if self.job is not None:
            self.job.cancel.set()

    def find_engines(self) -> None:
        """Look for Audiveris in the background (a flatpak check can take seconds)."""

        def look() -> None:
            try:
                from .omr import find_engine

                self.events.put(("engines", find_engine() is not None))
            except Exception:
                self.events.put(("engines", False))

        threading.Thread(target=look, name="choirsplit-engines", daemon=True).start()

    def _load(self, score: Path, opts):
        from .pipeline import load_tracks

        try:
            key = (
                str(score.resolve()),
                score.stat().st_mtime_ns,
                opts.load_key(),
                opts.export_musicxml,
            )
        except OSError:  # gone or unreadable: load_tracks says so in its own words
            return load_tracks(score, opts)
        if self._loaded is not None and self._loaded[0] == key:
            return self._loaded[1]
        st = load_tracks(score, opts)
        self._loaded = (key, st)
        return st

    def _run(self, kind: str, score: Path, out_dir: Path, opts, job: jobs.Job) -> None:
        from .audio_io import EncoderError, require_ffmpeg
        from .loaders import ScoreLoadError
        from .pipeline import process_tracks

        put = self.events.put
        with jobs.running(job):
            try:
                if kind == "make" and opts.fmt == "mp3":
                    require_ffmpeg()  # fail now, not after a slow PDF reading
                st = self._load(score, opts)
                if kind == "voices":
                    put(("voices", st.title, st.duration, [t.name for t in st.voices]))
                elif kind == "check":
                    put(("check", st.recognised, list(st.issues)))
                else:
                    written = process_tracks(st, out_dir, opts)
                    suspects = len({i.bar for i in st.issues if i.kind == "suspect"})
                    put(("done", out_dir, written, suspects, st.recognised))
            except jobs.Cancelled:
                put(("cancelled",))
            except (ScoreLoadError, EncoderError, ValueError, OSError) as exc:
                put(("error", str(exc)))
            except MemoryError:
                put(
                    (
                        "error",
                        "not enough memory for this score at these settings; "
                        "try a faster speed or fewer tracks",
                    )
                )
            except BaseException as exc:  # never let the worker die silently
                detail = traceback.format_exc()
                try:
                    log_path().parent.mkdir(parents=True, exist_ok=True)
                    with open(log_path(), "a", encoding="utf-8") as fh:
                        fh.write(detail)
                except OSError:
                    pass
                put(
                    (
                        "error",
                        f"something went wrong ({type(exc).__name__}: {exc}); "
                        f"details are in {log_path()}",
                    )
                )


# --------------------------------------------------------------------------- start-up


def tk_hint() -> str:
    """How to get Tk (tkinter) for this Python."""
    v = f"{sys.version_info.major}.{sys.version_info.minor}"
    if platforms.is_windows():
        return (
            'Run the Python installer again, choose Modify, and tick "tcl/tk and IDLE".'
        )
    if platforms.is_macos():
        return (
            f"With Homebrew's Python: brew install python-tk@{v}\n"
            "Or install Python from python.org, which includes it."
        )
    return (
        "Install your system's Tk package for Python, e.g.\n"
        "  Fedora: sudo dnf install python3-tkinter\n"
        "  Ubuntu / Debian: sudo apt install python3-tk\n"
        "  Arch: sudo pacman -S tk"
    )


def _tell(message: str, error: bool = True) -> None:
    """Show a start-up message, also when there's no console (pythonw on Windows)."""
    print(message, file=sys.stderr if error else sys.stdout)
    if platforms.is_windows():
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, message, APP_NAME, 0x10 if error else 0x40
            )
        except Exception:
            pass


def _dpi_aware() -> None:
    """Sharp text on Windows' scaled displays (Tk 8.6 handles system-wide DPI only)."""

    if platforms.is_linux():
        os.environ["GDK_BACKEND"] = "x11"
        return
    if not platforms.is_windows():
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("choirsplit.ui")
    except Exception:
        pass


def dnd_allowed(tk_version: float) -> bool:
    """Whether to load drag and drop. tkdnd crashes the whole process (an Xlib abort,
    which can't be caught) on Linux with Tk 9, so it's off there. CHOIRSPLIT_DND=0/1
    overrides."""
    env = os.environ.get("CHOIRSPLIT_DND", "").strip().lower()
    if env in ("0", "1", "no", "yes", "off", "on", "false", "true"):
        return env in ("1", "yes", "on", "true")
    return not (platforms.is_linux() and tk_version >= 9)


def main(argv: list[str] | None = None) -> int:
    ensure_streams()
    args = sys.argv[1:] if argv is None else argv
    if "--install-shortcut" in args:
        from .shortcut import install_shortcut

        try:
            _tell(f"added {install_shortcut()}", error=False)
        except (OSError, RuntimeError) as exc:
            _tell(f"could not add the shortcut: {exc}")
            return 1
        return 0
    try:
        import tkinter
    except ImportError:
        _tell(f"{APP_NAME} needs Tk (tkinter), which this Python lacks.\n{tk_hint()}")
        return 1
    _dpi_aware()
    try:
        root = tkinter.Tk(className="choirsplit")

        # Set tk scaling from Xft.dpi (Wayland/XWayland) so text is crisp
        # at the monitor's actual DPI.  Fall back to 96 if the value is
        # unusable.  This must be done once, before any widgets are built.
        try:
            import subprocess
            dpi: float | None = None
            try:
                out = subprocess.check_output(
                    ["xrdb", "-query"], timeout=2, stderr=subprocess.DEVNULL
                ).decode(errors="replace")
                for line in out.splitlines():
                    if line.startswith("Xft.dpi:"):
                        dpi = float(line.split(":")[1].strip())
                        break
            except Exception:
                pass
            if dpi is None or dpi < 96:
                try:
                    dpi = root.winfo_fpixels("1i")
                except Exception:
                    dpi = 96.0
            if dpi < 96:
                dpi = 96.0
            root.tk.call("tk", "scaling", dpi / 72)
        except Exception:
            pass

    except tkinter.TclError as exc:
        _tell(
            f"{APP_NAME} could not open a window: {exc}\n"
            "(No display? It needs a desktop session.)\n" + tk_hint()
        )
        return 1
    from ._window import App

    App(root, initial=next((a for a in args if not a.startswith("-")), None))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
