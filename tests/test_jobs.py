"""Running a job from the desktop app: progress, stopping, and settings a long-lived
process must refuse (pipeline, jobs.run_process)."""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from audio_helpers import make_score
from choirsplit import jobs
from choirsplit.audio_io import EncoderError, write_audio
from choirsplit.pipeline import Options, load_tracks, process_tracks, safe_filename

SATB = ["Soprano", "Alto", "Tenor", "Bass"]


@pytest.fixture(scope="module")
def satb(tmp_path_factory):
    d = tmp_path_factory.mktemp("jobs")
    return make_score(d / "satb.musicxml", [(n, [p] * 4) for n, p in zip(SATB, (72, 65, 57, 48))])


def wav(**kw):
    return Options(fmt="wav", **kw)


def test_progress_goes_to_the_job_and_the_command_line_stays_quiet(satb, tmp_path, capsys):
    seen = []
    with jobs.running(jobs.Job(progress=seen.append)):
        st = load_tracks(satb, wav())
        process_tracks(st, tmp_path / "a", wav(mode="solo", full_mix=False))
    assert seen[0] == "reading satb.musicxml" and "checking the score" in seen
    assert [s for s in seen if s.startswith("rendering")] == [f"rendering {n}" for n in SATB]
    assert sum(s.startswith("writing") for s in seen) == 4
    process_tracks(load_tracks(satb, wav()), tmp_path / "b", wav(mode="solo"))  # no job
    assert capsys.readouterr().err == ""


def test_a_cancelled_job_stops_before_writing(satb, tmp_path):
    st = load_tracks(satb, wav())
    job = jobs.Job()
    job.cancel.set()
    with jobs.running(job), pytest.raises(jobs.Cancelled):
        process_tracks(st, tmp_path, wav())
    assert list(tmp_path.iterdir()) == []


def test_a_failed_write_leaves_no_partial_file(tmp_path):
    import numpy as np

    with pytest.raises(EncoderError):
        write_audio(tmp_path / "x", np.zeros((4800, 2)), 48000, "mp3", bitrate="lots")
    assert list(tmp_path.iterdir()) == []
    assert write_audio(tmp_path / "y", np.zeros((4800, 2)), 48000, "wav").name == "y.wav"
    assert [p.name for p in tmp_path.iterdir()] == ["y.wav"]


def test_tracks_must_be_made_with_the_settings_they_were_loaded_with(satb, tmp_path):
    st = load_tracks(satb, wav(tempo_scale=0.5))
    with pytest.raises(ValueError, match="load it again"):
        process_tracks(st, tmp_path, wav())
    # Render-time settings may differ.
    assert process_tracks(st, tmp_path, wav(tempo_scale=0.5, mode="solo", transpose=2, full_mix=False))


def test_exporting_over_the_score_itself_is_refused(satb):
    before = satb.read_bytes()
    with pytest.raises(ValueError, match="overwrite the score"):
        load_tracks(satb, wav(export_musicxml=satb.parent / "." / satb.name))
    assert satb.read_bytes() == before


@pytest.mark.parametrize("kw, message", [
    ({"tempo_scale": 0.05}, "tempo scale must be between"),
    ({"tempo_scale": 10}, "tempo scale must be between"),
    ({"bpm": 1}, "bpm must be between"),
    ({"bpm": 1000}, "bpm must be between"),
    ({"bitrate": "abc"}, "bitrate"),
    ({"bitrate": "999k"}, "bitrate"),
    ({"bitrate": "16k"}, "bitrate"),
    ({"only": "Soprano"}, "list of voice names"),
    ({"others_gain": True}, "others volume"),
])
def test_settings_a_window_could_send_are_checked_up_front(kw, message):
    with pytest.raises(ValueError, match=message):
        Options(**kw).validate()


def test_undecodable_file_names_still_make_a_safe_name():
    assert safe_filename("caf\udce9 Ave") == "caf_ Ave"


def _sleeper(tmp_path):
    """A program that starts a child and then waits, like Audiveris starting Java."""
    pidfile = tmp_path / "child.pid"
    code = ("import subprocess, sys, time; "
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"open({str(pidfile)!r}, 'w').write(str(c.pid)); print('[pn#3] started', flush=True); "
            "time.sleep(60)")
    return [sys.executable, "-c", code], pidfile


def _alive(pid: int) -> bool:
    import os

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A killed child of a killed parent may linger as a zombie until reaped by init.
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return True


@pytest.mark.skipif(sys.platform == "win32", reason="checks POSIX process groups")
def test_cancelling_stops_the_program_and_its_children(tmp_path):
    argv, pidfile = _sleeper(tmp_path)
    job, lines = jobs.Job(), []
    threading.Timer(1.5, job.cancel.set).start()
    started = time.monotonic()
    with jobs.running(job), pytest.raises(jobs.Cancelled):
        jobs.run_process(argv, on_line=lines.append)
    assert time.monotonic() - started < 10
    assert lines == ["[pn#3] started\n"]
    child = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(child)


def test_run_process_times_out_and_returns_output(tmp_path):
    argv, _ = _sleeper(tmp_path)
    with pytest.raises(subprocess.TimeoutExpired):
        jobs.run_process(argv, timeout=1)
    done = jobs.run_process([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"])
    assert (done.returncode, done.stdout, done.stderr) == (0, "out\n", "err\n")
