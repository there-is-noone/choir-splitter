"""Write stereo float audio to WAV (stdlib) or MP3 (ffmpeg + libmp3lame)."""

from __future__ import annotations

import os
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from . import platforms


class EncoderError(RuntimeError):
    pass


def require_ffmpeg() -> str:
    """ffmpeg on PATH, else the one bundled with the imageio-ffmpeg package (all platforms)."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        exe = _bundled_ffmpeg()
    if exe is None:
        raise EncoderError(
            "ffmpeg was not found: reinstall choirsplit (it brings ffmpeg via the imageio-ffmpeg "
            "package), install ffmpeg yourself, or use --format wav"
        )
    return exe


def _bundled_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # not installed, or no binary for this platform
        return None


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(audio.shape[1] if audio.ndim == 2 else 1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def write_mp3(
    path: Path,
    audio: np.ndarray,
    sample_rate: int,
    *,
    bitrate: str = "192k",
    metadata: dict[str, str] | None = None,
) -> None:
    channels = audio.shape[1] if audio.ndim == 2 else 1
    cmd = [
        require_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "f32le", "-ar", str(sample_rate), "-ac", str(channels), "-i", "pipe:0",
        "-codec:a", "libmp3lame", "-b:a", bitrate,
    ]
    for key, value in (metadata or {}).items():
        cmd += ["-metadata", f"{key}={value}"]
    # "file:" stops ffmpeg reading a name like "-x.mp3" as an option or "a:b" as a protocol.
    cmd.append(f"file:{path}")
    data = np.ascontiguousarray(audio, dtype="<f4").tobytes()
    proc = subprocess.run(cmd, input=data, capture_output=True, **platforms.no_console())
    if proc.returncode != 0:
        raise EncoderError(f"ffmpeg failed for {path}: {proc.stderr.decode(errors='replace').strip()}")


def write_audio(path: Path, audio: np.ndarray, sample_rate: int, fmt: str, **kwargs) -> Path:
    if fmt not in ("wav", "mp3"):
        raise ValueError(f"unsupported output format: {fmt}")
    path = path.parent / f"{path.name}.{fmt}"  # not with_suffix: names may contain dots
    # Written under a temporary name first, so a stopped run leaves no half-written track.
    part = path.parent / f"{path.name}.part.{fmt}"  # (ffmpeg picks the format by extension)
    try:
        if fmt == "wav":
            write_wav(part, audio, sample_rate)
        else:
            write_mp3(part, audio, sample_rate, **kwargs)
        os.replace(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return path
