"""Write the hand-made MuseScore 4 fixtures in this directory.

usage: python make_fixtures.py

Each score is described per staff as a list of measures; a measure lists its
voices separated by "&&". Tokens: "C5/q" (pitch/duration), "C5+E5/h." (chord,
dotted), "r/q" (rest), "r/m" (measure rest), a trailing "~" ties into the next
chord of the same voice, "(3" ... ")" is a triplet. Durations: w h q e s.
"""

from __future__ import annotations

import zipfile
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).parent

DUR = {"w": ("whole", Fraction(1)), "h": ("half", Fraction(1, 2)), "q": ("quarter", Fraction(1, 4)),
       "e": ("eighth", Fraction(1, 8)), "s": ("16th", Fraction(1, 16))}
STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

VOICE_IDS = {
    "Soprano": ("soprano", "voice.soprano", "G"), "Alto": ("alto", "voice.alto", "G"),
    "Tenor": ("tenor", "voice.tenor", "G8_vb"), "Bass": ("bass", "voice.bass", "F"),
    "Women": ("women", "voice.female", "G"), "Men": ("men", "voice.male", "F"),
}


def pitch(text: str) -> tuple[int, int]:
    """'F#4' -> (midi, tpc)."""
    step, rest = text[0], text[1:]
    alter = 0
    while rest and rest[0] in "#b":
        alter += 1 if rest[0] == "#" else -1
        rest = rest[1:]
    midi = 12 * (int(rest) + 1) + STEPS[step] + alter
    tpc = 13 + "FCGDAEB".index(step) + 7 * alter
    return midi, tpc


def frac(f: Fraction) -> str:
    return f"{f.numerator}/{f.denominator}" if f.denominator != 1 else str(f.numerator)


def location(measures: int, fractions: Fraction, indent: str) -> str:
    inner = ""
    if measures:
        inner += f"{indent}    <measures>{measures}</measures>\n"
    if fractions:
        inner += f"{indent}    <fractions>{frac(fractions)}</fractions>\n"
    return f"{indent}<location>\n{inner}{indent}  </location>\n"


def parse_voice(text: str):
    """Yield (kind, data) items for one voice of one measure."""
    items = []
    ratio = Fraction(1)
    for tok in text.split():
        if tok == "(3":
            items.append(("tuplet", None))
            ratio = Fraction(2, 3)
            continue
        if tok == ")":
            items.append(("endtuplet", None))
            ratio = Fraction(1)
            continue
        tie = tok.endswith("~")
        tok = tok.rstrip("~")
        notes, _, dur = tok.partition("/")
        dots = len(dur) - len(dur.rstrip("."))
        dur = dur.rstrip(".")
        if dur == "m":
            items.append(("mrest", None))
            continue
        name, length = DUR[dur]
        length = length * (2 - Fraction(1, 2 ** dots)) * ratio
        pitches = [] if notes == "r" else [pitch(p) for p in notes.split("+")]
        items.append(("cr", {"type": name, "dots": dots, "len": length, "pitches": pitches, "tie": tie}))
    return items


