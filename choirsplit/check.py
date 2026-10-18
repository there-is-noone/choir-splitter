"""Find bars where a recognised score is probably wrong, so they can be checked by hand.

Optical music recognition misses dots and rests, reads stray marks as notes and gets
pitches wrong. ``omr.assemble`` repairs what it safely can and logs each repair as a
``fixed`` Issue; ``check_score`` looks at the result and reports ``suspect`` bars:

* a sung line that runs out before the end of its bar (missed dot or rest: the notes
  after it come in early);
* a note far outside the range of the voice the staff is named for;
* a leap of more than an octave in a line that otherwise doesn't leap like that;
* a line whose rhythm differs from the other voices in a bar where they all move
  together (a misread note length).

Pitches themselves can't be verified, so a misread pitch only shows up when it makes
an implausible leap or range.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from music21 import stream

from .score import is_accompaniment

_EPS = 1e-6
LEAP = 12  # semitones; larger leaps are rare in choral writing
BASS_LEAP = 17  # basses leap down to the low octave at cadences (Bach: up to an 11th)
RARE_LEAPS = 0.03  # a line with more leaps than this fraction leaps on purpose

# Plausible sounding range (MIDI) per voice type, with a few semitones to spare.
RANGES = {
    "soprano": (55, 86),  # G3-D6
    "alto": (50, 79),  # D3-G5
    "tenor": (43, 74),  # G2-D5
    "baritone": (38, 70),  # D2-Bb4
    "bass": (33, 67),  # A1-G4
}
_RANGE_WORDS = (
    ("sopran", "soprano"), ("treble", "soprano"), ("cant", "soprano"), ("mezzo", "alto"),
    ("contralt", "alto"), ("alt", "alto"), ("ten", "tenor"), ("bariton", "baritone"),
    ("bass", "bass"), ("baix", "bass"), ("bajo", "bass"),
)
_LETTERS = {"s": "soprano", "a": "alto", "t": "tenor", "b": "bass"}


@dataclass(frozen=True)
class Issue:
    bar: int | None  # measure number in the cleaned score; None for the whole part
    voice: str
    kind: str  # "fixed" (repaired automatically) or "suspect" (check by hand)
    detail: str
    page: int | None = None
    system: int | None = None

    def location(self) -> str:
        bar = f"bar {self.bar}" if self.bar is not None else "all bars"
        where = ", ".join(f"{k}{v}" for k, v in (("p", self.page), ("s", self.system)) if v)
        return f"{bar} ({where})" if where else bar


def voice_types(name: str) -> list[str]:
    """Voice types a staff name stands for: 'Soprano 1' -> [soprano], 'S A' -> [soprano, alto]."""
    found = []
    for token in re.findall(r"[^\W\d_]+", (name or "").lower()):
        kind = _LETTERS.get(token) if len(token) == 1 else next(
            (k for prefix, k in _RANGE_WORDS if token.startswith(prefix)), None)
        if token in ("women", "ladies", "upper"):
            found += ["soprano", "alto"]
        elif token in ("men", "lower"):
            found += ["tenor", "bass"]
        elif kind:
            found.append(kind)
    return list(dict.fromkeys(found))


def voice_range(name: str) -> tuple[int, int] | None:
    kinds = voice_types(name)
    if not kinds:
        return None
    return min(RANGES[k][0] for k in kinds), max(RANGES[k][1] for k in kinds)


def _lines(m: stream.Measure) -> list[list]:
    """The rhythmic lines of a measure: its direct notes/rests, and each Voice's."""
    out = []
    for container in [m, *m.voices]:
        base = float(container.getOffsetBySite(m)) if container is not m else 0.0
        els = [(base + float(e.getOffsetBySite(container)), e) for e in container.notesAndRests
               if float(e.duration.quarterLength) > 0]
        if els:
            out.append(sorted(els, key=lambda oe: oe[0]))
    return out


def _sung_parts(score: stream.Score) -> list[stream.Part]:
    parts = list(score.parts)
    acc = [is_accompaniment(p) for p in parts]
    return parts if parts and all(acc) else [p for p, a in zip(parts, acc) if not a]


