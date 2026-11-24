"""Pythia's platform names as droidtop's systems.

A game's variants carry a platform name the way Lutris and Pythia's runners spell it ("Sony PlayStation 2",
"Nintendo Entertainment System", "Windows"). droidtop groups its library by the systems of droidtop-platforms
(``platforms-database.json``: an id such as ``ps2`` and a name each), and a front end that shows both libraries
shelves them the same way. This maps one to the other from that same data, pinned as the submodule
``vendor/droidtop-platforms`` at one commit (``git submodule status``), never a table copied here.

A name is matched, ignoring case and punctuation, to a system's name or its id. The three operating systems
Pythia lists a PC game under have no system of their own in that data, only ``pc``, and are mapped to it. A name
that matches nothing is not guessed at: it keeps its own slug as the id and says ``known: false``.
"""

import json
import re
import threading
from pathlib import Path
from typing import Any

from pythia.util.log import logger
from pythia.util.strings import slugify

DATABASE_NAME = "platforms-database.json"
# Where the pinned data is in a source tree. A build that does not carry the submodule maps nothing (see known).
PINNED = Path(__file__).resolve().parents[1] / "vendor" / "droidtop-platforms" / DATABASE_NAME

# Names Pythia's own PC games are listed under, which droidtop-platforms has as one system, "pc".
PC_NAMES = ("windows", "linux", "macos", "mac os x", "pc windows", "pc linux")
PC_SYSTEM = "pc"

_lock = threading.Lock()
_loaded: dict[Path, "Systems"] = {}


def _key(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", name.casefold())


class Systems:
    """The systems of one platforms-database.json, looked up by name or id."""

    def __init__(self, platforms: list[dict[str, Any]]) -> None:
        self.by_id: dict[str, dict[str, Any]] = {}
        self._by_key: dict[str, dict[str, Any]] = {}
        for item in platforms:
            ident, name = item.get("id"), item.get("name")
            if not ident or not name:
                continue
            self.by_id[str(ident)] = {"id": str(ident), "name": str(name)}
        for system in self.by_id.values():
            # A name shared by two systems (Sega Mega Drive and its Japanese twin) belongs to the first.
            self._by_key.setdefault(_key(system["name"]), system)
        for system in self.by_id.values():
            self._by_key.setdefault(_key(system["id"]), system)

    def __len__(self) -> int:
        return len(self.by_id)

    def system_of(self, platform: str) -> dict[str, Any]:
        """``id``, ``name`` and ``known`` for one platform name."""
        key = _key(platform or "")
        found = self._by_key.get(key)
        if found is None and key in {_key(n) for n in PC_NAMES}:
            found = self.by_id.get(PC_SYSTEM)
        if found is not None:
            return {"id": found["id"], "name": found["name"], "known": True}
        return {"id": slugify(platform) if platform else "unknown", "name": platform or "Unknown platform", "known": False}


def load(path: Path | None = None) -> Systems:
    """The systems of ``path`` (the pinned data by default), read once. Missing or unreadable data is no systems."""
    path = Path(path) if path is not None else PINNED
    with _lock:
        if path in _loaded:
            return _loaded[path]
    try:
        data = json.loads(path.read_text("utf-8"))
        platforms = data.get("platforms") if isinstance(data, dict) else None
        systems = Systems(platforms if isinstance(platforms, list) else [])
    except FileNotFoundError:
        logger.info("No droidtop-platforms data at %s; systems keep their own names.", path)
        systems = Systems([])
    except (OSError, ValueError) as ex:
        logger.warning("Could not read droidtop-platforms data at %s: %s", path, ex)
        systems = Systems([])
    with _lock:
        _loaded[path] = systems
    return systems


def system_of(platform: str, path: Path | None = None) -> dict[str, Any]:
    return load(path).system_of(platform)
