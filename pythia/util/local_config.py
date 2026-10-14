"""Real config file controlling UI selection + debug logging (2026-08-07:
"We're gonna use a config file on windows. It'll be next to the portable
executable in the portable bundle, or next to local.db in the installed
version. It controls debug, ui selection, and etc. We won't give a console,
we'll write a log to the appdata folder's logs folder.")

INI format (stdlib configparser, human-editable): a [pythia] section with
`debug` (bool), `ui` (desktop/web/bigpicture, matching pythia.ui.mode's own
hint values), and `channel` (latest/nightly/unstable/stable -- which release
channel pythia.util.update_check checks against, 2026-08-07).

Two real locations are checked, in order -- the code never needs to know
which distribution channel produced the file, same "doesn't know or care
where it came from" shape as PluginMeta.data_dir() (pythia/plugins/__init__.py):
  1. Next to the running executable (the portable bundle's own copy,
     packaging/windows/pythia.cfg, bundled as a data file by pythia.spec).
  2. Next to local.db, i.e. app_data_dir() itself (an installed build's
     per-user copy -- created with real defaults on first run if missing).

Not Windows-specific in implementation (app_data_dir() already resolves
correctly per-platform), even though the request that created this was
Windows-focused -- there's no reason a Linux install couldn't use the same
mechanism next to its own local.db.
"""

import configparser
import os
import sys
import tempfile
from pathlib import Path

from pythia.identity import app_data_dir

CONFIG_FILENAME = "pythia.cfg"

CHANNELS = ("latest", "nightly", "unstable", "stable")
UI_MODES = ("desktop", "bigpicture", "web", "native")

_DEFAULTS = {"debug": "false", "ui": "", "channel": "stable"}

_DEFAULT_CONTENTS = (
    "; Pythia local configuration.\n"
    "; ui: desktop, web, or bigpicture (blank = default UI selection logic)\n"
    "; debug: true/false -- verbose logging, same as --debug/PYTHIA_DEBUG\n"
    "; channel: latest, nightly, unstable, or stable -- which release channel\n"
    ";          `check for updates` compares against\n"
    "[pythia]\n"
    "debug = false\n"
    "ui = \n"
    "channel = stable\n"
)


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent  # source checkout repo root


def _candidate_paths() -> list[Path]:
    return [_exe_dir() / CONFIG_FILENAME, app_data_dir() / CONFIG_FILENAME]


def _parse(path: Path) -> dict:
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    section = parser["pythia"] if parser.has_section("pythia") else {}
    debug = str(section.get("debug", _DEFAULTS["debug"])).strip().lower() in ("1", "true", "yes", "on")
    ui = (section.get("ui", _DEFAULTS["ui"]) or "").strip() or None
    channel = (section.get("channel", _DEFAULTS["channel"]) or "").strip().lower() or "stable"
    if channel not in CHANNELS:
        channel = "stable"
    return {"debug": debug, "ui": ui, "channel": channel}


def load_local_config(path: Path | None = None) -> dict:
    """{"debug": bool, "ui": str | None, "channel": str}. Reads the first
    existing config file among the real candidate locations; if neither
    exists, creates a real default one next to local.db (app_data_dir()) so
    it's discoverable and editable, then returns the defaults."""
    if path is not None:
        return _parse(Path(path)) if Path(path).is_file() else {"debug": False, "ui": None, "channel": "stable"}
    for candidate in _candidate_paths():
        if candidate.is_file():
            return _parse(candidate)

    default_path = app_data_dir() / CONFIG_FILENAME
    try:
        default_path.parent.mkdir(parents=True, exist_ok=True)
        default_path.write_text(_DEFAULT_CONTENTS, encoding="utf-8")
    except OSError:
        pass  # not fatal -- just means no default file got written this time
    return {"debug": False, "ui": None, "channel": "stable"}


def save_local_config(changes: dict, *, path: Path | None = None) -> dict:
    """Validate, merge, and atomically persist local startup configuration.

    Changes take effect on the next launch; this function does not silently mutate
    the already-running logger or UI process.
    """
    if not isinstance(changes, dict):
        raise ValueError("local configuration changes must be an object")
    unknown = set(changes) - {"debug", "ui", "channel"}
    if unknown:
        raise ValueError(f"unknown local configuration field(s): {', '.join(sorted(unknown))}")
    if "debug" in changes and not isinstance(changes["debug"], bool):
        raise ValueError("debug must be true or false")
    if "ui" in changes and changes["ui"] not in (*UI_MODES, None, ""):
        raise ValueError(f"ui must be blank or one of: {', '.join(UI_MODES)}")
    if "channel" in changes and changes["channel"] not in CHANNELS:
        raise ValueError(f"channel must be one of: {', '.join(CHANNELS)}")

    if path is None:
        path = next((candidate for candidate in _candidate_paths() if candidate.is_file()), app_data_dir() / CONFIG_FILENAME)
    path = Path(path)
    current = load_local_config(path)
    current.update(changes)
    current["ui"] = current["ui"] or None

    parser = configparser.ConfigParser()
    if path.is_file():
        parser.read(path, encoding="utf-8")
    if not parser.has_section("pythia"):
        parser.add_section("pythia")
    parser.set("pythia", "debug", "true" if current["debug"] else "false")
    parser.set("pythia", "ui", current["ui"] or "")
    parser.set("pythia", "channel", current["channel"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pythia-config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            parser.write(output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return {"status": "ok", "path": str(path), "config": current, "restart_required": True}
