"""The running Pythia, and how anything else reaches it (docs/SPEC.md, decision 12).

Pythia is one running app per person: its window, its tray icon, and the droidtop agent it serves while it
runs. A second `pythia` is never a second copy. A bare `pythia` asks the running one to show its window;
`--droidtop-serve`, the pairing commands and `--relaunch` are requests to it; and when none runs, the command
starts the app first (with its tray icon) and then asks. Owner, 2026-11-20: "let's force the tray icon and
stuff. They're calls to a running process, doing something headlessly just doesn't surface the window".

The channel is a local one, per person: a named pipe on Windows, a Unix socket elsewhere, both from
`multiprocessing.connection`, with a key in the person's own data folder so another account on the same
computer cannot drive their Pythia. A request is one message, ``{"op": ..., "args": {...}}``; the answer is
any number of ``{"line": ...}`` messages, then ``{"done": <exit code>}``.
"""

import getpass
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from multiprocessing.connection import Client, Listener
from pathlib import Path

from pythia.identity import app_data_dir
from pythia.util.log import logger

# A handler gets the request's args and a way to say a line; it returns the exit code.
Handler = Callable[[dict, Callable[[str], None]], int]

# How long a command waits for the app it started to answer.
START_TIMEOUT = 60.0


def _user() -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser() or "user")


def address() -> str:
    """Where the running Pythia listens for this person."""
    if os.name == "nt":
        return r"\\.\pipe\pythia-" + _user()
    return str(app_data_dir() / "instance.sock")


def key_path() -> Path:
    return app_data_dir() / "instance.key"


def _key(create: bool) -> bytes | None:
    """The channel's key, readable by this person alone; made by the running app when there is none."""
    path = key_path()
    try:
        return bytes.fromhex(path.read_text("ascii").strip())
    except (OSError, ValueError):
        if not create:
            return None
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(key.hex())
    return key


class Instance:
    """The running app's side: listens on the channel and answers requests on threads of their own."""

    def __init__(self, handlers: dict[str, Handler], where: str | None = None, key: bytes | None = None):
        self._handlers = handlers
        self._where = where or address()
        self._key = key or _key(create=True)
        self._listener: Listener | None = None

    def start(self) -> bool:
        """Starts listening; False when another Pythia already does (this one should not run)."""
        if request("ping", where=self._where, key=self._key) is not None:
            return False
        if os.name != "nt":
            # A socket file left by a Pythia that did not end cleanly; nothing answers it (asked just above).
            Path(self._where).unlink(missing_ok=True)
        self._listener = Listener(self._where, authkey=self._key)
        threading.Thread(target=self._accept, name="pythia-instance", daemon=True).start()
        return True

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.close()
            self._listener = None

    def _accept(self) -> None:
        while self._listener is not None:
            try:
                conn = self._listener.accept()
            except (OSError, EOFError):
                if self._listener is None:
                    return
                continue
            except Exception as ex:  # noqa: BLE001 -- a caller with the wrong key; keep listening
                logger.warning("Refused a request to the running Pythia: %s", ex)
                continue
            threading.Thread(target=self._answer, args=(conn,), daemon=True).start()

    def _answer(self, conn) -> None:
        with conn:
            try:
                message = conn.recv()
                op, args = message.get("op"), message.get("args") or {}
            except (OSError, EOFError, AttributeError):
                return
            if op == "ping":
                conn.send({"done": 0})
                return
            handler = self._handlers.get(op)
            if handler is None:
                conn.send({"line": f"This Pythia does not know '{op}'."})
                conn.send({"done": 2})
                return

            def say(line: str) -> None:
                conn.send({"line": line})

            try:
                code = handler(args, say)
            except (OSError, EOFError):
                return  # the command stopped listening (Ctrl+C); the handler's own cleanup ran
            except Exception as ex:  # noqa: BLE001 -- the command is told, the app stays up
                logger.exception("Request %s failed", op)
                conn.send({"line": f"Pythia could not do that: {ex}"})
                code = 1
            conn.send({"done": code})


def request(
    op: str,
    args: dict | None = None,
    on_line: Callable[[str], None] | None = None,
    where: str | None = None,
    key: bytes | None = None,
) -> int | None:
    """Asks the running Pythia to do ``op``; its lines go to ``on_line``. Returns its exit code, or None when no
    Pythia runs for this person."""
    key = key or _key(create=False)
    if key is None:
        return None
    try:
        conn = Client(where or address(), authkey=key)
    except (OSError, EOFError, ValueError):
        return None
    except Exception:  # noqa: BLE001 -- an old key or a stale socket: nothing that answers
        return None
    with conn:
        conn.send({"op": op, "args": args or {}})
        while True:
            try:
                message = conn.recv()
            except (OSError, EOFError):
                return 1
            if "line" in message and on_line is not None:
                on_line(message["line"])
            if "done" in message:
                return int(message["done"])


def start_app(tray_only: bool = False) -> bool:
    """Starts Pythia's window (and tray icon) on its own, then waits for it to answer; False when it did not."""
    from pythia.util import selfcmd  # noqa: PLC0415

    command = selfcmd.app_command(*(["--tray"] if tray_only else []))
    flags: dict[str, int | bool] = {}
    if os.name == "nt":
        # As pythia.folder_launch does: the names exist only on Windows, which mypy on Linux cannot see.
        flags["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200
        )
    else:
        flags["start_new_session"] = True
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)  # noqa: S603 (Pythia's own program)
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if request("ping") is not None:
            return True
        time.sleep(0.5)
    return False


def ask(op: str, args: dict | None = None, tray_only: bool = False) -> int:
    """A command's way in: asks the running Pythia, starting it first when none runs, and prints its lines."""
    code = request(op, args, on_line=print)
    if code is not None:
        return code
    print("Starting Pythia...", file=sys.stderr)
    if not start_app(tray_only=tray_only):
        print("Pythia did not start. Run `pythia` to see why.", file=sys.stderr)
        return 1
    code = request(op, args, on_line=print)
    return 1 if code is None else code
