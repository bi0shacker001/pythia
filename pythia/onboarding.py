"""
onboard(path) -- identify a single game root and start (or continue) tracking it.

Split into pure detection/matching (find_candidates) and an explicit commit step
(onboard) driven by a `decision` -- interactive prompting belongs in cli.py/gui.py,
not here. See the Milestone 1 plan for the full rationale.
"""

import difflib
import re
import shutil
from pathlib import Path

from pythia import datadir
from pythia.datadir import classify_variant_tokens
from pythia.identity import get_system_uuid, next_game_id
from pythia.plugins import engine as engine_plugins
from pythia.plugins.library import identify, identify_all

NAME_SIMILARITY_THRESHOLD = 0.6

# A bare trailing number only counts as a version if it's explicitly marked as one
# (a "v" prefix) or dotted (contains a '.'/'_' between digit groups, e.g. "0.8.3" or
# "1_3_3") -- a bare integer like the "5"/"6" in "Far Cry 5"/"Far Cry 6" or the "2077"
# in "Cyberpunk 2077" is far more often a title/sequel number than a version, and
# treating it as one was silently merging unrelated games (2026-08-02 real-library
# finding). This won't catch every real case either (a bare build number like
# "Bunnycop-049" now just stays part of the name) -- best-effort, not exhaustive; see
# this function's docstring.
_NAME_VERSION_RE = re.compile(r"^(.*?)[-_ ](?:v(\d+(?:[._]\d+)*[a-zA-Z]*)|(\d+[._]\d+(?:[._]\d+)*[a-zA-Z]*))(.*)$")

# A small set of generic "this is a part/chapter marker, not a title" words -- when a
# leaf folder is *just* one of these plus a number (e.g. "Week 1", "Part1", "Chap3+"),
# the real game name almost always lives on a parent folder instead (2026-08-02 real
# -library case: BeingADik/Chap3+/10.0-sancho, Fetish Locator/Week 1/2/3, My New
# Family/Part 1, Thief of Hearts/Part1 all fragmented into separate bogus "games"
# because only the leaf name was ever considered).
_GENERIC_PART_PREFIX_RE = re.compile(r"^(chap(?:ter)?|part|week|act|episode|ep|season|vol(?:ume)?)\.?\s*", re.IGNORECASE)


def _is_generic_part_leaf(name: str) -> bool:
    """True if `name` looks like a bare version/part marker with no real title of
    its own -- either it starts directly with a dotted version number ("10.0-sancho",
    requires an actual dot so a bare integer like "7" in "7 Days to Die" doesn't
    false-positive), or it's a known generic word (Week/Part/Chapter/...) immediately
    followed by a number and nothing else."""
    if re.match(r"^\d+\.\d", name):
        return True
    stripped = _GENERIC_PART_PREFIX_RE.sub("", name, count=1)
    if stripped == name:
        return False
    return bool(re.match(r"^\W*\d", stripped))


def _find_meaningful_ancestor_name(path: Path, max_levels: int = 2):
    """Walk up from `path` past any generic/part-marker-named parent folders (see
    _is_generic_part_leaf) to find the nearest ancestor that looks like a real title.
    Bounded to avoid ever walking up into a library/engine-root folder (e.g. "renpy")
    -- normal installs never trigger this at all, since it's only consulted when the
    leaf itself already looked like a bare version/part marker."""
    current = path
    for _ in range(max_levels):
        current = current.parent
        name = current.name
        if not name or _is_generic_part_leaf(name):
            continue
        return name
    return None


def _extract_version_only(leaf: str):
    """Pull version/mods/language out of a leaf folder name that's *just* a version
    or part marker (the real name comes from an ancestor folder instead -- see
    _find_meaningful_ancestor_name) -- unlike _derive_name_version_mods_language,
    there's no name prefix to strip here, and the generic part-word (if any) is
    stripped rather than kept as a mod tag."""
    stripped = _GENERIC_PART_PREFIX_RE.sub("", leaf, count=1)
    m = re.search(r"(\d+(?:[._]\d+)*[a-zA-Z]*)", stripped)
    if not m:
        mods, language = classify_variant_tokens(stripped)
        return stripped.strip() or leaf, mods, language
    version = m.group(1).replace("_", ".")
    rest = stripped[:m.start()] + " " + stripped[m.end():]
    mods, language = classify_variant_tokens(rest)
    return version, mods, language


