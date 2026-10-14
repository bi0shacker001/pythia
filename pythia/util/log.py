"""Utility module for creating an application wide logger.

2026-08-07 real fix ("unfuck the console output, and make debug logging
possible"): the shared `logger` object here used to be
`logging.getLogger(__name__)` -- i.e. `logging.getLogger("pythia.util.log")`,
a LEAF logger, not a namespace root -- with `propagate = False` set on it.
~130 files across the codebase do `from pythia.util.log import logger` and
call methods directly on that one shared object, so in practice almost all
of the app's logging already funneled through it -- but propagate=False
meant none of those records ever reached the real root logger, which is
exactly where `pythia.ui.backend.Backend._UiLogHandler` attaches to feed the
web UI's Event log. Two disconnected logging paths, one of which silently
dead-ended. Renaming to `logging.getLogger("pythia")` fixes propagation for
every existing call site at once (they all just keep importing the same
`logger` symbol) without touching any of them."""

import logging
import logging.handlers
import os
from pathlib import Path
import sys
import tempfile

from pythia.identity import app_data_dir

# Used to store log buffers for games (formerly typed as Gtk.TextBuffer under
# GTK; now just whatever the actual UI's log-view widget stores -- see the Qt
# UI's own log window once it exists).
LOG_BUFFERS: dict = {}

# 2026-08-07: moved under a real logs/ subfolder of app_data_dir() ("we'll
# write a log to the appdata folder's logs folder") -- also fixes a real,
# harmless-on-Windows-but-still-wrong inconsistency, where this used to
# maintain its own separate cache-dir resolution (a lowercase "pythia" next
# to identity.app_data_dir()'s "Pythia") instead of just using
# identity.app_data_dir() directly like everything else in the app already
# does.
LOG_DIR = app_data_dir() / "logs"

# Formatters
FILE_FORMATTER = logging.Formatter("[%(levelname)s:%(asctime)s:%(module)s]: %(message)s")
SIMPLE_FORMATTER = logging.Formatter("%(asctime)s: %(message)s")
DEBUG_FORMATTER = logging.Formatter("%(levelname)-8s %(asctime)s [%(module)s.%(funcName)s:%(lineno)s]:%(message)s")

# Log file setup. 2026-08-07 real fix (external review against a clean
# install found this): RotatingFileHandler opens its file immediately at
# construction, at MODULE IMPORT TIME -- before _install_excepthook(), the
# Qt event loop, or anything else exists to catch a failure here. On
# Windows this can raise PermissionError if another Pythia process (a
# second, legitimately-side-by-side instance -- see pythia.ui.singleton's
# own docstring: "launching a second copy side-by-side still works") has
# the same file in a state that blocks a fresh open. A logging setup
# failure must never abort startup -- fall back to a PID-suffixed filename
# so a second/concurrent instance always gets a writable log of its own.
def _create_file_handler(preferred_dir: Path):
    """Create a file handler without ever making logging fatal to startup."""
    candidates = (
        (preferred_dir, "pythia.log"),
        (preferred_dir, f"pythia-{os.getpid()}.log"),
        (Path(tempfile.gettempdir()) / "Pythia-logs", f"pythia-{os.getpid()}.log"),
    )
    for directory, filename in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / filename
            return logging.handlers.RotatingFileHandler(str(path), backupCount=5), str(path)
        except OSError:
            continue
    # Console/UI logging remains available even if every filesystem location
    # is read-only. NullHandler retains the module's stable handler contract.
    return logging.NullHandler(), None


file_handler, LOG_FILENAME = _create_file_handler(LOG_DIR)
file_handler.setFormatter(FILE_FORMATTER)

console_handler = logging.StreamHandler(stream=sys.stderr)
console_handler.setFormatter(SIMPLE_FORMATTER)

# Real, shared namespace root -- see the module docstring above for why this
# specific name (not __name__) matters. propagate stays True (the default) so
# records also reach the real root logger, where Backend._UiLogHandler
# attaches to feed the web UI's Event log; nothing else attaches a handler to
# root itself, so there's no risk of double-printing to the console.
logger = logging.getLogger("pythia")
logger.addHandler(file_handler)
logger.addHandler(console_handler)
logger.setLevel(logging.INFO)

_debug_enabled = False


def set_debug_logging(enabled: bool) -> None:
    """Real debug-level toggle (2026-08-07: previously nothing could ever
    lower the hardcoded INFO level, and DEBUG_FORMATTER was defined but never
    used). Swaps both handlers to the more detailed formatter and drops the
    level to DEBUG; called from a --debug/-v CLI flag, or automatically at
    import time below if PYTHIA_DEBUG is set -- the only way to get debug
    output out of a packaged, console-less Windows build without a rebuild."""
    global _debug_enabled
    _debug_enabled = enabled
    level = logging.DEBUG if enabled else logging.INFO
    formatter = DEBUG_FORMATTER if enabled else SIMPLE_FORMATTER
    logger.setLevel(level)
    file_handler.setFormatter(DEBUG_FORMATTER if enabled else FILE_FORMATTER)
    console_handler.setFormatter(formatter)


def is_debug_logging() -> bool:
    return _debug_enabled


def shutdown_logging() -> None:
    """Flush and close the persistent handler during application teardown.

    Idempotent so both UI entry points and tests can use it safely. Removing the
    handler first prevents later teardown logging from reopening a Windows-locked
    file whose temporary application directory is being deleted.
    """
    if file_handler in logger.handlers:
        logger.removeHandler(file_handler)
    try:
        file_handler.flush()
    finally:
        file_handler.close()


if os.environ.get("PYTHIA_DEBUG"):
    set_debug_logging(True)


def get_log_contents() -> str:
    """Returns the entire text of the log file for this run."""
    if not LOG_FILENAME or not os.path.exists(LOG_FILENAME):
        return ""
    try:
        with open(LOG_FILENAME, encoding="utf-8") as log_file:
            return log_file.read()
    except OSError:
        return ""


def get_log_tail(max_bytes: int = 262_144) -> tuple[str, bool]:
    """Read at most ``max_bytes`` from the end of the persisted log."""
    if not LOG_FILENAME or max_bytes <= 0 or not os.path.exists(LOG_FILENAME):
        return "", False
    try:
        with open(LOG_FILENAME, "rb") as log_file:
            size = log_file.seek(0, os.SEEK_END)
            truncated = size > max_bytes
            start = max(0, size - max_bytes)
            if truncated:
                # Read the preceding byte to tell a true line boundary from
                # a seek into the middle of a potentially sensitive line.
                log_file.seek(start - 1)
                data = log_file.read(max_bytes + 1)
                if data[:1] == b"\n":
                    data = data[1:]
                else:
                    newline = data.find(b"\n")
                    data = data[newline + 1 :] if newline >= 0 else b""
            else:
                log_file.seek(0)
                data = log_file.read(max_bytes)
        return data.decode("utf-8", errors="replace"), truncated
    except OSError:
        return "", False
