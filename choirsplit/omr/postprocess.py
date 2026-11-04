from __future__ import annotations

import copy
import re
from collections import Counter

from music21 import clef, layout, meter, note, stream, tie

from .score import is_accompaniment
from .types import Report, Segment, Unit

_EPS = 1e-6
DOT_X_TOLERANCE = 6.0
TIME_SIGNATURE_X = 8.0
POLYPHONY_THRESHOLD = 0.15
DEFAULT_NAME_RE = re.compile(
    r"^\s*(|voice|noname|piano|p\d+|part\s*\d*|staff\s*\d*|music\s*\d*)\s*$", re.I
)
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}
_OCR_LETTER = {"8": "s", "5": "s", "0": "o", "4": "a", "7": "t"}


def _measures(part: stream.Part) -> list[stream.Measure]:
    return list(part.getElementsByClass(stream.Measure))


def _group_units(score: stream.Score) -> list[list[stream.Part]]:
    groups: dict[str, list[stream.Part]] = {}
    for i, part in enumerate(score.parts):
        k = (
            re.sub(r"-Staff\d+$", "", str(part.id))
            if isinstance(part, stream.PartStaff)
            else f"{part.id}#{i}"
        )
        groups.setdefault(k, []).append(part)
    return list(groups.values())


def name_key(name: str) -> str:
    n = name.strip().lower()
    if not n or DEFAULT_NAME_RE.match(n):
        return ""
    n = (_OCR_LETTER.get(n[0], n[0]) + n[1:]) if len(n) <= 3 else n
    m = re.match(r"([a-z])[a-z.]*\s*(\d+|i{1,3}|iv)?\b", n)
    return (m.group(1) + _ROMAN.get(m.group(2) or "", m.group(2) or "")) if m else n


def _clef_sign(part: stream.Part, measure: stream.Measure) -> str:
    c = (
        measure.getElementsByClass(clef.Clef).first()
        or measure.getContextByClass(clef.Clef)
        or part.flatten().getElementsByClass(clef.Clef).first()
    )
    return getattr(c, "sign", None) or "?"


def _is_default_name(name: str | None) -> bool:
    return bool(DEFAULT_NAME_RE.match(name or ""))


def _part_name(part: stream.Part) -> str:
    return (part.partName or "").strip()


def segments_of(score: stream.Score) -> list[Segment]:
    groups = _group_units(score)
    if (
        n_measures := max((len(_measures(p)) for g in groups for p in g), default=0)
    ) == 0:
        return []
    starts, pages = {0}, set()
    for g in groups:
        for p in g:
            for i, m in enumerate(_measures(p)):
                if any(
                    getattr(x, "isNew", False)
                    for x in m.getElementsByClass(
                        (layout.SystemLayout, layout.PageLayout)
                    )
                ):
                    starts.add(i)
                if any(
                    getattr(x, "isNew", False)
                    for x in m.getElementsByClass(layout.PageLayout)
                ):
                    pages.add(i)
    bounds, segments = sorted(starts) + [n_measures], []
    for a, b in zip(bounds, bounds[1:]):
        units = []
        for g in groups:
            visible, clefs = False, []
            for p in g:
                ms = _measures(p)
                if a >= len(ms):
                    continue
                visible |= not any(
                    sl.hidden for sl in ms[a].getElementsByClass(layout.StaffLayout)
                )
                clefs.append(_clef_sign(p, ms[a]))
            if visible:
                name, abbr = _part_name(g[0]), (g[0].partAbbreviation or "").strip()
                units.append(
                    Unit(
                        list(g),
                        name,
                        abbr,
                        tuple(clefs),
                        name_key(name) or name_key(abbr),
                    )
                )
        if units:
            segments.append(Segment(units, range(a, b), new_page=a in pages))
    return segments


def _similarity(u: Unit, ref: Unit) -> float:
    s = 0.0
    if u.key and ref.key:
        s += 3 if u.key == ref.key else (1 if u.key[0] == ref.key[0] else -1)
    return (
        s
        + (1 if u.clefs == ref.clefs else -1)
        + (0 if len(u.staves) == len(ref.staves) else -2)
    )


