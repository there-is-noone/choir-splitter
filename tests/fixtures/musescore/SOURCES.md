# MuseScore fixtures

## Hand-made (this project)

Written by `make_fixtures.py` in MuseScore 4.20 format (`python make_fixtures.py` rebuilds them).
Each one was checked against MuseScore 4.7's own MusicXML export: identical tracks.

| File | Content |
|---|---|
| satb_open.mscx / .mscz | 4-bar hymn, one part per voice, 80 bpm |
| satb_closed.mscx | the same hymn on two staves ("Women", "Men"), voices 1 and 2 |
| ssaattbb_open.mscx / .mscz | 8 parts, Soprano 1 ... Bass 2, 60 bpm |
| ssaattbb_closed.mscx / .mscz | the same 8 lines on 4 staves with two voices each |
| satb_piano.mscx | the hymn plus a two-staff piano (keyboard.piano) |
| ties_tuplets_tempo.mscx | ties within and across bars, a triplet, dots, 60 -> 120 bpm, a 3/4 bar |
| pickup.mscx | a one-beat pickup (`len="1/4"`) in 3/4 |
| repeat.mscx | bars 1-2 repeated |
| dc_al_fine.mscx | repeat, Fine, D.C. al Fine (order checked against MuseScore 4.7's MIDI export) |
| ds_al_coda.mscx | Segno, To Coda, D.S. al Coda, Coda (order checked against MuseScore 4.7's MIDI export) |
| dc_al_fine_repeats.mscx | as dc_al_fine with "play repeats" on the D.C. (checked against MuseScore 4.7's MIDI export) |
| dc_voltas.mscx | repeat with volta 1 / volta 2, then D.C. (checked against MuseScore 4.7's MIDI export) |
| dc_voltas_repeats.mscx | as dc_voltas with "play repeats" on the D.C. (checked against MuseScore 4.7's MIDI export) |
| satb_piano_ms47.mscz | satb_piano.mscx saved as .mscz by MuseScore 4.7 (thumbnail, settings, style entries) |

## MuseScore repository (github.com/musescore/MuseScore, `main`, GPL-3.0)

Copied unchanged. They are test data and templates of the MuseScore project.

| File | Path in the repository | Used for |
|---|---|---|
| 03-SATB_+_Piano.mscx | share/templates/02-Choral/03-SATB_+_Piano/ | 5.00 template: part names, piano staves |
| 04-SATB_Closed_Score.mscx | share/templates/02-Choral/04-SATB_Closed_Score/ | 5.00 template: channelSwitch voice names |
| testTie.mscx | src/braille/tests/data/ | 3.01 ties |
| testTuplets_Example_8.5_MBC2015.mscx | src/braille/tests/data/ | 5.00 tuplets, staves without ids |
| tuplets.mscx | src/engraving/tests/compat206_data/ | 2.06 tuplets (`<Tuplet id>`, `<tick>`) |
| repeat_with_2_voltas.mscx | src/engraving/tests/playback/playbackmodel_data/repeat_with_2_voltas/ | 4.00 repeats and voltas |
| tempo_changes_during_notes.mscx | src/engraving/tests/playback/playbackmodel_data/tempo_changes_during_notes/ | 4.20 tempo changes |
| pickup-measure-test.mscx | src/engraving/tests/unrollrepeats_data/ | 3.01 pickup bar |
| testOrnamentsTrillsOttava.mscx | src/importexport/midi/tests/midiexport_data/ | 3.01 8va/15mb lines |
| voices.mscx | src/engraving/tests/parts_data/ | 3.01 several voices, piano staves |

`tests/fixtures/realworld/` also holds two MuseScore demos (Amazing_grace.mscz, adeste.mscx).
