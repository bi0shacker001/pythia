"""
launch_and_watch(command, watch_dir): launches an external downloader/tool (f95checker,
a browser, whatever) as its own normal window and passively watches a folder for new
files afterward, rather than requiring the tool to report back to Pythia directly.
Pythia never moves, extracts, or otherwise touches what shows up here -- `on_new_file`
just gets a path; deciding what to do with it (e.g. handing it to onboarding) is the
caller's job.

StdoutWatcher is defined as an interface for line-based progress/completion parsing
but ships with no concrete implementation -- f95checker's actual CLI output format
needs to be inspected before writing real parsing rules for it (see the Milestone 2
plan's Deferred section). The folder-watch path already covers browser-driven
downloads regardless of which tool is doing the downloading.
"""

import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

DEFAULT_POLL_INTERVAL = 3.0


class StdoutWatcher:
    """Interface for a line-based progress/completion parser fed each stdout line of
    a launched process. No concrete implementation ships yet -- see module docstring."""

    def on_line(self, line: str):
        raise NotImplementedError


def launch_and_watch(
    command,
    watch_dir: Path,
    on_new_file: Callable[[Path], None],
    stdout_watcher: Optional[StdoutWatcher] = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    stop_when_process_exits: bool = True,
    stop_event: Optional[threading.Event] = None,
) -> subprocess.Popen:
    """
    Launch `command` as its own process/window, then poll `watch_dir` (in the calling
    thread -- run this on a background thread yourself, same pattern as onboarding/
    scanning) for files that appear after launch, calling `on_new_file(path)` for each
    new one. Returns the process handle once watching stops, either because the
    process exited (if stop_when_process_exits) or `stop_event` was set.
    """
    watch_dir = Path(watch_dir)
    watch_dir.mkdir(parents=True, exist_ok=True)
    seen = {p.name for p in watch_dir.iterdir() if p.is_file()}

    stdout_pipe = subprocess.PIPE if stdout_watcher else None
    proc = subprocess.Popen(command, stdout=stdout_pipe, stderr=subprocess.STDOUT, text=True)

    if stdout_watcher:

        def _read_stdout():
            for line in proc.stdout:
                stdout_watcher.on_line(line.rstrip("\n"))

        threading.Thread(target=_read_stdout, daemon=True).start()

    while True:
        try:
            current = {p.name for p in watch_dir.iterdir() if p.is_file()}
        except OSError:
            current = set()
        for name in sorted(current - seen):
            on_new_file(watch_dir / name)
        seen = current

        if stop_event is not None and stop_event.is_set():
            break
        if stop_when_process_exits and proc.poll() is not None:
            break
        time.sleep(poll_interval)

    return proc
