from __future__ import annotations

import copy
import re

from music21 import clef, instrument, note, stream

from .check import voice_range
from .postprocess import (
    _clef_sign,
    _has_notes,
    _is_default_name,
    _measures,
    _note_elements,
)
from .score import DUPLICATE_LINE_SHARE, NoteEvent, _differing_share, is_accompaniment

_EPS = 1e-6
POLYPHONY_THRESHOLD = 0.15
TENOR_WRITTEN_MEDIAN = 63

VOICE_LAYOUTS = {
    1: ["Voice"],
    3: ["Soprano", "Alto", "Bass"],
    4: ["Soprano", "Alto", "Tenor", "Bass"],
    5: ["Soprano 1", "Soprano 2", "Alto", "Tenor", "Bass"],
    6: ["Soprano 1", "Soprano 2", "Alto", "Tenor", "Bass 1", "Bass 2"],
    7: ["Soprano 1", "Soprano 2", "Alto 1", "Alto 2", "Tenor", "Bass 1", "Bass 2"],
    8: [
        "Soprano 1",
        "Soprano 2",
        "Alto 1",
        "Alto 2",
        "Tenor 1",
        "Tenor 2",
        "Bass 1",
        "Bass 2",
    ],
}


def _intervals(elements, base: float) -> list[tuple[float, float]]:
    return [
        (
            base + float(e.offset),
            base + float(e.offset) + float(e.duration.quarterLength),
        )
        for e in elements
        if float(e.duration.quarterLength) > 0
    ]


def _union_length(iv: list[tuple[float, float]]) -> float:
    total, end = 0.0, float("-inf")
    for a, b in sorted(iv):
        if b <= end:
            continue
        total += b - max(a, end)
        end = b
    return total


def _overlap_length(lines: list[list[tuple[float, float]]]) -> float:
    events = sorted(
        [(a, 1) for line in lines for a, b in line]
        + [(b, -1) for line in lines for a, b in line],
        key=lambda e: (e[0], e[1]),
    )
    depth, last, total = 0, None, 0.0
    for t, d in events:
        if depth >= 2 and last is not None:
            total += t - last
        depth += d
        last = t
    return total


def _polyphony(measures: list[stream.Measure]) -> float:
    overlap = sounding = 0.0
    for m in measures:
        lines = [
            _intervals(v.notes, float(v.getOffsetBySite(m)))
            for v in m.voices
            if v.notes
        ]
        if m.notes:
            lines.append(_intervals(m.notes, 0.0))
        if len(lines) >= 2:
            overlap += _overlap_length([_merge_line(line) for line in lines])
        sounding += _union_length([iv for line in lines for iv in line])
    return overlap / sounding if sounding > 0 else 0.0


def _chordal(measures: list[stream.Measure]) -> bool:
    els = [el for m in measures for _, _, el in _note_elements(m)]
    chords = sum(1 for el in els if len(el.pitches) >= 2)
    return chords > 0 and chords >= 0.3 * len(els)


def _flatten_voices(measures: list[stream.Measure]) -> None:
    for m in measures:
        for v in list(m.voices):
            base = float(v.getOffsetBySite(m))
            for el in list(v.notes):
                off = base + float(el.getOffsetBySite(v))
                v.remove(el)
                m.insert(off, el)
            m.remove(v)


def simplify_voices(part: stream.Part) -> bool:
    measures = _measures(part)
    if _polyphony(measures) >= POLYPHONY_THRESHOLD:
        return False
    _flatten_voices(measures)
    return True


def drop_notes_under_bar_rests(part: stream.Part, log: list[int] | None = None) -> int:
    measures = _measures(part)
    if _polyphony(measures) >= POLYPHONY_THRESHOLD:
        return 0
    cleaned = 0
    for k, m in enumerate(measures):
        voices = list(m.voices)
        if len(voices) < 2 or m.notes:
            continue
        if not (
            resting := [
                v
                for v in voices
                if not v.notes
                and any(
                    getattr(r, "fullMeasure", False) is True
                    for r in v.getElementsByClass(note.Rest)
                )
            ]
        ):
            continue
        prev_m, next_m = (
            measures[k - 1] if k > 0 else None,
            measures[k + 1] if k + 1 < len(measures) else None,
        )
        if _has_notes(prev_m) or _has_notes(next_m):
            continue
        bar_rest = copy.deepcopy(
            next(
                r
                for r in resting[0].getElementsByClass(note.Rest)
                if getattr(r, "fullMeasure", False) is True
            )
        )
        for v in voices:
            m.remove(v)
        m.insert(0, bar_rest)
        cleaned += 1
        if log is not None:
            log.append(m.number)
    return cleaned


