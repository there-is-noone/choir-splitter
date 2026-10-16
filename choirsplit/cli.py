"""Command line interface: `choir-split score.musicxml -o tracks/`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audio_io import EncoderError, require_ffmpeg
from .loaders import ScoreLoadError, supported_suffixes
from .pipeline import Options, load_tracks, process_tracks


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="choir-split",
        description="Turn a choir score into separate audio tracks for every voice.",
    )
    p.add_argument("score", type=Path, help="score file (" + ", ".join(sorted(supported_suffixes())) + ")")
    p.add_argument("-o", "--output", type=Path, help="output directory (default: <score name>_tracks)")
    p.add_argument("--mode", choices=("solo", "practice", "both"), default="both",
                   help="solo = the voice alone; practice = the voice loud, others quiet (default: both)")
    p.add_argument("--no-full-mix", dest="full_mix", action="store_false", help="skip the all-voices mix")
    p.add_argument("--format", dest="fmt", choices=("mp3", "wav"), default="mp3")
    p.add_argument("--bitrate", default="192k", help="MP3 bitrate (default: 192k)")
    p.add_argument("--bpm", type=float, help="fixed tempo in quarter notes per minute (overrides the score)")
    p.add_argument("--tempo-scale", type=float, default=1.0, help="speed factor, e.g. 0.8 for slower practice")
    p.add_argument("--transpose", type=int, default=0, help="transpose by N semitones")
    p.add_argument("--others-volume", dest="others_gain", type=float, default=0.3,
                   help="level of the other voices in practice tracks, 0-1 (default: 0.3)")
    p.add_argument("--no-accompaniment", dest="include_accompaniment", action="store_false",
                   help="leave piano/organ parts out of the mixes")
    p.add_argument("--no-split-voices", dest="split_voices", action="store_false",
                   help="don't split staves that hold several voices (e.g. S and A on one staff)")
    p.add_argument("--no-split-chords", dest="split_chords", action="store_false",
                   help="don't split two-note chords on one staff into upper and lower parts")
    p.add_argument("--only", action="append", default=[], metavar="VOICE",
                   help="only write tracks for this voice (repeatable)")
    p.add_argument("--title", help="song title used in file names and MP3 tags (default: from the score)")
    p.add_argument("--list", action="store_true", help="list the detected voices and exit")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream_ in (sys.stdout, sys.stderr):
        if hasattr(stream_, "reconfigure"):  # e.g. a Windows console or pipe in cp1252
            stream_.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    # Every option goes through one Options object, so the CLI and process_file agree.
    opts = Options(
        mode=args.mode, full_mix=args.full_mix, fmt=args.fmt, bitrate=args.bitrate,
        bpm=args.bpm, tempo_scale=args.tempo_scale, transpose=args.transpose,
        others_gain=args.others_gain, include_accompaniment=args.include_accompaniment,
        split_voices=args.split_voices, split_chords=args.split_chords, only=args.only,
        title=args.title,
    )
    try:
        st = load_tracks(args.score, opts)
        if args.list:
            print(f"{st.title} ({st.duration:.1f}s)")
            for i, t in enumerate(st.tracks, start=1):
                print(f"  {i:2d}. {t.name} [{t.kind}] {len(t.notes)} notes")
            return 0
        if args.fmt == "mp3":
            require_ffmpeg()
        out_dir = args.output or args.score.with_name(args.score.stem + "_tracks")
        written = process_tracks(st, out_dir, opts)
    except (ScoreLoadError, EncoderError, ValueError, OSError) as exc:
        print(f"choir-split: error: {exc}", file=sys.stderr)
        return 1
    print(f"{st.title}: {len(st.voices)} voices, {st.duration:.1f}s")
    for path in written:
        print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
