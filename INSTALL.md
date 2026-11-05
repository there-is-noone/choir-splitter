# Installing choirsplit on Windows, macOS and Linux

choirsplit is a Python program. On every system you need:

1. **Python 3.10 or newer**.
2. **choirsplit itself**. Installing it also brings ffmpeg, the MP3 encoder, through the
   `imageio-ffmpeg` package, so MP3 output works with nothing else installed.
3. *Optional:* **Audiveris**, only for reading **PDFs and scanned images**.
4. *Optional:* **MuseScore**, only as a fallback for very old `.mscz` files (MuseScore 1.x).
   Current MuseScore files are read without it.

MusicXML (`.musicxml`, `.mxl`), MuseScore (`.mscz`, `.mscx`) and MIDI files need only
steps 1 and 2.

Pick your system:
- [Windows](#widdows)
- [macOS](#macos)
- [Linux](#linux)

---

## Windows

Windows 10 or 11, 64-bit. Type the commands below into **PowerShell** or **Command Prompt**.

### ONE-LINER to solve the problems
#### 1. Unzip the file to a directory of your choice
#### 2. move in the **PowerShell** or **Command Prompt** to this directory
```
cd <path-to-the-choirsplit directory>
```
#### 3. Holy One-liner
This command should install all of the needed dependencies and the GUI for the app at once, might take a while to run
```
winget install Python.Python.3.12 audiveris.org.Audiveris MuseScore.MuseScore --accept-package-agreements --accept-source-agreements; py -m pip install --user pipx; py -m pipx ensurepath; py -m pipx install "choirsplit[gui] @ git+https://github.com/there-is-noone/choir-splitter.git" --force; ~\.local\bin\choir-split-ui.exe --install-shortcut
```

### 1. Python

Install Python from <https://www.python.org/downloads/windows/>. On the first installer
screen, tick **"Add python.exe to PATH"**. Alternatively run:

```powershell
winget install Python.Python.3.12
```

Check it works:

```powershell
py --version
```

First get the `choir_splitter` folder: download the repository as a ZIP (on GitHub:
**Code ▸ Download ZIP**) and unzip it, clone it with git, or copy it from whoever shared
it with you.

**Option A: with pipx** (recommended; afterwards `choir-split` works in any folder):

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
# close and reopen the terminal, then:
pipx install path\to\projekty\choir_splitter
```

**Option B: in a virtual environment inside the folder:**

```powershell
cd path\to\projekty\choir_splitter
py -m venv .venv
.venv\Scripts\python -m pip install .
```

The command is then `.venv\Scripts\choir-split`. You can also run
`.venv\Scripts\activate` once per terminal and type just `choir-split`. If PowerShell
refuses to run `activate`, call `.venv\Scripts\choir-split` directly instead.

### 3. Try it

```powershell
choir-split "C:\Users\you\Music\Ave Verum.mxl"
choir-split "C:\Users\you\Music\Ave Verum.mxl" --list    # just show the voices it found
```

The MP3s are written to a folder next to the score, here `Ave Verum_tracks`.

### 4. PDFs (optional): Audiveris

1. Go to <https://github.com/Audiveris/audiveris/releases> and, under the newest
   release's *Assets*, download `Audiveris-<version>-windowsConsole-x86_64.msi`.
2. Run it and keep the default folder (`C:\Program Files\Audiveris`). choirsplit finds
   Audiveris there automatically. If you installed it elsewhere, set:
   ```powershell
   setx CHOIRSPLIT_AUDIVERIS "D:\Apps\Audiveris"
   ```
   then open a new terminal.
3. Test it:
   ```powershell
   choir-split "C:\Users\you\Music\score.pdf" --list
   ```

### 5. MuseScore (optional)

Install MuseScore 4 from <https://musescore.org>. choirsplit finds it in
`C:\Program Files\MuseScore 4\bin\MuseScore4.exe`; for any other location set
`CHOIRSPLIT_MUSESCORE` to the path of `MuseScore4.exe`.

---

## macOS

macOS 11 (Big Sur) or newer, on Apple silicon (M1/M2/…) or Intel. Use the **Terminal**
app.

### 1. Python

Install Python from <https://www.python.org/downloads/macos/>, or with
[Homebrew](https://brew.sh):

```bash
brew install python
```

Check it works:

```bash
python3 --version
```

First get the `choir_splitter` folder: download the repository as a ZIP (on GitHub:
**Code ▸ Download ZIP**) and unzip it, clone it with git, or copy it from whoever shared
it with you.

**Option A: with pipx** (recommended; afterwards `choir-split` works in any folder):

```bash
brew install pipx          # or: python3 -m pip install --user pipx
pipx ensurepath
# close and reopen Terminal, then:
pipx install path/to/projekty/choir_splitter
```

**Option B: in a virtual environment inside the folder:**

```bash
cd path/to/projekty/choir_splitter
python3 -m venv .venv
.venv/bin/pip install .
```

The command is then `.venv/bin/choir-split`. You can also run
`source .venv/bin/activate` once per terminal and type just `choir-split`.

### 3. Try it

```bash
choir-split ~/Music/"Ave Verum.mxl"
```

### 4. PDFs (optional): Audiveris

1. Go to <https://github.com/Audiveris/audiveris/releases> and download the `.dmg` that
   matches your Mac:
   - `macosx-arm64` for Apple silicon (M1 and later);
   - `macosx-x86_64` for Intel.

   ( > About This Mac shows which one you have.)
2. Open the `.dmg` and drag **Audiveris** into **Applications**. choirsplit finds it in
   `/Applications` or `~/Applications`.
3. If macOS says Audiveris "can't be opened" or is from an unidentified developer,
   right-click it in Applications, choose **Open**, and confirm once. Or run:
   ```bash
   xattr -dr com.apple.quarantine /Applications/Audiveris.app
   ```
4. Test it:
   ```bash
   choir-split ~/Music/score.pdf --list
   ```

### 5. MuseScore (optional)

Install MuseScore 4 from <https://musescore.org> into Applications; choirsplit finds
`/Applications/MuseScore 4.app` automatically.

---

## Linux

Most distributions already have Python 3.10 or newer (`python3 --version`). Some need the
venv module installed separately, for example `sudo apt install python3-venv` on
Debian/Ubuntu.

### choirsplit

With the `choir_splitter` folder downloaded or cloned:

```bash
cd choir-splitter
python3 -m venv .venv
.venv/bin/pip install .[gui] #installs the project with the graphical interface
scripts/install_omr.sh #script to install audiveris as the .pdf reader
choir-split-ui # opens user interface

```

A system ffmpeg (e.g. `sudo apt install ffmpeg`) is used when present; otherwise the
bundled one is.


### MuseScore (optional)

choirsplit finds `mscore`, `musescore`, `MuseScore4`, `mscore4portable` and similar on
`PATH`, or the flatpak `org.musescore.MuseScore`.

---

## The desktop app

Install choirsplit with the `gui` extra, the same way as above but with `[gui]` after the
folder (the quotes matter in zsh and PowerShell):

```bash
pipx install "path/to/projekty/choir_splitter[gui]"
```

In a virtual environment: `pip install ".[gui]"`. If choirsplit is already installed
with pipx, add the extra with `pipx install --force "path/to/projekty/choir_splitter[gui]"`.

Start it with `choir-split-ui`. To start it like any other program, add a shortcut once:

```bash
choir-split-ui --install-shortcut
```

| | Shortcut |
| --- | --- |
| Windows | **Choir Split** in the Start menu (runs without a console window) |
| macOS | `~/Applications/Choir Split.app`; drag it to the Dock if you like |
| Linux | **Choir Split** in the desktop's app menu (`~/.local/share/applications/choirsplit.desktop`) |

On macOS and Linux the shortcut remembers the PATH and `CHOIRSPLIT_*` settings of the
terminal you ran it from: programs started from the Dock or app menu don't read
`~/.zshrc` or `~/.bashrc`. Run it again after changing those settings.

**The window needs Tk** (Python's `tkinter`). The python.org installers for Windows and
macOS include it. Elsewhere:
- **Fedora:** `sudo dnf install python3-tkinter`
- **Ubuntu / Debian:** `sudo apt install python3-tk`
- **Arch:** `sudo pacman -S tk`
- **Homebrew Python:** `brew install python-tk@3.13` (your Python's version)
- **Windows, if it's missing:** run the Python installer again → Modify → tick **tcl/tk and IDLE**

`choir-split-ui` says which of these applies if Tk is missing.

Without the `gui` extra the window still works, with the system's plain theme and
without dragging files in. Dragging files in is also off on Linux with Tk 9, where the
drag-and-drop library crashed the app in testing; choose the score with **Choose file…** there.
`CHOIRSPLIT_DND=1` forces it on (at your own risk) and `CHOIRSPLIT_DND=0` turns it off.

On macOS the menu bar shows "Python" rather than "Choir Split".

## Installing straight from GitHub

If the repository is public, or you are signed in to git with access to it, pipx can
install without downloading the folder first, on any system:

```bash
pipx install "git+https://github.com/there-is-noone/projekty.git#subdirectory=choir_splitter"
```

This installs what is on the `main` branch.

---

## Where choirsplit keeps its files

| | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Data (user-local Audiveris, OCR data) | `%LOCALAPPDATA%\choirsplit` | `~/Library/Application Support/choirsplit` | `~/.local/share/choirsplit` |
| Cache of PDF recognition results | `%LOCALAPPDATA%\choirsplit\Cache\omr` | `~/Library/Caches/choirsplit/omr` | `~/.cache/choirsplit/omr` |
| Desktop app settings (`gui.json`) and error log (`gui.log`) | the data folder | the data folder | the data folder |

`CHOIRSPLIT_HOME` moves the data folder, and `CHOIRSPLIT_OMR_CACHE` moves the cache
(`0` turns it off). Deleting the cache is always safe.

## Settings by environment variable

| Variable | Meaning |
| --- | --- |
| `CHOIRSPLIT_AUDIVERIS` | Audiveris program or install folder |
| `CHOIRSPLIT_MUSESCORE` | MuseScore program |
| `CHOIRSPLIT_HOME` | choirsplit's data folder |
| `CHOIRSPLIT_OMR_CACHE` | PDF recognition cache folder, or `0` for no cache |
| `CHOIRSPLIT_OMR_TIMEOUT` | seconds before PDF recognition is stopped |
| `CHOIRSPLIT_DND` | `1` or `0`: dragging files onto the desktop app on or off |

To set one permanently:
- **Windows:** `setx NAME "value"`, then open a new terminal.
- **macOS and Linux:** add `export NAME="value"` to `~/.zshrc` (macOS) or `~/.bashrc` (Linux).

## Troubleshooting

- **`choir-split` is not recognised / command not found:** the folder holding the command
  isn't on your PATH.
  - **pipx:** run `pipx ensurepath` and reopen the terminal.
  - **venv:** use the full path, e.g. `.venv\Scripts\choir-split` (Windows) or
    `.venv/bin/choir-split` (macOS, Linux).
- **"no OMR engine" for a PDF:** Audiveris isn't installed, or is somewhere unusual. Set
  `CHOIRSPLIT_AUDIVERIS` (see above). Exporting MusicXML from your notation program
  always gives better results than a PDF.
- **"ffmpeg was not found":** reinstall with `pip install --force-reinstall imageio-ffmpeg`
  (inside your venv), or write WAV files instead with `--format wav`.
- **Wrong or missing voices:** run with `--list` to see what was detected.
- **The desktop app says "needs Tk":** install your system's Tk package for Python (see
  [The desktop app](#the-desktop-app)).
- **The desktop app found no Audiveris, though the command does:** you probably set
  `CHOIRSPLIT_AUDIVERIS` in `~/.zshrc` or `~/.bashrc`. Run `choir-split-ui
  --install-shortcut` again from a terminal, and start the app from that shortcut.
- **"Something went wrong" in the desktop app:** the details are in `gui.log` in the
  data folder (see the table above).
