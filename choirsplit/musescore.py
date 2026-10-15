"""Read MuseScore files (.mscz/.mscx) into a music21 Score.

The native reader understands the MuseScore 2, 3 and 4 file formats and keeps
what matters for practice tracks: parts and staves, measures, voices, notes,
chords, rests, ties, tuplets, tempo, time signatures, pickups and repeats.
Grace notes, ornaments, dynamics and layout are skipped.

Mode (``load(path, mode=...)`` or the CHOIRSPLIT_MUSESCORE_MODE env var):
  auto   - native reader; if it fails, convert with the MuseScore program (default)
  native - native reader only
  cli    - always convert with the MuseScore program (CHOIRSPLIT_MUSESCORE or PATH)
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from music21 import (
    bar, chord, instrument, key, metadata, meter, note, pitch, spanner, stream, tempo, tie,
)

from .loaders import ScoreLoadError
from .mscx_utils import (
    duration_ql, find_musescore_cli, fraction_to_ql, instrument_kind, parse_fraction,
    read_mscx_bytes, tempo_to_bpm, tpc_to_name,
)

MODE_ENV = "CHOIRSPLIT_MUSESCORE_MODE"
MODES = ("auto", "native", "cli")
CLI_TIMEOUT = 300

OTTAVA_NAMES = ["8va", "8vb", "15ma", "15mb", "22ma", "22mb"]  # MuseScore 2 numeric subtypes
OTTAVA_SHIFTS = {"8va": 12, "8vb": -12, "15ma": 24, "15mb": -24, "22ma": 36, "22mb": -36}

GRACE_TAGS = {
    "acciaccatura", "appoggiatura", "grace4", "grace16", "grace32",
    "grace8after", "grace16after", "grace32after",
}


def load(path: str | Path, mode: str | None = None) -> stream.Score:
    path = Path(path)
    mode = (mode or os.environ.get(MODE_ENV) or "auto").strip().lower()
    if mode not in MODES:
        raise ScoreLoadError(f"unknown MuseScore mode {mode!r} (use one of {', '.join(MODES)})")
    if mode == "cli":
        return load_with_cli(path)
    try:
        return parse_native(path)
    except ScoreLoadError as exc:
        if mode == "native" or find_musescore_cli() is None:
            raise
        try:
            return load_with_cli(path)
        except ScoreLoadError as cli_exc:
            raise ScoreLoadError(f"{exc}; MuseScore conversion also failed: {cli_exc}") from exc


# --------------------------------------------------------------------------- CLI


def load_with_cli(path: Path) -> stream.Score:
    """Convert with the MuseScore program to MusicXML and load that."""
    from .loaders import load_score

    cmd = find_musescore_cli()
    if cmd is None:
        raise ScoreLoadError(
            "MuseScore program not found (install MuseScore or set CHOIRSPLIT_MUSESCORE)")
    # A flatpak MuseScore can't see the host's /tmp, so convert under the home directory.
    tmp_root = None
    if cmd[0] == "flatpak":
        tmp_root = Path.home() / ".cache" / "choirsplit"
        tmp_root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    with tempfile.TemporaryDirectory(prefix="choirsplit-mscore-", dir=tmp_root) as tmp:
        out = Path(tmp) / (path.stem + ".musicxml")
        try:
            proc = subprocess.run(
                [*cmd, "-o", str(out), str(path.resolve())],
                capture_output=True, text=True, timeout=CLI_TIMEOUT, env=env,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScoreLoadError(f"could not run MuseScore: {exc}") from exc
        if not out.exists() or out.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            raise ScoreLoadError(
                f"MuseScore could not convert {path.name} (exit {proc.returncode})"
                + (": " + " | ".join(detail) if detail else ""))
        return load_score(out)


# ------------------------------------------------------------------------ native


def parse_native(path: str | Path) -> stream.Score:
    path = Path(path)
    try:
        data = read_mscx_bytes(path)
    except Exception as exc:
        raise ScoreLoadError(f"could not read {path.name}: {exc}") from exc
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ScoreLoadError(f"{path.name} is not valid MuseScore XML: {exc}") from exc
    if root.tag != "museScore":
        raise ScoreLoadError(f"{path.name} is not a MuseScore file")
    version = _major_version(root.get("version"))
    if version < 2:
        raise ScoreLoadError(
            f"{path.name} is a MuseScore {root.get('version')} file; only MuseScore 2-4 files "
            "can be read without MuseScore installed (re-save it in a newer MuseScore)")
    score_el = root.find("Score")
    if score_el is None:
        raise ScoreLoadError(f"{path.name} contains no score")
    try:
        return _Reader(score_el).build(path.stem)
    except ScoreLoadError:
        raise
    except Exception as exc:  # malformed or unexpected content
        raise ScoreLoadError(f"could not read {path.name}: {type(exc).__name__}: {exc}") from exc


def _major_version(text: str | None) -> int:
    try:
        return int(str(text).split(".")[0])
    except ValueError:
        return 0


def _text(el: ET.Element | None) -> str:
    """Plain text of an element, dropping inline markup such as <font>."""
    if el is None:
        return ""
    return " ".join("".join(el.itertext()).split())


def _int(text: str | None, default: int = 0) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return default


@dataclass
class _Event:
    offset: Fraction  # quarter lengths from the measure start
    voice: int
    obj: object


@dataclass
class _Measure:
    length: Fraction
    events: list[_Event] = field(default_factory=list)
    start_repeat: bool = False
    end_repeat: int = 0  # number of times played; 0 = no repeat


@dataclass
class _Volta:
    start: int  # measure index
    length: int  # measures
    numbers: list[int]


class _Reader:
    def __init__(self, score_el: ET.Element):
        self.score_el = score_el
        self.division = _int(score_el.findtext("Division"), 480) or 480
        # A part's <Staff> entries match the score's <Staff> elements in order (MuseScore 5
        # drops the ids from the part's entries).
        self.staff_list = score_el.findall("Staff")
        self.voltas: list[_Volta] = []
        self.legacy_tie_ids: set[str] = set()
        self.open_spanners: dict[str, tuple] = {}  # MuseScore 2 id -> ("volta"|"ottava", ...)
        self.ottavas: list[tuple] = []  # (measure, offset, end measure delta, end offset, shift)
        self.ottava_spans: list[tuple[Fraction, Fraction, int]] = []  # resolved per staff

    # -- top level

    def build(self, fallback_title: str) -> stream.Score:
        score = stream.Score()
        score.metadata = metadata.Metadata()
        title = self._title() or fallback_title
        score.metadata.title = title
        composer = self._meta("composer")
        if composer:
            score.metadata.composer = composer

        staves: list[tuple[stream.Part, list[_Measure]]] = []
        part_index = 0
        staff_pos = 0
        for part_el in self.score_el.findall("Part"):
            inst_el = part_el.find("Instrument")
            long_name = _text(inst_el.find("longName")) if inst_el is not None else ""
            short_name = _text(inst_el.find("shortName")) if inst_el is not None else ""
            track_name = _text(part_el.find("trackName")) or (
                _text(inst_el.find("trackName")) if inst_el is not None else "")
            iid = (inst_el.findtext("instrumentId") if inst_el is not None else None) or (
                inst_el.get("id") if inst_el is not None else None)
            kind = instrument_kind(iid, [track_name, long_name])
            staff_els = []
            for s in part_el.findall("Staff"):
                staff_el = self.staff_list[staff_pos] if staff_pos < len(self.staff_list) else None
                staff_pos += 1
                group = s.find("StaffType")
                if s.find("linkedTo") is not None:
                    continue  # a linked copy (e.g. tablature) of another staff
                if group is not None and group.get("group") in ("percussion", "tablature"):
                    continue
                if staff_el is not None:
                    staff_els.append(staff_el)
            if kind == "percussion" or not staff_els:
                continue
            part_index += 1
            name = track_name or long_name
            voice_names = self._channel_switch_names(staff_els)
            if voice_names:
                name = "/".join(voice_names)
            for n, staff_el in enumerate(staff_els, start=1):
                if len(staff_els) > 1:
                    part = stream.PartStaff(id=f"P{part_index}-Staff{n}")
                else:
                    part = stream.Part(id=f"P{part_index}")
                part.partName = name or None
                part.partAbbreviation = short_name or None
                part.insert(0, self._instrument(kind, name or long_name))
                measures = self._read_staff(staff_el)
                staves.append((part, measures))

        if not staves:
            raise ScoreLoadError("the score has no pitched staves")
        # Repeat barlines and voltas are written on the top staff only; apply them everywhere.
        n_measures = max(len(m) for _, m in staves)
        start_rep = [False] * n_measures
        end_rep = [0] * n_measures
        for _, measures in staves:
            for i, m in enumerate(measures):
                start_rep[i] |= m.start_repeat
                end_rep[i] = max(end_rep[i], m.end_repeat)
        for part, measures in staves:
            self._fill_part(part, measures, start_rep, end_rep)
            score.insert(0, part)
        return score

    def _meta(self, name: str) -> str:
        for tag in self.score_el.findall("metaTag"):
            if tag.get("name") == name:
                return _text(tag)
        return ""

    def _title(self) -> str:
        title = self._meta("workTitle")
        if title:
            return title
        for staff in self.staff_list:
            for box in staff.findall("VBox"):
                for t in box.findall("Text"):
                    if (t.findtext("style") or "").strip().lower() == "title":
                        return _text(t.find("text"))
        return ""

    @staticmethod
    def _channel_switch_names(staff_els: list[ET.Element]) -> list[str]:
        """Voice names from hidden "channelSwitch" staff texts (closed-score templates)."""
        names: dict[int, str] = {}
        for staff in staff_els:
            for cs in staff.iter("channelSwitch"):
                v = _int(cs.get("voice"), -1)
                cs_name = (cs.get("name") or "").strip()
                if v in (0, 1) and cs_name and v not in names:
                    names[v] = cs_name
        if len(names) == 2 and names[0] and names[1] and names[0].lower() != names[1].lower():
            return [names[0], names[1]]
        return []

    @staticmethod
    def _instrument(kind: str, name: str) -> instrument.Instrument:
        cls = {
            "piano": instrument.Piano, "organ": instrument.Organ,
            "keyboard": instrument.Harpsichord, "voice": instrument.Vocalist,
        }.get(kind, instrument.Instrument)
        inst = cls()
        if name:
            inst.instrumentName = name
        return inst

    # -- staves and measures

    def _read_staff(self, staff_el: ET.Element | None) -> list[_Measure]:
        if staff_el is None:
            return []
        self.ottavas, self.ottava_spans, self.open_spanners = [], [], {}
        measures: list[_Measure] = []
        starts: list[Fraction] = []
        ts_len = Fraction(4)  # 4/4 until a time signature says otherwise
        start = Fraction(0)  # measure start in quarter lengths (MuseScore 2 ticks)
        for index, m_el in enumerate(staff_el.findall("Measure")):
            ts_el = m_el.find(".//TimeSig")
            if ts_el is not None:
                n, d = _int(ts_el.findtext("sigN"), 4), _int(ts_el.findtext("sigD"), 4)
                if n > 0 and d > 0:
                    ts_len = Fraction(4 * n, d)
            length = ts_len
            if m_el.get("len"):
                try:
                    length = fraction_to_ql(parse_fraction(m_el.get("len")))
                except ValueError:
                    pass
            m = _Measure(length=length)
            starts.append(start)
            m.start_repeat = m_el.find("startRepeat") is not None
            if m_el.find("endRepeat") is not None:
                m.end_repeat = max(_int(m_el.findtext("endRepeat"), 2), 2)
            voices = m_el.findall("voice")
            if voices:
                for v, v_el in enumerate(voices):
                    self._read_sequence(list(v_el), v, m, index)
            else:
                self._read_legacy(m_el, m, index, start)
            measures.append(m)
            start += length
        self._apply_ottavas(measures, starts, start)
        return measures

    def _apply_ottavas(self, measures: list[_Measure], starts: list[Fraction], end: Fraction):
        """MuseScore stores notes under an 8va/8vb line at their written octave."""
        spans = list(self.ottava_spans)
        for index, offset, d_measures, d_offset, shift in self.ottavas:
            a = starts[index] + offset
            j = index + d_measures
            b = (starts[j] if j < len(starts) else end) + offset + d_offset
            spans.append((a, b, shift))
        for kind, *data in self.open_spanners.values():  # never closed: runs to the end
            if kind == "ottava":
                spans.append((data[0], end, data[1]))
            else:
                self._add_volta(data[1], data[0], 1)
        if not spans:
            return
        for m, m_start in zip(measures, starts):
            for ev in m.events:
                if not isinstance(ev.obj, (note.Note, chord.Chord)):
                    continue
                t = m_start + ev.offset
                octaves = sum(shift for a, b, shift in spans if a <= t < b) // 12
                if octaves:
                    for n in ([ev.obj] if isinstance(ev.obj, note.Note) else ev.obj.notes):
                        n.pitch.octave = (n.pitch.octave or 4) + octaves

    @staticmethod
    def _ottava_shift(el: ET.Element) -> int:
        sub = (el.findtext("subtype") or "").strip()
        if sub.isdigit() and int(sub) < len(OTTAVA_NAMES):
            sub = OTTAVA_NAMES[int(sub)]
        return OTTAVA_SHIFTS.get(sub, 0)

    def _read_sequence(self, children: list[ET.Element], voice: int, m: _Measure, index: int):
        """MuseScore 3/4: one <voice> holds elements in time order from the measure start."""
        cursor = Fraction(0)
        ratios: list[Fraction] = []
        for el in children:
            tag = el.tag
            if tag in ("Chord", "Rest"):
                ratio = Fraction(1)
                for r in ratios:
                    ratio *= r
                cursor += self._add_chord_rest(el, voice, cursor, ratio, m, legacy=False)
            elif tag == "Tuplet":
                normal, actual = _int(el.findtext("normalNotes"), 1), _int(el.findtext("actualNotes"), 1)
                ratios.append(Fraction(normal, actual) if normal > 0 and actual > 0 else Fraction(1))
            elif tag == "endTuplet":
                if ratios:
                    ratios.pop()
            elif tag == "location":
                try:
                    cursor += fraction_to_ql(parse_fraction(el.findtext("fractions") or "0"))
                except ValueError:
                    pass
            elif tag == "Spanner" and el.get("type") == "Volta":
                self._volta_v3(el, index)
            elif tag == "Spanner" and el.get("type") == "Ottava":
                self._ottava_v3(el, index, cursor)
            else:
                self._add_marking(el, voice, cursor, m)

    def _read_legacy(self, m_el: ET.Element, m: _Measure, index: int, start: Fraction):
        """MuseScore 2: one flat list; <tick> jumps (absolute ticks), <track> picks the voice."""
        cursor = Fraction(0)
        tuplets: dict[str, Fraction] = {}
        for el in m_el:
            tag = el.tag
            if tag == "tick":
                cursor = Fraction(_int(el.text), self.division) - start
            elif tag in ("Chord", "Rest"):
                voice = _int(el.findtext("track"), 0) % 4
                ratio = tuplets.get((el.findtext("Tuplet") or "").strip(), Fraction(1))
                cursor += self._add_chord_rest(el, voice, cursor, ratio, m, legacy=True)
            elif tag == "Tuplet" and el.get("id") is not None:
                normal, actual = _int(el.findtext("normalNotes"), 1), _int(el.findtext("actualNotes"), 1)
                ratio = Fraction(normal, actual) if normal > 0 and actual > 0 else Fraction(1)
                parent = (el.findtext("Tuplet") or "").strip()
                tuplets[el.get("id")] = ratio * tuplets.get(parent, Fraction(1))
            elif tag in ("Volta", "Ottava") and el.get("id") is not None:
                if tag == "Volta":
                    self.open_spanners[el.get("id")] = ("volta", index, el.findtext("endings"))
                else:
                    self.open_spanners[el.get("id")] = ("ottava", start + cursor, self._ottava_shift(el))
            elif tag == "endSpanner":
                self._end_spanner_v2(el.get("id"), index, start + cursor, cursor)
            else:
                self._add_marking(el, 0, cursor, m)

    # -- elements

    def _add_chord_rest(self, el: ET.Element, voice: int, offset: Fraction, ratio: Fraction,
                        m: _Measure, legacy: bool) -> Fraction:
        """Add a chord or rest at `offset`; return how far it moves the cursor."""
        if any(child.tag in GRACE_TAGS for child in el):
            return Fraction(0)
        dtype = (el.findtext("durationType") or "quarter").strip()
        if dtype == "measure":
            ql = m.length
            d_el = el.find("duration")
            if d_el is not None:
                try:
                    if d_el.get("z") and d_el.get("n"):
                        ql = Fraction(4 * _int(d_el.get("z")), _int(d_el.get("n"), 1) or 1)
                    elif d_el.text and d_el.text.strip():
                        ql = fraction_to_ql(parse_fraction(d_el.text))
                except (ValueError, ZeroDivisionError):
                    pass
        else:
            try:
                ql = duration_ql(dtype, _int(el.findtext("dots"), 0)) * ratio
            except (KeyError, ValueError):
                return Fraction(0)
        if ql <= 0:
            return Fraction(0)
        if el.tag == "Rest":
            m.events.append(_Event(offset, voice, note.Rest(quarterLength=ql)))
            return ql
        notes = []
        for n_el in el.findall("Note"):
            n = self._note(n_el, legacy)
            if n is not None:
                notes.append(n)
        if not notes:
            m.events.append(_Event(offset, voice, note.Rest(quarterLength=ql)))
            return ql
        if len(notes) == 1:
            obj = notes[0]
            obj.quarterLength = ql
        else:
            obj = chord.Chord(notes, quarterLength=ql)
        m.events.append(_Event(offset, voice, obj))
        return ql

    def _note(self, n_el: ET.Element, legacy: bool) -> note.Note | None:
        midi = _int(n_el.findtext("pitch"), -1)
        if not 0 <= midi <= 127:
            return None
        p = None
        tpc_text = n_el.findtext("tpc")
        if tpc_text is not None:
            try:
                p = pitch.Pitch(tpc_to_name(_int(tpc_text, 14)))
                p.octave = 4
                p.octave += round((midi - p.ps) / 12)
                if p.ps != midi:
                    p = None
            except Exception:
                p = None
        if p is None:
            p = pitch.Pitch(midi=midi)
        n = note.Note(p)
        start = stop = False
        if legacy:
            t_el = n_el.find("Tie")
            if t_el is not None:
                start = True
                if t_el.get("id") is not None:
                    self.legacy_tie_ids.add(t_el.get("id"))
            for end in n_el.findall("endSpanner"):
                if end.get("id") in self.legacy_tie_ids:
                    stop = True
        else:
            for sp in n_el.findall("Spanner"):
                if sp.get("type") != "Tie":
                    continue
                start |= sp.find("next") is not None
                stop |= sp.find("prev") is not None
        if start and stop:
            n.tie = tie.Tie("continue")
        elif start:
            n.tie = tie.Tie("start")
        elif stop:
            n.tie = tie.Tie("stop")
        return n

    def _add_marking(self, el: ET.Element, voice: int, offset: Fraction, m: _Measure):
        tag = el.tag
        if tag == "TimeSig" and voice == 0:
            n, d = _int(el.findtext("sigN"), 0), _int(el.findtext("sigD"), 0)
            if n > 0 and d > 0:
                try:
                    m.events.append(_Event(offset, -1, meter.TimeSignature(f"{n}/{d}")))
                except Exception:
                    pass
        elif tag == "KeySig" and voice == 0:
            acc = el.findtext("concertKey") or el.findtext("accidental")
            if acc is not None and -7 <= _int(acc, 99) <= 7:
                m.events.append(_Event(offset, -1, key.KeySignature(_int(acc))))
        elif tag == "Tempo":
            try:
                qps = float(el.findtext("tempo") or "")
            except ValueError:
                return
            if qps > 0:
                mm = tempo.MetronomeMark(number=round(tempo_to_bpm(qps), 3), referent=1.0)
                mm.numberImplicit = False
                m.events.append(_Event(offset, -1, mm))

    # -- voltas

    def _volta_v3(self, sp: ET.Element, index: int):
        volta = sp.find("Volta")
        nxt = sp.find("next")
        if volta is None or nxt is None:
            return  # the closing half of the spanner
        length = _int(nxt.findtext("location/measures"), 1)
        fractions = nxt.findtext("location/fractions")
        if fractions:  # a volta ending part-way into a measure still covers it
            try:
                if parse_fraction(fractions) > 0:
                    length += 1
            except ValueError:
                pass
        self._add_volta(volta.findtext("endings"), index, max(length, 1))

    def _ottava_v3(self, sp: ET.Element, index: int, cursor: Fraction):
        ottava, nxt = sp.find("Ottava"), sp.find("next")
        if ottava is None or nxt is None:
            return
        d_offset = Fraction(0)
        try:
            d_offset = fraction_to_ql(parse_fraction(nxt.findtext("location/fractions") or "0"))
        except ValueError:
            pass
        self.ottavas.append((index, cursor, _int(nxt.findtext("location/measures"), 0),
                             d_offset, self._ottava_shift(ottava)))

    def _end_spanner_v2(self, sid: str | None, index: int, at: Fraction, cursor: Fraction):
        """MuseScore 2: <endSpanner id> sits where a volta or ottava line ends."""
        opened = self.open_spanners.pop(sid or "", None)
        if opened is None:
            return
        if opened[0] == "volta":
            _, start_index, endings = opened
            last = index - 1 if cursor == 0 else index  # ends at a barline: the previous measure
            self._add_volta(endings, start_index, max(last - start_index + 1, 1))
        else:
            self.ottava_spans.append((opened[1], at, opened[2]))

    def _add_volta(self, endings: str | None, index: int, length: int):
        numbers = [_int(x, 0) for x in (endings or "").replace(" ", "").split(",")]
        numbers = [n for n in numbers if n > 0]
        volta = _Volta(index, length, numbers)
        if numbers and volta not in self.voltas:  # some files repeat voltas on every staff
            self.voltas.append(volta)

    # -- music21 output

    def _fill_part(self, part: stream.Part, measures: list[_Measure],
                   start_rep: list[bool], end_rep: list[int]):
        multi_voice = any(ev.voice > 0 and not isinstance(ev.obj, note.Rest)
                          for m in measures for ev in m.events)
        m21_measures = []
        offset = Fraction(0)
        for i, m in enumerate(measures):
            mm = stream.Measure(number=i + 1)
            voices: dict[int, stream.Voice] = {}
            for ev in sorted(m.events, key=lambda e: (e.offset, e.voice)):
                if ev.offset < 0:
                    continue
                if ev.voice < 0:
                    mm.insert(ev.offset, ev.obj)
                elif not multi_voice:
                    if ev.voice == 0:
                        mm.insert(ev.offset, ev.obj)
                else:
                    if ev.voice not in voices:
                        voices[ev.voice] = stream.Voice(id=str(ev.voice + 1))
                    voices[ev.voice].insert(ev.offset, ev.obj)
            for v in sorted(voices):
                mm.insert(0, voices[v])
            if start_rep[i]:
                mm.leftBarline = bar.Repeat(direction="start")
            if end_rep[i]:
                mm.rightBarline = bar.Repeat(direction="end", times=end_rep[i])
            if i == 0 and measures and m.length < self._nominal_length(m):
                mm.paddingLeft = float(self._nominal_length(m) - m.length)
            part.insert(offset, mm)
            m21_measures.append(mm)
            offset += m.length
        for volta in self.voltas:
            span = m21_measures[volta.start:volta.start + volta.length]
            if span:
                part.insert(0, spanner.RepeatBracket(
                    span, number=",".join(str(n) for n in volta.numbers)))

    @staticmethod
    def _nominal_length(m: _Measure) -> Fraction:
        for ev in m.events:
            if isinstance(ev.obj, meter.TimeSignature):
                return Fraction(ev.obj.barDuration.quarterLength).limit_denominator(1024)
        return m.length
