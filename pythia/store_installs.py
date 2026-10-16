"""The games the stores on this computer have installed: Steam, Epic, GOG,
Amazon, itch, Battle.net and the Linux launchers (Lutris, Bottles,
Minigalaxy), wherever their folders are, without scanning a folder tree.

Droidtop/tracker#538, the Windows UI test: Pythia found a store's games only
inside folders the person scanned, and had no local-install detection for
GOG, itch or Epic. droidtop-agent's scanner reads each store's own records on
every system (Steam's library folders, Epic's manifests, GOG Galaxy and the
registry, itch's database...), and the droidtop changes for Pythia
(tracker coordination/pythia/droidtop-delta.md, detection) say detection
comes from that scanner. So this asks it (``pythia._native.scan.scan_with``,
store sources only) and records each store game's folder the way any folder
is recorded (``onboarding.onboard``), keeping the store's id on the entry
even where no Pythia plugin recognises the folder itself.
"""

from pathlib import Path
from typing import Any

from pythia import datadir, onboarding
from pythia.droidtop.native import plain_path, submodule
from pythia.identity import get_system_uuid
from pythia.util.log import logger

# The scanner's sources that are folder trees rather than store records.
_NOT_STORES = ["game folders", "ROM folders"]
# The scanner's key prefix -> Pythia's platform name (datadir platform_ids).
_PLATFORMS = {
    "steam": "steam",
    "epic": "egs",
    "gog": "gog",
    "itch": "itch",
    "amazon": "amazon",
    "battlenet": "battlenet",
}


def find() -> list[dict]:
    """Every installed store game the scanner finds: ``key`` (``steam:620``),
    ``title``, ``folder`` and ``store`` (its launcher)."""
    scan = submodule("scan").scan_with(
        game_folders=[], rom_folders=[], disabled_folders=[], disabled_sources=_NOT_STORES
    )
    found = []
    for item in scan.get("games") or []:
        game = item.get("game") or {}
        install = game.get("install") or {}
        folder = plain_path(item.get("base") or install.get("path"))
        if not install.get("installed") or not folder or not Path(folder).is_dir():
            continue
        found.append(
            {
                "key": game.get("key", ""),
                "title": game.get("title", ""),
                "folder": folder,
                "store": install.get("launcher"),
            }
        )
    return found


def _tracked_folders() -> set[str]:
    uuid = get_system_uuid()
    return {
        str(Path(v["path"]).resolve())
        for e in datadir.list_all_entries()
        if e.get("system_uuid") == uuid
        for g in e.get("versions") or []
        for v in g.get("variants") or []
        if v.get("path")
    }


def _keep_store_id(entry_file: str, key: str) -> None:
    """Adds the store's id (``platform_ids``) and the claim to it there
    (``ownership``) to the entry onboarding just wrote, when it lacks them."""
    from pythia.database import games as games_db  # noqa: PLC0415

    store, _, store_id = key.partition(":")
    platform = _PLATFORMS.get(store)
    if not platform or not store_id:
        return
    entry = datadir.load_entry(Path(entry_file))
    changed = False
    if not any(p.get("platform") == platform for p in entry.setdefault("platform_ids", [])):
        entry["platform_ids"].append({"platform": platform, "id": store_id})
        changed = True
    if not any(o.get("platform") == platform for o in entry.setdefault("ownership", [])):
        entry["ownership"].append({"platform": platform})
        changed = True
    if changed:
        games_db.save_entry_and_sync_cache(entry)


def record_new() -> dict:
    """Records every store game whose folder is not tracked yet. Returns the
    counts (``found``, ``recorded``, ``already``, ``failed``) and each
    ``failure``'s folder and message."""
    found = find()
    tracked = _tracked_folders()
    counts: dict[str, Any] = {"found": len(found), "recorded": 0, "already": 0, "failed": 0, "failures": []}
    for game in found:
        if str(Path(game["folder"]).resolve()) in tracked:
            counts["already"] += 1
            continue
        result = onboarding.onboard(game["folder"])
        if result.get("status") != "ok":
            counts["failed"] += 1
            counts["failures"].append({"folder": game["folder"], "message": result.get("message", "")})
            logger.warning("Could not record %s (%s): %s", game["title"], game["folder"], result.get("message"))
            continue
        _keep_store_id(result["path"], game["key"])
        counts["recorded"] += 1
    return counts


def entries() -> list[dict]:
    """What each launcher on this computer lists, against Pythia's library:
    ``game`` (title), ``key``, ``manager`` (the launcher), ``folder``, and
    ``state``: ``tracked`` (in the library at that folder), ``drifted`` (the
    game is in the library under another folder; ``library_folder``) or
    ``untracked``."""
    uuid = get_system_uuid()
    from pythia.droidtop.library import game_key  # noqa: PLC0415

    by_key: dict[str, list[str]] = {}
    for e in datadir.list_all_entries():
        if e.get("system_uuid") != uuid:
            continue
        folders = [
            str(Path(v["path"]).resolve())
            for g in e.get("versions") or []
            for v in g.get("variants") or []
            if v.get("path")
        ]
        by_key.setdefault(game_key(e), []).extend(folders)
    tracked = {f for folders in by_key.values() for f in folders}
    rows = []
    for game in find():
        folder = str(Path(game["folder"]).resolve())
        if folder in tracked:
            state, library_folder = "tracked", folder
        elif by_key.get(game["key"]):
            state, library_folder = "drifted", by_key[game["key"]][0]
        else:
            state, library_folder = "untracked", None
        rows.append(
            {
                "game": game["title"],
                "key": game["key"],
                "manager": game["store"] or game["key"].partition(":")[0],
                "folder": game["folder"],
                "state": state,
                "library_folder": library_folder,
            }
        )
    return sorted(rows, key=lambda r: (r["game"].casefold(), r["manager"]))


def record(folder: str, key: str) -> dict:
    """Records one launcher entry's folder (onboarding.onboard), keeping the
    store's id on the entry."""
    result = onboarding.onboard(folder)
    if result.get("status") == "ok":
        _keep_store_id(result["path"], key)
    return result
