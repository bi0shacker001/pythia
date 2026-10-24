"""The game page's primary slot: Play, Install or Update, and what each does.

UX review 2026-10-16 (tracker coordination/pythia/ux-review-2026-10-16.md),
A12, with the owner's decision (docs/SPEC.md 1.1): a game page has one
primary slot, first and largest, holding Play, Install or Update. A game
installed on this computer plays through Steam when it has a Steam id and
Steam is here (Steam first), else through Pythia's own runner, the way
pythia.droidtop.launch starts it for a handheld. "Open in <launcher>" is an
ordinary action, never this slot.

Decision 1.2: an update or install Pythia cannot do yet is never hidden. It
is drawn disabled with its reason, with "Open store page" beside it when the
game has a store link.
"""

import os

from pythia import datadir
from pythia.identity import get_system_uuid
from pythia.util.log import logger

# A store id -> its page, for "Open store page".
STORE_PAGES = {
    "f95": "https://f95zone.to/threads/{id}/",
    "steam": "https://store.steampowered.com/app/{id}/",
    "gog": "https://www.gog.com/game/{id}",
    "dlsite": "https://www.dlsite.com/maniax/work/=/product_id/{id}.html",
}
NO_INSTALL = "Pythia does not install games yet. Install it with its store or launcher, then add its folder."
NO_UPDATE = "Pythia records updates but does not download them yet."
NO_RUNNER = "Nothing starts it here yet: choose how it runs."
NO_EPIC = "Epic's games start in Epic's launcher: More > Open in Epic Games Store."


def _entries(game_id: str) -> list[dict]:
    return [e for e in datadir.list_all_entries() if e.get("game_id") == game_id]


def _variants(entry: dict):
    for group in entry.get("versions") or []:
        yield from group.get("variants") or []


def _ids(entries: list[dict], platform: str) -> list[str]:
    return [
        str(p["id"])
        for e in entries
        for p in e.get("platform_ids") or []
        if p.get("platform") == platform and p.get("id")
    ]


def store_page(entries: list[dict]) -> str | None:
    for e in entries:
        for p in e.get("platform_ids") or []:
            ident = str(p.get("id") or "")
            if ident.startswith(("http://", "https://")):
                return ident
            pattern = STORE_PAGES.get(p.get("platform") or "")
            if pattern and ident:
                return pattern.format(id=ident)
    return None


def _steam_here() -> bool:
    from pythia.util.steam.config import get_steam_dir  # noqa: PLC0415

    try:
        return bool(get_steam_dir())
    except Exception:  # noqa: BLE001 -- no Steam is an answer, not an error
        return False


def _runner_check(game_id: str) -> str | None:
    """None when Lutris's own check says the game can start, else its words."""
    from pythia.game import Game  # noqa: PLC0415

    try:
        Game(game_id).check_launchable()
    except Exception as ex:  # noqa: BLE001 -- GameConfigError, or a game Lutris does not know
        return str(ex) or type(ex).__name__
    return None


def folder_variant(own: dict | None) -> dict | None:
    """The folder Play starts for a folder game: the Current version's, else
    the one added last whose folder is still there."""
    if not own:
        return None
    current = own.get("current_version")
    best = None
    for group in own.get("versions") or []:
        for variant in group.get("variants") or []:
            path = variant.get("path")
            if not path or not os.path.isdir(path):
                continue
            rank = (group.get("version") == current and current is not None, variant.get("detected_at") or "")
            if best is None or rank > best[0]:
                best = (rank, variant)
    return best[1] if best else None


def launch_of(own: dict | None) -> tuple[dict | None, dict | None]:
    """A folder game's folder and what Play starts in it (the person's choice,
    else Pythia's pick), either None when there is none."""
    from pythia import folder_launch  # noqa: PLC0415

    variant = folder_variant(own)
    if variant is None:
        return None, None
    chosen = variant.get("launch")
    if chosen and chosen.get("exe"):
        return variant, chosen
    return variant, folder_launch.pick(variant["path"], (own or {}).get("canonical_name") or "")


def state(game_id: str, entries: list[dict] | None = None) -> dict:
    """``primary`` (``action`` play or install, ``enabled``, ``via`` steam or
    runner, ``reason`` when disabled), ``update`` (``available``, ``version``,
    ``reason``) and ``store_url``. ``entries`` are the game's records when the
    caller has them (the backend's library), so the library is not read again."""
    entries = entries if entries is not None else _entries(game_id)
    if not entries:
        return {"status": "error", "message": f"No game {game_id} in the library"}
    uuid = get_system_uuid()
    own = next((e for e in entries if e.get("system_uuid") == uuid), None)
    installed = own is not None and any(v.get("path") for v in _variants(own))
    updates = (own or {}).get("available_updates") or []
    update = {
        "available": bool(updates),
        "version": (updates[0].get("version") if updates else None),
        "enabled": False,
        "reason": NO_UPDATE if updates else None,
    }
    steam = _ids(entries, "steam")
    if not installed:
        primary = {"action": "install", "enabled": False, "via": None, "reason": NO_INSTALL}
    elif steam and _steam_here():
        primary = {"action": "play", "enabled": True, "via": "steam", "reason": None}
    elif any(v.get("runner") for v in _variants(own or {})):
        refused = _runner_check(game_id)
        primary = {"action": "play", "enabled": refused is None, "via": "runner", "reason": refused}
    else:
        # A folder game (Droidtop/tracker#629): its own program, natively or through Wine.
        from pythia import folder_launch  # noqa: PLC0415

        variant, launch = launch_of(own)
        if _ids(entries, "egs"):
            # Epic's games check for its launcher; started from their folder they mostly quit at once.
            primary = {"action": "play", "enabled": False, "via": None, "reason": NO_EPIC}
        elif launch:
            primary = {"action": "play", "enabled": True, "via": "folder", "reason": None, "launch": launch}
        else:
            reason = folder_launch.why_not(variant["path"], own.get("canonical_name") or "") if variant else NO_RUNNER
            primary = {"action": "play", "enabled": False, "via": None, "reason": reason}
        primary["choose"] = variant is not None
    return {"status": "ok", "primary": primary, "update": update, "store_url": store_page(entries)}


