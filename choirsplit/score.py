"""Turn a music21 score into per-voice note lists on a shared timeline (seconds)."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from music21 import chord, note, stream, tempo

ACCOMPANIMENT_WORDS = (
    "piano", "pno", "organ", "org.", "keyboard", "kbd", "harp", "guitar",
    "accomp", "acc.", "harmonium", "harpsichord", "celesta", "reduction",
)

# Orchestral instruments (Bach chorales with horns/oboes/continuo, etc.). Checked after
# VOICE_RE so that "Basso", "Soprano / Oboe 1" stay sung parts.
INSTRUMENT_WORDS = (
    "violin", "viola", "viol ", "cello", "violone", "contrabass", "double bass", "flute",
    "flauto", "piccolo", "recorder", "oboe", "clarinet", "bassoon", "fagott", "horn",
    "corno", "trumpet", "tromba", "trombone", "tuba", "cornet", "timpani", "percussion",
    "drum", "continuo", "strings", "sax", "lute", "theorbo", "gamba", "orchestra",
)
# Range words used for instruments too: "Alto Sax", "Bass Clarinet", "Basso continuo".
_INSTRUMENT_COMPOUND_RE = re.compile(
    r"\b(sopran\w*|alt\w*|tenor\w*|bass\w*|bariton\w*|treble)[ \t-]*(sax|clarinet|flute|recorder|"
    r"trombone|horn|drum|guitar|viol|cornet|tuba|oboe|continuo|bassoon)"
)
# Words that mark a sung part, even when its playback instrument is a piano (common in
# MusicXML exports: Monteverdi madrigals in the music21 corpus are all "Grand Piano").
VOICE_RE = re.compile(
    r"\b(sopran\w*|alt[oiu]?s?|contralto|mezzo\w*|tenor\w*|bass[oiu]?s?|basses|bariton\w*|"
    r"cant[ou]s?|quint[ou]s?|sext[ou]s?|sest[oa]|superius|discantus|contratenor|countertenor|"
    r"treble|vo(?:ice|ix|ce|x)\w*|choir|chorus|chor|coro|vocal\w*|singers?)\b"
)
# Range words: a chordal staff named just "Tenor" is split into "Tenor 1" / "Tenor 2".
_RANGE_NAME_RE = re.compile(r"(sopran\w*|alt\w*|mezzo\w*|tenor\w*|bass\w*|bariton\w*)(\s+(\d+|[IV]+))?", re.I)

# Names used when one staff carries two voices but the staff name doesn't say which.
STAFF_PAIR_NAMES = {
    "women": ("Soprano", "Alto"),
    "ladies": ("Soprano", "Alto"),
    "upper voices": ("Soprano", "Alto"),
    "upper": ("Soprano", "Alto"),  # LilyPond hymn MIDI track names
    "treble": ("Soprano", "Alto"),
    "men": ("Tenor", "Bass"),
    "lower voices": ("Tenor", "Bass"),
    "lower": ("Tenor", "Bass"),
}

ABBREVIATIONS = {
    "s": "Soprano", "sop": "Soprano", "a": "Alto", "alt": "Alto",
    "t": "Tenor", "ten": "Tenor", "b": "Bass", "bar": "Baritone",
    "mz": "Mezzo", "ms": "Mezzo", "ct": "Countertenor",
}

GENERIC_VOICE_NAMES = {"", "voice", "voices", "vocal", "vocals", "choir", "chorus", "vocalist"}
DEFAULT_VELOCITY = 80
DEFAULT_BPM = 96  # used when the score has no tempo marking at all

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
    issues: list = field(default_factory=list)  # check.Issue: OMR repairs and suspect bars
    recognised: bool = False  # read from a PDF/image by optical music recognition

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
            # Read the real tempo marks: metronomeMarkBoundaries() invents a 120 bpm mark
            # for a score without tempo (or before its first mark).
            flat = score.flatten()
            marks = []
            for ti in flat.getElementsByClass(tempo.TempoIndication):
                try:
                    mm = ti.getSoundingMetronomeMark()
                    spq = mm.secondsPerQuarter()
                except Exception:  # text-only tempo music21 can't put a number on
                    continue
                if spq and spq > 0:
                    explicit = isinstance(ti, tempo.MetronomeMark) and not ti.numberImplicit
                    marks.append((float(ti.getOffsetBySite(flat)), explicit, spq))
            # At one offset (marks repeated in every part, or "Allegro" + "q = 132"),
            # an explicit number beats a tempo word.
            for start, _explicit, spq in sorted(marks, key=lambda m: (m[0], m[1])):
                if self.starts and abs(self.starts[-1] - start) < 1e-9:
                    self.sec_per_q[-1] = spq
                else:
                    self.starts.append(start)
                    self.sec_per_q.append(spq)
            if not self.starts:
                self.starts, self.sec_per_q = [0.0], [60.0 / DEFAULT_BPM]
            # A first mark after the start (e.g. at bar 1 after a pickup) also covers the opening.
            self.starts[0] = 0.0
        self.sec_per_q = [s / scale for s in self.sec_per_q]
        # Seconds elapsed at the start of each tempo segment.
        self.seconds_at: list[float] = [0.0]
        for i in range(1, len(self.starts)):
            span = self.starts[i] - self.starts[i - 1]
            self.seconds_at.append(self.seconds_at[-1] + span * self.sec_per_q[i - 1])

    def seconds(self, offset: float) -> float:
        i = max(bisect.bisect_right(self.starts, offset) - 1, 0)
        return self.seconds_at[i] + (offset - self.starts[i]) * self.sec_per_q[i]


def _classify_name(names: str) -> str | None:
    """'accompaniment', 'voice' or None (undecided) from part/instrument names."""
    names = names.lower() + " "
    if _INSTRUMENT_COMPOUND_RE.search(names) or any(w in names for w in ACCOMPANIMENT_WORDS):
        return "accompaniment"
    if VOICE_RE.search(names):
        return "voice"
    if any(w in names for w in INSTRUMENT_WORDS):
        return "accompaniment"
    return None


def _is_keyboard(part: stream.Part) -> bool:
    from music21 import instrument

    inst = part.getInstrument(returnDefault=False)
    names = " ".join(x for x in (part.partName, inst.instrumentName if inst else None) if x).lower()
    return (isinstance(inst, (instrument.KeyboardInstrument, instrument.Harp, instrument.Guitar))
            or any(w in names for w in ACCOMPANIMENT_WORDS))


def is_accompaniment(part: stream.Part) -> bool:
    from music21 import instrument

    inst = part.getInstrument(returnDefault=False)
    # The part's own name wins over its playback instrument ("Canto" played by "Grand Piano").
    # The abbreviation is checked separately: exports often derive it from the playback
    # instrument (bwv52.6: "Soprano" abbreviated "Pno").
    for names in ((part.partName,), (part.partAbbreviation,),
                  (inst.instrumentName if inst else None, inst.partName if inst else None)):
        kind = _classify_name(" ".join(x for x in names if x))
        if kind is not None:
            return kind == "accompaniment"
    return isinstance(inst, (instrument.KeyboardInstrument, instrument.Harp, instrument.Guitar))


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
    tokens = [t for t in re.split(r"[\s/&+,.]+|\band\b", clean, flags=re.I) if t]
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
    # "S A T B", "Soprano/Alto", "S1 S2" (but not "Tenor I" -> "Tenor", "I")
    if len(expanded) == count and all(e[0].isalpha() and not _NUMBER_RE.fullmatch(e) for e in expanded):
        return expanded
    # "SA" / "TB" / "SATB" packed abbreviations
    if len(clean) == count and clean.isalpha() and clean.isupper():
        return [ABBREVIATIONS.get(c.lower(), c) for c in clean]
    # "Soprano 1 2", "Tenor I II" -> "Soprano 1", "Soprano 2"
    base = ABBREVIATIONS.get(low, clean) or "Voice"  # "B" -> "Bass 1", "Bass 2"
    return [f"{base} {i + 1}" for i in range(count)]


def _unique_names(tracks: list[Track]) -> None:
    seen: dict[str, int] = {}
    for t in tracks:
        seen[t.name] = seen.get(t.name, 0) + 1
    counters: dict[str, int] = {}
    taken = {name for name, n in seen.items() if n == 1}
    for t in tracks:
        if seen[t.name] > 1:
            base = t.name
            while True:  # skip numbers already used, e.g. an existing "Soprano 1"
                counters[base] = counters.get(base, 0) + 1
                candidate = f"{base} {counters[base]}"
                if candidate not in taken:
                    break
            t.name = candidate
            taken.add(candidate)


def _has_chordal_voice(items) -> bool:
    chords = sum(1 for _, el in items if isinstance(el, chord.Chord) and len(el.pitches) >= 2)
    return chords > 0 and chords >= 0.3 * max(len(items), 1)


def _staff_names(name: str, staves: int) -> list[str]:
    """Names for the staves of one sung part. A two-staff "SATB" / "Choir" part (closed
    score in a single MusicXML part) becomes "Soprano Alto" + "Tenor Bass", so each staff
    then splits into its two voices; otherwise staves are numbered ("Soprano 1", "Soprano 2")."""
    if staves == 1:
        return [name]
    numbered = [f"{name} {i + 1}" for i in range(staves)]
    split = _split_names(name, 2 * staves)
    if not all(s.endswith(f" {i + 1}") for i, s in enumerate(split)):  # not just numbered
        return [" ".join(split[2 * i:2 * i + 2]) for i in range(staves)]
    if staves == 2 and re.search(r"\b(choir|chorus|chor|coro|satb|mixed)\b", name.lower()):
        return ["Soprano Alto", "Tenor Bass"]
    return numbered


def _lend_chord_bottoms(upper: list, lower: list) -> None:
    """Closed scores often write S+A as chords in voice 1 and split into two voices only
    where the rhythms differ. Wherever the lower voice is silent, it sings the bottom
    note of the upper voice's chords (the caller renders the lower voice in 'bottom' mode)."""
    spans = [(off, off + float(el.duration.quarterLength)) for off, el in lower]
    for off, el in upper:
        if not (isinstance(el, chord.Chord) and len(el.pitches) >= 2):
            continue
        end = off + float(el.duration.quarterLength)
        if not any(s < end - 1e-6 and off < e - 1e-6 for s, e in spans):
            lower.append((off, el))


