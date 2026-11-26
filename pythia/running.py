"""The games Pythia started and that are still running, so a front end can show them and end one.

Pythia starts a folder game or a runner game as a process of its own (pythia.folder_launch, pythia.droidtop.launch).
Those starts are recorded here with the process, and a thread asks each one now and then whether it has ended. A
game started by Steam's own link, by another launcher, or before this Pythia started is known from outside
(pythia.running_external reads Steam's running app and the operating system's processes and tells ``set_external``):
it is listed and its start and end are told, but it ends only by a route that is its own. A game of Steam's is
Steam's to end, and ``quit`` says so instead of pretending.
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
STEAM_OWNS = (
    "Steam started this game, and Steam is the one that ends it: it has no way to end a single game from outside. "
    "Close it in Steam, or in the game."
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


def _terminate_pid(pid: int) -> None:
    """Asks a game Pythia did not start to end: its process group when it leads one, else the process alone;
    Windows' own taskkill (closed, else ended) with what it started."""
    if os.name == "nt":
        for flags in ([], ["/F"]):
            done = subprocess.run(  # noqa: S603 -- Windows' own taskkill
                ["taskkill", "/PID", str(pid), "/T", *flags],  # noqa: S607
                check=False,
                capture_output=True,
            )
            if done.returncode == 0:
                break
        return
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


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
        self._external: dict[str, dict] = {}  # games running that Pythia did not start (running_external)
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
        return {
            "game_id": game_id,
            "via": run.via,
            "started_at": run.started_at,
            "pid": getattr(run.proc, "pid", None),
            "quittable": True,
        }

    @staticmethod
    def _external_info(game_id: str, found: dict) -> dict:
        return {
            "game_id": game_id,
            "via": found["via"],
            "started_at": None,
            "pid": found["pid"],
            "quittable": found["quittable"],
        }

    def is_tracked(self, game_id: str) -> bool:
        """Whether Pythia started this game and it still runs (what the outside readings leave alone)."""
        with self._lock:
            return game_id in self._runs

    def set_external(self, found: dict[str, dict]) -> None:
        """The games now running that Pythia did not start (pythia.running_external.detect): tells the start of each
        new one and the end of each that is gone, as for a game Pythia started. ``started_at`` is None (unknown)."""
        with self._lock:
            before = self._external
            self._external = {g: dict(f) for g, f in found.items() if g not in self._runs}
            now = dict(self._external)
        for game_id, item in now.items():
            if game_id not in before:
                self._tell("started", self._external_info(game_id, item))
        for game_id, item in before.items():
            if game_id not in now:
                info = self._external_info(game_id, item)
                info["exit_code"] = None
                self._tell("stopped", info)

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
            found += [self._external_info(gid, item) for gid, item in self._external.items() if gid not in self._runs]
        return sorted(found, key=lambda info: (info["started_at"] or 0, info["game_id"]))

    def is_running(self, game_id: str) -> bool:
        self._sweep()
        with self._lock:
            return game_id in self._runs or game_id in self._external

    def why_not(self, game_id: str) -> str:
        """Why ``quit`` cannot end this game: Steam's to end, or one Pythia has no hold of."""
        with self._lock:
            external = self._external.get(game_id)
        return STEAM_OWNS if external and external["via"] == "steam" else NOT_STARTED_HERE

    def quit(self, game_id: str) -> bool:
        """Asks a running game to end; False when it cannot be ended from here (``why_not`` says why). A game
        found running by its folder is asked to end through the operating system; a game of Steam's is not.
        The stop is told as an event."""
        self._sweep()
        with self._lock:
            runs = list(self._runs.get(game_id, []))
            external = None if runs else self._external.get(game_id)
        if external is not None:
            if not external["quittable"]:
                return False
            for pid in external["pids"]:
                _terminate_pid(pid)
            self._wake.set()
            return True
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
