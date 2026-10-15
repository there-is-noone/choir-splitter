"""Turn a music21 score into per-voice note lists on a shared timeline (seconds)."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from music21 import chord, note, stream

ACCOMPANIMENT_WORDS = (
    "piano", "pno", "organ", "org.", "keyboard", "kbd", "harp", "guitar",
    "accomp", "acc.", "harmonium", "harpsichord", "celesta", "reduction",
)

# Names used when one staff carries two voices but the staff name doesn't say which.
STAFF_PAIR_NAMES = {
    "women": ("Soprano", "Alto"),
    "ladies": ("Soprano", "Alto"),
    "upper voices": ("Soprano", "Alto"),
    "treble": ("Soprano", "Alto"),
    "men": ("Tenor", "Bass"),
    "lower voices": ("Tenor", "Bass"),
}

ABBREVIATIONS = {
    "s": "Soprano", "sop": "Soprano", "a": "Alto", "alt": "Alto",
    "t": "Tenor", "ten": "Tenor", "b": "Bass", "bar": "Baritone",
    "mz": "Mezzo", "ms": "Mezzo", "ct": "Countertenor",
}

DEFAULT_VELOCITY = 80

_NUMBER_RE = re.compile(r"\d+|I{1,3}|IV|V|VI{1,3}")


@dataclass
class NoteEvent:
    start: float  # seconds
    duration: float  # seconds
    pitch: float  # MIDI pitch (float, so microtones survive)
    velocity: int = DEFAULT_VELOCITY


@dataclass
class Track:
    name: str
    kind: str = "voice"  # "voice" or "accompaniment"
    notes: list[NoteEvent] = field(default_factory=list)

    @property
    def end(self) -> float:
        return max((n.start + n.duration for n in self.notes), default=0.0)


@dataclass
class ScoreTracks:
    title: str
    tracks: list[Track]
    duration: float  # seconds; every rendered track is padded to this length

    @property
    def voices(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "voice"]

    @property
    def accompaniment(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "accompaniment"]


class TempoMap:
    """Converts quarter-length offsets into seconds, honouring tempo changes."""

    def __init__(self, score: stream.Score, bpm: float | None = None, scale: float = 1.0):
        if scale <= 0:
            raise ValueError("tempo scale must be positive")
        self.starts: list[float] = []
        self.sec_per_q: list[float] = []
        if bpm is not None:
            if bpm <= 0:
                raise ValueError("bpm must be positive")
            self.starts = [0.0]
            self.sec_per_q = [60.0 / bpm]
        else:
            for start, _end, mm in score.metronomeMarkBoundaries():
                spq = mm.secondsPerQuarter()
                if self.starts and abs(self.starts[-1] - start) < 1e-9:
                    self.sec_per_q[-1] = spq
                else:
                    self.starts.append(float(start))
                    self.sec_per_q.append(spq)
            if not self.starts:
                self.starts, self.sec_per_q = [0.0], [60.0 / 96]
        self.sec_per_q = [s / scale for s in self.sec_per_q]
        # Seconds elapsed at the start of each tempo segment.
        self.seconds_at: list[float] = [0.0]
        for i in range(1, len(self.starts)):
            span = self.starts[i] - self.starts[i - 1]
            self.seconds_at.append(self.seconds_at[-1] + span * self.sec_per_q[i - 1])

    def seconds(self, offset: float) -> float:
        i = max(bisect.bisect_right(self.starts, offset) - 1, 0)
        return self.seconds_at[i] + (offset - self.starts[i]) * self.sec_per_q[i]


def is_accompaniment(part: stream.Part) -> bool:
    from music21 import instrument

    inst = part.getInstrument(returnDefault=False)
    if isinstance(inst, (instrument.KeyboardInstrument, instrument.Harp, instrument.Guitar)):
        return True
    names = " ".join(
        x for x in (part.partName, part.partAbbreviation,
                    inst.instrumentName if inst else None, inst.partName if inst else None) if x
    ).lower()
    return any(w in names for w in ACCOMPANIMENT_WORDS)


def _group_staves(score: stream.Score) -> list[list[stream.Part]]:
    """Group PartStaff objects belonging to one instrument (e.g. both piano staves)."""
    groups: dict[str, list[stream.Part]] = {}
    order: list[str] = []
    for i, part in enumerate(score.parts):
        pid = str(part.id)
        if isinstance(part, stream.PartStaff):
            key = re.sub(r"-Staff\d+$", "", pid)
        else:
            key = f"{pid}#{i}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(part)
    return [groups[k] for k in order]


def _collect_voices(part: stream.Part, offset_map) -> dict[str, list]:
    """Return {voice_id: [(abs_offset_q, element), ...]} for every note/chord in a part."""
    voices: dict[str, list] = {}
    measures = list(part.getElementsByClass(stream.Measure))
    if not measures:  # e.g. un-measured MIDI imports
        for el in part.flatten().notes:
            voices.setdefault("1", []).append((offset_map(el, part), el))
        return voices
    for m in measures:
        m_off = m.getOffsetBySite(part)
        for el in m.notes:  # notes outside Voice containers belong to voice 1
            voices.setdefault("1", []).append((m_off + el.getOffsetBySite(m), el))
        for v_index, v in enumerate(m.voices):
            # music21 uses MusicXML voice numbers as ids; fall back to position otherwise.
            vid = str(v.id)
            if not vid.isdigit() or len(vid) > 2:
                vid = str(v_index + 1)
            v_off = m_off + v.getOffsetBySite(m)
            for el in v.notes:
                voices.setdefault(vid, []).append((v_off + el.getOffsetBySite(v), el))
    return voices


def _element_pitches(el, chord_mode: str) -> list[tuple[float, object]]:
    """Return [(midi, tie)] for a note or chord according to chord_mode (all/top/bottom)."""
    if isinstance(el, note.Note):
        return [(el.pitch.ps, el.tie)]
    if isinstance(el, chord.Chord):
        notes = sorted(el.notes, key=lambda n: n.pitch.ps)
        if chord_mode == "top":
            notes = notes[-1:]
        elif chord_mode == "bottom":
            notes = notes[:1]
        return [(n.pitch.ps, n.tie if n.tie is not None else el.tie) for n in notes]
    return []  # unpitched / percussion


def _velocity(el) -> int:
    vol = getattr(el, "volume", None)
    if vol is not None and vol.velocity is not None:
        return int(vol.velocity)
    return DEFAULT_VELOCITY


def _events_from_elements(items, tempo_map: TempoMap, chord_mode: str) -> list[NoteEvent]:
    """Build note events, merging tied notes so they don't re-attack."""
    items = sorted(items, key=lambda x: x[0])
    events: list[NoteEvent] = []
    open_ties: dict[float, tuple[NoteEvent, float]] = {}  # pitch -> (event, end offset in q)
    for off, el in items:
        ql = float(el.duration.quarterLength)
        if ql <= 0 or el.duration.isGrace:
            continue
        start_s = tempo_map.seconds(off)
        end_s = tempo_map.seconds(off + ql)
        for midi, tie in _element_pitches(el, chord_mode):
            tie_type = tie.type if tie is not None else None
            pending = open_ties.get(midi)
            if tie_type in ("stop", "continue") and pending and abs(pending[1] - off) < 1e-6:
                ev = pending[0]
                ev.duration = end_s - ev.start
                if tie_type == "continue":
                    open_ties[midi] = (ev, off + ql)
                else:
                    del open_ties[midi]
                continue
            ev = NoteEvent(start_s, end_s - start_s, midi, _velocity(el))
            events.append(ev)
            if tie_type in ("start", "continue"):
                open_ties[midi] = (ev, off + ql)
            else:
                open_ties.pop(midi, None)
    events.sort(key=lambda e: (e.start, e.pitch))
    return events


