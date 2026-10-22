"""What syncs with a paired device (UX review AG6; docs/SPEC.md 7.7),
generated from the sync model as it is, so the page never says more or less
than happens: one row per kind of thing, whether it syncs with this device,
and why. Game files never sync."""

SYNCTHING = "droidtop.syncthing"

GAME_FILES = {
    "what": "Game files",
    "syncs": False,
    "detail": "Game files never sync. A handheld copies one game's folder only when you ask for it on the handheld.",
}


def what_syncs(device: dict, settings: dict) -> list[dict]:
    """The rows for ``device`` (as ``store.devices()`` gives it), with the
    agent's ``settings``: each has ``what``, ``syncs`` and ``detail``."""
    kind = device.get("kind") or "handheld"
    syncthing = bool(settings.get("syncthing", True)) and SYNCTHING in (device.get("announced") or {})
    syncthing_row = {
        "what": "Syncthing folders",
        "syncs": syncthing,
        "detail": "Folders you share in Syncthing with your paired devices; this device's Syncthing is added for it."
        if syncthing
        else "This device has not offered its Syncthing, or Syncthing is turned off here.",
    }
    if kind == "computer":
        return [
            {
                "what": "Library, saves and launches",
                "syncs": False,
                "detail": "Nothing of the library syncs between two computers yet. They trust each other, so it can "
                "be added without pairing again.",
            },
            syncthing_row,
            GAME_FILES,
        ]
    approved = sorted((settings.get("approved") or {}).keys())
    rows = [
        {
            "what": "Library",
            "syncs": True,
            "detail": "Which games are on this computer and on the handheld, and where each is installed.",
        },
        {
            "what": "Saves",
            "syncs": True,
            "detail": "Game saves, where their location is known. When both changed, "
            + (
                "this handheld's copy wins (it is the primary handheld); "
                if device.get("primary")
                else "the newest copy wins; "
            )
            + "the other is kept as a copy on the Save backups page.",
        },
        {
            "what": "Starting games here",
            "syncs": bool(device.get("launch")),
            "detail": "It may start Steam games on this computer, to stream them with Steam Link."
            if device.get("launch")
            else "It may not start games on this computer. Allow it on its device page.",
        },
        {
            "what": "Plugin data",
            "syncs": bool(approved),
            "detail": f"What these plugins keep in step: {', '.join(approved)}."
            if approved
            else "No plugin keeps data in step with it.",
        },
        syncthing_row,
        GAME_FILES,
    ]
    return rows
