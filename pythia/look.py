"""Scanning in three steps: Look, Review, Add (UX review A16, O1).

Look walks a folder the way a scan does (scanning._discover_roots) but only
previews each game folder it finds (onboarding.preview): nothing is written.
The folders are grouped into proposed games by title, so a folder holding UX
Test Novel v1.0, v1.1, v1.2 and "v1.2 (Spanish translation)" is one proposed
game with three versions and a translation, and UX Test Serial's Part1, Part2
and Part3 are one game in three parts (droidtop's segments; owner decision 3).

Review is the person's: per game, keep it or not, link it to a game already
in the library or keep it new, and per folder its kind and language; a store
link for the game. Add writes only the games kept, as reviewed.
"""

import hashlib
import re
from collections import Counter
from pathlib import Path

from pythia import datadir, onboarding, scanning, store_links
from pythia.identity import get_system_uuid
from pythia.plugins import engine, library
from pythia.util.log import logger


def _key(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (name or "").casefold())


def _folder(pv: dict) -> dict:
    detection = pv.get("detection") or {}
    return {
        "path": str(pv["path"]),
        "name": pv.get("name") or Path(pv["path"]).name,
        "version": pv.get("version_label") or "",
        "part": pv.get("part"),
        "kind": pv.get("kind") or "game",
        "language": pv.get("language"),
        "flags": pv.get("flags") or [],
        "mods": [m.get("name") for m in pv.get("mods") or []],
        "engine": detection.get("kind"),
        "platforms": detection.get("platforms") or [],
        "candidates": {
            "certain": _candidate(pv["candidates"].get("certain")),
            "suggested": [_candidate(c) for c in pv["candidates"].get("suggested") or []],
        },
    }


def _candidate(entry: dict | None) -> dict | None:
    if not entry:
        return None
    return {"game_id": entry.get("game_id"), "name": entry.get("canonical_name") or entry.get("game_id")}


def _match(name: str, folders: list[dict]) -> dict | None:
    """The game already in the library this proposal is, with the reason in words."""
    for f in folders:
        certain = f["candidates"]["certain"]
        if certain:
            return {**certain, "reason": f"{Path(f['path']).name} is already recorded as {certain['name']}"}
    for f in folders:
        for c in f["candidates"]["suggested"]:
            if c and _key(c["name"]) == _key(name):
                return {**c, "reason": f"A game named {c['name']} is already in the library"}
    return None


def group(folders: list[dict]) -> list[dict]:
    """Proposed games, one per title, in name order."""
    by_title: dict[str, list[dict]] = {}
    for f in folders:
        by_title.setdefault(_key(f["name"]), []).append(f)
    proposals = []
    for key, members in by_title.items():
        members.sort(key=lambda f: (f["part"] or "", f["kind"] != "game", f["path"]))
        name = Counter(f["name"] for f in members).most_common(1)[0][0]
        parts = sorted(
            {f["part"] for f in members if f["part"]},
            key=lambda p: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p)],
        )
        version_labels = sorted(
            {f["version"] for f in members if f["version"] and f["kind"] == "game" and not f["part"]}
        )
        engines = Counter(f["engine"] for f in members if f["engine"])
        proposals.append(
            {
                "id": hashlib.sha1(key.encode()).hexdigest()[:10],
                "name": name,
                "engine": engines.most_common(1)[0][0] if engines else None,
                "folders": members,
                "versions": version_labels,
                "parts": parts,
                "translations": sum(1 for f in members if f["kind"] == "translation"),
                "match": _match(name, members),
            }
        )
    return sorted(proposals, key=lambda p: p["name"].casefold())


