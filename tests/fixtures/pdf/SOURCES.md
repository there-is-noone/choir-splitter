# OMR test fixtures

Printed choir scores used by `tests/test_omr.py`. The `*.musicxml` files are the ground
truth that the engraved PDFs were made from, used to measure recognition accuracy.

| File | What | Source | Licence |
|------|------|--------|---------|
| `bwv66_6_satb.pdf`, `bwv66_6_satb.musicxml` | J. S. Bach, chorale BWV 66.6, SATB, 1 page, open score with part names | music21 corpus `bach/bwv66.6` (music21 10.5), title set to "Chorale BWV 66.6"; engraved with LilyPond 2.26.0 (`musicxml2ly` + `lilypond`) | Public domain composition; the music21 corpus file is distributed under music21's BSD licence |
| `agnus_ssaattbb.pdf`, `agnus_ssaattbb.musicxml` | G. P. da Palestrina, Agnus Dei II, SSAATTBB, 3 pages, 8 staves | music21 corpus `palestrina/Agnus_II_07.krn`, bars 1-30, re-notated like a modern octavo: note values halved (4/2 to 4/4, so no breves), lyrics dropped, parts renamed Soprano 1 ... Bass 2 (LilyPond prints the full names on the first system and S1 ... B2 after that); tenors use the treble-8 clef. Engraved with LilyPond 2.26.0 | Public domain composition; music21 corpus (BSD) |
| `old100_mutopia.pdf` | "Old 100th" (Genevan Psalter 1551), closed-score SATB hymn (S/A and T/B sharing two staves, no part names), 1 page | Mutopia Project, Mutopia-2009/04/11-194, typeset by Peter Chubb, <https://www.mutopiaproject.org/ftp/Anonymous/Old100-orig/Old100-orig-a4.pdf> (sha256 `55d76255749ecafbb33520e14250e619c5c03e01af800d8995cf17793348201d`) | Public domain (dedicated by the typesetter, see the PDF footer) |

`make_fixtures.py` regenerates the two engraved fixtures:

    python tests/fixtures/pdf/make_fixtures.py /path/to/lilypond-2.26.0/bin

LilyPond's official Linux tarball (`lilypond-2.26.0-linux-x86_64.tar.gz` from
<https://gitlab.com/lilypond/lilypond/-/releases>) runs unpacked, without installation.

CPDL (cpdl.org) was tried first but blocks scripted downloads behind a browser check.