def _overlapping_lines(items) -> bool:
    """Two independent lines merged into one voice (a MIDI "Women" track with S and A in
    different rhythms): at least 30% of the notes overlap another note by at least half
    the shorter note (legato tails in a one-line MIDI track don't count)."""
    spans = sorted((off, off + float(el.duration.quarterLength)) for off, el in items
                   if float(el.duration.quarterLength) > 0)
    overlapping = set()
    for i, (s1, e1) in enumerate(spans):
        for j in range(i + 1, len(spans)):
            s2, e2 = spans[j]
            if s2 >= e1 - 1e-6:
                break
            if min(e1, e2) - s2 >= 0.5 * min(e1 - s1, e2 - s2) - 1e-6:
                overlapping.update((i, j))
    return len(spans) > 1 and len(overlapping) >= 0.3 * len(spans)


def _only_overlap_voices(voices: dict[str, list]) -> bool:
    """True when extra voices are just stray overlaps of a main (chordal) line.

    music21's MIDI import puts the odd overlapping note into a second Voice while the
    rest of a two-part track (e.g. hymn "upper" = S+A) stays as chords in voice 1."""
    sizes = sorted((len(v) for v in voices.values()), reverse=True)
    main = max(voices.values(), key=len)
    rest = sum(sizes[1:])
    return rest < 0.05 * sizes[0] or (rest < 0.25 * sizes[0] and _has_chordal_voice(main))


