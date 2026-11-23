"""The games Pythia started and that are still running, so a front end can show them and end one.

Pythia starts a folder game or a runner game as a process of its own (pythia.folder_launch, pythia.droidtop.launch).
Those starts are recorded here with the process, and a thread asks each one now and then whether it has ended. A
game started by Steam's own link, by another launcher, or before this Pythia started is not known here: Steam owns
that process, and nothing in Pythia could end it. ``quit`` says so instead of pretending.
"""

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from pythia.util.log import logger

POLL_SECONDS = 1.0
NOT_STARTED_HERE = (
    "Pythia did not start this game, or has lost track of it, so it cannot end it. "
    "A game started through Steam is Steam's to end."
)


def _now() -> int:
    return int(time.time())


class _Run:
    def __init__(self, proc: Any, via: str, started_at: int) -> None:
        self.proc = proc
        self.via = via
        self.started_at = started_at

    def alive(self) -> bool:
        return self.proc.poll() is None


def _terminate(proc: Any) -> None:
    """Asks the game, and what it started, to end: its process group where it has one of its own (a game is
    started in a new session), else the process and its children by Windows' own tool (closed, else ended)."""
    if os.name == "nt":
        # A game with a window is asked to close; a program with none cannot be, so it is ended outright.
        for flags in ([], ["/F"]):
            done = subprocess.run(  # noqa: S603 -- Windows' own taskkill, the pid is ours
                ["taskkill", "/PID", str(proc.pid), "/T", *flags],  # noqa: S607
                check=False,
                capture_output=True,
            )
            if done.returncode == 0:
                break
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()


class Tracker:
    """The running games by game id. Listeners are called as ``listener("started" | "stopped", info)`` from
    whichever thread noticed, with ``info`` = {"game_id", "via", "started_at", "pid"} (and "exit_code" when stopped)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, list[_Run]] = {}
        self._listeners: list[Callable[[str, dict], None]] = []
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()

    def subscribe(self, listener: Callable[[str, dict], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[str, dict], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _tell(self, kind: str, info: dict) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(kind, dict(info))
            except Exception as ex:  # noqa: BLE001 -- one listener must not stop the others or the watcher
                logger.exception("A running-games listener failed: %s", ex)

    @staticmethod
    def _info(game_id: str, run: _Run) -> dict:
        return {"game_id": game_id, "via": run.via, "started_at": run.started_at, "pid": getattr(run.proc, "pid", None)}

    def track(self, game_id: str, proc: Any, via: str) -> None:
        """Records a game just started as ``proc`` (a subprocess.Popen)."""
        run = _Run(proc, via, _now())
        with self._lock:
            first = game_id not in self._runs
            self._runs.setdefault(game_id, []).append(run)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._watch, name="pythia-running-games", daemon=True)
                self._thread.start()
        self._wake.set()
        if first:
            self._tell("started", self._info(game_id, run))

    def _sweep(self) -> None:
        """Forgets the processes that ended; a game is stopped when its last process has."""
        stopped: list[tuple[str, _Run]] = []
        with self._lock:
            for game_id in list(self._runs):
                still = []
                for run in self._runs[game_id]:
                    if run.alive():
                        still.append(run)
                    else:
                        stopped.append((game_id, run))
                if still:
                    self._runs[game_id] = still
                else:
                    del self._runs[game_id]
        told: set[str] = set()
        for game_id, run in stopped:
            with self._lock:
                gone = game_id not in self._runs
            if gone and game_id not in told:
                told.add(game_id)
                info = self._info(game_id, run)
                code = run.proc.poll()
                info["exit_code"] = code if isinstance(code, int) else None
                self._tell("stopped", info)

    def _watch(self) -> None:
        while True:
            self._sweep()
            with self._lock:
                if not self._runs:
                    self._thread = None
                    return
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    def games(self) -> list[dict]:
        """The running games, oldest start first."""
        self._sweep()
        with self._lock:
            found = [self._info(gid, runs[0]) for gid, runs in self._runs.items()]
        return sorted(found, key=lambda info: (info["started_at"], info["game_id"]))

    def is_running(self, game_id: str) -> bool:
        self._sweep()
        with self._lock:
            return game_id in self._runs

    def quit(self, game_id: str) -> bool:
        """Asks a running game to end; False when this tracker does not have it. The stop is told as an event."""
        self._sweep()
        with self._lock:
            runs = list(self._runs.get(game_id, []))
        if not runs:
            return False
        for run in runs:
            try:
                _terminate(run.proc)
            except OSError as ex:
                logger.warning("Could not end %s (pid %s): %s", game_id, getattr(run.proc, "pid", "?"), ex)
        self._wake.set()
        return True


TRACKER = Tracker()