def align_units(units: list[Unit], refs: list[Unit]) -> list[int]:
    n, k = len(units), len(refs)
    if n == k:
        return list(range(n))
    neg = float("-inf")
    best = [[neg] * (k + 1) for _ in range(n + 1)]
    best[0] = [0.0] * (k + 1)
    for i in range(1, n + 1):
        for j in range(i, k + 1):
            best[i][j] = max(
                best[i][j - 1],
                best[i - 1][j - 1] + _similarity(units[i - 1], refs[j - 1]),
            )
    out, j = [], k
    for i in range(n, 0, -1):
        while best[i][j] == best[i][j - 1] and j > i:
            j -= 1
        out.append(j - 1)
        j -= 1
    return out[::-1]


def _content_length(m: stream.Measure) -> float:
    return float(m.highestTime)


def _nominal_lengths(
    seg: Segment, meters: dict[int, float] | None = None
) -> list[float]:
    meters = meters or {}
    out = []
    for i in seg.measure_range:
        lengths = []
        if (bar := meters.get(i)) is None:
            ms = [m for _, _, m in _bar_measures(seg, i)]
            ts = next(
                (ts for m in ms for ts in m.getElementsByClass(meter.TimeSignature)),
                None,
            ) or (ms[0].getContextByClass(meter.TimeSignature) if ms else None)
            bar = float(ts.barDuration.quarterLength) if ts is not None else None
        for u in seg.units:
            for p in u.staves:
                if i < len(ms := _measures(p)):
                    rest_only = (
                        bar is not None and _bar_rest(ms[i]) and not _has_notes(ms[i])
                    )
                    lengths.append(
                        bar if rest_only else round(_content_length(ms[i]), 4)
                    )
        if not (lengths := [x for x in lengths if x > 0]):
            out.append(bar or 4.0)
            continue
        counts = Counter(lengths).most_common()
        top = [v for v, c in counts if c == counts[0][1]]
        out.append(
            bar
            if bar is not None and any(abs(v - bar) < _EPS for v in top)
            else max(top)
        )
    return out


def clip_measure(m: stream.Measure, length: float) -> bool:
    cut = False
    for el in list(m.recurse().notesAndRests):
        start = float(el.getOffsetInHierarchy(m))
        if start >= length - _EPS:
            el.activeSite.remove(el)
            cut |= not el.isRest
        elif start + float(el.duration.quarterLength) > length + _EPS:
            el.duration.quarterLength = length - start
            cut |= not el.isRest
    return cut


def _x(el) -> float | None:
    x = getattr(el.style, "absoluteX", None) if el.hasStyleInformation else None
    return float(x) if isinstance(x, (int, float)) else None


def _lines(m: stream.Measure) -> list[list]:
    return [
        els
        for c in [m, *m.voices]
        if (els := [e for e in c.notesAndRests if float(e.duration.quarterLength) > 0])
    ]


def _complete_lines(
    measures: list[stream.Measure], length: float
) -> list[tuple[float | None, float, float, bool]]:
    return [
        (_x(e), float(e.offset), float(e.duration.quarterLength), not e.isRest)
        for m in measures
        for line in _lines(m)
        if abs(sum(float(e.duration.quarterLength) for e in line) - length) <= _EPS
        for e in line
    ]


def _repair_line(
    line: list, ref: list[tuple[float | None, float, float, bool]], length: float
) -> int:
    if any(e.duration.tuplets or e.duration.isGrace for e in line):
        return 0
    durs = [float(e.duration.quarterLength) for e in line]
    if sum(durs) > length - _EPS or float(line[0].offset) > _EPS:
        return 0
    onsets = []
    for k, e in enumerate(line):
        if (x := _x(e)) is None:
            return 0
        hits = {
            round(o, 4)
            for rx, o, _, _ in ref
            if rx is not None and abs(rx - x) <= DOT_X_TOLERANCE
        }
        if len(hits) > 1 or (not hits and k > 0):
            return 0
        onsets.append(hits.pop() if hits else 0.0)
    if onsets[0] != 0.0 or any(b <= a + _EPS for a, b in zip(onsets, onsets[1:])):
        return 0
    new = [b - a for a, b in zip(onsets, onsets[1:])] + [length - onsets[-1]]
    changed = []
    for e, old, nd in zip(line, durs, new):
        if abs(nd - old) < _EPS:
            continue
        if (
            abs(nd - 1.5 * old) < _EPS
            and e.duration.dots == 0
            and e.duration.type not in ("complex", "inexpressible")
        ):
            changed.append(e)
            continue
        return 0
    if not changed:
        return 0
    if changed[-1] is line[-1]:
        start, old_end = onsets[-1], onsets[-1] + durs[-1]
        if any(old_end - _EPS <= o < length - _EPS for _, o, _, _ in ref):
            return 0
        if (
            sum(
                1
                for _, o, d, is_note in ref
                if is_note and abs(o - start) < _EPS and abs(o + d - length) < _EPS
            )
            < 2
        ):
            return 0
    for e in changed:
        e.duration.dots = 1
    for e, onset in zip(line, onsets):
        e.activeSite.setElementOffset(e, onset)
    for container in {id(e.activeSite): e.activeSite for e in line}.values():
        container.coreElementsChanged()
    return len(changed)


