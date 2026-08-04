"""
Single-instance tracking for the UI process, via a plain PID file in
app_data_dir() (no lock-file library, no extra dependency -- stdlib subprocess/os
only, consistent with the project's dependency policy).

This does NOT enforce single-instance on its own (launching a second copy
side-by-side still works, same as before) -- it only backs the explicit
`--relaunch` flag: close whatever instance the PID file points at, then start
fresh. There's a known, accepted limitation: if the recorded PID has since been
reused by an unrelated process (rare, but possible after a reboot/PID wraparound),
`--relaunch` could terminate that unrelated process. Not guarded against further
since detecting "is this PID actually still our Qt process" reliably without a
psutil-class dependency isn't worth it for a convenience flag.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from pythia.identity import app_data_dir


def _pidfile_path() -> Path:
    return app_data_dir() / "pythia.pid"


def _read_pid() -> int | None:
    try:
        return int(_pidfile_path().read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        return
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(50):
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def terminate_existing_instance(timeout: float = 5.0) -> bool:
    """If the PID file points at a still-running process, kill it and wait for it
    to actually exit (bounded by `timeout`). Returns whether anything was
    terminated. Safe to call when no instance is running -- it's a no-op then."""
    pid = _read_pid()
    if pid is None or pid == os.getpid() or not _pid_alive(pid):
        return False

    print(f"--relaunch: closing existing Pythia instance (pid {pid})...", file=sys.stderr)
    _kill_pid(pid)

    deadline = time.monotonic() + timeout
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)

    try:
        _pidfile_path().unlink()
    except OSError:
        pass
    return True


def write_pidfile() -> None:
    path = _pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()))


def clear_pidfile() -> None:
    """Remove the PID file, but only if it's still ours -- a --relaunch call from
    a newer instance may have already overwritten it with its own PID by the time
    this (older) instance's shutdown handler runs."""
    if _read_pid() == os.getpid():
        try:
            _pidfile_path().unlink()
        except OSError:
            pass
