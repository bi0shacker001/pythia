"""This computer's droidtop agent state (``pythia._native.store``): its
identity, the paired handhelds and the library every paired device shares.
Each call is quick; refusals raise AgentError."""

from typing import Any

from pythia.droidtop.native import submodule


def identity() -> dict:
    """This computer as paired handhelds know it: ``name``, ``id``."""
    return submodule("store").identity()


def devices() -> list[dict]:
    """The paired handhelds: ``id``, ``name``, ``last_seen_ms``,
    ``last_address``, ``trusted``, ``primary`` (its saves win here) and
    ``launch`` (it may start games here)."""
    return submodule("store").devices()


def unpair(device_id: str) -> None:
    """Forgets a handheld (the start of its id is enough)."""
    submodule("store").unpair(device_id)


def set_primary(device_id: str | None) -> None:
    """The handheld whose saves win when both sides changed; None: the newest."""
    submodule("store").set_primary(device_id)


def set_launch(device_id: str, allowed: bool) -> None:
    """Whether a handheld may start games here (to stream them with Steam Link)."""
    submodule("store").set_launch(device_id, allowed)


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