def repair_missing_dots(
    seg: Segment, lengths: list[float], report: Report | None = None
) -> int:
    repaired = 0
    for i, length in zip(seg.measure_range, lengths):
        located = _bar_measures(seg, i)
        ref = None
        for u, s, m in located:
            for line in _lines(m):
                if sum(float(e.duration.quarterLength) for e in line) < length - _EPS:
                    ref = (
                        _complete_lines([m for _, _, m in located], length)
                        if ref is None
                        else ref
                    )
                    if dots := _repair_line(line, ref, length):
                        repaired += 1
                        if report is not None:
                            report(
                                i,
                                u,
                                s,
                                f"restored {dots} missed dot{'s' if dots > 1 else ''}",
                            )
    return repaired


def _retime_line(
    line: list, ref: list[tuple[float | None, float, float, bool]], length: float
) -> int:
    if (
        any(e.duration.tuplets or e.duration.isGrace for e in line)
        or sum(float(e.duration.quarterLength) for e in line) > length - _EPS
        or float(line[0].offset) > _EPS
    ):
        return 0
    if not (notes := [e for e in line if not e.isRest]):
        return 0
    onsets = []
    for e in notes:
        hits = {
            round(o, 4)
            for rx, o, _, _ in ref
            if (x := _x(e)) is not None
            and rx is not None
            and abs(rx - x) <= DOT_X_TOLERANCE
        }
        if not hits and e is line[0]:
            hits = {0.0}
        if len(hits) != 1:
            return 0
        onsets.append(hits.pop())
    moved = 0
    for k, (e, onset) in enumerate(zip(notes, onsets)):
        next_onset = onsets[k + 1] if k + 1 < len(notes) else length
        if (
            onset < float(e.offset) - _EPS
            or next_onset <= onset + _EPS
            or onset + float(e.duration.quarterLength) > next_onset + _EPS
        ):
            return 0
        moved += abs(onset - float(e.offset)) > _EPS
    if not moved:
        return 0
    site = line[0].activeSite
    for e in line:
        if e.isRest:
            site.remove(e)
    pos = 0.0
    for e, onset in zip(notes, onsets):
        if onset > pos + _EPS:
            site.insert(pos, note.Rest(quarterLength=onset - pos))
        site.setElementOffset(e, onset)
        pos = onset + float(e.duration.quarterLength)
    site.coreElementsChanged()
    return moved


def repair_onsets(
    seg: Segment, lengths: list[float], report: Report | None = None
) -> int:
    repaired = 0
    for i, length in zip(seg.measure_range, lengths):
        located = _bar_measures(seg, i)
        ref = None
        for u, s, m in located:
            for line in _lines(m):
                if (
                    len({id(e.activeSite) for e in line}) == 1
                    and sum(float(e.duration.quarterLength) for e in line)
                    < length - _EPS
                ):
                    ref = (
                        _complete_lines([m for _, _, m in located], length)
                        if ref is None
                        else ref
                    )
                    if moved := _retime_line(line, ref, length):
                        repaired += 1
                        if report is not None:
                            report(
                                i,
                                u,
                                s,
                                f"moved {moved} note{'s' if moved > 1 else ''} to where they are printed (missed rest or note length)",
                            )
    return repaired


def _bar_rest(m: stream.Measure) -> bool:
    return any(
        getattr(r, "fullMeasure", False) is True
        for r in m.recurse().getElementsByClass(note.Rest)
    )