def write_score(name: str, title: str, parts: list[dict], marks: dict[int, dict],
                measure_len: dict[int, Fraction] | None = None, mscz: bool = False):
    """parts: [{"name", "id", "instrumentId", "clefs", "staves": [[measure strings]]}]."""
    measure_len = measure_len or {}
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<museScore version="4.20">',
           "  <programVersion>4.2.0</programVersion>", "  <Score>", "    <Division>480</Division>",
           f'    <metaTag name="workTitle">{escape(title)}</metaTag>']
    staff_specs = []
    for part in parts:
        out.append("    <Part>")
        for n, _ in enumerate(part["staves"]):
            staff_id = len(staff_specs) + 1 + n
            out.append(f'      <Staff id="{staff_id}">')
            out.append('        <StaffType group="pitched">\n          <name>stdNormal</name>\n'
                       "          </StaffType>")
            if len(part["staves"]) > 1:
                out.append(f"        <defaultClef>{part['clefs'][n]}</defaultClef>")
            out.append("        </Staff>")
        out.append(f"      <trackName>{escape(part['name'])}</trackName>")
        out.append(f'      <Instrument id="{part["id"]}">')
        out.append(f"        <longName>{escape(part['name'])}</longName>")
        out.append(f"        <trackName>{escape(part['name'])}</trackName>")
        out.append(f"        <instrumentId>{part['instrumentId']}</instrumentId>")
        for n, clef in enumerate(part["clefs"]):
            attr = f' staff="{n + 1}"' if n else ""
            out.append(f"        <clef{attr}>{clef}</clef>")
        out.append(f'        <Channel>\n          <program value="{part.get("program", 52)}"/>\n'
                   "          </Channel>")
        out.append("        </Instrument>")
        out.append("      </Part>")
        staff_specs.extend(part["staves"])

    ts = (4, 4)
    for staff_index, measures in enumerate(staff_specs):
        out.append(f'    <Staff id="{staff_index + 1}">')
        if staff_index == 0:
            out.append(f"      <VBox>\n        <Text>\n          <style>title</style>\n"
                       f"          <text>{escape(title)}</text>\n          </Text>\n        </VBox>")
        ts = (4, 4)
        # Positions of every chord per voice, for tie locations.
        parsed = [[parse_voice(v) for v in m.split("&&")] for m in measures]
        for mi, voices in enumerate(parsed):
            mark = marks.get(mi, {})
            if "time" in mark:
                ts = mark["time"]
            length = measure_len.get(mi, Fraction(ts[0], ts[1]))
            attr = f' len="{frac(length)}"' if mi in measure_len else ""
            out.append(f"      <Measure{attr}>")
            if mark.get("startRepeat"):
                out.append("        <startRepeat/>")
            for vi, items in enumerate(voices):
                out.append("        <voice>")
                if vi == 0:
                    if mi == 0 or "key" in mark:
                        out.append(f"          <KeySig>\n            <concertKey>{mark.get('key', 0)}"
                                   "</concertKey>\n            </KeySig>")
                    if "time" in mark:
                        out.append(f"          <TimeSig>\n            <sigN>{ts[0]}</sigN>\n"
                                   f"            <sigD>{ts[1]}</sigD>\n            </TimeSig>")
                    if "tempo" in mark and staff_index == 0:
                        qps = Fraction(mark["tempo"], 60)
                        out.append(f"          <Tempo>\n            <tempo>{float(qps):.6f}</tempo>\n"
                                   "            <followText>1</followText>\n"
                                   f"            <text>♩ = {mark['tempo']}</text>\n            </Tempo>")
                pos = Fraction(0)
                for kind, data in items:
                    if kind == "tuplet":
                        out.append("          <Tuplet>\n            <normalNotes>2</normalNotes>\n"
                                   "            <actualNotes>3</actualNotes>\n"
                                   "            <baseNote>eighth</baseNote>\n            </Tuplet>")
                    elif kind == "endtuplet":
                        out.append("          <endTuplet/>")
                    elif kind == "mrest":
                        out.append(f"          <Rest>\n            <durationType>measure</durationType>\n"
                                   f"            <duration>{ts[0]}/{ts[1]}</duration>\n            </Rest>")
                        pos += length
                    else:
                        out.extend(chord_xml(data, parsed, staff_index, mi, vi, pos))
                        pos += data["len"]
                out.append("          </voice>")
            if mark.get("endRepeat"):
                out.append(f"        <endRepeat>{mark['endRepeat']}</endRepeat>")
            out.append("        </Measure>")
        out.append("      </Staff>")
    out.append("    </Score>")
    out.append("  </museScore>")
    text = "\n".join(out) + "\n"
    (HERE / f"{name}.mscx").write_text(text, encoding="utf-8")
    if mscz:
        with zipfile.ZipFile(HERE / f"{name}.mscz", "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("META-INF/container.xml",
                        '<?xml version="1.0" encoding="UTF-8"?>\n<container>\n  <rootfiles>\n'
                        f'    <rootfile full-path="{name}.mscx"/>\n  </rootfiles>\n</container>\n')
            zf.writestr(f"{name}.mscx", text)