def play(game_id: str, entries: list[dict] | None = None) -> dict:
    """Starts the game the way state() says. Steam's start is a ``url`` the
    front end opens (steam://rungameid/<id>); a runner start runs Pythia's
    own ``pythia:rungameid`` in a process of its own."""
    entries = entries if entries is not None else _entries(game_id)
    current = state(game_id, entries)
    if current.get("status") != "ok":
        return current
    primary = current["primary"]
    if primary["action"] != "play" or not primary["enabled"]:
        return {"status": "error", "message": primary.get("reason") or "It cannot start here"}
    name = entries[0].get("canonical_name") or game_id
    if primary["via"] == "steam":
        logger.info("Starting %s through Steam", name)
        return {"status": "ok", "via": "steam", "url": f"steam://rungameid/{_ids(entries, 'steam')[0]}"}
    if primary["via"] == "folder":
        return _play_folder(game_id, name)
    from pythia.droidtop.launch import _run_in_pythia  # noqa: PLC0415

    result = _run_in_pythia(game_id, name)
    if result.get("result") != "started":
        return {"status": "error", "message": result.get("message", "")}
    return {"status": "ok", "via": "runner"}


def _own(game_id: str) -> dict | None:
    uuid = get_system_uuid()
    return next((e for e in _entries(game_id) if e.get("system_uuid") == uuid), None)


def _play_folder(game_id: str, name: str) -> dict:
    """Starts a folder game's program and notes when it was played, which the
    library's Continue playing shelf reads."""
    from pythia import folder_launch  # noqa: PLC0415
    from pythia.database import games as games_db  # noqa: PLC0415

    own = _own(game_id)
    variant, launch = launch_of(own)
    if not variant or not launch:
        return {"status": "error", "message": NO_RUNNER}
    try:
        folder_launch.start(variant["path"], launch, game_id)
    except (OSError, RuntimeError) as ex:
        return {"status": "error", "message": f"{name} did not start: {ex}"}
    variant["lastplayed"] = folder_launch.now()
    games_db.save_entry_and_sync_cache(own)
    logger.info("Started %s from its folder", name)
    return {"status": "ok", "via": "folder"}


def launch_choices(game_id: str) -> dict:
    """What "Choose how it runs" offers: the folder, the programs in it
    (``candidates``), what Play starts now (``current``, and whether the person
    ``chose`` it), the ways a program can run here (``vias``) and Wine's path."""
    from pythia import folder_launch  # noqa: PLC0415

    own = _own(game_id)
    variant, launch = launch_of(own)
    if variant is None:
        return {"status": "error", "message": "It has no folder on this computer."}
    vias = ["native"] + ([] if folder_launch.on_windows() else ["wine"])
    return {
        "status": "ok",
        "folder": variant["path"],
        "candidates": folder_launch.candidates(variant["path"], own.get("canonical_name") or ""),
        "current": launch,
        "chose": bool(variant.get("launch")),
        "vias": vias,
        "wine": folder_launch.wine(),
        "windows": folder_launch.on_windows(),
    }


def set_launch(game_id: str, exe: str, via: str) -> dict:
    """Keeps the person's choice of what Play starts; an empty ``exe`` goes
    back to Pythia's pick. Only this computer's record holds it."""
    from pathlib import Path  # noqa: PLC0415

    from pythia.database import games as games_db  # noqa: PLC0415

    own = _own(game_id)
    variant = folder_variant(own)
    if own is None or variant is None:
        return {"status": "error", "message": "It has no folder on this computer."}
    if not exe:
        variant.pop("launch", None)
    else:
        if via not in ("native", "wine"):
            return {"status": "error", "message": f"{via} is not a way to run it."}
        root = Path(variant["path"]).resolve()
        target = (root / exe).resolve()
        if root not in target.parents or not target.is_file():
            return {"status": "error", "message": f"{exe} is not a file in its folder."}
        variant["launch"] = {"exe": target.relative_to(root).as_posix(), "via": via}
    games_db.save_entry_and_sync_cache(own)
    return {"status": "ok"}