def _split_names(part_name: str, count: int) -> list[str]:
    """Derive names for `count` voices sharing one staff, e.g. "Soprano Alto" -> [Soprano, Alto]."""
    clean = part_name.strip()
    low = clean.lower()
    if count == 2 and low in STAFF_PAIR_NAMES:
        return list(STAFF_PAIR_NAMES[low])
    tokens = [t for t in re.split(r"[\s/&+,.]+|\band\b", clean) if t]
    # "Soprano 1 2", "Tenor I/II" -> "Soprano 1", "Soprano 2"
    numbers = []
    while tokens and _NUMBER_RE.fullmatch(tokens[-1]):
        numbers.insert(0, tokens.pop())
    if tokens and len(numbers) == count:
        base = " ".join(ABBREVIATIONS.get(t.lower(), t) for t in tokens)
        return [f"{base} {n}" for n in numbers]
    expanded = []
    for t in tokens + numbers:
        m = re.fullmatch(r"([A-Za-z]+?)(\d+|I{1,3}|IV)", t)  # "S1", "TII"
        if m and m.group(1).lower() in ABBREVIATIONS:
            expanded.append(f"{ABBREVIATIONS[m.group(1).lower()]} {m.group(2)}")
        else:
            expanded.append(ABBREVIATIONS.get(t.lower(), t))
    # "S A T B", "Soprano/Alto", "S1 S2"
    if len(expanded) == count and all(e[0].isalpha() for e in expanded):
        return expanded
    # "SA" / "TB" / "SATB" packed abbreviations
    if len(clean) == count and clean.isalpha() and clean.isupper():
        return [ABBREVIATIONS.get(c.lower(), c) for c in clean]
    # "Soprano 1 2", "Tenor I II" -> "Soprano 1", "Soprano 2"
    base = clean or "Voice"
    return [f"{base} {i + 1}" for i in range(count)]


