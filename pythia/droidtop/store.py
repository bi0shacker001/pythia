"""This computer's droidtop agent state (``pythia._native.store``): its
identity, the paired handhelds and the library every paired device shares.
Each call is quick; refusals raise AgentError."""

from typing import Any

from pythia.droidtop.native import submodule


def identity() -> dict:
    """This computer as paired handhelds know it: ``name``, ``id``."""
    return submodule("store").identity()


def settings() -> dict:
    """The agent's settings, as droidtop-agent keeps them (``approved``
    plugin adapters, ``syncthing``, ``launch_devices``...)."""
    return submodule("store").settings()


def library_folders() -> dict[str, list[str]]:
    """The person's library folders: ``games`` and ``roms``. One list for Pythia's library and for the droidtop agent's
    scanner (its ``game_folders`` and ``rom_folders``), so the games a handheld sees are the games Pythia keeps."""
    current = settings()
    return {"games": list(current.get("game_folders") or []), "roms": list(current.get("rom_folders") or [])}


def set_library_folders(games: list[str] | None = None, roms: list[str] | None = None) -> None:
    """Replaces the game folders, the ROM folders or both (None leaves one as it is)."""
    changes: dict[str, Any] = {}
    if games is not None:
        changes["game_folders"] = games
    if roms is not None:
        changes["rom_folders"] = roms
    if changes:
        submodule("store").set_settings(**changes)


def scan_minutes() -> int:
    """How often the library folders are scanned, in minutes: the agent's own ``scan_minutes`` (30 when unset)."""
    try:
        return max(1, int(settings().get("scan_minutes") or 30))
    except (TypeError, ValueError):
        return 30


def devices() -> list[dict]:
    """The paired handhelds: ``id``, ``name``, ``last_seen_ms``,
    ``last_address``, ``trusted``, ``launch`` (it may start games here) and ``offered`` (what this computer
    offers it: ``library``, ``saves``)."""
    return submodule("store").devices()


def unpair(device_id: str) -> None:
    """Forgets a handheld (the start of its id is enough)."""
    submodule("store").unpair(device_id)


def set_launch(device_id: str, allowed: bool) -> None:
    """Whether a handheld may start games here (to stream them with Steam Link)."""
    submodule("store").set_launch(device_id, allowed)


def set_offer(device_id: str, library: bool | None = None, saves: bool | None = None) -> None:
    """What this computer offers a paired device: its library, its saves, or
    both (None leaves one as it is). Saves need the library, so offering
    saves offers it too and withdrawing it withdraws them."""
    submodule("store").set_offer(device_id, library=library, saves=saves)


def library() -> dict[str, Any]:
    """The library every paired device shares, by game key."""
    return submodule("store").library()


def games_by_device(library: dict[str, Any], devices: list[dict]) -> list[dict]:
    """Each paired handheld (``devices``, as devices() gives them) with the
    games the shared ``library`` says are installed on it: ``key``,
    ``title``, ``platform`` and ``version``, by title. A handheld that has not
    synced yet has none."""
    out = []
    for device in devices:
        if device.get("kind") == "computer":  # a paired computer is no handheld
            continue
        games = []
        for key, record in library.items():
            install = ((record.get("installs") or {}).get(device["id"]) or {}).get("install") or {}
            if not install.get("installed"):
                continue
            games.append(
                {
                    "key": key,
                    "title": record.get("title") or key,
                    "platform": record.get("platform"),
                    "version": install.get("version"),
                }
            )
        games.sort(key=lambda g: g["title"].lower())
        out.append({"id": device["id"], "name": device.get("name") or device["id"][:16], "games": games})
    return out