def chord_xml(data, parsed, staff_index, mi, vi, pos) -> list[str]:
    if not data["pitches"]:
        lines = ["          <Rest>", f"            <durationType>{data['type']}</durationType>"]
        if data["dots"]:
            lines.append(f"            <dots>{data['dots']}</dots>")
        return lines + ["            </Rest>"]
    lines = ["          <Chord>"]
    if data["dots"]:
        lines.append(f"            <dots>{data['dots']}</dots>")
    lines.append(f"            <durationType>{data['type']}</durationType>")
    prev_tied = _previous_chord_tied(parsed, mi, vi, pos)
    nxt = _next_chord_location(parsed, mi, vi, pos) if data["tie"] else None
    for midi, tpc in data["pitches"]:
        lines.append("            <Note>")
        if prev_tied is not None:
            lines.append('              <Spanner type="Tie">\n                <prev>')
            lines.append(location(-prev_tied[0], -prev_tied[1], "                  ").rstrip("\n"))
            lines.append("                  </prev>\n                </Spanner>")
        if nxt is not None:
            lines.append('              <Spanner type="Tie">\n                <Tie>\n                  </Tie>'
                         "\n                <next>")
            lines.append(location(nxt[0], nxt[1], "                  ").rstrip("\n"))
            lines.append("                  </next>\n                </Spanner>")
        lines.append(f"              <pitch>{midi}</pitch>\n              <tpc>{tpc}</tpc>")
        lines.append("              </Note>")
    lines.append("            </Chord>")
    return lines


def _chords(parsed, vi):
    """[(measure, position, data)] for one voice across the staff."""
    found = []
    for mi, voices in enumerate(parsed):
        if vi >= len(voices):
            continue
        pos = Fraction(0)
        for kind, data in voices[vi]:
            if kind == "cr":
                found.append((mi, pos, data))
                pos += data["len"]
    return found


def _next_chord_location(parsed, mi, vi, pos):
    chords = _chords(parsed, vi)
    for i, (m, p, _) in enumerate(chords):
        if (m, p) == (mi, pos) and i + 1 < len(chords):
            m2, p2, _ = chords[i + 1]
            return m2 - m, p2 - p
    return None


def _previous_chord_tied(parsed, mi, vi, pos):
    chords = _chords(parsed, vi)
    for i, (m, p, _) in enumerate(chords):
        if (m, p) == (mi, pos) and i > 0 and chords[i - 1][2]["tie"]:
            m0, p0, _ = chords[i - 1]
            return m - m0, p - p0
    return None


def voice_part(name, staves, **extra):
    pid, iid, clef = VOICE_IDS.get(name.split()[0], ("voice", "voice.vocals", "G"))
    return {"name": name, "id": pid, "instrumentId": iid, "clefs": [clef], "staves": [staves], **extra}


# A short hymn (4 bars of 4/4), written so voice 1 and voice 2 of a closed staff differ in rhythm.
S = ["E5/q E5/q F5/q G5/q", "G5/q F5/q E5/q D5/q", "C5/q C5/q D5/q E5/q", "E5/q. D5/e D5/h"]
A = ["C5/q C5/q C5/q B4/q", "C5/h C5/q B4/q", "A4/q A4/q B4/q C5/q", "C5/q B4/q B4/h"]
T = ["G4/q G4/q A4/q G4/q", "G4/q A4/q G4/q G4/q", "E4/q F4/q G4/q G4/q", "G4/q. G4/e G4/h"]
B = ["C4/q C4/q F3/q E3/q", "E3/q F3/q G3/q G3/q", "A3/q F3/q G3/q C4/q", "G3/q G3/q G3/h"]