def _derive_name_version_mods_language(path: Path):
    """Minimal placeholder splitter -- good enough to seed a canonical name, a
    version-ish label, a `mods` guess, and a `language` guess from a raw folder name; a
    proper Sonarr/Radarr-style naming filter system replaces this later. Best-effort --
    expected to misclassify sometimes, same caveat as the name/version split this
    extends.

    When the leaf folder name itself is just a version/part marker with no real title
    (see _is_generic_part_leaf), looks to a meaningful ancestor folder for the name
    instead of minting a bogus separate "game" per part/chapter/version subfolder."""
    folder_name = path.name

    if _is_generic_part_leaf(folder_name):
        ancestor_name = _find_meaningful_ancestor_name(path)
        if ancestor_name is not None:
            version, mods, language = _extract_version_only(folder_name)
            return ancestor_name, version, mods, language

    m = _NAME_VERSION_RE.search(folder_name)
    if not m:
        return folder_name, "", [], None
    name = m.group(1).strip(" -_") or folder_name
    version = (m.group(2) or m.group(3)).replace("_", ".")
    mods, language = classify_variant_tokens(m.group(4))
    return name, version, mods, language


def _own_entries():
    uuid = get_system_uuid()
    return [e for e in datadir.list_all_entries() if e.get("system_uuid") == uuid]


def find_candidates(detection: dict, name_hint: str, path: Path):
    """Return {"certain": entry_or_None, "suggested": [entries]} -- plausible matches
    for `detection` among this system's own already-tracked games."""
    own = _own_entries()
    path_str = str(Path(path).resolve())

    # Strongest possible signal: this exact on-disk path is already tracked under some
    # game. Without this check, re-onboarding the same path (e.g. re-running `scan`)
    # would mint a brand new game every time instead of updating the existing one.
    certain = None
    for entry in own:
        if any(
            variant.get("path") == path_str
            for group in entry.get("versions", [])
            for variant in group.get("variants", [])
        ):
            certain = entry
            break

    external_id = detection.get("external_id")
    if certain is None and external_id is not None:
        expected_source = f"{detection['kind']}_id"
        for entry in own:
            # Both the ID *and* its source kind must match -- otherwise a small
            # locally-minted counter value could coincidentally collide with an
            # unrelated external ID (e.g. a low-numbered Steam AppID) and produce a
            # false "certain" link between two different games.
            if entry.get("game_id") == external_id and entry_id_source(entry) == expected_source:
                certain = entry
                break

    suggested = []
    if certain is None:
        target = name_hint.lower()
        for entry in own:
            ratio = difflib.SequenceMatcher(None, target, entry.get("canonical_name", "").lower()).ratio()
            if ratio >= NAME_SIMILARITY_THRESHOLD:
                suggested.append((ratio, entry))
        suggested.sort(key=lambda t: t[0], reverse=True)
        suggested = [e for _, e in suggested]

    return {"certain": certain, "suggested": suggested}


def entry_id_source(entry: dict) -> str:
    return entry.get("game_id_source", "local")


