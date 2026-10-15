# Real-world fixture sources

All files are unmodified copies. CPDL (cpdl.org) could not be used: it sits behind a
Cloudflare browser challenge that blocks scripted downloads.

## music21 corpus (music21 10.5, `music21/corpus/`, BSD-3 / public-domain encodings; see music21 corpus licence notes)

| File | Corpus path | Content |
|---|---|---|
| bach_bwv66.6.mxl | bach/bwv66.6.mxl | Bach chorale, SATB open score |
| bach_bwv1.6_horn.mxl | bach/bwv1.6.mxl | Bach chorale, Horn 2 + SATB (instrument must count as accompaniment) |
| bach_bwv227.3_ssatb.mxl | bach/bwv227.3.mxl | Jesu meine Freude, SSATB (5 voices) |
| monteverdi_madrigal.3.1.mxl | monteverdi/madrigal.3.1.mxl | 5-voice madrigal; every part uses a "Grand Piano" instrument |
| aloha_oe_satb_piano.mxl | liliuokalani/aloha_oe.mxl | SATB + solo voice + piano (2 staves) |
| palestrina_agnus_02_8v.krn | palestrina/Agnus_02.krn | Humdrum, 8 voices (double choir, duplicate names) |
| palestrina_credo_15_b_8v.krn | palestrina/Credo_15_b.krn | Humdrum, 8 voices, 24 s (used for the full CLI render) |
| josquin_mille_regrets.abc | josquin/milleRegrets.abc | ABC, one tune per voice (X:1..X:4), GPL-2+ (Laura Conrad) |

## Mutopia Project (mutopiaproject.org), LilyPond-generated MIDI/PDF

| File | Piece (piece-info id) | Licence | Content |
|---|---|---|---|
| new_britain.mid | New Britain hymn tune (1283) | Public Domain | SATB on 2 tracks "upper"/"lower" (S+A, T+B as chords) |
| new_britain-a4.pdf | same | Public Domain | 1-page PDF for OMR |
| nicaea.mid | Nicaea, Dykes (1284) | Public Domain | SATB on 2 tracks "upper"/"lower" |
| GrabliedM.mid | Cornelius, Grablied op. 9/4 (799) | CC BY 4.0 | TTBB on 2 tracks "TenorStaff:TenorA"/"BassStaff:BassA" |
| Wiegenlied.mid | Bruch, Wiegenlied der Hirten (632) | CC BY 4.0 | SATB on 2 tracks "women:Sopran"/"men:Tenor" |
| ComeAgain.mid | Dowland, Come Again (1691) | Public Domain | 4 tracks cantus/altus/tenor/bassus (+ empty lyric tracks) |
| miserere-mei-deus.mid | Allegri, Miserere (2158) | CC BY-SA 4.0 | 9 voices (SSATB + SSAB), junk names ":uniqueContextN", 22 min |
| SchubertF-D706-Psalm23.mid | Schubert, Psalm 23 D.706 (1135) | Public Domain | SSAA played by clarinet/flute programs, track names "\new", piano up/down |

URLs: `https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=<id>`; files under
`https://www.mutopiaproject.org/ftp/...` (e.g. `ftp/Anonymous/new_britain/new_britain.mid`).

## MuseScore repository (github.com/musescore/MuseScore, `main`, GPL-3.0)

| File | Path | Content |
|---|---|---|
| Amazing_grace.mscz | demos/Amazing_grace.mscz | SATB (Soprano/Alto/Tenor/Bass instruments) |
| adeste.mscx | demos/adeste.mscx | Adeste Fideles, MuseScore 2.0.2, closed SATB (2 "Voice" staves, 2 voices each) |