def _sung_units(seg: Segment) -> set[int]:
    acc = [is_accompaniment(unit.staves[0]) for unit in seg.units]
    return {u for u, a in enumerate(acc) if not a} or set(range(len(seg.units)))


def _nothing_recognised(m: stream.Measure) -> bool:
    return all(
        e.isRest and getattr(e, "fullMeasure", None) == "auto" and _x(e) is None
        for e in m.recurse().notesAndRests
    )


def unrecognised_bars(seg: Segment) -> list[tuple[int, int, int]]:
    sung, out = _sung_units(seg), []
    for i in seg.measure_range:
        located = [(u, s, m) for u, s, m in _bar_measures(seg, i) if u in sung]
        for u, s, m in located:
            if _nothing_recognised(m) and any(
                _has_notes(m2) for _, _, m2 in located if m2 is not m
            ):
                out.append((i, u, s))
    return out


def _notes_signature(m: stream.Measure) -> list[tuple[float, tuple[float, ...]]]:
    return sorted(
        (round(off, 4), tuple(sorted(p.ps for p in el.pitches)))
        for _, off, el in _note_elements(m)
    )


def fill_from_second_reading(
    first: list[stream.Score], second: list[stream.Score], dpi: int
) -> int:
    if len(first) != len(second):
        return 0
    filled = 0
    for s1, s2 in zip(first, second):
        if len(segs1 := segments_of(s1)) != len(segs2 := segments_of(s2)):
            continue
        for seg1, seg2 in zip(segs1, segs2):
            if seg1.measure_range != seg2.measure_range or [
                len(u.staves) for u in seg1.units
            ] != [len(u.staves) for u in seg2.units]:
                continue
            sung = _sung_units(seg1)
            for i, u, s in unrecognised_bars(seg1):
                ones, twos = (
                    {(uu, ss): m for uu, ss, m in _bar_measures(seg1, i)},
                    {(uu, ss): m for uu, ss, m in _bar_measures(seg2, i)},
                )
                m1, m2 = ones.get((u, s)), twos.get((u, s))
                if (
                    m1 is None
                    or m2 is None
                    or not _has_notes(m2)
                    or _clef_sign(seg1.units[u].staves[s], m1)
                    != _clef_sign(seg2.units[u].staves[s], m2)
                ):
                    continue
                checked = [
                    (key, mm)
                    for key, mm in ones.items()
                    if key[0] in sung and key != (u, s) and _has_notes(mm)
                ]
                if not checked or any(
                    key not in twos
                    or _notes_signature(mm) != _notes_signature(twos[key])
                    for key, mm in checked
                ):
                    continue
                for el in list(m1.notesAndRests) + list(m1.voices):
                    m1.remove(el)
                for el in list(m2.voices) + list(m2.notesAndRests):
                    m1.insert(float(el.offset), copy.deepcopy(el))
                m1.choirsplit_filled, filled = dpi, filled + 1
    return filled


def drop_unaligned_beside_bar_rests(seg: Segment, report: Report | None = None) -> int:
    sung, polyphonic, cleaned = _sung_units(seg), {}, 0
    for i in seg.measure_range:
        located = [(u, s, m) for u, s, m in _bar_measures(seg, i) if u in sung]
        if not (
            resting := [
                (u, s, m) for u, s, m in located if _bar_rest(m) and _has_notes(m)
            ]
        ):
            continue
        ref = [
            x
            for _, _, m in located
            if not _bar_rest(m)
            for _, _, el in _note_elements(m)
            if (x := _x(el)) is not None
        ]
        for u, s, m in resting:
            staff = seg.units[u].staves[s]
            if id(staff) not in polyphonic:
                polyphonic[id(staff)] = (
                    _polyphony(
                        [
                            mm
                            for mm in _measures(staff)
                            if not (_bar_rest(mm) and _has_notes(mm))
                        ]
                    )
                    >= POLYPHONY_THRESHOLD
                )
            if polyphonic[id(staff)]:
                continue
            notes = [el for _, _, el in _note_elements(m)]
            xs = [_x(el) for el in notes]
            if any(x is None for x in xs) or any(
                abs(x - r) <= DOT_X_TOLERANCE for x in xs for r in ref
            ):
                continue
            for el in notes:
                el.activeSite.remove(el)
            cleaned += 1
            if report is not None:
                report(
                    i,
                    u,
                    s,
                    "dropped notes beside a whole-bar rest (cue notes or stray marks)",
                )
    return cleaned


