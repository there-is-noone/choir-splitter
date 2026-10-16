"""WAV / MP3 writing, metadata, file names, encoder errors."""

from __future__ import annotations

import subprocess
import sys

import numpy as np
import pytest

from audio_helpers import SR, ffprobe, mp3_duration, read_wav
from choirsplit import audio_io
from choirsplit.audio_io import EncoderError, require_ffmpeg, write_audio, write_mp3, write_wav
from choirsplit.pipeline import safe_filename


def stereo(seconds: float = 0.5, freq: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return np.stack([0.5 * np.sin(2 * np.pi * freq * t), 0.25 * np.cos(2 * np.pi * freq * t)],
                    axis=1).astype(np.float32)


# --- WAV ------------------------------------------------------------------------------

def test_wav_round_trip_stereo(tmp_path):
    audio = stereo()
    path = tmp_path / "x.wav"
    write_wav(path, audio, SR)
    back, sr = read_wav(path)
    assert sr == SR and back.shape == audio.shape
    np.testing.assert_allclose(back, audio, atol=1.5 / 32767)
    # channels are not swapped / interleaving is right
    assert np.abs(back[:, 0]).max() == pytest.approx(0.5, abs=1e-3)
    assert np.abs(back[:, 1]).max() == pytest.approx(0.25, abs=1e-3)


def test_wav_round_trip_mono_and_other_rate(tmp_path):
    audio = stereo()[:, 0]
    path = tmp_path / "m.wav"
    write_wav(path, audio, 22050)
    back, sr = read_wav(path)
    assert sr == 22050 and back.shape == (len(audio), 1)
    np.testing.assert_allclose(back[:, 0], audio, atol=1.5 / 32767)


def test_wav_clips_out_of_range(tmp_path):
    audio = np.array([[2.0, -3.0], [1.0, -1.0], [0.0, 0.0]], dtype=np.float32)
    path = tmp_path / "c.wav"
    write_wav(path, audio, SR)
    back, _ = read_wav(path)
    np.testing.assert_allclose(back, [[1, -1], [1, -1], [0, 0]], atol=1e-4)


def test_wav_non_contiguous_input(tmp_path):
    audio = np.asfortranarray(stereo())
    path = tmp_path / "f.wav"
    write_wav(path, audio, SR)
    back, _ = read_wav(path)
    np.testing.assert_allclose(back, audio, atol=1.5 / 32767)


def test_write_audio_wav_appends_suffix(tmp_path):
    path = write_audio(tmp_path / "name", stereo(0.1), SR, "wav")
    assert path == tmp_path / "name.wav" and path.exists()


def test_write_audio_unknown_format(tmp_path):
    with pytest.raises(ValueError, match="flac"):
        write_audio(tmp_path / "x", stereo(0.1), SR, "flac")


# --- MP3 ------------------------------------------------------------------------------

def test_mp3_written_with_metadata(tmp_path):
    path = write_audio(tmp_path / "song", stereo(1.0), SR, "mp3",
                       metadata={"title": "Sopran 1 (practice)", "album": "Ave Maria — Op. 5"})
    assert path == tmp_path / "song.mp3"
    info = ffprobe(path)
    tags = {k.lower(): v for k, v in info["format"]["tags"].items()}
    assert tags["title"] == "Sopran 1 (practice)"
    assert tags["album"] == "Ave Maria — Op. 5"
    [stream] = info["streams"]
    assert stream["codec_name"] == "mp3"
    assert int(stream["channels"]) == 2 and int(stream["sample_rate"]) == SR
    assert mp3_duration(path) == pytest.approx(1.0, abs=0.06)


def test_mp3_metadata_with_special_characters(tmp_path):
    title = 'a=b; c:"d" \'e\' — ż 1/2'
    path = write_audio(tmp_path / "t", stereo(0.2), SR, "mp3", metadata={"title": title})
    assert ffprobe(path)["format"]["tags"]["title"] == title


def test_mp3_bitrate(tmp_path):
    lo = write_audio(tmp_path / "lo", stereo(2.0), SR, "mp3", bitrate="64k")
    hi = write_audio(tmp_path / "hi", stereo(2.0), SR, "mp3", bitrate="256k")
    assert int(ffprobe(lo)["streams"][0]["bit_rate"]) == 64000
    assert int(ffprobe(hi)["streams"][0]["bit_rate"]) == 256000
    assert hi.stat().st_size > 3 * lo.stat().st_size


def test_mp3_mono(tmp_path):
    path = write_audio(tmp_path / "mono", stereo(0.3)[:, 0], SR, "mp3")
    assert int(ffprobe(path)["streams"][0]["channels"]) == 1


def test_mp3_overwrites_existing(tmp_path):
    p1 = write_audio(tmp_path / "x", stereo(0.2), SR, "mp3")
    p2 = write_audio(tmp_path / "x", stereo(1.0), SR, "mp3")
    assert p1 == p2 and mp3_duration(p2) == pytest.approx(1.0, abs=0.06)


# --- file names -----------------------------------------------------------------------

AWKWARD_NAMES = [
    "Op. 5: Ave/Maria — Sopran 1",
    "Ave Maria - 01 Soprano 1 (practice)",
    "v1.2.3",
    'a\\b*c?d"e<f>g|h',
    "Zażółć gęślą jaźń ♪ 日本語",
    "-starts with a dash",
    "ends with dots...",
    "tab\tand\nnewline",
]
# ffmpeg start-up is slow (~0.3 s), so MP3 runs only for names that could confuse its
# command line (options, protocols) or need unicode handling.
MP3_NAMES = ["Op. 5: Ave/Maria — Sopran 1", "-starts with a dash", "Zażółć gęślą jaźń ♪ 日本語"]


@pytest.mark.parametrize("name, fmt", [(n, "wav") for n in AWKWARD_NAMES] + [(n, "mp3") for n in MP3_NAMES])
def test_awkward_names_are_safe(tmp_path, name, fmt, monkeypatch):
    # relative output dir, as with `choir-split score.xml -o .`
    monkeypatch.chdir(tmp_path)
    from pathlib import Path

    stem = safe_filename(name)
    path = write_audio(Path(".") / stem, stereo(0.2), SR, fmt)
    assert path.exists() and path.parent.resolve() == tmp_path
    assert path.name == stem + "." + fmt
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


_NO_COLON = pytest.mark.skipif(sys.platform == "win32", reason="':' is not allowed in Windows file names")


@pytest.mark.parametrize("dirname", [pytest.param("http:tracks", marks=_NO_COLON),
                                     pytest.param("file:x", marks=_NO_COLON), "-o"])
def test_mp3_relative_dir_that_looks_like_protocol_or_option(tmp_path, monkeypatch, dirname):
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    Path(dirname).mkdir()
    path = write_audio(Path(dirname) / "Soprano", stereo(0.2), SR, "mp3")
    assert (tmp_path / dirname / "Soprano.mp3").is_file() and path.exists()


def test_safe_filename_keeps_dots_and_unicode():
    assert safe_filename("Op. 5: Ave/Maria — Sopran 1") == "Op. 5_ Ave_Maria — Sopran 1"
    assert safe_filename("v1.2.3") == "v1.2.3"
    assert safe_filename("..") == "untitled"
    assert safe_filename("") == "untitled"
    assert "/" not in safe_filename("a/b/../c") and safe_filename("a/b/../c").count(".") == 2


def test_safe_filename_limits_length():
    name = safe_filename("Ü" * 400)
    assert len(name.encode()) <= 200
    assert name.startswith("ÜÜ")


@pytest.mark.parametrize("fmt", ["wav", "mp3"])
def test_write_audio_long_name(tmp_path, fmt):
    stem = safe_filename("A very long title ž " * 30 + " - 01 Soprano (practice)")
    path = write_audio(tmp_path / stem, stereo(0.1), SR, fmt)
    assert path.exists()


# --- encoder errors -------------------------------------------------------------------

def test_bundled_ffmpeg_used_when_none_on_path(monkeypatch):
    monkeypatch.setattr(audio_io.shutil, "which", lambda name: None)
    monkeypatch.setattr(audio_io, "_bundled_ffmpeg", lambda: "/bundled/ffmpeg")
    assert require_ffmpeg() == "/bundled/ffmpeg"


def test_ffmpeg_on_path_wins_over_bundled(monkeypatch):
    monkeypatch.setattr(audio_io.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(audio_io, "_bundled_ffmpeg", lambda: "/bundled/ffmpeg")
    assert require_ffmpeg() == "/usr/bin/ffmpeg"


def test_bundled_ffmpeg_can_encode_mp3():
    exe = audio_io._bundled_ffmpeg()
    if exe is None:
        pytest.skip("imageio-ffmpeg not installed")
    out = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    assert "libmp3lame" in out


def test_missing_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_io.shutil, "which", lambda name: None)
    monkeypatch.setattr(audio_io, "_bundled_ffmpeg", lambda: None)
    with pytest.raises(EncoderError, match="ffmpeg") as exc:
        require_ffmpeg()
    assert "--format wav" in str(exc.value)
    with pytest.raises(EncoderError, match="ffmpeg"):
        write_audio(tmp_path / "x", stereo(0.1), SR, "mp3")
    assert not list(tmp_path.iterdir())
    # WAV doesn't need ffmpeg at all
    assert write_audio(tmp_path / "x", stereo(0.1), SR, "wav").exists()


def test_ffmpeg_failure_surfaces_stderr(tmp_path):
    with pytest.raises(EncoderError) as exc:
        write_mp3(tmp_path / "x.mp3", stereo(0.1), SR, bitrate="lots")
    msg = str(exc.value)
    assert "ffmpeg failed" in msg and "x.mp3" in msg
    assert len(msg) > len(f"ffmpeg failed for {tmp_path / 'x.mp3'}: ")  # stderr included


def test_ffmpeg_failure_unwritable_dir(tmp_path):
    with pytest.raises(EncoderError, match="ffmpeg failed"):
        write_mp3(tmp_path / "missing-dir" / "x.mp3", stereo(0.1), SR)


@pytest.mark.skipif(sys.platform == "win32", reason="the fake ffmpeg is a shell script")
def test_ffmpeg_failure_fake_binary(monkeypatch, tmp_path):
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\ncat >/dev/null\necho 'Unknown encoder libmp3lame' >&2\nexit 3\n")
    fake.chmod(0o755)
    monkeypatch.setattr(audio_io.shutil, "which", lambda name: str(fake))
    with pytest.raises(EncoderError, match="Unknown encoder libmp3lame"):
        write_mp3(tmp_path / "x.mp3", stereo(0.1), SR)


def test_ffmpeg_available():
    assert subprocess.run([require_ffmpeg(), "-hide_banner", "-encoders"],
                          capture_output=True, text=True).stdout.count("libmp3lame") >= 1
