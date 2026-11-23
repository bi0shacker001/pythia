"""Where the backend channel is and how to open it: a Unix socket on Linux and macOS, a named pipe on Windows.

Windows uses a named pipe because CPython's `socket` has no AF_UNIX there (`hasattr(socket, "AF_UNIX")` is False
on Windows builds, whatever the OS supports), and a pipe is what `pythia.instance` already uses on Windows.
Neither is reachable from another computer, and there is no TCP anywhere.

Who may connect: the Unix socket is a file in a folder only this person can enter (0700, the socket 0600). A
Windows pipe is made as `multiprocessing.connection` makes one (first-instance flag) and refuses remote clients;
under its default security descriptor other accounts get at most read access, which cannot open a duplex pipe.
On both, the first message must carry the install's key (pythia.instance.channel_key), which only this person's
data folder holds.

A `Stream` is the same on both: `recv()` gives the next bytes (b"" when the other side has gone), `send()` all
of its bytes, `close()` ends it. A pipe delivers whole writes as messages, which the frame decoder does not mind.
"""

import contextlib
import getpass
import os
import re
import socket
import stat
from pathlib import Path
from typing import Protocol

from pythia.identity import app_data_dir

IS_WINDOWS = os.name == "nt"
# A Unix socket's path must fit sockaddr_un (104 bytes on macOS, 108 on Linux).
_MAX_UNIX_PATH = 100


class TransportError(OSError):
    """The channel cannot be opened (a path too long, a folder others can enter)."""


class AddressInUse(TransportError):
    """Something already answers at the address: another Pythia serves this person."""


class Stream(Protocol):
    def recv(self) -> bytes: ...

    def send(self, data: bytes) -> None: ...

    def close(self) -> None: ...


class Listener(Protocol):
    def accept(self) -> Stream: ...

    def close(self) -> None: ...


def _user() -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser() or "user")


def runtime_dir() -> Path:
    """The folder of the socket: XDG_RUNTIME_DIR on Linux (tmpfs, 0700, gone at sign-out), else under Pythia's
    own data folder; on Windows under %LOCALAPPDATA% (not the roaming profile)."""
    override = os.environ.get("PYTHIA_RUNTIME_DIR")
    if override:
        return Path(override)
    if os.environ.get("PYTHIA_DATA_DIR"):
        return app_data_dir() / "run"
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Pythia" / "run"
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "pythia"
    return app_data_dir() / "run"


def address() -> str:
    """The channel: a pipe name on Windows, a socket path elsewhere. One per person."""
    if IS_WINDOWS:
        return r"\\.\pipe\pythia-backend-" + _user()
    return str(runtime_dir() / "backend.sock")


def _private_dir(path: Path) -> None:
    """The socket's folder, entered by this person alone; refuses one somebody else could enter."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid():
        raise TransportError(f"{path} belongs to another account.")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        path.chmod(0o700)


class _SocketStream:
    def __init__(self, sock: socket.socket):
        self._sock = sock

    def recv(self) -> bytes:
        try:
            return self._sock.recv(65536)
        except OSError:
            return b""

    def send(self, data: bytes) -> None:
        self._sock.sendall(data)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self._sock.close()


class _SocketListener:
    def __init__(self, sock: socket.socket, path: str):
        self._sock = sock
        self._path = path

    def accept(self) -> Stream:
        conn, _ = self._sock.accept()
        return _SocketStream(conn)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.close()
        with contextlib.suppress(OSError):
            os.unlink(self._path)


class _PipeStream:
    """A pipe connection of `multiprocessing.connection`, read as the stream it carries."""

    def __init__(self, conn):
        self._conn = conn

    def recv(self) -> bytes:
        try:
            return self._conn.recv_bytes()
        except (EOFError, OSError):
            return b""

    def send(self, data: bytes) -> None:
        self._conn.send_bytes(data)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._conn.close()


class _PipeListener:
    def __init__(self, listener):
        self._listener = listener

    def accept(self) -> Stream:
        return _PipeStream(self._listener.accept())

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._listener.close()


def _local_pipe_listener(where: str):
    """multiprocessing's pipe listener (message pipe, first-instance flag), made to refuse remote clients too."""
    import _winapi  # noqa: PLC0415
    from multiprocessing import connection  # noqa: PLC0415

    class LocalOnly(connection.PipeListener):  # type: ignore[name-defined]
        def _new_handle(self, first=False):
            flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
            if first:
                flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
            reject_remote = 0x8  # PIPE_REJECT_REMOTE_CLIENTS, not in _winapi
            return _winapi.CreateNamedPipe(
                self._address,
                flags,
                _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT | reject_remote,
                _winapi.PIPE_UNLIMITED_INSTANCES,
                connection.BUFSIZE,
                connection.BUFSIZE,
                _winapi.NMPWAIT_WAIT_FOREVER,
                _winapi.NULL,
            )

    return LocalOnly(where)


def _answers(where: str) -> bool:
    try:
        connect(where, timeout=1.0).close()
    except OSError:
        return False
    return True


def listen(where: str | None = None) -> Listener:
    """Opens the channel. AddressInUse when a running Pythia already answers there; a socket file left by one
    that did not end cleanly is replaced."""
    where = where or address()
    if IS_WINDOWS:
        try:
            return _PipeListener(_local_pipe_listener(where))
        except PermissionError as ex:  # the first-instance flag: the pipe exists
            raise AddressInUse(f"{where} is already served.") from ex
    path = Path(where)
    if len(os.fsencode(where)) > _MAX_UNIX_PATH:
        raise TransportError(f"{where} is too long for a Unix socket; set PYTHIA_RUNTIME_DIR to a shorter folder.")
    _private_dir(path.parent)
    if path.exists():
        if _answers(where):
            raise AddressInUse(f"{where} is already served.")
        path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous = os.umask(0o177)  # the socket is made 0600, not 0666 minus the umask
    try:
        sock.bind(where)
    finally:
        os.umask(previous)
    os.chmod(where, 0o600)
    sock.listen(8)
    return _SocketListener(sock, where)


def connect(where: str | None = None, timeout: float = 5.0) -> Stream:
    where = where or address()
    if IS_WINDOWS:
        from multiprocessing.connection import Client as PipeClient  # noqa: PLC0415

        return _PipeStream(PipeClient(where, family="AF_PIPE"))
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(where)
    except OSError:
        sock.close()
        raise
    sock.settimeout(None)
    return _SocketStream(sock)