def _outer_line(events: list[NoteEvent], top: bool) -> list[NoteEvent]:
    """Keep the highest (top) or lowest line: one note per onset, and no inner note that
    starts while the outer note is still held."""
    sign = 1 if top else -1
    out: list[NoteEvent] = []
    for ev in sorted(events, key=lambda e: (e.start, -sign * e.pitch)):
        if out and ev.start - out[-1].start < 1e-6:
            continue
        if out and out[-1].start + out[-1].duration > ev.start + 1e-6 and sign * (out[-1].pitch - ev.pitch) >= 0:  # inner or unison
            continue
        out.append(ev)
    return out


def _clean_name(name: str | None) -> str:
    """Drop LilyPond/MIDI artefacts: 'women:Sopran' -> 'women', 'TenorStaff:TenorA' -> 'Tenor',
    ':uniqueContext0', '\\new', 'control track' -> ''."""
    name = (name or "").strip()
    if ":" in name:
        staff, _, voice = name.partition(":")
        name = staff.strip() or voice.strip()
    name = re.sub(r"(?<=[a-z])Staff$", "", name)
    if name.startswith("\\") or re.fullmatch(r"(?i)(uniqueContext\d*|control track|track \d+|untitled)", name):
        return ""
    return name


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
    # music21 invents Voice objects for overlapping MIDI notes; they don't follow the parts.
    from_midi = score.metadata is not None and dict(score.metadata.all()).get("fileFormat") == "midi"
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
    groups = _group_staves(score)
    acc_flags = [is_accompaniment(g[0]) for g in groups]
    if acc_flags and all(acc_flags):
        # Nothing looks sung (e.g. MIDI voices played by clarinet/flute): treat every
        # non-keyboard part as a voice rather than failing with "no sung voices".
        acc_flags = [_is_keyboard(g[0]) for g in groups]
    hymn_names = _hymn_staff_names(groups, acc_flags)
    for group_index, (group, accompaniment) in enumerate(zip(groups, acc_flags)):
        first = group[0]
        name = (_clean_name(first.partName) or _clean_name(first.getInstrument().instrumentName)
                or f"Part {group_index + 1}")
        tracks_before = len(tracks)
        if accompaniment:
            if not include_accompaniment:
                continue
            items = []
            for staff in group:
                for vitems in _collect_voices(staff, flat_offset).values():
                    items.extend(vitems)
            tracks.append(Track(name, "accompaniment", _events_from_elements(items, tempo_map, "all")))
            continue

        staff_names = [hymn_names[group_index]] if group_index in hymn_names else _staff_names(name, len(group))
        for staff_index, staff in enumerate(group):
            staff_name = staff_names[staff_index]
            voices = _collect_voices(staff, flat_offset)
            voices = {k: v for k, v in voices.items() if v}
            if not voices:
                continue
            if len(voices) > 1 and (from_midi or _only_overlap_voices(voices)):
                voices = {"1": [x for v in voices.values() for x in v]}
            if split_voices and len(voices) > 1:
                ids = sorted(voices, key=lambda k: int(k) if k.isdigit() else 99)
                names = _split_names(staff_name, len(ids))
                if split_chords:
                    _lend_chord_bottoms(voices[ids[0]], voices[ids[-1]])
                for vid, vname in zip(ids, names):
                    # In a voice shared with others, keep the line's outer note of any chord.
                    mode = "top" if vid == ids[0] else ("bottom" if vid == ids[-1] else "all")
                    tracks.append(Track(vname, "voice", _events_from_elements(voices[vid], tempo_map, mode)))
            else:
                items = [x for v in voices.values() for x in v]
                pair = _split_names(staff_name, 2)
                named_pair = (pair != [f"{staff_name.strip() or 'Voice'} {i}" for i in (1, 2)]
                              or _RANGE_NAME_RE.fullmatch(staff_name.strip()) is not None)
                # Overlapping single notes only count for a real two-part name ("Women",
                # "S/A"), never for "Soprano" / "B" (fallback names "Soprano 1", "Bass 1").
                clean = staff_name.strip()
                fallback = [f"{ABBREVIATIONS.get(clean.lower(), clean) or 'Voice'} {i}" for i in (1, 2)]
                if split_chords and named_pair and (
                        _has_chordal_voice(items) or (pair != fallback and _overlapping_lines(items))):
                    # Hymnal style: two parts written as chords in a single voice
                    # (or a TTBB "Tenor" staff with Tenor 1 + 2 as chords).
                    events = _events_from_elements(items, tempo_map, "all")
                    tracks.append(Track(pair[0], "voice", _outer_line(events, top=True)))
                    tracks.append(Track(pair[1], "voice", _outer_line(events, top=False)))
                else:
                    tracks.append(Track(staff_name, "voice", _events_from_elements(items, tempo_map, "all")))
        if group_index in hymn_names and len(tracks) - tracks_before == 1:
            tracks[-1].name = name  # a single line after all: keep the score's own name

    tracks = [t for t in tracks if t.notes]
    _unique_names(tracks)
    end_q = float(score.highestTime)
    duration = max([tempo_map.seconds(end_q)] + [t.end for t in tracks])
    if title is None:
        md = score.metadata
        candidates = [md.title, md.movementName] if md is not None else []
        title = next((c for c in candidates if _meaningful_title(c)), None)
    return ScoreTracks(title or "Score", tracks, duration)


def _hymn_staff_names(groups, acc_flags) -> dict[int, str]:
    """Two sung staves with generic names ("Voice", "Choir", none): a hymn, S+A over T+B."""
    sung = [i for i, acc in enumerate(acc_flags) if not acc]
    if len(sung) != 2 or any(len(groups[i]) != 1 for i in sung):
        return {}
    for i in sung:
        part = groups[i][0]
        inst = part.getInstrument(returnDefault=False)
        name = _clean_name(part.partName) or _clean_name(inst.instrumentName if inst else None)
        if name.lower() not in GENERIC_VOICE_NAMES:
            return {}
    return {sung[0]: "Soprano/Alto", sung[1]: "Tenor/Bass"}


def _meaningful_title(title: str | None) -> bool:
    """Reject empty titles, music21 placeholders and bare file names like 'bwv66.6.mxl'."""
    if not title or not title.strip():
        return False
    low = title.strip().lower()
    if low.startswith("music21") or low in {"untitled", "untitled score", "title"}:
        return False
    return not re.search(r"\.(mxl|musicxml|xml|midi?|krn|abc|mscz|mscx|pdf)$", low)