def drop_range_strays(
    part: stream.Part, below: bool, above: bool, log: list[int] | None = None
) -> int:
    if (
        rng := voice_range(part.partName or "")
        if (below or above) and not _is_default_name(part.partName)
        else None
    ) is None:
        return 0
    measures = _measures(part)
    replaced, bars = [], []
    for m in measures:
        elements, bad = [(off, el) for _, off, el in _note_elements(m)], []
        for off, el in elements:
            ps = [p.ps for p in el.pitches]
            if not ((below and max(ps) < rng[0]) or (above and min(ps) > rng[1])):
                continue
            end = off + float(el.duration.quarterLength)
            others = [(o, e) for o, e in elements if e is not el]
            if not others or any(
                o < end - _EPS and o + float(e.duration.quarterLength) > off + _EPS
                for o, e in others
            ):
                bad.append(el)
        for el in bad:
            site, rest = (
                el.activeSite,
                note.Rest(quarterLength=el.duration.quarterLength),
            )
            site.replace(el, rest)
            replaced.append((site, rest, el))
        if bad:
            bars.append(m.number)
    if replaced and _polyphony(measures) >= POLYPHONY_THRESHOLD:
        for site, rest, el in replaced:
            site.replace(rest, el)
        return 0
    if log is not None:
        log.extend(bars)
    return len(bars)


def tidy_voices(part: stream.Part, log: list[int] | None = None) -> int:
    drop_notes_under_bar_rests(part, log)
    if simplify_voices(part):
        return 2 if _chordal(_measures(part)) else 1
    revoice_two_lines(part)
    if _copied_line(part):
        _keep_one_line(part)
        return 1
    return 2


def _voice_events(measures: list[stream.Measure], vid: str) -> list[NoteEvent]:
    return [
        NoteEvent(float(m.offset) + off, float(el.duration.quarterLength), p.ps)
        for m in measures
        for v_id, off, el in _note_elements(m)
        if v_id == vid
        for p in el.pitches
    ]


def _copied_line(part: stream.Part) -> bool:
    measures = _measures(part)
    return (
        _differing_share(_voice_events(measures, "1"), _voice_events(measures, "2"))
        < DUPLICATE_LINE_SHARE
    )


def _keep_one_line(part: stream.Part) -> None:
    for m in _measures(part):
        voices = {str(v.id): v for v in m.voices}
        keep = (
            voices.get("1")
            if (voices.get("1") and voices["1"].notes)
            else voices.get("2", voices.get("1"))
        )
        base = float(keep.getOffsetBySite(m)) if keep is not None else 0.0
        for v in list(m.voices):
            m.remove(v)
        if keep is not None:
            for el in list(keep.notesAndRests):
                m.insert(base + float(el.offset), el)


