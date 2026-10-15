"""Shared fixtures: build small music21 scores programmatically and round-trip them.

Scores are described with a tiny note syntax so tests stay readable:

    "C5 D5:2 r E5:1/3 E5:1/3 E5:1/3"   -> quarter, half, quarter rest, triplet eighths
    "C5+E5:2"                          -> chord (two-note chord, half note)
    ["C5:4~", "C5:2"]                  -> "~" ties the note to the next same pitch (also across bars)
    "gD5 C5"                           -> "g" prefix makes a grace note
    "C5:1.5"                           -> dotted quarter

A part is a list of measures; each measure is either a string (one voice, notes
straight in the measure) or a tuple/list of strings (one music21 Voice each,
ids "1", "2", ...). Ties are tracked across measures inside one voice line.

Builders are plain functions (``build_part``, ``build_score``, ...) and are also
exposed as fixtures of the same short names (``make_part``, ``make_score``,
``make_piano``, ``write_and_load``, ``load_tracks``) so other test modules can
use them without importing conftest.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest
from music21 import chord, instrument as m21instrument, layout, metadata, meter, note, stream, tempo, tie

from choirsplit.loaders import load_score
from choirsplit.score import ScoreTracks, extract_tracks

# --------------------------------------------------------------------------- notes


def _parse_duration(text: str) -> float | Fraction:
    if "/" in text:
        num, den = text.split("/")
        return Fraction(int(num), int(den))
    return float(text)


def parse_notes(spec: str, open_ties: set[float] | None = None) -> list:
    """Turn the mini syntax into music21 notes/chords/rests.

    ``open_ties`` (pitch.ps values tied from the previous note) is updated in place so
    ties can run across measure boundaries; pass the same set for consecutive measures.
    """
    if open_ties is None:
        open_ties = set()
    out = []
    for tok in spec.split():
        grace = tok.startswith("g") and len(tok) > 1 and tok[1].isupper()
        if grace:
            tok = tok[1:]
        tied = tok.endswith("~")
        if tied:
            tok = tok[:-1]
        if ":" in tok:
            body, dur = tok.split(":", 1)
            ql = _parse_duration(dur)
        else:
            body, ql = tok, 1.0
        if body.lower() == "r":
            open_ties.clear()
            out.append(note.Rest(quarterLength=ql))
            continue
        names = body.split("+")
        objs = [note.Note(n, quarterLength=ql) for n in names]
        new_open = set()
        for n in objs:
            ps = n.pitch.ps
            if ps in open_ties:
                n.tie = tie.Tie("continue" if tied else "stop")
            elif tied:
                n.tie = tie.Tie("start")
            if tied:
                new_open.add(ps)
        open_ties.clear()
        open_ties.update(new_open)
        el = objs[0] if len(objs) == 1 else chord.Chord(objs, quarterLength=ql)
        if grace:
            el = el.getGrace()
        out.append(el)
    return out


# --------------------------------------------------------------------------- parts / scores


def build_part(
    name: str | None,
    measures,
    *,
    ts: str | None = "4/4",
    instrument=None,
    cls=stream.Part,
    part_id: str | None = None,
    abbreviation: str | None = None,
    first_number: int = 1,
):
    """Build a Part (or PartStaff) from a list of measure specs (see module docstring)."""
    part = cls()
    if part_id is not None:
        part.id = part_id
    if instrument is not None:
        part.insert(0, instrument)
    if name is not None:
        part.partName = name
    if abbreviation is not None:
        part.partAbbreviation = abbreviation
    ties: dict[int, set[float]] = {}
    for i, spec in enumerate(measures):
        m = stream.Measure(number=first_number + i)
        if i == 0 and ts:
            m.insert(0, meter.TimeSignature(ts))
        if isinstance(spec, str):
            for el in parse_notes(spec, ties.setdefault(1, set())):
                m.append(el)
        else:
            for v_index, vspec in enumerate(spec):
                v = stream.Voice(id=str(v_index + 1))
                for el in parse_notes(vspec, ties.setdefault(v_index + 1, set())):
                    v.append(el)
                m.insert(0, v)
        if i == 0 and first_number == 0 and ts:
            # anacrusis: mark the missing beats so exporters keep the bar short
            bar_ql = meter.TimeSignature(ts).barDuration.quarterLength
            m.paddingLeft = max(bar_ql - m.duration.quarterLength, 0)
        part.append(m)
    return part


def build_piano(rh, lh, *, name: str = "Piano", instrument=None, cls=stream.PartStaff):
    """Two PartStaffs (right hand, left hand) that MusicXML export joins into one part."""
    inst = instrument if instrument is not None else m21instrument.Piano()
    upper = build_part(name, rh, instrument=inst, cls=cls)
    lower = build_part(name, lh, cls=cls)
    return [upper, lower]


def build_score(*parts, title: str | None = None, bpm: float | None = None, composer: str | None = None):
    """Assemble a Score. A list/tuple of parts becomes a braced staff group (piano, organ...)."""
    score = stream.Score()
    if title is not None or composer is not None:
        score.insert(0, metadata.Metadata())
        if title is not None:
            score.metadata.title = title
        if composer is not None:
            score.metadata.composer = composer
    flat_parts = []
    for p in parts:
        if isinstance(p, (list, tuple)):
            for staff in p:
                score.insert(0, staff)
                flat_parts.append(staff)
            score.insert(0, layout.StaffGroup(list(p), symbol="brace"))
        else:
            score.insert(0, p)
            flat_parts.append(p)
    if bpm is not None and flat_parts:
        first_measure = flat_parts[0].getElementsByClass(stream.Measure).first()
        (first_measure if first_measure is not None else flat_parts[0]).insert(
            0, tempo.MetronomeMark(number=bpm))
    return score


def name_tracks_for_midi(score):
    """music21's MIDI writer names each track after the part's *instrument*; give unnamed
    parts a Vocalist called like the part so the MIDI file has one named track per voice."""
    for part in score.parts:
        inst = part.getInstrument(returnDefault=False)
        if inst is None:
            inst = m21instrument.Vocalist()
            part.insert(0, inst)
        inst.instrumentName = part.partName
    return score


def satb_parts(names=("Soprano", "Alto", "Tenor", "Bass"),
               pitches=("C5", "E4", "G3", "C3"), measures: int = 2):
    """Simple open SATB: each voice holds one pitch per quarter so tests can identify it."""
    return [build_part(n, [" ".join([p] * 4)] * measures) for n, p in zip(names, pitches)]


def write_score(score, directory: Path, name: str = "score", fmt: str = "musicxml") -> Path:
    ext = {"musicxml": ".musicxml", "midi": ".mid", "mxl": ".mxl"}[fmt]
    path = Path(directory) / f"{name}{ext}"
    score.write(fmt, fp=str(path))
    return path


def track_summary(st: ScoreTracks) -> dict[str, list[tuple[float, float, float]]]:
    """{track name: [(start, duration, pitch), ...]} rounded for easy comparison."""
    return {
        t.name: [(round(n.start, 4), round(n.duration, 4), n.pitch) for n in t.notes]
        for t in st.tracks
    }


def pitches_of(track) -> list[float]:
    return [n.pitch for n in track.notes]


def onsets_of(track) -> list[float]:
    return [round(n.start, 4) for n in track.notes]


def overlaps(track, tol: float = 1e-6) -> int:
    """Number of notes that start before the previous note in the track has ended."""
    ns = sorted(track.notes, key=lambda e: (e.start, e.pitch))
    return sum(1 for a, b in zip(ns, ns[1:]) if b.start < a.start + a.duration - tol)


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def make_part():
    return build_part


@pytest.fixture
def make_piano():
    return build_piano


@pytest.fixture
def make_score():
    return build_score


@pytest.fixture
def write_and_load(tmp_path):
    """write_and_load(score, name='score', fmt='musicxml'|'midi'|'mxl') -> re-parsed Score."""
    counter = {"n": 0}

    def _write_and_load(score, name: str | None = None, fmt: str = "musicxml"):
        counter["n"] += 1
        path = write_score(score, tmp_path, name or f"score{counter['n']}", fmt)
        return load_score(path)

    return _write_and_load


@pytest.fixture
def load_tracks(write_and_load):
    """load_tracks(score, fmt='musicxml', **extract_kwargs) -> ScoreTracks after a file round trip."""

    def _load_tracks(score, fmt: str = "musicxml", **kwargs) -> ScoreTracks:
        return extract_tracks(write_and_load(score, fmt=fmt), **kwargs)

    return _load_tracks
