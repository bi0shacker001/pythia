"""Games that are running and that Pythia did not start itself: a game Steam started, and a game that was already
running when Pythia came up (or that another program started).

Two sources, each the system's own state and nothing Pythia guesses at:

- **Steam's running app.** Steam keeps the app it is running in its own registry: ``RunningAppID`` and, per app,
  ``Apps/<appid>/Running``: in the Windows registry (``HKCU\\Software\\Valve\\Steam``), and in ``registry.vdf``
  (``Registry/HKCU/Software/Valve/Steam``) in Steam's folder on Linux (also the Flatpak's) and macOS. A game of the
  library with that Steam id is running, started by Steam; it is Steam's to end, and Steam has no route that ends
  one game, so Pythia does not.
- **A program of the game's own folder.** A process whose executable is inside the folder of an install Pythia
  records, or a Wine process whose command line names a file in it, is that game running (nothing else with the
  folder in its command line, such as a file manager or an editor, is taken for the game). It is read from the
  operating system: ``/proc`` on Linux, ``ps`` on macOS, the process list of Windows' own API (psapi). A game with a
  Steam id is only ever reported by Steam's state above, so Pythia does not end what Steam owns.

``detect`` is a pure function of the library and those two readings, so it is the same everywhere; ``Observer`` is
the thread that asks every few seconds and tells pythia.running.TRACKER what began and what ended.
"""

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pythia.util.log import logger

POLL_SECONDS = 4.0
# A folder shorter than this is not one game's (a drive, /usr, a home folder): nothing inside it is claimed.
MIN_FOLDER_DEPTH = 3

Process = tuple[int, str, list[str]]  # pid, executable path, command-line arguments


# ---- Steam's own running state ---------------------------------------------------------------------------------------


def _steam_registry_files() -> list[Path]:
    home = Path.home()
    return [
        home / ".steam" / "registry.vdf",
        home / ".local" / "share" / "Steam" / "registry.vdf",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".steam" / "registry.vdf",
        home / "Library" / "Application Support" / "Steam" / "registry.vdf",
    ]


def _walk(tree: Any, *names: str) -> dict:
    """``tree[name]...`` with the names matched ignoring case (Steam writes "Steam", "steam"), or {}."""
    node: Any = tree
    for name in names:
        if not isinstance(node, dict):
            return {}
        node = next((v for k, v in node.items() if k.lower() == name.lower()), {})
    return node if isinstance(node, dict) else {}


def running_apps_from_registry(registry: dict) -> set[str]:
    """The Steam app ids ``registry`` (a parsed registry.vdf) says are running: ``RunningAppID`` and each
    ``Apps/<id>/Running`` that is 1."""
    steam = _walk(registry, "Registry", "HKCU", "Software", "Valve", "Steam")
    found: set[str] = set()
    running = next((v for k, v in steam.items() if k.lower() == "runningappid"), "0")
    if str(running).isdigit() and int(running) > 0:
        found.add(str(int(running)))
    for appid, state in _walk(steam, "Apps").items():
        flag = next((v for k, v in state.items() if k.lower() == "running"), "0") if isinstance(state, dict) else "0"
        if str(appid).isdigit() and str(flag) == "1":
            found.add(str(appid))
    return found


def _windows_running_apps() -> set[str]:
    if sys.platform != "win32":
        return set()
    import winreg  # noqa: PLC0415

    found: set[str] = set()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as steam:
            try:
                value, _ = winreg.QueryValueEx(steam, "RunningAppID")
                if int(value) > 0:
                    found.add(str(int(value)))
            except OSError:
                pass
            try:
                with winreg.OpenKey(steam, "Apps") as apps:
                    index = 0
                    while True:
                        try:
                            appid = winreg.EnumKey(apps, index)
                        except OSError:
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(apps, appid) as app:
                                if int(winreg.QueryValueEx(app, "Running")[0]) == 1 and appid.isdigit():
                                    found.add(appid)
                        except OSError:
                            continue
            except OSError:
                pass
    except OSError:
        return found
    return found


def steam_running_apps() -> set[str]:
    """The Steam app ids Steam says are running right now on this computer; none when Steam is not here."""
    if sys.platform == "win32":
        return _windows_running_apps()
    from pythia.util.steam.vdfutils import vdf_parse  # noqa: PLC0415

    found: set[str] = set()
    for path in _steam_registry_files():
        try:
            with open(path, encoding="utf-8") as handle:
                found |= running_apps_from_registry(vdf_parse(handle, {}))
        except (OSError, ValueError):
            continue
    return found


# ---- the operating system's process list -----------------------------------------------------------------------------


def _linux_processes() -> list[Process]:
    found: list[Process] = []
    me = os.getpid()
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == me:
            continue
        base = f"/proc/{name}"
        try:
            exe = os.readlink(f"{base}/exe")
        except OSError:
            exe = ""
        try:
            with open(f"{base}/cmdline", "rb") as handle:
                args = [a.decode("utf-8", "replace") for a in handle.read().split(b"\x00") if a]
        except OSError:
            args = []
        if exe or args:
            found.append((int(name), exe or args[0], args))
    return found


def _macos_processes() -> list[Process]:
    done = subprocess.run(  # noqa: S603 -- the system's own ps
        ["/bin/ps", "-axo", "pid=,comm="], check=False, capture_output=True, text=True, timeout=10
    )
    found: list[Process] = []
    for line in done.stdout.splitlines():
        pid, _, exe = line.strip().partition(" ")
        if pid.isdigit() and exe:
            found.append((int(pid), exe.strip(), []))
    return found


