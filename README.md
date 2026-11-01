# choirsplit

Turn a choir score into separate MP3 practice tracks for every voice, the
kind of rehearsal tracks choirs hand out so each singer can learn their part.

For every voice in the score you get:

| File | What it is |
| --- | --- |
| `<title> - 01 Soprano.mp3` | the soprano line alone |
| `<title> - 01 Soprano (practice).mp3` | soprano loud and centred, the other voices quiet and spread left/right, accompaniment softly underneath |
| `<title> - All voices.mp3` | everything together |

`<title>` is the score's title, or its file name when the score has none.

Any number of voices works: SATB, SSA, TTBB, 5–6 voice madrigals, and
8-voice SSAATTBB or double-choir scores.

## Install

Works on **Windows, macOS and Linux** with Python 3.10+. MP3 encoding (ffmpeg) is
installed with the package, so there is nothing else to set up for MusicXML, MuseScore
or MIDI scores. **Step-by-step instructions for each system, including Audiveris for
PDFs, are in [INSTALL.md](INSTALL.md).**

Quick version, from inside this folder:

```bash
pipx install .                        # the choir-split command then works everywhere
```

or with a virtual environment:

```bash
python3 -m venv .venv                 # Windows: py -m venv .venv
.venv/bin/pip install .               # Windows: .venv\Scripts\python -m pip install .
```

## Desktop app

For singers who'd rather not use a terminal there is a window: drop or choose a score,
set the options with switches and sliders, and press **Make tracks**. It does everything
the command does: solo/practice tracks, speed, key, voices, MusicXML export, and **Check
score**. A PDF is read once per session even if you check it and make tracks from it.

```bash
pipx install ".[gui]"                 # the [gui] extra adds a modern theme (Windows, Linux) and drag and drop
choir-split-ui                        # or: choir-split-ui "Ave Verum.pdf"
choir-split-ui --install-shortcut     # add it to the Start menu / Applications / app menu
```

