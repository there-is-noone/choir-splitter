"""Regenerate the engraved OMR test fixtures from known MusicXML (ground truth).

Usage:
    python tests/fixtures/pdf/make_fixtures.py /path/to/lilypond-2.26.0/bin

Needs LilyPond (its official Linux tarball runs without installation:
https://lilypond.org/download.html). For each fixture this writes <name>.musicxml
(ground truth, from the music21 corpus) and <name>.pdf (engraved by LilyPond via
musicxml2ly) into this directory. The PDFs are committed, so the tests don't need
LilyPond; this script only documents how they were made.
"""

from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
from pathlib import Path

from music21 import clef, corpus, key, metadata, meter, stream

HERE = Path(__file__).resolve().parent

SSAATTBB = ["Soprano 1", "Soprano 2", "Alto 1", "Alto 2", "Tenor 1", "Tenor 2", "Bass 1", "Bass 2"]


def satb_chorale() -> stream.Score:
    """Bach, chorale BWV 66.6 - four voices, one page."""
    s = corpus.parse("bach/bwv66.6")
    s.metadata = metadata.Metadata(title="Chorale BWV 66.6", composer="J. S. Bach")
    return s


def ssaattbb_agnus(measures: int = 30) -> stream.Score:
    """Palestrina, Agnus Dei II (Agnus_II_07 in the music21 corpus) - eight voices, 3 pages.

    Re-notated like a modern octavo: note values halved (4/2 -> 4/4, so no breves),
    parts named Soprano 1 ... Bass 2 (LilyPond abbreviates them S1 ... B2 after the first
    system, which is exactly what trips OMR part collation).
    """
    src = corpus.parse("palestrina/Agnus_II_07.krn").measures(1, measures)
    s = stream.Score()
    for part, name in zip(src.parts, SSAATTBB):
        flat = part.flatten()
        line = stream.Part()
        line.insert(0, copy.deepcopy(flat.getElementsByClass(clef.Clef).first() or clef.TrebleClef()))
        ks = flat.getElementsByClass(key.KeySignature).first()
        if ks is not None:
            line.insert(0, copy.deepcopy(ks))
        line.insert(0, meter.TimeSignature("4/4"))
        for el in flat.notesAndRests:
            new = copy.deepcopy(el)
            new.duration.quarterLength = el.duration.quarterLength / 2
            new.lyrics = []
            line.insert(flat.elementOffset(el) / 2, new)
        barred = line.makeMeasures()
        barred.makeTies(inPlace=True)
        barred.partName = name
        barred.partAbbreviation = name[0] + name.split()[-1]
        s.insert(0, barred)
    s.metadata = metadata.Metadata(title="Agnus Dei II", composer="G. P. da Palestrina")
    return s


FIXTURES = {
    "bwv66_6_satb": satb_chorale,
    "agnus_ssaattbb": ssaattbb_agnus,
}


def engrave(score: stream.Score, name: str, lily_bin: Path) -> None:
    xml = HERE / f"{name}.musicxml"
    score.write("musicxml", fp=str(xml))
    with tempfile.TemporaryDirectory() as tmp:
        ly = Path(tmp) / f"{name}.ly"
        subprocess.run([str(lily_bin / "musicxml2ly"), "-o", str(ly), str(xml)], check=True)
        subprocess.run([str(lily_bin / "lilypond"), "-dno-point-and-click",
                        "-o", str(Path(tmp) / name), str(ly)], check=True, cwd=tmp)
        (HERE / f"{name}.pdf").write_bytes((Path(tmp) / f"{name}.pdf").read_bytes())
    print(f"wrote {name}.musicxml and {name}.pdf")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    lily_bin = Path(sys.argv[1])
    for name, build in FIXTURES.items():
        engrave(build(), name, lily_bin)


if __name__ == "__main__":
    main()
