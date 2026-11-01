"""The job a thread is running: where its progress messages go, and whether it was asked
to stop. The command line runs without one (messages go to stderr); the desktop app sets
one per run, so code deep in the loaders can report and stop without passing it along."""

from __future__ import annotations

import contextlib
import contextvars
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field

from . import platforms


class Cancelled(Exception):
    """The job was asked to stop. Not a ScoreLoadError, so fallbacks don't swallow it."""


@dataclass
class Job:
    progress: Callable[[str], None] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)


_current: ContextVar[Job | None] = ContextVar("choirsplit_job", default=None)


@contextlib.contextmanager
def running(job: Job) -> Iterator[Job]:
    token = _current.set(job)
    try:
        yield job
    finally:
        _current.reset(token)


def report(message: str) -> None:
    """A message the user should see (a page skipped, a second reading): to the job's
    listener, else to stderr."""
    job = _current.get()
    if job is not None and job.progress is not None:
        job.progress(message)
    elif sys.stderr is not None:
        print(f"choirsplit: {message}", file=sys.stderr)


def progress(stage: str) -> None:
    """What the job is doing now ("reading page 3 of 14"); the command line stays quiet."""
    job = _current.get()
    if job is not None and job.progress is not None:
        job.progress(stage)


def check_cancelled() -> None:
    job = _current.get()
    if job is not None and job.cancel.is_set():
        raise Cancelled("cancelled")


def _kill(proc: subprocess.Popen) -> None:
    """Stop `proc` and everything it started (Audiveris runs Java, flatpak a sandbox)."""
    try:
        if platforms.is_windows():
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10, **platforms.no_console())
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    with contextlib.suppress(OSError):
        proc.kill()
    proc.wait()


def run_process(argv: list[str], timeout: float | None = None, env: dict[str, str] | None = None,
                on_line: Callable[[str], None] | None = None) -> subprocess.CompletedProcess:
    """Like subprocess.run(capture_output=True, text=True), but stops the program (and its
    children) when the job is cancelled or `timeout` passes, and shows no console window.
    `on_line` sees each output line as it comes. Raises Cancelled or TimeoutExpired."""
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, errors="replace", env=env,
                            start_new_session=not platforms.is_windows(), **platforms.no_console())
    out: list[str] = []
    err: list[str] = []

    def pump(stream, sink: list[str]) -> None:
        for line in stream:
            sink.append(line)
            if on_line is not None:
                on_line(line)
        stream.close()

    # In the caller's context, so `on_line` can report to the job (threads start without it).
    readers = [threading.Thread(target=contextvars.copy_context().run, args=(pump, s, sink), daemon=True)
               for s, sink in ((proc.stdout, out), (proc.stderr, err))]
    for r in readers:
        r.start()
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while True:
            try:
                proc.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                check_cancelled()
                if deadline is not None and time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(argv, timeout) from None
    except BaseException:  # cancelled, timed out, Ctrl+C: leave nothing running
        _kill(proc)
        raise
    finally:
        for r in readers:
            r.join(timeout=5)
    return subprocess.CompletedProcess(argv, proc.returncode, "".join(out), "".join(err))