def preview(path: Path):
    """Identify `path` and find candidate existing games, without writing anything.
    This is what cli.py/gui.py call before asking the user for a decision. Returns
    {"status": "ok", "path", "detection", "name", "version_label", "candidates"} or
    {"status": "error", "message": ...}.
    """
    path = Path(path).resolve()
    if not path.is_dir():
        return {"status": "error", "message": f"'{path}' does not exist or is not a directory."}

    detection = identify(path)
    if detection is None:
        return {"status": "error", "message": f"Could not identify '{path}' as any known kind of game."}

    # A library plugin only ever finds structural boundaries (where does an install
    # start/stop) -- it never claims engine identity itself, even one with its own
    # structural marker like Ren'Py (2026-08-03 root-cause fix: engine identity and
    # distribution identity are different, independent questions -- a Ren'Py game
    # sold on Steam is both "steam" and "renpy" at once, see plugins/library/
    # (renpy.py removed entirely) and EnginePlugin.refines_kinds's docstring for the
    # full history). So engine identification is *always* attempted here, regardless
    # of what -- if anything -- a library plugin already found; if an engine matches,
    # its kind wins as primary (more actionable for Pythia's purposes than a bare
    # distribution kind), and the original distribution detection is preserved via
    # additional_sources below rather than being merged/lost.
    _engine_plugin, engine_detection = engine_plugins.identify(path, current_kind=detection.get("kind"))
    if engine_detection is not None and engine_detection.get("kind") != detection.get("kind"):
        detection = {**detection, "kind": engine_detection["kind"]}
        if "platforms" in engine_detection:
            detection["platforms"] = engine_detection["platforms"]
        # Also reset: the prior kind's version_label (e.g. a Steam buildid) is a
        # meaningless number under the new engine kind, not a real game version --
        # left alone it would leak through as the displayed version. Cleared back to
        # None so the folder-name heuristic (which every engine plugin already
        # expects to supply the real game version) wins instead, same as an install
        # with no distribution-kind override at all.
        detection["version_label"] = None
        # Replaced, not merged: the prior kind's extra fields (e.g. a Steam AppID)
        # stay correctly scoped to that kind via additional_sources below, rather
        # than ending up mixed into an "extra" dict whose kind no longer matches them.
        detection["extra"] = {k: v for k, v in engine_detection.items() if k not in ("kind", "platforms")}

    # A path can legitimately match more than one library plugin at once -- e.g. a
    # Ren'Py game distributed via Steam matches both the Ren'Py and Steam plugins
    # (2026-08-03 real-library finding: Sunrider Academy). identify() only returns a
    # single winner for engine/version-label purposes, but any *other* match that
    # carries a real platform ID is still worth recording for ownership/platform_ids
    # -- otherwise a Steam-distributed Ren'Py game would silently lose its AppID
    # entirely just because Ren'Py's structural marker outranks Steam's for naming.
    additional_sources = [
        other
        for _plugin, other in identify_all(path)
        if other.get("kind") != detection.get("kind") and other.get("kind") in datadir.PLATFORM_ID_KINDS
    ]

    derived_name, derived_version, mods, language = _derive_name_version_mods_language(path)
    name = detection.get("name") or derived_name
    version_label = detection.get("version_label") or derived_version or path.name

    candidates = find_candidates(detection, name, path)

    return {
        "status": "ok",
        "path": path,
        "detection": detection,
        "additional_sources": additional_sources,
        "name": name,
        "version_label": version_label,
        "mods": mods,
        "language": language,
        "candidates": candidates,
    }


def onboard(path: Path, decision: dict | None = None):
    """Identify `path` and record it in this system's datadir. See the plan's
    Onboarding flow section for what `decision` accepts. Returns a result dict:
    {"status": "ok", "game_id": N, "candidates": {...}} or {"status": "error", "message": ...}
    """
    pv = preview(path)
    if pv["status"] != "ok":
        return pv
    path, detection, additional_sources, name, version_label, mods, language, candidates = (
        pv["path"],
        pv["detection"],
        pv["additional_sources"],
        pv["name"],
        pv["version_label"],
        pv["mods"],
        pv["language"],
        pv["candidates"],
    )

    action = (decision or {}).get("action")
    import_to_location = (decision or {}).get("import_to_location", False)

    if action == "new":
        game_id, id_source, existing_entry = _mint_new(detection)
    elif action == "link":
        game_id = decision["game_id"]
        id_source = "linked"
        existing_entry = _find_own_entry_by_game_id(game_id)
        if existing_entry is None:
            return {"status": "error", "message": f"No existing tracked game with id {game_id} on this system."}
    else:
        # Automatic: certain match -> link, otherwise new. Used by scan_library() and
        # any caller that doesn't want to prompt.
        if candidates["certain"] is not None:
            existing_entry = candidates["certain"]
            game_id = existing_entry["game_id"]
            id_source = entry_id_source(existing_entry)
        else:
            game_id, id_source, existing_entry = _mint_new(detection)

    sibling_variant = None
    if existing_entry:
        for group in existing_entry.get("versions", []):
            variants = group.get("variants", [])
            if variants:
                sibling_variant = variants[0]
                break

    if action == "link" and import_to_location and sibling_variant is not None:
        sibling_parent = Path(sibling_variant["path"]).resolve().parent
        if path.parent != sibling_parent:
            new_path = sibling_parent / path.name
            shutil.move(str(path), str(new_path))
            path = new_path

    entry = existing_entry or {
        "system_uuid": get_system_uuid(),
        "game_id": game_id,
        "game_id_source": id_source,
        "canonical_name": name,
        "platform_ids": [],
        "ownership": [],
        "managed_externally": False,
        "version_management_enabled": True,
        "notes": "",
        "versions": [],
        "available_updates": [],
    }
    entry["canonical_name"] = entry.get("canonical_name") or name
    _update_platform_ids_and_ownership(entry, detection)
    for other in additional_sources:
        _update_platform_ids_and_ownership(entry, other)

    source = {"kind": detection["kind"], "extra": detection.get("extra", {})}
    _merge_version(entry, path, version_label, mods, language, detection.get("platforms", []), source)

    saved_path = datadir.save_entry(entry)

    from pythia import cache

    cache.upsert_entry(entry, saved_path.name)

    return {
        "status": "ok",
        "game_id": game_id,
        "candidates": candidates,
        "path": str(saved_path),  # the datadir JSON file this was recorded in
        "game_path": str(path),  # the actual game folder that was onboarded
        "name": entry["canonical_name"],
    }