def pair(upper, lower):
    return [f"{u} && {l}" for u, l in zip(upper, lower)]


def eight_voice():
    # S1 S2 A1 A2 T1 T2 B1 B2, each its own line (2 bars).
    lines = {
        "Soprano 1": ["G5/h A5/h", "G5/w"], "Soprano 2": ["E5/h F5/h", "E5/w"],
        "Alto 1": ["C5/h C5/h", "C5/w"], "Alto 2": ["G4/q A4/q A4/h", "G4/w"],
        "Tenor 1": ["E4/h F4/h", "E4/w"], "Tenor 2": ["C4/h C4/h", "C4/w"],
        "Bass 1": ["G3/h F3/h", "G3/w"], "Bass 2": ["C3/h F2/h", "C3/w"],
    }
    return lines


def main():
    marks = {0: {"time": (4, 4), "tempo": 80}}
    write_score("satb_open", "Open SATB Hymn",
                [voice_part("Soprano", S), voice_part("Alto", A), voice_part("Tenor", T),
                 voice_part("Bass", B)], marks, mscz=True)
    write_score("satb_closed", "Closed SATB Hymn",
                [voice_part("Women", pair(S, A)), voice_part("Men", pair(T, B))], marks)

    lines = eight_voice()
    marks8 = {0: {"time": (4, 4), "tempo": 60}}
    write_score("ssaattbb_open", "Eight Voices",
                [voice_part(n, m) for n, m in lines.items()], marks8, mscz=True)
    closed = []
    for section in ("Soprano", "Alto", "Tenor", "Bass"):
        closed.append(voice_part(section, pair(lines[f"{section} 1"], lines[f"{section} 2"])))
    write_score("ssaattbb_closed", "Eight Voices", closed, marks8, mscz=True)

    piano = {"name": "Piano", "id": "piano", "instrumentId": "keyboard.piano", "clefs": ["G", "F"],
             "program": 0,
             "staves": [["E5+C5/w", "G5+C5/w", "C5+A4/w", "D5+B4/w"],
                        ["C3+G3/w", "E3+G3/w", "F3+A3/w", "G2+G3/w"]]}
    write_score("satb_piano", "SATB with Piano",
                [voice_part("Soprano", S), voice_part("Alto", A), voice_part("Tenor", T),
                 voice_part("Bass", B), piano], marks)

    # Ties (within and across bars), triplets, dots, a tempo change and a 3/4 bar.
    rhythm = ["C5/h~ C5/q D5/q~", "D5/q (3 E5/e F5/e G5/e ) A5/h", "G5/q. F5/e E5/h~", "E5/q D5/q C5/q"]
    rhythm_marks = {0: {"time": (4, 4), "tempo": 60}, 2: {"tempo": 120}, 3: {"time": (3, 4)}}
    write_score("ties_tuplets_tempo", "Rhythm Test", [voice_part("Soprano", rhythm)], rhythm_marks)

    # A one-beat pickup in 3/4.
    write_score("pickup", "Pickup Test",
                [voice_part("Soprano", ["G4/q", "C5/q D5/q E5/q", "D5/h."]),
                 voice_part("Alto", ["G4/q", "G4/h G4/q", "G4/h."])],
                {0: {"time": (3, 4), "tempo": 90}}, measure_len={0: Fraction(1, 4)})

    # Repeat of bars 1-2 with first and second endings written as plain bars.
    write_score("repeat", "Repeat Test",
                [voice_part("Soprano", ["C5/w", "D5/w", "E5/w"])],
                {0: {"time": (4, 4), "tempo": 120, "startRepeat": True}, 1: {"endRepeat": 2}})


if __name__ == "__main__":
    main()
