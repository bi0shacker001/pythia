"""Starting a game added from a folder (Droidtop/tracker#629): the person
presses Play and it runs.

A folder game has no Lutris runner. Pythia picks the program to start from
what the folder holds:

- On Windows, the game's own ``.exe``, run natively.
- On Linux and macOS, a native build when the folder has one (``.sh``,
  ``.x86_64``, an executable file); else the ``.exe`` through Wine, when Wine
  is installed. Each game gets a Wine prefix of its own under Pythia's data
  folder, so one game's settings never reach another's.

The pick is a guess the person can change ("Choose how it runs..."). Their
choice is kept on this computer's record of the folder (``launch``: ``exe``,
relative to the folder, and ``via``, ``native`` or ``wine``), so it is only
this computer's: another computer's paths are its own.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pythia.util.log import logger

# Programs in a game folder that are not the game: installers, crash
# reporters, redistributables, helpers. Compared in lower case, by stem prefix.
NOT_THE_GAME = (
    "unins",
    "setup",
    "install",
    "vc_redist",
    "vcredist",
    "dxsetup",
    "dxwebsetup",
    "directx",
    "dotnet",
    "unitycrashhandler",
    "crashpad",
    "crashreport",
    "crashhandler",
    "notification_helper",
    "zsync",
    "python",
    "pythonw",
    "renpy",
    "lib",
    "uninstall",
    "updater",
    "launcherpatcher",
    "easyanticheat",
    "redist",
    "dxdiag",
    "7z",
    "winrar",
)
# How deep a folder is looked into for the game's program.
MAX_DEPTH = 2
LINUX_SUFFIXES = (".sh", ".x86_64", ".x86", ".appimage")


def on_windows() -> bool:
    return sys.platform.startswith("win")


def wine() -> str | None:
    """The Wine program on this computer, or None. Never on Windows."""
    if on_windows():
        return None
    return shutil.which("wine") or shutil.which("wine64")


def _squash(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum())


def _is_linux_program(path: Path) -> bool:
    if path.suffix.lower() in LINUX_SUFFIXES:
        return True
    if path.suffix or not path.is_file():
        return False
    try:
        with path.open("rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def candidates(folder: str, title: str = "") -> list[dict]:
    """The programs in ``folder`` that may be the game, likeliest first: each
    with ``exe`` (relative to the folder, with forward slashes), ``kind``
    (``windows`` or ``linux``) and ``size``."""
    root = Path(folder)
    if not root.is_dir():
        return []
    want = _squash(title)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        # An engine's own folders hold its tools, not the game.
        dirnames[:] = [d for d in dirnames if d.lower() not in ("lib", "renpy", "redist", "_commonredist", "directx")]
        for name in filenames:
            path = Path(dirpath) / name
            stem = path.stem.lower()
            if path.suffix.lower() == ".exe":
                kind = "windows"
            elif _is_linux_program(path):
                kind = "linux"
            else:
                continue
            if any(stem.startswith(word) for word in NOT_THE_GAME):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            rel = path.relative_to(root).as_posix()
            # Likelier: named for the game, at the top, then larger.
            named = bool(want) and (_squash(path.stem) in want or want in _squash(path.stem))
            found.append(((0 if named else 1), depth, -size, rel, {"exe": rel, "kind": kind, "size": size}))
    found.sort(key=lambda row: row[:4])
    return [row[4] for row in found]


def pick(folder: str, title: str = "") -> dict | None:
    """What Play starts when the person chose nothing: ``exe`` and ``via``, or
    None when nothing in the folder can start here."""
    found = candidates(folder, title)
    if on_windows():
        windows = [c for c in found if c["kind"] == "windows"]
        return {"exe": windows[0]["exe"], "via": "native"} if windows else None
    linux = [c for c in found if c["kind"] == "linux"]
    if linux:
        return {"exe": linux[0]["exe"], "via": "native"}
    windows = [c for c in found if c["kind"] == "windows"]
    if windows and wine():
        return {"exe": windows[0]["exe"], "via": "wine"}
    return None


def why_not(folder: str, title: str = "") -> str:
    """Why a folder game cannot start here, in the person's words."""
    found = candidates(folder, title)
    if not found:
        return "Pythia found no program to start in its folder. Choose how it runs."
    if not on_windows() and all(c["kind"] == "windows" for c in found):
        return "It is a Windows game and Wine is not installed: install your system's wine package, then Play."
    return "Nothing in its folder starts on this system. Choose how it runs."


def prefix_for(game_id: str) -> Path:
    """The Wine prefix of one game, under Pythia's data folder."""
    from pythia.identity import app_data_dir  # noqa: PLC0415

    return Path(app_data_dir()) / "prefixes" / game_id


def command(folder: str, launch: dict, game_id: str) -> tuple[list[str], dict]:
    """The command line and environment that start ``launch`` in ``folder``."""
    exe = Path(folder) / launch["exe"]
    env = dict(os.environ)
    if launch.get("via") == "wine":
        program = wine()
        if not program:
            raise RuntimeError("Wine is not installed: install your system's wine package.")
        prefix = prefix_for(game_id)
        prefix.mkdir(parents=True, exist_ok=True)
        env["WINEPREFIX"] = str(prefix)
        return [program, str(exe)], env
    return [str(exe)], env


def start(folder: str, launch: dict, game_id: str) -> None:
    """Starts the game in its own folder, in a process of its own that outlives
    Pythia. Raises OSError or RuntimeError with the reason when it cannot."""
    exe = Path(folder) / launch["exe"]
    if not exe.is_file():
        raise RuntimeError(f"{launch['exe']} is not in the game's folder any more. Choose how it runs.")
    args, env = command(folder, launch, game_id)
    if launch.get("via") == "native" and not on_windows() and not os.access(exe, os.X_OK):
        exe.chmod(exe.stat().st_mode | 0o111)
    kwargs: dict = {"cwd": str(exe.parent), "env": env, "stdin": subprocess.DEVNULL}
    if on_windows():
        # The game outlives Pythia and has no console of Pythia's (Windows' own values, so this reads anywhere).
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)  # noqa: S603 (the person's own game, chosen in its folder)
    logger.info("Started %s (%s)", exe, launch.get("via"))


def now() -> int:
    return int(time.time())