def look(root: Path | str, progress=None) -> dict:
    """What scanning ``root`` would add, without writing anything. ``progress``
    gets the live counts (``folders``, ``games``) as the walk goes."""
    root = Path(root).resolve()
    enabled = library.enabled_plugins() + engine.enabled_plugins()
    generic = library.generic_plugin if library.generic_plugin in library.enabled_plugins() else None
    roms = scanning._rom_folders(root)
    is_launcher = scanning._launcher_rule()
    folders, skipped, errors = [], [], []
    titles: set[str] = set()
    empty: list = []
    for discovered in scanning._discover_roots(root, enabled, generic, empty):
        launcher = next(
            (f for f in (discovered, *discovered.parents) if f != root and root in f.parents and is_launcher(f)), None
        )
        if launcher is not None:
            skipped.append({"path": str(discovered), "message": f"{launcher.name} is a launcher's own folder."})
            continue
        if discovered in roms:
            skipped.append({"path": str(discovered), "message": f"{discovered.name} is a ROM folder."})
            continue
        pv = onboarding.preview(discovered)
        if pv.get("status") != "ok":
            errors.append({"path": str(discovered), "message": pv.get("message", "")})
            continue
        folder = _folder(pv)
        folders.append(folder)
        titles.add(_key(folder["name"]))
        if progress:
            progress({"folders": len(folders) + len(skipped) + len(errors), "games": len(titles)})
    skipped += [{"path": str(path), "message": f"{path.name}: {reason}."} for path, reason in empty]
    proposals = group(folders)
    return {
        "status": "ok",
        "root": str(root),
        "folders": len(folders),
        "proposals": proposals,
        "skipped": skipped,
        "errors": errors,
        "summary": summary(len(folders), proposals),
    }


def summary(folders: int, proposals: list[dict]) -> str:
    """ "8 folders, 6 games (3 are parts of UX Test Serial)" """
    text = f"{folders} folder{'' if folders == 1 else 's'}, {len(proposals)} game{'' if len(proposals) == 1 else 's'}"
    notes = []
    for p in proposals:
        if len(p["parts"]) > 1:
            notes.append(f"{len(p['parts'])} are parts of {p['name']}")
        elif len(p["folders"]) > 1:
            notes.append(f"{len(p['folders'])} are folders of {p['name']}")
    return text + (f" ({'; '.join(notes)})" if notes else "")


def _own_entry(game_id: str) -> tuple[Path, dict] | None:
    path = datadir.find_entry_file(get_system_uuid(), game_id)
    return (path, datadir.load_entry(path)) if path else None


def add(rows: list[dict]) -> dict:
    """Writes the reviewed games: each row is a proposal as Review left it, with
    ``keep`` (false rows are left out), ``link_to`` (a game id, or None for a
    new game), ``store_link`` (text, optional) and its folders' ``kind`` and
    ``language``. Returns the counts and each failure."""
    from pythia.database import games as games_db  # noqa: PLC0415

    added, folders_added, failures = 0, 0, []
    for row in rows:
        if not row.get("keep", True):
            continue
        game_id = row.get("link_to") or None
        base_versions = {f["version"] for f in row["folders"] if f.get("kind", "game") == "game" and f.get("version")}
        ok_paths = []
        for f in row["folders"]:
            decision = {"action": "link", "game_id": game_id} if game_id else {"action": "new"}
            result = onboarding.onboard(f["path"], decision=decision)
            if result.get("status") != "ok":
                failures.append({"name": row["name"], "path": f["path"], "message": result.get("message", "")})
                continue
            game_id = game_id or result.get("game_id")
            ok_paths.append(f)
        if not ok_paths or not game_id:
            continue
        found = _own_entry(game_id)
        if found is None:
            continue
        _path, entry = found
        for f in ok_paths:
            for group_ in entry.get("versions") or []:
                for variant in group_.get("variants") or []:
                    if variant.get("path") != str(Path(f["path"]).resolve()) and variant.get("path") != f["path"]:
                        continue
                    if f.get("kind") and f["kind"] in datadir.KINDS and f["kind"] != variant.get("kind"):
                        variant["kind"], variant["kind_set_by_person"] = f["kind"], True
                    if "language" in f:
                        variant["language"] = f["language"] or None
                    if f.get("kind") in ("translation", "patch", "mod") and f.get("version") in base_versions:
                        variant["base_version"] = f["version"]
        if row.get("store_link"):
            try:
                store_links.put(entry, store_links.parse(row["store_link"]))
            except store_links.LinkError as ex:
                failures.append({"name": row["name"], "path": "", "message": str(ex)})
        games_db.save_entry_and_sync_cache(entry)
        added += 1
        folders_added += len(ok_paths)
        logger.info("Added %s (%d folders) from a reviewed scan", row["name"], len(ok_paths))
    return {"status": "ok", "games": added, "folders": folders_added, "failures": failures}