def check_score(score: stream.Score, locations: dict[int, tuple[int, int]] | None = None) -> list[Issue]:
    """Suspect bars of `score`, in bar order. `locations` maps a measure number to its
    (page, system) in the original, for reports."""
    locations = locations or {}
    issues: list[Issue] = []

    def add(bar, voice, detail):
        page, system = locations.get(bar, (None, None)) if bar is not None else (None, None)
        issues.append(Issue(bar, voice, "suspect", detail, page, system))

    parts = _sung_parts(score)
    # Bars are keyed by their position, not their number: a bar split in two halves (a
    # repeat sign mid-bar) has two measures with the same number.
    ends: dict[float, list[float]] = defaultdict(list)  # bar position -> where each line ends
    rhythms: dict[float, dict[int, tuple[float, ...]]] = defaultdict(dict)  # -> {part: onsets}
    per_part = []
    for part in parts:
        measures = [(round(float(m.getOffsetBySite(part)), 4), m)
                    for m in part.getElementsByClass(stream.Measure)]
        n_lines = max((len([ln for ln in _lines(m) if any(not e.isRest for _, e in ln)])
                       for _, m in measures), default=1)
        per_part.append((part, measures, n_lines))
        for pos, m in measures:
            for line in _lines(m):
                ends[pos].append(round(max(o + float(e.duration.quarterLength) for o, e in line), 4))
    # A bar's length is where most lines end (ties: the longest), not its longest line.
    bars = {pos: max(Counter(v).items(), key=lambda kv: (kv[1], kv[0]))[0] for pos, v in ends.items()}
    order = sorted(bars)
    numbers: dict[float, int] = {}

    for k, (part, measures, n_lines) in enumerate(per_part):
        name = part.partName or "Voice"
        rng = voice_range(name)
        sequence = []  # (bar, MIDI) of a single-line staff, for leaps
        for bar_pos, m in measures:
            numbers[bar_pos] = m.number
            lines = _lines(m)
            for line in lines:
                notes = [(o, e) for o, e in line if not e.isRest]
                if not notes:
                    continue
                # Short line: starts on the downbeat, no gaps, but stops before the bar ends.
                end, pos, contiguous = bars[bar_pos], 0.0, True
                for o, e in line:
                    if abs(o - pos) > _EPS:
                        contiguous = False
                        break
                    pos = o + float(e.duration.quarterLength)
                if contiguous and pos < end - _EPS:
                    add(m.number, name, f"notes fill {pos:g} of {end:g} beats (missed dot or rest?)")
                if n_lines == 1 and len(lines) == 1:
                    rhythms[bar_pos][k] = tuple(round(o, 4) for o, _ in notes)
            for _, e in (oe for line in lines for oe in line if not oe[1].isRest):
                pitches = [p.ps for p in e.pitches]
                if rng and any(p < rng[0] or p > rng[1] for p in pitches):
                    bad = next(p for p in e.pitches if p.ps < rng[0] or p.ps > rng[1])
                    add(m.number, name, f"{bad.nameWithOctave} is outside the usual range")
                if n_lines == 1 and len(lines) == 1:
                    sequence.append((m.number, max(pitches)))
        leaps = [(bar, abs(b - a)) for (_, a), (bar, b) in zip(sequence, sequence[1:])]
        kinds = voice_types(name)
        limit = BASS_LEAP if kinds and set(kinds) <= {"bass", "baritone"} else LEAP
        big = [(bar, d) for bar, d in leaps if d > limit]
        if big and len(big) <= max(2, RARE_LEAPS * len(leaps)):
            for bar, d in big:
                add(bar, name, f"leap of {d:g} semitones (misread pitch?)")

    # Out of step: every other single-line voice has the same rhythm in this bar and this
    # one has as many notes but different onsets, while it moves with them in the bars on
    # either side (so it's a homophonic passage, not independent counterpoint).
    def others_common(entries, k):
        others = {r for j, r in entries.items() if j != k}
        return next(iter(others)) if len(others) == 1 else None

    def in_step(i, k):
        if not 0 <= i < len(order):
            return False
        entries = rhythms.get(order[i], {})
        return k in entries and len(entries) >= 3 and others_common(entries, k) == entries[k]

    for i, bar_pos in enumerate(order):
        entries = rhythms.get(bar_pos, {})
        if len(entries) < 3:
            continue
        for k, onsets in entries.items():
            common = others_common(entries, k)
            if (common is not None and onsets != common and len(onsets) == len(common) > 1
                    and in_step(i - 1, k) and in_step(i + 1, k)):
                add(numbers[bar_pos], per_part[k][0].partName or "Voice",
                    "rhythm differs from the other voices (misread length?)")

    issues = list(dict.fromkeys(issues))
    issues.sort(key=lambda i: (i.bar if i.bar is not None else -1, i.voice))
    return issues


def format_issues(issues: list[Issue]) -> list[str]:
    """One line per issue, e.g. 'bar 37 (p3 s2)  Alto  suspect: notes fill 2.5 of 3 beats'."""
    width = max((len(i.location()) for i in issues), default=0)
    vwidth = max((len(i.voice) for i in issues), default=0)
    return [f"{i.location():<{width}}  {i.voice:<{vwidth}}  {i.kind}: {i.detail}" for i in issues]