def _mint_new(detection: dict):
    external_id = detection.get("external_id")
    if external_id is not None:
        return external_id, f"{detection['kind']}_id", None
    return next_game_id(), "local", None


def _find_own_entry_by_game_id(game_id: int):
    for entry in _own_entries():
        if entry.get("game_id") == game_id:
            return entry
    return None


def add_ownership(game_id: int, platform: str, note: str | None = None):
    """Record ownership of an already-tracked game on `platform` without any local
    install to detect it from (2026-08-03) -- real cases from the user's own library:
    a Patreon subscription with no local Patreon-branded files at all, or owning a
    game on itch.io without having installed it there (only the Steam copy is on
    disk). `_update_platform_ids_and_ownership()` only ever runs as a side effect of
    onboarding a real path; this is the explicit, no-install counterpart. Returns a
    result dict matching onboard()'s shape."""
    entry = _find_own_entry_by_game_id(game_id)
    if entry is None:
        return {"status": "error", "message": f"No existing tracked game with id {game_id} on this system."}

    ownership = entry.setdefault("ownership", [])
    existing = next((o for o in ownership if o.get("platform") == platform), None)
    if existing is not None:
        if note:
            existing["note"] = note
    else:
        record = {"platform": platform}
        if note:
            record["note"] = note
        ownership.append(record)

    saved_path = datadir.save_entry(entry)

    from pythia import cache

    cache.upsert_entry(entry, saved_path.name)

    return {"status": "ok", "game_id": game_id, "ownership": entry["ownership"]}


def _update_platform_ids_and_ownership(entry: dict, detection: dict):
    """Record what this detection tells us about the game's identity on other
    platforms and the user's claim to it there -- deliberately two separate lists,
    not one (2026-08-03): `platform_ids` ("this game is known as X on platform Y") is
    useful for pulling metadata from that platform's public API even for a platform
    the user doesn't own the game on; `ownership` ("the user actually has a claim to
    it on platform Y") is seeded here from an install of that kind actually existing,
    which is strong evidence of ownership, but is conceptually a different fact --
    e.g. a game owned via a Patreon subscription but f95-downloaded has ownership
    with no platform_id at all. Additive only: a game already known to be owned on
    Steam stays so even after also onboarding an Epic install of the same game_id."""
    kind = detection.get("kind")
    extra = detection.get("extra") or {}
    id_key = datadir.PLATFORM_ID_KINDS.get(kind)

    platform_ids = entry.setdefault("platform_ids", [])
    if id_key and extra.get(id_key) and not any(p.get("platform") == kind for p in platform_ids):
        platform_ids.append({"platform": kind, "id": extra[id_key]})

    ownership = entry.setdefault("ownership", [])
    if kind in datadir.PLATFORM_ID_KINDS and not any(o.get("platform") == kind for o in ownership):
        ownership.append({"platform": kind})


def _merge_version(entry: dict, path: Path, version: str, mods: list, language, platforms: list, source: dict):
    """Find-or-create the `versions[]` group for `version`, then find-by-path within
    that group's `variants[]` (update in place) or append a new variant. `source`
    (what this specific on-disk install actually is -- see PLATFORM_ID_KINDS'
    docstring in datadir.py) lives per-variant, not on the shared game entry: two
    variants of the same game_id can legitimately have different sources (a Steam
    install and an Epic install of the same game are different on-disk copies)."""
    import datetime

    path_str = str(path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    versions = entry.setdefault("versions", [])
    group = next((v for v in versions if v.get("version") == version), None)
    if group is None:
        group = {"version": version, "variants": []}
        versions.append(group)

    for variant in group["variants"]:
        if variant.get("path") == path_str:
            variant["mods"] = mods
            variant["language"] = language
            variant["platforms"] = platforms
            variant["source"] = source
            variant["detected_at"] = now
            return

    group["variants"].append(
        {
            "mods": mods,
            "language": language,
            "path": path_str,
            "platforms": platforms,
            "source": source,
            "detected_at": now,
        }
    )