def _median(values: list[float]) -> float:
    return sorted(values)[len(values) // 2] if values else 0.0


def revoice_two_lines(part: stream.Part) -> None:
    measures = _measures(part)
    by_id: dict[str, list[float]] = {}
    for m in measures:
        for vid, _, el in _note_elements(m):
            by_id.setdefault(vid, []).extend(p.ps for p in el.pitches)
    staff_median = _median([x for v in by_id.values() for x in v])
    upper_ids = {vid for vid, ps in by_id.items() if _median(ps) >= staff_median}

    for m in measures:
        events, rests = [], []
        for vid, off, el in _note_elements(m):
            end = off + float(el.duration.quarterLength)
            notes_ = (
                sorted(el.notes, key=lambda n: n.pitch.ps)
                if len(el.pitches) > 1
                else [el]
            )
            for i, n in enumerate(notes_):
                role = None
                if len(notes_) > 1:
                    role = (
                        "L"
                        if i == 0
                        else "U"
                        if i == len(notes_) - 1
                        else "U"
                        if i >= len(notes_) / 2
                        else "L"
                    )
                events.append(
                    [
                        off,
                        end,
                        copy.deepcopy(n.pitch),
                        n.tie if n.tie is not None else el.tie,
                        vid,
                        role,
                    ]
                )

        seen: set[tuple[float, float]] = set()
        unique = []
        for ev in events:
            if (k := (round(ev[0], 4), ev[2].ps)) not in seen:
                seen.add(k)
                unique.append(ev)
        events = unique

        for v in m.voices:
            base = float(v.getOffsetBySite(m))
            rests.extend(
                (
                    base + float(r.getOffsetBySite(v)),
                    base
                    + float(r.getOffsetBySite(v))
                    + float(r.duration.quarterLength),
                    str(v.id),
                )
                for r in v.getElementsByClass(note.Rest)
            )

        for ev in events:
            if ev[5] is not None:
                continue
            start, end, pitch, _, vid, _ = ev
            others = [
                o
                for o in events
                if o is not ev
                and o[0] < end - _EPS
                and o[1] > start + _EPS
                and (o[4] != vid or o[5] is not None)
            ]
            higher, lower = (
                any(o[2].ps > pitch.ps for o in others),
                any(o[2].ps < pitch.ps for o in others),
            )
            if higher and not lower:
                ev[5] = "L"
            elif lower and not higher:
                ev[5] = "U"
            elif others or any(
                r[0] < end - _EPS and r[1] > start + _EPS and r[2] != vid for r in rests
            ):
                ev[5] = "U" if vid in upper_ids else "L"
            else:
                ev[5] = "B"

        for el in list(m.notesAndRests):
            m.remove(el)
        for v in list(m.voices):
            m.remove(v)

        for vid, roles in (("1", ("U", "B")), ("2", ("L", "B"))):
            v = stream.Voice(id=vid)
            for start, end, pitch, tie_, _, role in events:
                if role in roles:
                    n = note.Note(copy.deepcopy(pitch), quarterLength=end - start)
                    if tie_ is not None:
                        n.tie = copy.deepcopy(tie_)
                    v.insert(start, n)
            m.insert(0, v)


def _merge_line(line: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(line):
        if out and a <= out[-1][1] + _EPS:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _abbrev(name: str) -> str:
    words = name.split()
    return words[0][0].upper() + (words[1] if len(words) > 1 else "")


def _voice_count(part: stream.Part) -> int:
    ids = {str(v.id) for m in _measures(part) for v in m.voices if v.notes}
    return 2 if (len(ids) <= 1 and _chordal(_measures(part))) else max(len(ids), 1)


def name_unnamed_parts(
    score: stream.Score, lines: dict[int, int] | None = None
) -> None:
    everything = list(score.parts)
    acc = [is_accompaniment(p) for p in everything]
    parts = (
        everything
        if everything and all(acc)
        else [p for p, a in zip(everything, acc) if not a]
    )
    unnamed = [i for i, p in enumerate(parts) if _is_default_name(p.partName)]
    if len(unnamed) == 1 < len(parts):
        _name_the_gap(parts, unnamed[0], lines)
    if not parts or len(unnamed) < len(parts):
        return
    counts = [(lines or {}).get(id(p)) or _voice_count(p) for p in parts]
    lines = counts
    total = sum(lines)

    if total in VOICE_LAYOUTS:
        names = VOICE_LAYOUTS[total]
    elif total == 2:
        signs = [_clef_sign(p, _measures(p)[0]) if _measures(p) else "?" for p in parts]
        names = (
            ["Tenor", "Bass"]
            if all(s == "F" for s in signs)
            else (
                ["Soprano", "Bass"]
                if len(parts) == 2 and signs[0] == "G" and signs[1] == "F"
                else ["Soprano", "Alto"]
            )
        )
    else:
        names = [f"Voice {i + 1}" for i in range(total)]

    it = iter(names)
    for i, (p, n) in enumerate(zip(parts, lines)):
        mine = [next(it) for _ in range(n)]
        label = mine[0] if n == 1 else " ".join(_abbrev(x) for x in mine)
        new = stream.Part()
        new.id = f"V{i + 1}"
        for el in list(p.elements):
            if not isinstance(el, instrument.Instrument):
                new.insert(p.elementOffset(el), el)
        voc = instrument.Vocalist()
        voc.partName = label
        new.insert(0, voc)
        new.partName = label
        new.partAbbreviation = " ".join(_abbrev(x) for x in mine)
        score.replace(p, new)


def _name_the_gap(
    parts: list[stream.Part], i: int, lines: dict[int, int] | None
) -> None:
    layout = VOICE_LAYOUTS.get(len(parts))
    if layout is None or any(((lines or {}).get(id(p)) or 1) != 1 for p in parts):
        return
    if all(
        (p.partName or "").strip().casefold() == name.casefold()
        for j, (p, name) in enumerate(zip(parts, layout))
        if j != i
    ):
        part = parts[i]
        part.partName, part.partAbbreviation = layout[i], _abbrev(layout[i])
        for inst in part.getElementsByClass(instrument.Instrument):
            inst.partName = layout[i]


def is_tenor_name(name: str) -> bool:
    low = name.strip().lower()
    return bool(
        re.search(r"\btenors?\b|\bten\.", low)
        or re.fullmatch(r"t\.?\s*(\d+|i{1,3}|iv)?", low)
    )


def fix_tenor_octave(part: stream.Part) -> bool:
    if not is_tenor_name(part.partName or ""):
        return False
    if not (clefs := list(part.recurse().getElementsByClass(clef.Clef))) or any(
        c.sign != "G" or c.octaveChange != 0 for c in clefs
    ):
        return False
    pitches = sorted(p.ps for n in part.recurse().notes for p in n.pitches)
    if not pitches or pitches[len(pitches) // 2] < TENOR_WRITTEN_MEDIAN:
        return False
    for n in part.recurse().notes:
        for p in n.pitches:
            p.octave = p.implicitOctave - 1
    for c in clefs:
        if c.activeSite is not None:
            c.activeSite.replace(c, clef.Treble8vbClef())
    return True
