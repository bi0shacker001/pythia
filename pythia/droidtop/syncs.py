"""What syncs with a paired device (UX review AG6; docs/SPEC.md 7.8),
generated from the sync model as it is, so the page never says more or less
than happens: one row per kind of thing, whether it syncs with this device,
and why. Game files never sync."""

SYNCTHING = "droidtop.syncthing"

GAME_FILES = {
    "what": "Game files",
    "syncs": False,
    "detail": "Game files never sync on their own. A device this computer offers its library copies one "
    "game's folder only when you ask for it there.",
}


def offered(device: dict) -> dict:
    """What this computer offers ``device`` (droidtop-agent docs/DESIGN.md
    section 20): ``library`` and ``saves``. A device from an agent that
    does not say gets its kind's default: a handheld its library and saves,
    a computer nothing."""
    if isinstance(device.get("offered"), dict):
        return {"library": bool(device["offered"].get("library")), "saves": bool(device["offered"].get("saves"))}
    on = (device.get("kind") or "handheld") != "computer"
    return {"library": on, "saves": on}


def what_syncs(device: dict, settings: dict) -> list[dict]:
    """The rows for ``device`` (as ``store.devices()`` gives it), with the
    agent's ``settings``: each has ``what``, ``syncs`` and ``detail``."""
    kind = device.get("kind") or "handheld"
    offer = offered(device)
    syncthing = bool(settings.get("syncthing", True)) and SYNCTHING in (device.get("announced") or {})
    syncthing_row = {
        "what": "Syncthing folders",
        "syncs": syncthing,
        "detail": "Folders you share in Syncthing with your paired devices; this device's Syncthing is added for it."
        if syncthing
        else "This device has not offered its Syncthing, or Syncthing is turned off here.",
    }
    library = {
        "what": "Library",
        "syncs": offer["library"],
        "detail": "This computer offers it its library: which games are here and where, and copying any of them."
        if offer["library"]
        else "This computer does not offer it its library. Turn on Library for it on the Devices page.",
    }
    if not offer["saves"]:
        saves_detail = "This computer does not offer it its saves. Turn on Saves for it on the Devices page."
    elif kind == "handheld" and device.get("primary"):
        saves_detail = (
            "Game saves, where their location is known. When both changed, this handheld's copy wins (it is "
            "the primary handheld); the other is kept as a copy on the Save backups page."
        )
    else:
        saves_detail = (
            "Game saves, where their location is known. When both changed, the newest copy wins; the other is "
            "kept as a copy on the Save backups page."
        )
    launch = {
        "what": "Starting games here",
        "syncs": bool(device.get("launch")),
        "detail": "It may start games on this computer, to stream them."
        if device.get("launch")
        else "It may not start games on this computer. Allow it on the Devices page.",
    }
    if kind == "computer":
        plugins = {
            "what": "Plugin data",
            "syncs": False,
            "detail": "Plugin data never syncs between two computers.",
        }
    else:
        approved = sorted((settings.get("approved") or {}).keys())
        plugins = {
            "what": "Plugin data",
            "syncs": bool(approved),
            "detail": f"What these plugins keep in step: {', '.join(approved)}."
            if approved
            else "No plugin keeps data in step with it.",
        }
    return [
        library,
        {"what": "Saves", "syncs": offer["saves"], "detail": saves_detail},
        launch,
        plugins,
        syncthing_row,
        GAME_FILES,
    ]