def _unique_names(tracks: list[Track]) -> None:
    seen: dict[str, int] = {}
    for t in tracks:
        seen[t.name] = seen.get(t.name, 0) + 1
    counters: dict[str, int] = {}
    for t in tracks:
        if seen[t.name] > 1:
            counters[t.name] = counters.get(t.name, 0) + 1
            t.name = f"{t.name} {counters[t.name]}"


def _has_chordal_voice(items) -> bool:
    chords = sum(1 for _, el in items if isinstance(el, chord.Chord) and len(el.pitches) >= 2)
    return chords > 0 and chords >= 0.3 * max(len(items), 1)


def extract_tracks(
    score: stream.Score,
    *,
    bpm: float | None = None,
    tempo_scale: float = 1.0,
    split_voices: bool = True,
    split_chords: bool = True,
    include_accompaniment: bool = True,
    title: str | None = None,
) -> ScoreTracks:
    """Split a score into one Track per sung voice (plus accompaniment tracks)."""
    try:
        expanded = score.expandRepeats()
        if expanded is not None and len(expanded.parts) == len(score.parts):
            score = expanded
    except Exception:  # repeat structure music21 can't resolve: play as written
        pass

    tempo_map = TempoMap(score, bpm=bpm, scale=tempo_scale)

    def flat_offset(el, part):
        return el.getOffsetInHierarchy(part)

    tracks: list[Track] = []
    for group in _group_staves(score):
        first = group[0]
        name = (first.partName or first.getInstrument().instrumentName or "").strip() or f"Part {len(tracks) + 1}"
        accompaniment = is_accompaniment(first)
        if accompaniment:
            if not include_accompaniment:
                continue
            items = []
            for staff in group:
                for vitems in _collect_voices(staff, flat_offset).values():
                    items.extend(vitems)
            tracks.append(Track(name, "accompaniment", _events_from_elements(items, tempo_map, "all")))
            continue

        for staff_index, staff in enumerate(group):
            staff_name = name if len(group) == 1 else f"{name} {staff_index + 1}"
            voices = _collect_voices(staff, flat_offset)
            voices = {k: v for k, v in voices.items() if v}
            if not voices:
                continue
            if split_voices and len(voices) > 1:
                ids = sorted(voices, key=lambda k: int(k) if k.isdigit() else 99)
                names = _split_names(staff_name, len(ids))
                for vid, vname in zip(ids, names):
                    # In a voice shared with others, keep the line's outer note of any chord.
                    mode = "top" if vid == ids[0] else ("bottom" if vid == ids[-1] else "all")
                    tracks.append(Track(vname, "voice", _events_from_elements(voices[vid], tempo_map, mode)))
            else:
                items = [x for v in voices.values() for x in v]
                pair = _split_names(staff_name, 2)
                named_pair = pair != [f"{staff_name.strip() or 'Voice'} {i}" for i in (1, 2)]
                if split_chords and named_pair and _has_chordal_voice(items):
                    # Hymnal style: two parts written as chords in a single voice.
                    tracks.append(Track(pair[0], "voice", _events_from_elements(items, tempo_map, "top")))
                    tracks.append(Track(pair[1], "voice", _events_from_elements(items, tempo_map, "bottom")))
                else:
                    tracks.append(Track(staff_name, "voice", _events_from_elements(items, tempo_map, "all")))

    tracks = [t for t in tracks if t.notes]
    _unique_names(tracks)
    end_q = float(score.highestTime)
    duration = max([tempo_map.seconds(end_q)] + [t.end for t in tracks])
    if title is None:
        md = score.metadata
        candidates = [md.title, md.movementName] if md is not None else []
        title = next((c for c in candidates if _meaningful_title(c)), None)
    return ScoreTracks(title or "Score", tracks, duration)


def _meaningful_title(title: str | None) -> bool:
    """Reject empty titles, music21 placeholders and bare file names like 'bwv66.6.mxl'."""
    if not title or not title.strip():
        return False
    low = title.strip().lower()
    if low.startswith("music21") or low in {"untitled", "untitled score", "title"}:
        return False
    return not re.search(r"\.(mxl|musicxml|xml|midi?|krn|abc|mscz|mscx|pdf)$", low)
