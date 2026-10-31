"""The Tk window of the desktop app (see gui.py, which keeps the logic testable)."""

from __future__ import annotations

import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import platforms
from .gui import (APP_NAME, BITRATES, MODE_HINTS, READING_DETAIL, GuiState, Worker, dnd_allowed,
                  dropped_files, load_settings, save_settings, supported, to_options)
from .loaders import OMR_SUFFIXES, supported_suffixes

PAD = 24  # outer padding
GAP = 16  # between groups
SMALL = 8  # between a label and its control
VOICE_COLUMNS = 3
LINUX_FONTS = ("cantarell", "adwaita sans", "inter", "noto sans", "ubuntu", "dejavu sans", "liberation sans")


def _minutes(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class App:
    def __init__(self, root: tk.Tk, initial: str | None = None) -> None:
        self.root = root
        self.worker = Worker()
        self.state = GuiState()
        load_settings(self.state)
        self.started = 0.0
        self.closing = False
        self.voice_vars: dict[str, tk.BooleanVar] = {}

        root.title(APP_NAME)
        try:
            self.icon = tk.PhotoImage(file=str(Path(__file__).with_name("icon.png")))
            root.iconphoto(True, self.icon)
        except tk.TclError:
            pass
        root.minsize(560, 0)
        self.themed = self._theme()
        self._fonts()
        self._vars()
        self._build()
        self.dnd = self._enable_dnd()
        self._show_score()
        self._sync()
        root.protocol("WM_DELETE_WINDOW", self._close)
        try:  # macOS: Cmd+Q goes through the same check as closing the window
            root.createcommand("::tk::mac::Quit", self._close)
        except tk.TclError:
            pass
        root.bind("<Return>", lambda e: self._start("make"))
        root.bind("<Escape>", lambda e: self.worker.cancel())
        self.worker.find_engines()
        root.after(100, self._poll)
        if initial:
            self._set_score(Path(initial))

    # ----------------------------------------------------------------- look

    def _theme(self) -> bool:
        """sv-ttk (Windows 11 style, light or dark like the system) where installed, except
        on macOS, whose native theme already follows dark mode."""
        style = ttk.Style(self.root)
        if not platforms.is_macos():
            try:
                import sv_ttk
                try:
                    import darkdetect
                    dark = (darkdetect.theme() or "").lower() == "dark"
                except Exception:
                    dark = False
                sv_ttk.set_theme("dark" if dark else "light", self.root)
                self.dark = dark
                return True
            except Exception:
                pass
        names = style.theme_names()
        for name in ("aqua", "vista", "clam"):
            if name in names:
                style.theme_use(name)
                break
        self.dark = False
        return False

    def _fonts(self) -> None:
        base = tkfont.nametofont("TkDefaultFont")
        if platforms.is_linux():
            # Some Tk builds (uv's own Pythons) see only X's core fonts and default to the
            # bitmap "fixed": use the best sans-serif family this Tk can find instead.
            families = {f.lower(): f for f in tkfont.families(self.root)}
            good = next((families[f] for f in LINUX_FONTS if f in families), None)
            if good and base.actual("family").lower() not in LINUX_FONTS:
                for name in tkfont.names(self.root):
                    font = tkfont.nametofont(name)
                    if name.startswith(("Tk", "SunValley")) and "Fixed" not in name:
                        font.configure(family=good)
        if not self.themed and not platforms.is_macos() and 0 < base.actual("size") < 10:
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                tkfont.nametofont(name).configure(size=10)
        style = ttk.Style(self.root)
        # Derive from the font labels really use: sv-ttk has its own, sized in pixels (so a
        # point size from Tk's default would come out far larger on a high-DPI screen).
        body = tkfont.nametofont("SunValleyBodyFont" if self.themed else "TkDefaultFont")
        family, size = body.actual("family"), int(body.cget("size"))

        def bigger(points: int) -> int:  # negative sizes are pixels: grow away from zero
            return size - round(points * 1.33) if size < 0 else size + points

        self.title_font = tkfont.Font(family=family, size=bigger(9), weight="bold")
        self.bold_font = tkfont.Font(family=family, size=bigger(1), weight="bold")
        self.caption_font = tkfont.Font(family=family, size=bigger(0))
        muted = "#9a9a9a" if self.dark else "#6b6b6b"
        style.configure("Title.TLabel", font=self.title_font)
        style.configure("Name.TLabel", font=self.bold_font)
        style.configure("Caption.TLabel", font=self.caption_font, foreground=muted)
        style.configure("Error.TLabel", foreground="#ff6b6b" if self.dark else "#c42b1c")
        style.configure("Ok.TLabel", foreground="#6ccb5f" if self.dark else "#0f7b0f")
        if not self.themed:
            style.configure("Card.TFrame", relief="solid", borderwidth=1)
        self.switch = "Switch.TCheckbutton" if self.themed else "TCheckbutton"
        self.segment = "Toggle.TButton" if self.themed else "Toolbutton"

    def _vars(self) -> None:
        s = self.state
        self.v_mode = tk.StringVar(value=s.mode)
        self.v_others = tk.DoubleVar(value=s.others_volume)
        self.v_speed = tk.DoubleVar(value=s.speed)
        self.v_full_mix = tk.BooleanVar(value=s.full_mix)
        self.v_acc = tk.BooleanVar(value=s.accompaniment)
        self.v_title = tk.StringVar(value=s.title)
        self.v_transpose = tk.StringVar(value=str(s.transpose))
        self.v_fixed = tk.BooleanVar(value=s.fixed_tempo)
        self.v_bpm = tk.StringVar(value=f"{s.bpm:g}")
        self.v_fmt = tk.StringVar(value=s.fmt)
        self.v_bitrate = tk.StringVar(value=s.bitrate)
        self.v_split_voices = tk.BooleanVar(value=s.split_voices)
        self.v_split_chords = tk.BooleanVar(value=s.split_chords)
        self.v_export = tk.BooleanVar(value=s.export_musicxml)
        self.v_detail = tk.StringVar(value=next(k for k, d in READING_DETAIL.items() if d == s.omr_dpi))
        self.v_output = tk.StringVar()
        self.v_more = tk.BooleanVar(value=False)
        for var in (self.v_mode, self.v_fmt, self.v_fixed):
            var.trace_add("write", lambda *a: self._sync())
        # Voice names depend on how staves and chords are split and whether piano counts.
        for var in (self.v_split_voices, self.v_split_chords, self.v_acc):
            var.trace_add("write", lambda *a: self._clear_voices())

    # ----------------------------------------------------------------- layout

    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        main = ttk.Frame(root, padding=(PAD, PAD, PAD, SMALL))
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(1, weight=1)
        self.main = main
        row = 0

        ttk.Label(main, text=APP_NAME, style="Title.TLabel").grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        ttk.Label(main, text="Turn a score into practice tracks for every voice.",
                  style="Caption.TLabel").grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, GAP))
        row += 1

        # Drop zone
        zone = ttk.Frame(main, style="Card.TFrame", padding=GAP)
        zone.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, GAP))
        zone.columnconfigure(0, weight=1)
        self.zone = zone
        self.zone_title = ttk.Label(zone, style="Name.TLabel", anchor="center")
        self.zone_title.grid(row=0, column=0, sticky="ew")
        self.zone_caption = ttk.Label(zone, style="Caption.TLabel", anchor="center", justify="center")
        self.zone_caption.grid(row=1, column=0, sticky="ew", pady=(4, SMALL))
        buttons = ttk.Frame(zone)
        buttons.grid(row=2, column=0)
        self.choose_button = ttk.Button(buttons, text="Choose file…", command=self._browse)
        self.choose_button.grid(row=0, column=0)
        self.clear_button = ttk.Button(buttons, text="Clear", command=self._clear_score)
        self.clear_button.grid(row=0, column=1, padx=(SMALL, 0))
        self.engine_caption = ttk.Label(zone, style="Caption.TLabel", anchor="center")
        self.engine_caption.grid(row=3, column=0, sticky="ew", pady=(SMALL, 0))
        self.engine_caption.grid_remove()  # shown only when PDF reading needs Audiveris
        for w in (zone, self.zone_title, self.zone_caption, self.engine_caption):
            w.bind("<Button-1>", lambda e: self._browse())
        row += 1

        # Tracks
        ttk.Label(main, text="Tracks").grid(row=row, column=0, sticky="w")
        seg = ttk.Frame(main)
        seg.grid(row=row, column=1, sticky="ew")
        for i, (mode, label) in enumerate((("solo", "Solo"), ("practice", "Practice"), ("both", "Both"))):
            seg.columnconfigure(i, weight=1, uniform="seg")
            ttk.Radiobutton(seg, text=label, value=mode, variable=self.v_mode,
                            style=self.segment).grid(row=0, column=i, sticky="ew")
        row += 1
        self.mode_hint = ttk.Label(main, style="Caption.TLabel")
        self.mode_hint.grid(row=row, column=1, sticky="w", pady=(4, GAP))
        row += 1

        self.others_scale = self._slider(main, row, "Other voices", self.v_others, 0, 100, 5)
        row += 1
        self._slider(main, row, "Speed", self.v_speed, 50, 150, 5)
        row += 1

        ttk.Checkbutton(main, text="Add a track with all voices", variable=self.v_full_mix,
                        style=self.switch).grid(row=row, column=0, columnspan=2, sticky="w", pady=(SMALL, 4))
        row += 1
        ttk.Checkbutton(main, text="Include piano / organ", variable=self.v_acc,
                        style=self.switch).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, GAP))
        row += 1

        # Voices
        ttk.Label(main, text="Voices").grid(row=row, column=0, sticky="nw")
        vrow = ttk.Frame(main)
        vrow.grid(row=row, column=1, sticky="ew")
        vrow.columnconfigure(0, weight=1)
        self.voices_caption = ttk.Label(vrow, text="All voices", style="Caption.TLabel")
        self.voices_caption.grid(row=0, column=0, sticky="w")
        self.find_button = ttk.Button(vrow, text="Find voices", command=lambda: self._start("voices"))
        self.find_button.grid(row=0, column=1, sticky="e")
        row += 1
        self.voice_box = ttk.Frame(main)
        self.voice_box.grid(row=row, column=1, sticky="ew", pady=(4, 0))
        row += 1

        # Save to
        ttk.Label(main, text="Save to").grid(row=row, column=0, sticky="w", pady=(GAP, 0))
        out = ttk.Frame(main)
        out.grid(row=row, column=1, sticky="ew", pady=(GAP, 0))
        out.columnconfigure(0, weight=1)
        self.output_entry = ttk.Entry(out, textvariable=self.v_output, state="readonly")
        self.output_entry.grid(row=0, column=0, sticky="ew")
        self.output_button = ttk.Button(out, text="Change…", command=self._choose_output)
        self.output_button.grid(row=0, column=1, padx=(SMALL, 0))
        row += 1

        # More options
        self.more_toggle = ttk.Checkbutton(main, text="More options…", variable=self.v_more,
                                           style="Toolbutton", command=self._toggle_more)
        self.more_toggle.grid(row=row, column=0, columnspan=2, sticky="w", pady=(GAP, 0))
        row += 1
        self.more = self._build_more(main)
        self.more.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(SMALL, 0))
        self.more.grid_remove()
        row += 1

        # Status
        self.status = ttk.Frame(main)
        self.status.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(GAP, 0))
        self.status.columnconfigure(0, weight=1)
        main.rowconfigure(row, weight=1)
        self.progress = ttk.Progressbar(self.status, mode="indeterminate")
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.stage = ttk.Label(self.status, style="Caption.TLabel")
        self.stage.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.cancel_button = ttk.Button(self.status, text="Cancel", command=self.worker.cancel)
        self.cancel_button.grid(row=1, column=1, sticky="e", pady=(4, 0))
        self.banner = ttk.Label(self.status, wraplength=520, justify="left")
        self.banner.grid(row=2, column=0, sticky="w", pady=(SMALL, 0))
        actions = ttk.Frame(self.status)
        actions.grid(row=2, column=1, sticky="e", pady=(SMALL, 0))
        self.open_button = ttk.Button(actions, text="Open folder", command=self._open_folder)
        self.open_button.grid(row=0, column=0)
        self.tree = ttk.Treeview(self.status, columns=("bar", "page", "voice", "problem"), show="headings", height=7)
        for col, text, width, stretch in (("bar", "Bar", 50, False), ("page", "Page", 50, False),
                                          ("voice", "Voice", 100, False),
                                          ("problem", "Problem", 300, True)):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=width, stretch=stretch, anchor="w")
        self.tree.tag_configure("group", font=self.bold_font)
        self.tree.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(SMALL, 0))
        self.status.rowconfigure(3, weight=1)
        for w in (self.progress, self.stage, self.cancel_button, self.banner, actions, self.tree,
                  self.open_button):
            w.grid_remove()
        self.actions = actions

        # Footer
        foot = ttk.Frame(root, padding=(PAD, SMALL, PAD, PAD - SMALL))
        foot.grid(row=1, column=0, sticky="ew")
        foot.columnconfigure(1, weight=1)
        ttk.Separator(foot).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, GAP - 4))
        self.check_button = ttk.Button(foot, text="Check score", command=lambda: self._start("check"))
        self.check_button.grid(row=1, column=0, sticky="w")
        self.make_button = ttk.Button(foot, text="Make tracks", style="Accent.TButton",
                                      command=lambda: self._start("make"))
        self.make_button.grid(row=1, column=2, sticky="e", ipadx=SMALL)

    def _slider(self, parent, row: int, label: str, var: tk.DoubleVar, lo: int, hi: int, step: int):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, GAP))
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=1, sticky="ew", pady=4)
        frame.columnconfigure(0, weight=1)
        value = ttk.Label(frame, width=5, anchor="e")

        def moved(*_):  # ttk.Scale doesn't snap; round to the step and show the value
            snapped = min(hi, max(lo, round(var.get() / step) * step))
            if var.get() != snapped:
                var.set(snapped)
            value.configure(text=f"{snapped:.0f} %")

        scale = ttk.Scale(frame, from_=lo, to=hi, variable=var)
        scale.grid(row=0, column=0, sticky="ew")
        value.grid(row=0, column=1, padx=(SMALL, 0))
        var.trace_add("write", moved)  # dragged, or set by "Reset to defaults"
        moved()
        return scale

    def _build_more(self, parent) -> ttk.Frame:
        more = ttk.Frame(parent)
        more.columnconfigure(1, weight=1)
        r = 0

        def label(text):
            ttk.Label(more, text=text).grid(row=r, column=0, sticky="w", pady=4, padx=(0, GAP))

        label("Song title")
        self.title_entry = ttk.Entry(more, textvariable=self.v_title)
        self.title_entry.grid(row=r, column=1, sticky="ew", pady=4)
        r += 1
        ttk.Label(more, text="Leave empty to use the score's title", style="Caption.TLabel").grid(
            row=r, column=1, sticky="w")
        r += 1
        label("Change key")
        key = ttk.Frame(more)
        key.grid(row=r, column=1, sticky="w", pady=4)
        self.transpose_box = ttk.Spinbox(key, from_=-24, to=24, increment=1, width=5,
                                         textvariable=self.v_transpose)
        self.transpose_box.grid(row=0, column=0)
        ttk.Label(key, text="semitones", style="Caption.TLabel").grid(row=0, column=1, padx=(SMALL, 0))
        r += 1
        label("Tempo")
        tempo = ttk.Frame(more)
        tempo.grid(row=r, column=1, sticky="w", pady=4)
        ttk.Checkbutton(tempo, text="Fixed", variable=self.v_fixed).grid(row=0, column=0)
        self.bpm_box = ttk.Spinbox(tempo, from_=20, to=300, increment=1, width=6, textvariable=self.v_bpm)
        self.bpm_box.grid(row=0, column=1, padx=(SMALL, 0))
        ttk.Label(tempo, text="beats per minute", style="Caption.TLabel").grid(row=0, column=2, padx=(SMALL, 0))
        r += 1
        label("File type")
        ftype = ttk.Frame(more)
        ftype.grid(row=r, column=1, sticky="w", pady=4)
        for i, (fmt, text) in enumerate((("mp3", "MP3"), ("wav", "WAV"))):
            ttk.Radiobutton(ftype, text=text, value=fmt, variable=self.v_fmt,
                            style=self.segment, width=6).grid(row=0, column=i)
        ttk.Label(ftype, text="Quality").grid(row=0, column=2, padx=(GAP, SMALL))
        self.bitrate_box = ttk.Combobox(ftype, values=BITRATES, textvariable=self.v_bitrate,
                                        state="readonly", width=6)
        self.bitrate_box.grid(row=0, column=3)
        r += 1
        for text, var in (("Split a staff that holds two voices (e.g. S + A)", self.v_split_voices),
                          ("Split two-note chords into upper and lower parts", self.v_split_chords),
                          ("Also save the score as MusicXML (to fix it in MuseScore)", self.v_export)):
            ttk.Checkbutton(more, text=text, variable=var, style=self.switch).grid(
                row=r, column=0, columnspan=2, sticky="w", pady=4)
            r += 1
        label("PDF reading detail")
        self.detail_box = ttk.Combobox(more, values=list(READING_DETAIL), textvariable=self.v_detail,
                                       state="readonly", width=18)
        self.detail_box.grid(row=r, column=1, sticky="w", pady=4)
        r += 1
        ttk.Button(more, text="Reset to defaults", command=self._reset).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(SMALL, 0))
        return more

    # ----------------------------------------------------------------- drag and drop

    def _enable_dnd(self) -> bool:
        if not dnd_allowed(tk.TkVersion):
            return False
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD
            TkinterDnD._require(self.root)
            for w in (self.zone, self.zone_title, self.zone_caption, self.engine_caption):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<DropEnter>>", self._drag_enter)
                w.dnd_bind("<<DropLeave>>", self._drag_leave)
                w.dnd_bind("<<Drop>>", self._drop)
        except (ImportError, RuntimeError, tk.TclError):
            return False
        return True

    def _drag_enter(self, event):
        if not self.worker.busy:
            self.zone_title.configure(text="Release to open")
        return event.action

    def _drag_leave(self, event):
        self._show_score()
        return event.action

    def _drop(self, event):
        files = dropped_files(list(self.root.tk.splitlist(event.data)))
        if self.worker.busy:
            pass
        elif files:
            self._set_score(files[0])
        else:
            self._show_score()
            self._message("That isn't a score file choirsplit can read.", error=True)
        return event.action

    # ----------------------------------------------------------------- score and folder

    def _browse(self) -> None:
        if self.worker.busy:
            return
        patterns = sorted(supported_suffixes())
        if platforms.is_linux():  # Tk's own dialog matches case-sensitively
            patterns += [p.upper() for p in patterns]
        start = Path(self.state.score).parent if self.state.score else Path.home()
        name = filedialog.askopenfilename(
            parent=self.root, title="Choose a score", initialdir=str(start),
            filetypes=[("Scores", tuple(f"*{p}" for p in patterns)), ("All files", "*")])
        if name:
            self._set_score(Path(name))

    def _set_score(self, path: Path) -> None:
        if not path.is_file():
            self._message(f"{path} doesn't exist.", error=True)
            return
        if not supported(path):
            self._message(f"{path.name} isn't a score file choirsplit can read.", error=True)
            return
        self.state.score = str(path)
        self.state.output = ""
        self._clear_voices()
        self._hide_results()
        self._show_score()
        self._sync()

    def _clear_score(self) -> None:
        self.state.score = ""
        self.state.output = ""
        self._clear_voices()
        self._hide_results()
        self._show_score()
        self._sync()

    def _show_score(self) -> None:
        kinds = "MusicXML, MuseScore, MIDI, PDF or photo"
        if self.state.score:
            p = Path(self.state.score)
            self.zone_title.configure(text=f"♪  {p.name}")
            self.zone_caption.configure(text=str(p.parent))
            self.choose_button.configure(text="Change…")
            self.clear_button.grid()
        else:
            self.zone_title.configure(text="Drop your score here" if self.dnd else "Choose a score to start")
            self.zone_caption.configure(text=kinds)
            self.choose_button.configure(text="Choose file…")
            self.clear_button.grid_remove()
        if self.state.score:
            try:
                score, out_dir, _ = to_options(self._read(), need_voices=False)
                self.v_output.set(str(out_dir))
            except ValueError:
                self.v_output.set("")
        else:
            self.v_output.set("")

    def _choose_output(self) -> None:
        start = self.v_output.get() or str(Path.home())
        folder = filedialog.askdirectory(parent=self.root, title="Save the tracks in",
                                         initialdir=str(Path(start).parent if not Path(start).exists() else start))
        if folder:
            self.state.output = folder
            self.v_output.set(folder)

    def _open_folder(self) -> None:
        try:
            platforms.open_folder(Path(self.v_output.get()))
        except OSError as exc:
            self._message(f"Couldn't open the folder: {exc}", error=True)

    # ----------------------------------------------------------------- voices

    def _clear_voices(self) -> None:
        self.state.voices, self.state.chosen = [], []
        self.voice_vars = {}
        for w in self.voice_box.winfo_children():
            w.destroy()
        self.voices_caption.configure(text="All voices")

    def _show_voices(self, title: str, seconds: float, names: list[str]) -> None:
        self._clear_voices()
        self.state.voices = list(names)
        for i, name in enumerate(names):
            var = tk.BooleanVar(value=True)
            self.voice_vars[name] = var
            ttk.Checkbutton(self.voice_box, text=name, variable=var).grid(
                row=i // VOICE_COLUMNS, column=i % VOICE_COLUMNS, sticky="w", padx=(0, GAP), pady=2)
        n = len(names)
        self.voices_caption.configure(text=f"{title} · {_minutes(seconds)} · {n} voice{'s' * (n != 1)}")

    # ----------------------------------------------------------------- state

    def _read(self) -> GuiState:
        """The window's settings; ValueError for a number that isn't one."""
        s = self.state
        s.mode = self.v_mode.get()
        s.others_volume = int(round(self.v_others.get()))
        s.speed = int(round(self.v_speed.get()))
        s.full_mix = self.v_full_mix.get()
        s.accompaniment = self.v_acc.get()
        s.title = self.v_title.get()
        try:
            s.transpose = int(self.v_transpose.get().strip() or 0)
        except ValueError:
            raise ValueError("change key must be a whole number of semitones") from None
        s.fixed_tempo = self.v_fixed.get()
        try:
            s.bpm = float(self.v_bpm.get().strip().replace(",", "."))
        except ValueError:
            if s.fixed_tempo:
                raise ValueError("the fixed tempo must be a number of beats per minute") from None
        s.fmt = self.v_fmt.get()
        s.bitrate = self.v_bitrate.get()
        s.split_voices = self.v_split_voices.get()
        s.split_chords = self.v_split_chords.get()
        s.export_musicxml = self.v_export.get()
        s.omr_dpi = READING_DETAIL.get(self.v_detail.get())
        s.chosen = [n for n, v in self.voice_vars.items() if v.get()]
        return s

    def _reset(self) -> None:
        d = GuiState()
        self.v_mode.set(d.mode)
        self.v_others.set(d.others_volume)
        self.v_speed.set(d.speed)
        self.v_full_mix.set(d.full_mix)
        self.v_acc.set(d.accompaniment)
        self.v_title.set(d.title)
        self.v_transpose.set(str(d.transpose))
        self.v_fixed.set(d.fixed_tempo)
        self.v_bpm.set(f"{d.bpm:g}")
        self.v_fmt.set(d.fmt)
        self.v_bitrate.set(d.bitrate)
        self.v_split_voices.set(d.split_voices)
        self.v_split_chords.set(d.split_chords)
        self.v_export.set(d.export_musicxml)
        self.v_detail.set(next(k for k, v in READING_DETAIL.items() if v == d.omr_dpi))
        self._sync()

    def _sync(self) -> None:
        """Enable what makes sense for the current choices (all off while a job runs)."""
        busy = self.worker.busy
        has_score = bool(self.state.score)
        scan = has_score and Path(self.state.score).suffix.lower() in OMR_SUFFIXES

        def enable(widget, on: bool) -> None:
            widget.state(["!disabled"] if on and not busy else ["disabled"])

        self.mode_hint.configure(text=MODE_HINTS.get(self.v_mode.get(), ""))
        for w in self._inputs():
            enable(w, True)
        enable(self.others_scale, self.v_mode.get() != "solo")
        enable(self.bpm_box, self.v_fixed.get())
        enable(self.bitrate_box, self.v_fmt.get() == "mp3")
        enable(self.detail_box, scan)
        for w in (self.find_button, self.check_button, self.make_button):
            enable(w, has_score)
        enable(self.output_button, has_score)
        if busy:
            self.cancel_button.state(["!disabled"])

    def _inputs(self):
        """Every input widget, so a running job can lock them."""
        skip = {self.cancel_button, self.open_button, self.output_entry}
        found, todo = [], [self.main]
        while todo:
            w = todo.pop()
            todo.extend(w.winfo_children())
            if w not in skip and isinstance(w, (ttk.Button, ttk.Checkbutton, ttk.Radiobutton, ttk.Scale,
                                                ttk.Entry, ttk.Spinbox, ttk.Combobox)):
                found.append(w)
        return found

    def _toggle_more(self) -> None:
        if self.v_more.get():
            self.more_toggle.configure(text="Fewer options")
            self.more.grid()
        else:
            self.more_toggle.configure(text="More options…")
            self.more.grid_remove()
        self._fit()

    def _fit(self) -> None:
        """Grow the window when its content grew (voices found, options or results shown):
        once shown, a window keeps its size and would cut the bottom rows off."""
        self.root.update_idletasks()
        want = self.root.winfo_reqheight()
        room = self.root.winfo_screenheight() - 80
        if self.root.winfo_height() < want:
            self.root.geometry(f"{max(self.root.winfo_width(), self.root.winfo_reqwidth())}x{min(want, room)}")

    # ----------------------------------------------------------------- running

    def _start(self, kind: str) -> None:
        if self.worker.busy or not self.state.score:
            return
        try:
            state = self._read()
            score, out_dir, opts = to_options(state, need_voices=kind == "make")
        except ValueError as exc:
            self._message(str(exc)[:1].upper() + str(exc)[1:] + ".", error=True)
            return
        if kind == "make":
            save_settings(state)
        self._hide_results()
        self.started = time.monotonic()
        first = {"voices": "Finding the voices…", "check": "Checking the score…", "make": "Starting…"}[kind]
        if score.suffix.lower() in OMR_SUFFIXES:
            first += " Reading a PDF or photo can take a few minutes."
        self.stage.configure(text=first)
        for w in (self.progress, self.stage, self.cancel_button):
            w.grid()
        self.progress.start(12)
        self._fit()
        self.worker.start(kind, score, out_dir, opts)
        self._sync()

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.worker.events.get_nowait())
        except Exception as exc:  # queue.Empty, or a bug in a handler: keep polling
            if type(exc).__name__ != "Empty":
                self._message(f"Something went wrong: {exc}", error=True)
        if self.worker.busy and self.started:
            text = self.stage.cget("text").split("  ·  ")[0]
            self.stage.configure(text=f"{text}  ·  {_minutes(time.monotonic() - self.started)}")
        if self.closing and not self.worker.busy:
            self.root.destroy()
            return
        self.root.after(100, self._poll)

    def _handle(self, event: tuple) -> None:
        kind = event[0]
        if kind == "stage":
            text = event[1]
            self.stage.configure(text=text[:1].upper() + text[1:] + "…")
            return
        if kind == "engines":
            if not event[1]:
                self.engine_caption.configure(
                    text="To read PDFs and photos, install Audiveris (see INSTALL.md).")
                self.engine_caption.grid()
            return
        self._finished()
        if kind == "voices":
            _, title, seconds, names = event
            self._show_voices(title, seconds, names)
        elif kind == "check":
            self._show_check(event[1], event[2])
        elif kind == "done":
            _, folder, files, suspects, recognised = event
            self.v_output.set(str(folder))
            n = len(files)
            text = f"Done: {n} track{'s' * (n != 1)} saved"
            if suspects:
                what = "may be misread" if recognised else "look wrong"
                text += f"\n{suspects} bar{'s' * (suspects != 1)} {what}: Check score lists them."
            self._message(text, ok=True)
            self.actions.grid()
            self.open_button.grid()
        elif kind == "error":
            self._message(event[1][:1].upper() + event[1][1:], error=True)
        elif kind == "cancelled":
            self._message("Stopped.")
        self._sync()
        self._fit()

    def _finished(self) -> None:
        self.progress.stop()
        for w in (self.progress, self.stage, self.cancel_button):
            w.grid_remove()

    def _show_check(self, recognised: bool, issues: list) -> None:
        self.tree.delete(*self.tree.get_children())
        issues = sorted(issues, key=lambda i: (i.bar if i.bar is not None else -1, i.voice))
        fixed = [i for i in issues if i.kind == "fixed"]
        suspects = [i for i in issues if i.kind == "suspect"]
        for heading, group in ((f"Repaired automatically ({len(fixed)})", fixed),
                               (f"Please check ({len(suspects)})", suspects)):
            if group:
                self.tree.insert("", "end", values=("", "", "", heading), tags=("group",))
                for i in group:
                    self.tree.insert("", "end", values=(
                        "" if i.bar is None else i.bar, i.page or "", i.voice, i.detail))
        if not issues:
            self._message("No problems found.", ok=True)
            return
        self.tree.grid()
        if suspects and recognised:
            self._message("To correct them: tick \"Also save the score as MusicXML\" under More options, "
                          "make the tracks, fix those bars in MuseScore, then open the saved file here.")
        elif suspects:
            self._message("If they are mistakes: fix the bars in MuseScore, save, and open the score again.")

    def _hide_results(self) -> None:
        for w in (self.banner, self.actions, self.open_button, self.tree):
            w.grid_remove()

    def _message(self, text: str, error: bool = False, ok: bool = False) -> None:
        self.banner.configure(text=text, style="Error.TLabel" if error else "Ok.TLabel" if ok else "TLabel")
        self.banner.grid()
        self._fit()

    def _close(self) -> None:
        if self.worker.busy:
            if not messagebox.askyesno(APP_NAME, "Tracks are still being made. Stop and quit?",
                                       parent=self.root):
                return
            self.worker.cancel()
            self.closing = True  # _poll closes the window once the job has stopped
            self.root.after(5000, self.root.destroy)  # don't wait forever
            return
        try:
            save_settings(self._read())
        except ValueError:
            pass
        self.root.destroy()
