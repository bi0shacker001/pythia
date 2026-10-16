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
NO_RUNNER = "Nothing starts it here yet: set up how it runs in Pythia (a runner)."


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
        primary = {"action": "play", "enabled": False, "via": None, "reason": NO_RUNNER}
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
    from pythia.droidtop.launch import _run_in_pythia  # noqa: PLC0415

    result = _run_in_pythia(game_id, name)
    if result.get("result") != "started":
        return {"status": "error", "message": result.get("message", "")}
    return {"status": "ok", "via": "runner"}
