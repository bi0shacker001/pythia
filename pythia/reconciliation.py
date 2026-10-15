"""
Reconciliation -- merging game entries that automatic onboarding correctly refused to
auto-link (only a "certain" match -- exact path or external ID -- ever auto-links, by
original Milestone 1 design: never silently guess two differently-pathed folders are
the same game) but which really are the same game. This is a real gap the external
spec review flagged directly (Section 4.2, "a future merge operation") and the user
found live in the running UI: BeingADik/BeingADIK/BeingADik landed as 3 separate
game_ids, Fetish Locator as 3 more, purely because the automatic scan path never
guesses a link from name similarity alone.

merge() is the explicit, user-confirmed fix: fold a "retired" game_id's versions,
platform_ids, and ownership into a "surviving" one, on this system's own records
only. Known simplification (2026-08-03, not yet built): no retained "removed"-state
alias record and no cross-system merge propagation -- the retired entry's file is
just deleted outright. The external review's Section 4.2 describes the fuller model
(retire-with-alias, other systems reconcile independently when they next run) this is
a first, real step toward, not the final shape.

2026-08-04 port onto the Lutris base: writes go through
pythia.database.games.save_entry_and_sync_cache()/delete_game() instead of the old
separate datadir+cache calls, same single-write-path consolidation as onboarding.py.
"""

import difflib
from pathlib import Path

from pythia import datadir
from pythia.database import games as games_db
from pythia.identity import get_system_uuid
from pythia.onboarding import _is_generic_part_leaf

NAME_SIMILARITY_THRESHOLD = 0.6


def _own_entries():
    system_uuid = get_system_uuid()
    return [e for e in datadir.list_all_entries() if e.get("system_uuid") == system_uuid]


def _summarize(entry: dict) -> dict:
    """Enough real context for a human to actually judge a candidate pair (2026-08-03
    -- a bare name+score dump made false positives like "BONEWORKS"/"Noble Works" and
    real matches like "MyNewFamily-Epilogue-pc"/"My New Family" equally hard to tell
    apart at a glance; the path/engine/platform/version-count differences are exactly
    what a person actually reasons from)."""
    versions = entry.get("versions", [])
    first_variant = None
    for group in versions:
        variants = group.get("variants", [])
        if variants:
            first_variant = variants[0]
            break
    return {
        "game_id": entry["game_id"],
        "name": entry.get("canonical_name"),
        "path": first_variant.get("path") if first_variant else None,
        "engine_kind": (first_variant.get("source") or {}).get("kind") if first_variant else None,
        "version_count": len(versions),
        "platforms": first_variant.get("platforms", []) if first_variant else [],
    }


def _part_folders_of_one_game(a: dict, b: dict) -> bool:
    """Both entries' installs are bare part folders (Part1, Week 3, ...) under one
    parent folder: parts of one game, recorded before onboarding learned to link
    them (2026-10-14). Stronger evidence than a similar name, so the pair is
    reported with `reason` "parts" and the top score."""
    for va in _variants(a):
        for vb in _variants(b):
            pa, pb = Path(va["path"]), Path(vb["path"])
            if pa.parent == pb.parent and _is_generic_part_leaf(pa.name) and _is_generic_part_leaf(pb.name):
                return True
    return False


def _variants(entry: dict):
    return [v for g in entry.get("versions", []) for v in g.get("variants", []) if v.get("path")]


def find_merge_candidates():
    """Every pair of this system's own tracked games whose canonical names are
    similar enough to plausibly be the same game, sorted by similarity descending.
    Returns a list of {"score", "a": {...}, "b": {...}} dicts (see _summarize) -- a
    suggestion queue for the user to confirm or dismiss, never auto-applied."""
    entries = _own_entries()
    candidates = []
    for i, a in enumerate(entries):
        for b in entries[i + 1 :]:
            name_a = (a.get("canonical_name") or "").lower()
            name_b = (b.get("canonical_name") or "").lower()
            if not name_a or not name_b:
                continue
            if _part_folders_of_one_game(a, b):
                candidates.append({"score": 1.0, "reason": "parts", "a": _summarize(a), "b": _summarize(b)})
                continue
            score = difflib.SequenceMatcher(None, name_a, name_b).ratio()
            if score >= NAME_SIMILARITY_THRESHOLD:
                candidates.append({"score": round(score, 3), "reason": "name", "a": _summarize(a), "b": _summarize(b)})
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def merge(surviving_game_id: str, retired_game_id: str) -> dict:
    """Fold `retired_game_id`'s versions/platform_ids/ownership into
    `surviving_game_id`, on this system's own records only, then delete the retired
    entry's file. Returns {"status": "ok", "game_id", "retired_game_id"} or an error
    dict."""
    if surviving_game_id == retired_game_id:
        return {"status": "error", "message": "Cannot merge a game into itself."}

    entries = {e["game_id"]: e for e in _own_entries()}
    survivor = entries.get(surviving_game_id)
    retired = entries.get(retired_game_id)
    if survivor is None or retired is None:
        return {"status": "error", "message": "Both game IDs must be tracked by this system."}

    # Merge versions -> variants, avoiding duplicate paths (same shape as
    # onboarding._merge_version, but folding whole groups instead of one variant).
    survivor_versions = survivor.setdefault("versions", [])
    for retired_group in retired.get("versions", []):
        version = retired_group.get("version")
        group = next((v for v in survivor_versions if v.get("version") == version), None)
        if group is None:
            group = {"version": version, "variants": []}
            survivor_versions.append(group)
        existing_paths = {v.get("path") for v in group["variants"]}
        for variant in retired_group.get("variants", []):
            if variant.get("path") not in existing_paths:
                group["variants"].append(variant)
                existing_paths.add(variant.get("path"))

    # Merge platform_ids/ownership by union (platform name is the dedupe key).
    survivor_platform_ids = survivor.setdefault("platform_ids", [])
    known_platforms = {p.get("platform") for p in survivor_platform_ids}
    for p in retired.get("platform_ids", []):
        if p.get("platform") not in known_platforms:
            survivor_platform_ids.append(p)
            known_platforms.add(p.get("platform"))

    survivor_ownership = survivor.setdefault("ownership", [])
    known_owned = {o.get("platform") for o in survivor_ownership}
    for o in retired.get("ownership", []):
        if o.get("platform") not in known_owned:
            survivor_ownership.append(o)
            known_owned.add(o.get("platform"))

    datadir.add_timeline_event(
        survivor, "merged", f"folded in game_id {retired_game_id} ({retired.get('canonical_name', '?')})"
    )
    # Delete the retired entry *before* saving the survivor with its merged-in
    # variants -- real bug caught by testing: the retired variants keep their
    # original "id" values when folded into the survivor, and those ids were
    # still sitting in cache.db under the retired game_id's own rows until
    # deleted, so saving the survivor first violated variants.id's uniqueness
    # constraint (the same id existing under two different game_ids at once).
    games_db.delete_game(retired_game_id)
    games_db.save_entry_and_sync_cache(survivor)

    return {"status": "ok", "game_id": surviving_game_id, "retired_game_id": retired_game_id}
