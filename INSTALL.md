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
- [Windows](#windows)
- [macOS](#macos)
- [Linux](#linux)

---

## Windows

Windows 10 or 11, 64-bit. Type the commands below into **PowerShell** or **Command Prompt**.

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

### 2. choirsplit

**Option A: with pipx** (no git needed; the `choir-split` command works in any folder):

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
# close and reopen the terminal, then:
pipx install "git+https://github.com/there-is-noone/projekty.git#subdirectory=choir_splitter"
```

The pipx line needs [Git for Windows](https://git-scm.com/download/win). Without git,
download the repository as a ZIP from GitHub, unzip it, and use option B.

**Option B: from a downloaded or cloned copy:**

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

### 2. choirsplit

**Option A: with pipx:**

```bash
brew install pipx          # or: python3 -m pip install --user pipx
pipx ensurepath
# close and reopen Terminal, then:
pipx install "git+https://github.com/there-is-noone/projekty.git#subdirectory=choir_splitter"
```

If macOS asks to install the "command line developer tools" (they provide git),
accept.

**Option B: from a downloaded or cloned copy:**

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

```bash
pipx install "git+https://github.com/there-is-noone/projekty.git#subdirectory=choir_splitter"
```

or, from a clone:

```bash
cd projekty/choir_splitter
python3 -m venv .venv
.venv/bin/pip install .
```

A system ffmpeg (e.g. `sudo apt install ffmpeg`) is used when present; otherwise the
bundled one is.

### PDFs (optional): Audiveris

With no root needed, on x86-64:

```bash
scripts/install_omr.sh
```

This puts Audiveris under `~/.local/share/choirsplit/audiveris/`, with OCR data for part
names and titles. Alternatively, install the official `.deb` from the Audiveris releases
page into `/opt/audiveris`.

### MuseScore (optional)

choirsplit finds `mscore`, `musescore`, `MuseScore4`, `mscore4portable` and similar on
`PATH`, or the flatpak `org.musescore.MuseScore`.

---

## Where choirsplit keeps its files

| | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Data (user-local Audiveris, OCR data) | `%LOCALAPPDATA%\choirsplit` | `~/Library/Application Support/choirsplit` | `~/.local/share/choirsplit` |
| Cache of PDF recognition results | `%LOCALAPPDATA%\choirsplit\Cache\omr` | `~/Library/Caches/choirsplit/omr` | `~/.cache/choirsplit/omr` |

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