def _time_signature_heads(m: stream.Measure) -> list:
    return [
        e
        for c in [m, *m.voices]
        for e in c.notes
        if float(e.getOffsetBySite(c)) < _EPS and (_x(e) or 999) <= TIME_SIGNATURE_X
    ]


def _remove_heads(m: stream.Measure, heads: list) -> None:
    for el in heads:
        site, length = el.activeSite, float(el.duration.quarterLength)
        site.remove(el)
        for e in list(site.notesAndRests):
            if (
                getattr(e, "fullMeasure", None) is not True
                and (off := float(e.getOffsetBySite(site))) >= length - _EPS
            ):
                site.setElementOffset(e, off - length)
        site.coreElementsChanged()
    m.coreElementsChanged()


def _tie_slurred_repeats(prev: stream.Measure, m: stream.Measure) -> None:
    if not (before := list(prev.recurse().notes)):
        return
    last = max(float(n.getOffsetInHierarchy(prev)) for n in before)
    for n in m.recurse().getElementsByClass(note.Note):
        if float(n.getOffsetInHierarchy(m)) > _EPS:
            continue
        for slur in n.getSpannerSites("Slur"):
            if (
                (start := slur.getFirst()) is not n
                and isinstance(start, note.Note)
                and start.pitch == n.pitch
                and any(start is b for b in before)
                and abs(float(start.getOffsetInHierarchy(prev)) - last) < _EPS
            ):
                held = start.tie is not None and start.tie.type in ("stop", "continue")
                start.tie, n.tie = (
                    tie.Tie("continue" if held else "start"),
                    tie.Tie("stop"),
                )


def drop_misread_time_signatures(
    seg: Segment, report: Report | None = None
) -> dict[int, float | None]:
    found: dict[int, float | None] = {}
    for i in seg.measure_range[1:]:
        located = _bar_measures(seg, i)
        marked = [
            (u, s, m, heads)
            for u, s, m in located
            if (heads := _time_signature_heads(m))
        ]
        if len(marked) < 2 or 2 * len(marked) < len(located):
            continue
        for u, s, m, heads in marked:
            _remove_heads(m, heads)
            if report is not None:
                report(i, u, s, "removed a time signature misread as a note")
        for u, s, m in located:
            _tie_slurred_repeats(_measures(seg.units[u].staves[s])[i - 1], m)
        sung = Counter(
            round(_content_length(m), 4)
            for _, _, m in located
            if _has_notes(m) and not _bar_rest(m)
        )
        length, votes = sung.most_common(1)[0] if sung else (0.0, 0)
        found[i] = (
            length
            if votes >= 2 and votes == sum(sung.values()) and (2 * length).is_integer()
            else None
        )
    return found


def meters_of(
    seg: Segment, found: dict[int, float | None], carried: float | None
) -> tuple[dict[int, float], float | None]:
    out: dict[int, float] = {}
    for i in seg.measure_range:
        if i in found:
            carried = found[i]
        elif any(
            m.getElementsByClass(meter.TimeSignature)
            for _, _, m in _bar_measures(seg, i)
        ):
            carried = None
        if carried is not None:
            out[i] = carried
    return out, carried


def _time_signature(length: float) -> meter.TimeSignature:
    return meter.TimeSignature(
        f"{int(length)}/4" if float(length).is_integer() else f"{int(2 * length)}/8"
    )


def _bar_measures(seg: Segment, i: int) -> list[tuple[int, int, stream.Measure]]:
    return [
        (u, s, ms[i])
        for u, unit in enumerate(seg.units)
        for s, p in enumerate(unit.staves)
        for ms in [_measures(p)]
        if i < len(ms)
    ]


def _rest_measure(length: float) -> stream.Measure:
    m = stream.Measure()
    m.insert(0, note.Rest(quarterLength=length))
    return m


def _carry_context(
    src_part: stream.Part, src_m: stream.Measure, dst_m: stream.Measure
) -> None:
    for cls in (clef.Clef, meter.TimeSignature):
        if (
            dst_m.getElementsByClass(cls).first() is None
            and (ctx := src_m.getContextByClass(cls)) is not None
        ):
            dst_m.insert(0, copy.deepcopy(ctx))