It runs on Windows, macOS (in the native look) and Linux, and follows the system's light
or dark mode. A long PDF reading shows the page it is on and can be cancelled. On Linux
with Tk 9, dragging files in is off, because the drag-and-drop library crashed the app
in testing; choose the score with **Choose file…** there. [INSTALL.md](INSTALL.md#the-desktop-app) has the details for each system.

## Usage

```bash
choir-split score.musicxml                 # writes score_tracks/*.mp3
choir-split score.mxl -o tracks/ --list    # just show which voices were detected
choir-split score.mxl --mode practice      # only the practice mixes (+ full mix)
choir-split score.mxl --tempo-scale 0.8    # 80 % speed for learning
choir-split score.mxl --only "Tenor 2"     # tracks for one voice
choir-split scan.pdf --title "Ave Verum"   # name the files and tags yourself
choir-split scan.pdf --check               # which bars of a PDF reading to check
```

| Option | Meaning |
| --- | --- |
| `-o DIR` | output directory (default `<score>_tracks`) |
| `--mode solo\|practice\|both` | which per-voice tracks to write (default `both`) |
| `--no-full-mix` | skip the all-voices mix |
| `--format mp3\|wav` | output format |
| `--bitrate 192k` | MP3 bitrate |
| `--bpm N` | ignore the score's tempo marks and use N quarter notes per minute |
| `--tempo-scale F` | speed factor applied to the score's tempo |
| `--transpose N` | shift everything by N semitones (at most 48 either way; notes must stay within MIDI 0–127) |
| `--others-volume F` | level of the other voices in practice tracks (0–1, default 0.3) |
| `--no-accompaniment` | leave piano/organ out of the mixes |
| `--no-split-voices` | keep a staff with several voices (e.g. S+A) as one track |
| `--no-split-chords` | don't split hymnal-style two-note chords into upper/lower parts |
| `--only VOICE` | only write tracks for the named voice (repeatable) |
| `--title TITLE` | song title for the file names and the MP3 album tag (default: the score's title, else its file name) |
| `--list` | print the detected voices and exit |
| `--check` | list the bars that look wrong (and, for PDFs, what was repaired) and exit |
| `--export-musicxml [FILE]` | also save the score as MusicXML, e.g. to correct a PDF reading in MuseScore. A bare file name (default: `<score name>.musicxml`) is saved in the output directory beside the tracks; a path with a directory is used as given |
| `--omr-dpi N` | resolution to read PDFs at (default: Audiveris's 300) |

## Input formats

| Format | Notes |
| --- | --- |
| MusicXML (`.musicxml`, `.xml`, `.mxl`) | best choice; export it from MuseScore, Sibelius, Finale or Dorico |
| MuseScore (`.mscz`, `.mscx`) | read directly, MuseScore 2, 3, 4 and 5 files; no MuseScore install needed |
| PDF and images (`.pdf`, `.png`, `.jpg`, `.tif`) | optical music recognition with Audiveris (install below) |
| MIDI (`.mid`, `.midi`) | one track per voice works best |
| Humdrum, ABC, MEI, Capella | anything [music21](https://www.music21.org) can read |

### MuseScore files

`.mscz` / `.mscx` files are read by a built-in parser: parts and staves,
voices 1–4, chords, ties, tuplets, tempo marks, time signatures, pickup bars,
repeats and voltas, D.C./D.S. al Fine/al Coda jumps (played the way MuseScore
plays them), 8va/8vb lines, and the voice names of MuseScore's closed-score
templates. Grace notes and ornaments are skipped.

If the built-in parser can't read a file (MuseScore 1.x files, or something
unexpected), choirsplit converts it with the MuseScore program when one is
installed (`mscore`, `musescore4`, `mscore4portable`, … on `PATH`, or the
flatpak). Environment variables:

| Variable | Meaning |
| --- | --- |
| `CHOIRSPLIT_MUSESCORE` | path to the MuseScore executable or AppImage |
| `CHOIRSPLIT_MUSESCORE_MODE` | `auto` (default: built-in, then MuseScore), `native` (built-in only), `cli` (always MuseScore) |

### PDF and scanned scores

PDFs and images are read with [Audiveris](https://github.com/Audiveris/audiveris)
optical music recognition.
- **Windows:** install the official `windowsConsole` `.msi`.
- **macOS:** install the `.dmg`.

choirsplit finds both in their default folders (see [INSTALL.md](INSTALL.md)).

On Linux, install it user-locally (no root, about 210 MB):

```bash
scripts/install_omr.sh        # Linux x86-64; needs curl, ar, tar (zstd), sha256sum, python3
```

This puts Audiveris under `~/.local/share/choirsplit/audiveris/` and English
OCR data (for part names and titles) under `~/.local/share/choirsplit/tessdata/`.
`AUDIVERIS_VERSION`, `OCR_LANGS="eng deu"` and `CHOIRSPLIT_HOME` change what
and where it installs. Recognition takes about 20 s per page; results are
cached, so a second run is instant.

choirsplit cleans up the recognised score: parts are matched across systems
by staff position, bars are aligned, two-voice hymn staves are split into
upper and lower lines, unnamed staves are named by layout (SATB, SSAATTBB, …),
and tenor parts written in a plain treble clef are moved down an octave.
Misreadings are repaired where the printed page leaves no doubt. Notes struck
together are printed at the same horizontal position on every staff of a system,
so when one voice's bar comes up short:
- augmentation dots OMR missed are restored;
- notes pulled early by a missed rest are moved back to where they are printed.

A time signature that changes inside a system (a 3/4 bar between 5/4 bars) is sometimes
missed, and its digits are read as a note on every staff at the bar line. That column of
notes is removed, and the bar is given the length the voices really sing there, for as
long as no other time signature follows.

Notes that aren't sung are dropped:
- notes in a bar that shows a whole-bar rest, if they line up with nothing the other voices sing. These are small cue notes of an instrument printed in a resting staff ("Vn. II", "Vc."), or bar lines and courtesy time signatures misread as notes. A divisi voice that sings while the other voice of its staff rests is kept.
- a note OMR took from the piano into a voice: next to a whole-bar rest, or far outside the voice's range on the piano side of the staff.

Pitches are never changed.

Sometimes Audiveris misses a note entirely, leaving a staff's bar empty while the other
voices sing. A whole note on a ledger line under a fermata is a typical case. When that
happens, choirsplit reads the PDF a second time at 250 dpi and fills the empty bars from
that reading, but only where the other voices of the bar are identical in both readings.
This takes one more recognition run (about 20 s per page), only for scores that need
it, and it is cached like the first.

### When recognition isn't perfect

OMR is never perfect. `--check` lists what was repaired and which bars still look
wrong, with the page and system to find them in the PDF:

```
$ choir-split lullaby.pdf --check
Repaired automatically (4):
  all bars         Tenor  fixed: sung an octave lower (treble clef read without its small 8)
  bar 2 (p1, s1)   Bass   fixed: dropped stray notes beside a whole-bar rest
  bar 31 (p2, s2)  Alto   fixed: moved 1 note to where they are printed (missed rest or note length)
  bar 93 (p6, s2)  Alto   fixed: restored 1 missed dot
Please check (7):
  bar 32 (p2, s2)  Alto   suspect: notes fill 2 of 3 beats (missed dot or rest?)
  bar 32 (p2, s2)  Bass   suspect: notes fill 2 of 3 beats (missed dot or rest?)
  ...
```

Bars are flagged when:
- **Empty:** nothing was recognised on a staff while the other voices sing (even after the second reading).
- **Short:** a voice's notes run out before the end of the bar.
- **Out of range:** a note is far outside the voice's range.
- **Big leap:** a jump is much larger than the voice makes anywhere else.
- **Out of step:** the voice's rhythm breaks away from the others in a passage where they all move together.

A normal run prints a one-line reminder when the score has such bars.

`--check` works on every file type, not only PDFs: in a MusicXML, MuseScore or MIDI file
it finds typing mistakes the same way (wrong octave, missing rest, wrong length). MIDI has
no written rests, so lines that stop before the end of a bar aren't reported there.

To fix them for good, save the cleaned score, correct it in MuseScore (free), and make
the tracks from the corrected file. That file loads without OMR from then on:

```bash
choir-split lullaby.pdf --export-musicxml --list
# open lullaby_tracks/lullaby.musicxml in MuseScore, fix the bars --check listed, save
choir-split lullaby_tracks/lullaby.musicxml -o lullaby_tracks
```

If a scan reads badly, try another resolution with `--omr-dpi`. Audiveris renders PDFs at
300 dpi by default. Higher is not automatically better: on our test scores, 400 dpi
read worse than 300. Results for each resolution are cached separately.

Audiveris refuses pages of more than 20 million pixels, which is a poster-sized PDF page
(some scans are 29 × 42 inches) at 300 dpi. choirsplit then reads the PDF again at the
highest resolution that fits, and says so. The notes on such pages are drawn large, so
this loses nothing.

| Variable | Meaning |
| --- | --- |
| `CHOIRSPLIT_AUDIVERIS` | Audiveris launcher or install directory |
| `CHOIRSPLIT_OMR_CMD` | use another OMR engine: a command with `{input}` (or `{image}`, run per page) and `{outdir}` that writes MusicXML |
| `CHOIRSPLIT_OMR_TIMEOUT` | seconds before recognition is stopped (default 120 + 120 per page) |
| `CHOIRSPLIT_OMR_CACHE` | cache directory, or `0` to disable the cache |
| `CHOIRSPLIT_OMR_DPI` | resolution PDFs are read at, like `--omr-dpi` |

### How voices are found

- Each staff becomes a voice, named after the part (`Soprano`, `Tenor 2`, …).
- A staff carrying several voices (e.g. Soprano and Alto on one staff, as voice
  1 and voice 2) is split. Names come from the staff name: `Soprano Alto`,
  `S A`, `SA`, `Women`, `Soprano 1 2`, `Tenor I/II`, `S1 S2`.
- Hymnal-style staves that write two parts as two-note chords are split into
  the upper and lower note, if the staff name tells which two parts it holds.
- Before splitting, each staff's voices are compared: a second voice that just
  repeats the first (PDF recognition sometimes writes a single line twice, and
  files exported from such a reading keep the copy) becomes one track, so a
  two-part song gets two tracks. A real divisi stays split, even when it sings
  in unison most of the time. `--check` lists the staves merged this way.
- Piano/organ/harp/guitar parts are treated as accompaniment. They get no
  track of their own but play softly in the practice and full mixes.
- Ties are merged, grace notes skipped and repeats expanded. Every file has
  exactly the same length, so they line up in any player or DAW.
- The tracks are a steady recording: dynamics (p, f, hairpins) are ignored and
  every note is equally loud. The first tempo mark sets the tempo; after it only
  a written number (♩ = 72) changes it. Tempo words such as "Poco Lento" or
  "a tempo", rit./accel. and fermatas are left out. (MIDI files have no tempo
  words, so all their tempo changes are followed.)

## Sound

Audio is synthesised by a small built-in numpy synthesiser (vowel-like sung
tone with light vibrato; a piano-like tone for accompaniment). No soundfont or
external synthesiser is needed.

## Development

```bash
.venv/bin/pip install -e ".[test]"             # Windows: .venv\Scripts\python -m pip install -e ".[test]"
.venv/bin/python -m pytest -q                  # full suite (OMR tests need Audiveris)
.venv/bin/python -m pytest -q -m "not slow"     # quick run
.venv/bin/python -m pytest -q -m slow            # slow tests only, incl. the Bach chorale sweep
```

Tests needing Audiveris (`omr`) or the MuseScore program (`musescore`) are
skipped when those aren't installed. The suite runs on Linux, macOS and Windows in
GitHub Actions (`.github/workflows/choirsplit.yml`); a few tests that fake programs
with shell scripts are skipped on Windows. Fixture sources and licences are listed
in `tests/fixtures/*/SOURCES.md`.

Code layout (`choirsplit/`):

- `loaders.py`: file → music21 score (dispatches to `musescore.py` / `omr.py`)
- `score.py`: score → per-voice note lists on one tempo-mapped timeline
- `synth.py`: note lists → audio
- `mixer.py`: solo / practice / full mixes with panning and normalisation
- `audio_io.py`: WAV / MP3 output
- `musescore.py`, `mscx_utils.py`: MuseScore file reader
- `omr.py`: PDF/image recognition with Audiveris and clean-up of its output
- `check.py`: bars that look misread (`--check`)
- `pipeline.py`, `cli.py`: glue and command line
- `platforms.py`: per-OS data/cache folders and where installers put Audiveris and MuseScore
