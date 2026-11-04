from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

from music21 import instrument, layout, stream, text
from music21 import metadata as m21md

from .check import Issue
from .engine import find_engine, recognise
from .postprocess import (
    _bar_measures,
    _best_name,
    _carry_context,
    _is_default_name,
    _measures,
    _nominal_lengths,
    _rest_measure,
    _time_signature,
    align_units,
    clip_measure,
    drop_misread_time_signatures,
    drop_unaligned_beside_bar_rests,
    fill_from_second_reading,
    meters_of,
    repair_missing_dots,
    repair_onsets,
    segments_of,
    unrecognised_bars,
)
from .score import is_accompaniment
from .types import _error
from .voice import (
    _closed_score,
    drop_range_strays,
    fix_tenor_octave,
    name_unnamed_parts,
    tidy_voices,
)


def omr_dpi(dpi: int | None = None) -> int | None:
    if dpi is not None:
        return dpi
    if not (env := os.environ.get("CHOIRSPLIT_OMR_DPI", "").strip()):
        return None
    try:
        value = int(env)
    except ValueError:
        raise _error(
            f"CHOIRSPLIT_OMR_DPI must be a whole number, got {env!r}"
        ) from None
    if not 100 <= value <= 1200:
        raise _error(f"CHOIRSPLIT_OMR_DPI must be between 100 and 1200, got {value}")
    return value


def load(path: Path, dpi: int | None = None) -> stream.Score:
    path = Path(path)
    if (engine := find_engine()) is None:
        raise _error(f"cannot read {path.name}: " + install_hint())
    dpi = omr_dpi(dpi)
    scores, read_at = recognise(path, engine, dpi)
    if not scores:
        raise _error(f"OMR found no music in {path.name}")
    if dpi is None and engine.name == "audiveris" and path.suffix.lower() == ".pdf":
        if missed := sum(
            len(unrecognised_bars(seg)) for s in scores for seg in segments_of(s)
        ):
            again = (
                SECOND_READING_DPI
                if read_at is None
                else round(read_at * SECOND_READING_DPI / AUDIVERIS_DEFAULT_DPI)
            )
            second_reading(path, engine, scores, missed, again)
    return assemble(scores, title_hint=path.stem)


def second_reading(
    path: Path, engine: Engine, scores: list[stream.Score], missed: int, dpi: int = 250
) -> int:
    jobs.report(
        f"{missed} sung bar{'s' if missed > 1 else ''} not recognised; reading {path.name} again at {dpi} dpi"
    )
    try:
        second, dpi = recognise(path, engine, dpi)
    except ScoreLoadError as exc:
        jobs.report(f"second reading failed: {exc}")
        return 0
    return fill_from_second_reading(scores, second, dpi)