def _windows_processes() -> list[Process]:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    psapi = ctypes.WinDLL("psapi", use_last_error=True)  # type: ignore[attr-defined]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    pids = (wintypes.DWORD * 4096)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids), ctypes.byref(needed)):
        return []
    found: list[Process] = []
    me = os.getpid()
    for pid in list(pids)[: needed.value // ctypes.sizeof(wintypes.DWORD)]:
        if not pid or pid == me:
            continue
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                found.append((int(pid), buffer.value, []))
        finally:
            kernel.CloseHandle(handle)
    return found


def processes() -> list[Process]:
    """Every process this user can see as ``(pid, executable, arguments)``."""
    try:
        if sys.platform == "win32":
            return _windows_processes()
        if sys.platform == "darwin":
            return _macos_processes()
        return _linux_processes()
    except (OSError, subprocess.SubprocessError) as ex:
        logger.warning("Could not list the running programs: %s", ex)
        return []


# ---- which game each reading is ------------------------------------------------------------------------------------


def _folders(entry: dict) -> list[str]:
    found = []
    for group in entry.get("versions") or []:
        for variant in group.get("variants") or []:
            for key in ("path", "directory"):
                value = variant.get(key)
                if value and len(Path(value).parts) >= MIN_FOLDER_DEPTH:
                    found.append(str(value))
    return found


def _steam_ids(entries: list[dict]) -> list[str]:
    return [
        str(p["id"])
        for e in entries
        for p in e.get("platform_ids") or []
        if p.get("platform") == "steam" and p.get("id")
    ]


def _inside(text: str, folder: str) -> bool:
    folder = folder.rstrip("/\\")
    if os.name == "nt":
        text, folder = text.lower(), folder.lower()
    return text.startswith((folder + os.sep, folder + "/"))


def _is_game_process(exe: str, args: list[str], folders: list[str]) -> bool:
    """Whether this process is a game of one of ``folders``: its executable is inside one, or it is Wine (which
    runs the game's file, named in its arguments) and an argument is inside one."""
    if any(_inside(exe, f) for f in folders):
        return True
    if Path(exe).name.lower().startswith("wine"):
        return any(_inside(a, f) for a in args for f in folders)
    return False


def detect(
    games: list[dict],
    system_uuid: str,
    steam_apps: set[str],
    process_list: list[Process],
    skip: Callable[[str], bool] = lambda game_id: False,
) -> dict[str, dict[str, Any]]:
    """The games running that Pythia did not start, by game id: ``via`` ("steam" or "process"), ``pid`` (the first
    process of a game found by its folder, else None), ``pids`` (all of them) and ``quittable`` (only a game that
    is not Steam's). ``games`` are Backend._games records; ``skip(game_id)`` is True for a game Pythia started."""
    found: dict[str, dict[str, Any]] = {}
    for game in games:
        game_id = game["game_id"]
        if skip(game_id):
            continue
        entries = game.get("entries") or []
        steam_ids = _steam_ids(entries)
        if steam_ids:
            # A Steam game is reported by Steam's state, and ended by Steam: never by what is in its folder.
            if any(i in steam_apps for i in steam_ids):
                found[game_id] = {"via": "steam", "pid": None, "pids": [], "quittable": False}
            continue
        folders = [f for e in entries if e.get("system_uuid") == system_uuid for f in _folders(e)]
        if not folders:
            continue
        pids = [pid for pid, exe, args in process_list if _is_game_process(exe, args, folders)]
        if pids:
            found[game_id] = {"via": "process", "pid": pids[0], "pids": pids, "quittable": True}
    return found


class Observer:
    """The thread that reads Steam's state and the process list every few seconds and tells the tracker (see
    pythia.running.Tracker.set_external) what began and ended. ``games`` returns the library's records."""

    def __init__(
        self,
        games: Callable[[], list[dict]],
        system_uuid: str,
        tracker: Any,
        steam: Callable[[], set[str]] = steam_running_apps,
        process_list: Callable[[], list[Process]] = processes,
        interval: float = POLL_SECONDS,
    ) -> None:
        self._games, self._uuid, self._tracker = games, system_uuid, tracker
        self._steam, self._processes, self._interval = steam, process_list, interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll(self) -> None:
        try:
            found = detect(self._games(), self._uuid, self._steam(), self._processes(), skip=self._tracker.is_tracked)
        except Exception as ex:  # noqa: BLE001 -- a reading that fails is no games running, not a dead thread
            logger.warning("Could not read which games are running: %s", ex)
            return
        self._tracker.set_external(found)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="pythia-running-observer", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()


_current: Observer | None = None


def watch(games: Callable[[], list[dict]], system_uuid: str, tracker: Any, **kwargs: Any) -> Observer:
    """Starts the one Observer of this process, ending the one before it (a new Backend replaces the old)."""
    global _current  # noqa: PLW0603 -- one observer per process
    if _current is not None:
        _current.stop()
    _current = Observer(games, system_uuid, tracker, **kwargs)
    _current.start()
    return _current


def stop() -> None:
    """Ends the process's Observer, if any."""
    global _current  # noqa: PLW0603
    if _current is not None:
        _current.stop()
        _current = None