def assemble(scores: list[stream.Score], title_hint: str | None = None) -> stream.Score:
    segments = []
    for s in scores:
        if segs := segments_of(s):
            segs[0].new_page = True
            segments.extend(segs)
    if not segments:
        raise _error("OMR output contains no measures")

    n_logical = max(len(seg.units) for seg in segments)
    refs = next(seg.units for seg in segments if len(seg.units) == n_logical)
    mappings = [align_units(seg.units, refs) for seg in segments]

    n_staves = [1] * n_logical
    names, abbrs = [[] for _ in range(n_logical)], [[] for _ in range(n_logical)]
    instruments: list[instrument.Instrument | None] = [None] * n_logical

    for seg, mapping in zip(segments, mappings):
        for u, k in zip(seg.units, mapping):
            n_staves[k] = max(n_staves[k], len(u.staves))
            names[k].append(u.name)
            abbrs[k].append(u.abbreviation)
            if instruments[k] is None:
                instruments[k] = u.staves[0].getInstrument(returnDefault=False)

    out_parts: list[list[stream.Part]] = []
    for k in range(n_logical):
        name = _best_name(names[k])
        abbr = min(
            (a for a in abbrs[k] if a and not _is_default_name(a)),
            key=len,
            default=None,
        )
        staves = []
        for s in range(n_staves[k]):
            p = stream.PartStaff() if n_staves[k] > 1 else stream.Part()
            p.id = f"V{k + 1}-Staff{s + 1}" if n_staves[k] > 1 else f"V{k + 1}"
            p.partName, p.partAbbreviation = name or None, abbr
            if instruments[k] is not None:
                inst = copy.deepcopy(instruments[k])
                inst.partName = name or None
                p.insert(0, inst)
            staves.append(p)
        out_parts.append(staves)

    log: list[tuple[int | None, int, int, str, str]] = []
    locations: dict[int, tuple[int, int]] = {}
    offset, number, page, system = 0.0, 0, 0, 0
    previous_source: list[list[int | None]] = [[None] * n for n in n_staves]
    carried: float | None = None

    for seg, mapping in zip(segments, mappings):
        page, system = (page + 1, 1) if seg.new_page else (page, system + 1)
        first = number + 1 - seg.measure_range.start

        def report(i, u, s, detail, kind="fixed", mapping=mapping, first=first):
            log.append((first + i, mapping[u], s, kind, detail))

        meters, carried = meters_of(
            seg, drop_misread_time_signatures(seg, report), carried
        )
        drop_unaligned_beside_bar_rests(seg, report)
        lengths = _nominal_lengths(seg, meters)
        repair_missing_dots(seg, lengths, report)
        repair_onsets(seg, lengths, report)
        for i, u, s in unrecognised_bars(seg):
            report(i, u, s, "nothing recognised in this bar (missed note?)", "suspect")

        slot_unit = {k: u for u, k in zip(seg.units, mapping)}
        for i, length in zip(seg.measure_range, lengths):
            number += 1
            locations[number] = (page, system)
            bar_ts = (
                _time_signature(meters[i])
                if (
                    i in meters
                    and (i == seg.measure_range.start or meters.get(i - 1) != meters[i])
                )
                else next(
                    (
                        ts
                        for _, _, mm in _bar_measures(seg, i)
                        for ts in mm.getElementsByClass(meter.TimeSignature)
                    ),
                    None,
                )
            )

            for k in range(n_logical):
                u = slot_unit.get(k)
                for s, dest in enumerate(out_parts[k]):
                    src = u.staves[s] if u is not None and s < len(u.staves) else None
                    if src is not None and i < len(src_ms := _measures(src)):
                        if filled := getattr(src_ms[i], "choirsplit_filled", None):
                            log.append(
                                (
                                    number,
                                    k,
                                    s,
                                    "fixed",
                                    f"filled from a second reading at {filled} dpi",
                                )
                            )
                        m = copy.deepcopy(src_ms[i])
                        if previous_source[k][s] != id(src):
                            _carry_context(src, src_ms[i], m)
                            previous_source[k][s] = id(src)
                        if clip_measure(m, length):
                            log.append(
                                (
                                    number,
                                    k,
                                    s,
                                    "suspect",
                                    "notes ran past the end of the bar and were cut (missed bar line?)",
                                )
                            )
                    else:
                        m = _rest_measure(length)
                        previous_source[k][s] = None

                    if bar_ts is not None:
                        for ts in list(m.getElementsByClass(meter.TimeSignature)):
                            m.remove(ts)
                        m.insert(0, copy.deepcopy(bar_ts))
                    m.number = number
                    dest.insert(offset, m)
            offset += length

    result = stream.Score()
    md = copy.deepcopy(scores[0].metadata) if scores[0].metadata is not None else None
    hymn = _closed_score(out_parts)
    acc = [is_accompaniment(staves[0]) and not hymn for staves in out_parts]
    lines, slots = {}, []

    for k, staves in enumerate(out_parts):
        for s, p in enumerate(staves):
            if acc[k]:
                lines[id(p)] = 1
            else:
                strays, dropped = [], []
                drop_range_strays(
                    p,
                    below=k + 1 < len(acc) and acc[k + 1],
                    above=k > 0 and acc[k - 1],
                    log=strays,
                )
                lines[id(p)] = tidy_voices(p, dropped)
                log.extend(
                    (
                        n,
                        k,
                        s,
                        "fixed",
                        "dropped a note outside the voice's range next to the accompaniment",
                    )
                    for n in strays
                )
                log.extend(
                    (n, k, s, "fixed", "dropped stray notes beside a whole-bar rest")
                    for n in dropped
                )
            result.insert(0, p)
            slots.append((k, s))

    name_unnamed_parts(result, lines)
    part_names, grouped = {}, {}
    for p, (k, s) in zip(result.parts, slots):
        part_names.setdefault((k, s), p.partName or f"Part {k + 1}")
        grouped.setdefault(k, []).append(p)

    for staves in grouped.values():
        if len(staves) > 1 and all(isinstance(p, stream.PartStaff) for p in staves):
            result.insert(
                0, layout.StaffGroup(staves, name=staves[0].partName, symbol="brace")
            )

    for p, (k, s) in zip(result.parts, slots):
        if fix_tenor_octave(p):
            log.append(
                (
                    None,
                    k,
                    s,
                    "fixed",
                    "sung an octave lower (treble clef read without its small 8)",
                )
            )

    if md is not None:
        result.metadata = md
    _fix_title(result, scores[0], title_hint)
    result.omr_issues = [
        Issue(
            bar,
            part_names.get((k, s), f"Part {k + 1}"),
            kind,
            detail,
            *(locations.get(bar, (None, None)) if bar is not None else (None, None)),
        )
        for bar, k, s, kind, detail in sorted(
            log, key=lambda e: (e[0] or 0, e[1], e[2])
        )
        if not acc[k]
    ]
    result.omr_locations = locations
    return result


def _fix_title(
    result: stream.Score, original: stream.Score, title_hint: str | None
) -> None:
    md = result.metadata
    if (title := md.title if md is not None else None) and title != title_hint:
        return
    if (
        movement := md.movementName if md is not None else None
    ) and movement != title_hint:
        if md is not None and not title:
            md.title = movement
        return
    boxes = [
        tb
        for tb in original.getElementsByClass(text.TextBox)
        if (tb.page in (None, 1)) and re.search(r"[A-Za-z]{2}", tb.content or "")
    ]
    if not boxes:
        return
    biggest = max(boxes, key=lambda tb: float(tb.style.fontSize or 0))
    if md is None:
        md = m21md.Metadata()
        result.metadata = md
    md.title = biggest.content.strip()


if __name__ == "__main__":
    sc = load(Path(sys.argv[1]))
    for p in sc.parts:
        print(p.id, p.partName, len(p.flatten().notes))
