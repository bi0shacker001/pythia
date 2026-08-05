"""
onboard(path) -- identify a single game root and start (or continue) tracking it.

Split into pure detection/matching (find_candidates) and an explicit commit step
(onboard) driven by a `decision` -- interactive prompting belongs in cli.py or the
Qt UI's backend.py, not here. See the Milestone 1 plan for the full rationale.

2026-08-04 port onto the Lutris base: the two write sites (onboard(),
record_ownership()) now call pythia.database.games.save_entry_and_sync_cache()
instead of datadir.save_entry() + a separate pythia.cache.upsert_entry() --
that's the one shared "persist this entry, keep cache.db in sync" primitive
add_game()/update_existing() use too, so there's a single code path for
writing a datadir entry, not two that could drift apart. The plugin lookups
(pythia.plugins.engine/library/metadata) are still lazily imported here
because that tree hasn't been ported onto this base yet -- this module
imports and its pure detection-logic functions work standalone, but preview()/
onboard() themselves aren't actually usable until plugins/ lands.
"""

import difflib
import re
import shutil
import uuid
from pathlib import Path

from pythia import datadir
from pythia.core import instance_marker
from pythia.database import games as games_db
from pythia.datadir import classify_variant_tokens
from pythia.identity import get_system_uuid, new_game_id

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
    rest = stripped[: m.start()] + " " + stripped[m.end() :]
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
    # path.name can be "" for a path a real directory listing would never produce
    # (e.g. a leading "//" parses as a UNC-style anchor with no separate name
    # component on Windows) -- not reachable through scanning.py's Path.iterdir()
    # walk, but reachable if a user hand-types a path to `pythia onboard`. Fall back
    # to the fully-resolved path string so the caller never receives an empty name
    # (2026-08-03, caught by the fuzz harness in tests/test_naming_fuzz.py).
    folder_name = path.name or str(path)

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
    system_uuid = get_system_uuid()
    return [e for e in datadir.list_all_entries() if e.get("system_uuid") == system_uuid]


def find_candidates(detection: dict, name_hint: str, path: Path):
    """Return {"certain": entry_or_None, "suggested": [entries]} -- plausible matches
    for `detection`.

    **Cross-system aware since 2026-08-03** (a real, previously-flagged foundational
    gap: this used to search only `_own_entries()`, so if System B already tracked a
    game, System A onboarding the same install independently would mint a duplicate
    game_id instead of recognizing System B's existing one -- defeating the whole
    point of a shared `datadir/`). External-ID and fuzzy-name matching now search
    every known system's entries (`datadir.list_all_entries()`), not just this
    system's own. Path matching stays own-only on purpose: an on-disk path is
    inherently system-local (different machines, different drive letters/mount
    points), so a path match from another system's file would be coincidence, not a
    real signal.

    **A `certain`/`suggested` entry may belong to a different system than this one.**
    Callers must never treat it as directly mutable/saveable -- see `onboard()`'s
    `_own_or_none()`/`_seed_from()` helpers, which turn a foreign match into a seed
    for a brand-new entry in *this* system's own file (same game_id, carried-over
    name/platform_ids/ownership) rather than ever writing to another system's file,
    which would violate the single-writer-per-file rule everything else in datadir/
    depends on."""
    own = _own_entries()
    all_entries = datadir.list_all_entries()
    path_str = str(Path(path).resolve())

    # Strongest possible signal: this exact on-disk path is already tracked under some
    # game *by this system*. Without this check, re-onboarding the same path (e.g.
    # re-running `scan`) would mint a brand new game every time instead of updating
    # the existing one.
    certain = None
    for entry in own:
        if any(
            variant.get("path") == path_str
            for group in entry.get("versions", [])
            for variant in group.get("variants", [])
        ):
            certain = entry
            break

    # Marker-file match (Milestone 9, 2026-08-04): real physical evidence this
    # exact folder was already onboarded by *another* system and synced here
    # by something outside Pythia (OneDrive, rsync, a NAS mount, ...) -- not a
    # guess from name similarity. Stronger than the external-ID check below,
    # since it's proof of the same synced bytes, not an inference from a
    # platform's own ID scheme. Absence of a marker is not negative evidence
    # (an install onboarded before this milestone, or hand-copied without
    # Pythia's involvement, simply has none yet) -- falls through to the
    # weaker tiers below exactly as before.
    if certain is None:
        marker = instance_marker.read_marker(path)
        if marker and marker.get("game_uuid"):
            certain = next((e for e in all_entries if e.get("game_uuid") == marker["game_uuid"]), None)

    external_id = detection.get("external_id")
    if certain is None and external_id is not None:
        kind = detection["kind"]
        # Cross-system, via platform_ids[] (2026-08-04 redesign -- game_id is now
        # always an internally-minted opaque ID, never a reused platform ID, so a
        # platform match has to look at platform_ids[] instead of comparing
        # game_id directly). Prefer this system's own record if it has one,
        # otherwise take the first match from any system -- if ANY system in the
        # sync group already claimed this external ID under some game_id, every
        # system should recognize and converge on it, not each independently mint
        # its own.
        matches = [
            e
            for e in all_entries
            if any(
                p.get("platform") == kind and str(p.get("id")) == str(external_id) for p in e.get("platform_ids", [])
            )
        ]
        this_uuid = get_system_uuid()
        certain = next((e for e in matches if e.get("system_uuid") == this_uuid), None) or (
            matches[0] if matches else None
        )

    suggested = []
    if certain is None:
        target = name_hint.lower()
        seen_game_ids = set()
        scored = []
        for entry in all_entries:
            gid = entry.get("game_id")
            if gid in seen_game_ids:
                continue  # one suggestion per game_id, not one per system's copy of it
            ratio = difflib.SequenceMatcher(None, target, entry.get("canonical_name", "").lower()).ratio()
            if ratio >= NAME_SIMILARITY_THRESHOLD:
                scored.append((ratio, entry))
                seen_game_ids.add(gid)
        scored.sort(key=lambda t: t[0], reverse=True)
        suggested = [e for _, e in scored]

    return {"certain": certain, "suggested": suggested}


def entry_id_source(entry: dict) -> str:
    return entry.get("game_id_source", "local")


def preview(path: Path):
    """Identify `path` and find candidate existing games, without writing anything.
    This is what cli.py/the Qt UI's backend.py call before asking the user for a
    decision. Returns {"status": "ok", "path", "detection", "name", "version_label",
    "candidates"} or {"status": "error", "message": ...}.
    """
    from pythia.plugins import engine as engine_plugins  # noqa: PLC0415
    from pythia.plugins.library import identify, identify_all  # noqa: PLC0415

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
    this_uuid = get_system_uuid()
    # A match `find_candidates()`/a `link` decision points at may belong to a
    # *different* system (2026-08-03 cross-system fix -- see find_candidates()'s
    # docstring). `existing_entry` below is only ever set when it's genuinely this
    # system's own record, safe to mutate and save directly; a foreign match instead
    # populates `foreign_seed`, used further down to seed a brand-new entry in this
    # system's own file under the *same* game_id -- never writing to another
    # system's file, which would violate the single-writer-per-file rule.
    foreign_seed = None

    if action == "new":
        game_id, id_source, existing_entry = _mint_new(detection)
    elif action == "link":
        game_id = decision["game_id"]
        id_source = "linked"
        existing_entry = _find_own_entry_by_game_id(game_id)
        if existing_entry is None:
            foreign_seed = _find_any_entry_by_game_id(game_id)
            if foreign_seed is None:
                return {"status": "error", "message": f"No existing tracked game with id {game_id} on any known system."}
    else:
        # Automatic: certain match -> link, otherwise new. Used by scan_library() and
        # any caller that doesn't want to prompt.
        certain = candidates["certain"]
        if certain is not None:
            game_id = certain["game_id"]
            id_source = entry_id_source(certain)
            if certain.get("system_uuid") == this_uuid:
                existing_entry = certain
            else:
                existing_entry = None
                foreign_seed = certain
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

    is_new_entry = existing_entry is None
    entry = existing_entry or {
        "system_uuid": this_uuid,
        "game_id": game_id,
        "game_id_source": id_source,
        # Milestone 9 (2026-08-04, game_uuid-only slice): a real UUID, distinct
        # from the collision-prone numeric game_id, that a marker-file match
        # (see pythia.core.instance_marker) can converge across systems even
        # when no external platform ID exists to converge on. Carried over
        # from a foreign_seed when linking to another system's already-known
        # game, never re-minted for an existing entry.
        "game_uuid": (foreign_seed or {}).get("game_uuid") or str(uuid.uuid4()),
        "canonical_name": (foreign_seed or {}).get("canonical_name") or name,
        # Cross-system seed carries over additively (2026-08-03) -- if another
        # system already knows this game's platform_ids/ownership, this system's
        # brand-new file starts with that context instead of losing it; still just
        # a starting point, not a live link back to the foreign entry.
        "platform_ids": list((foreign_seed or {}).get("platform_ids", [])),
        "ownership": list((foreign_seed or {}).get("ownership", [])),
        "managed_externally": False,
        "version_management_enabled": True,
        "notes": "",
        "versions": [],
        "available_updates": [],
    }
    entry["canonical_name"] = entry.get("canonical_name") or name
    entry["game_uuid"] = entry.get("game_uuid") or str(uuid.uuid4())  # lazy backfill for pre-Milestone-9 entries
    _update_platform_ids_and_ownership(entry, detection)
    for other in additional_sources:
        _update_platform_ids_and_ownership(entry, other)

    source = {"kind": detection["kind"], "extra": detection.get("extra", {})}
    is_new_variant_path = not any(
        variant.get("path") == str(path) for group in entry.get("versions", []) for variant in group.get("variants", [])
    )
    _merge_version(entry, path, version_label, mods, language, detection.get("platforms", []), source)
    instance_marker.write_marker(path, entry["game_uuid"], version_label, mods, language, this_uuid)

    if is_new_entry and foreign_seed is not None:
        datadir.add_timeline_event(
            entry, "linked_cross_system", f"{version_label or '?'} ({source['kind']}), matched another system's record"
        )
    elif is_new_entry:
        datadir.add_timeline_event(entry, "game_added", f"{version_label or '?'} ({source['kind']})")
    elif is_new_variant_path:
        datadir.add_timeline_event(entry, "version_added", f"{version_label or '?'} ({source['kind']})")

    saved_path = games_db.save_entry_and_sync_cache(entry)

    return {
        "status": "ok",
        "game_id": game_id,
        "candidates": candidates,
        "path": str(saved_path),  # the datadir JSON file this was recorded in
        "game_path": str(path),  # the actual game folder that was onboarded
        "name": entry["canonical_name"],
    }


def _mint_new(detection: dict):
    # 2026-08-04: every game gets a fresh, internally-minted ID -- no exceptions
    # for external-ID-sourced detections. A platform's own ID (Steam AppID, GOG
    # ID, ...) never becomes the internal game_id; it's recorded purely as
    # informational data in platform_ids[] via _update_platform_ids_and_ownership,
    # independently of this.
    return new_game_id(), "local", None


def _find_own_entry_by_game_id(game_id: str):
    for entry in _own_entries():
        if entry.get("game_id") == game_id:
            return entry
    return None


def _find_any_entry_by_game_id(game_id: str):
    """Cross-system lookup (2026-08-03, part of the same find_candidates() fix) --
    used when a `link` decision names a game_id this system doesn't have its own
    file for yet, but another system does. The caller must treat the result as a
    read-only seed, never mutate/save it directly (see onboard()'s own handling)."""
    for entry in datadir.list_all_entries():
        if entry.get("game_id") == game_id:
            return entry
    return None


def add_ownership(game_id: str, platform: str, note: str | None = None):
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

    games_db.save_entry_and_sync_cache(entry)

    return {"status": "ok", "game_id": game_id, "ownership": entry["ownership"]}


def _resolve_name_via_metadata(kind: str, detection: dict):
    """A real name-resolution fallback for services whose ownership listing has no
    name field at all (GOG's bare product IDs, see plugins/library/gog.py) -- tries
    every enabled MetadataPlugin willing to serve this platform (2026-08-03, closes
    a real, previously-stated fidelity gap). Best-effort: returns None (falls
    through to the existing "Unknown (...)" placeholder) if no metadata plugin has
    an answer, never raises."""
    id_key = datadir.PLATFORM_ID_KINDS.get(kind)
    platform_id_value = detection.get("extra", {}).get(id_key) if id_key else None
    if platform_id_value is None:
        return None

    from pythia.plugins import metadata as metadata_plugins  # noqa: PLC0415

    for plugin in metadata_plugins.find_for_platform(kind):
        try:
            name = plugin.resolve_name({"platform": kind, "id": platform_id_value})
        except Exception:
            continue
        if name:
            return name
    return None


def record_ownership(detection: dict, own: list[dict] | None = None) -> tuple[dict, bool]:
    """Fold one ownership detection dict (the shape `list_owned()` produces) into
    this system's tracked games -- the single-item primitive `sync_owned()` loops
    over, factored out (2026-08-03) so a single-thread add (F95's browser-extension
    RPC `/games/add` handler, real logic ported from F95Checker's own
    `callbacks.add_games()`) can reuse the exact same matching/creation logic as a
    bulk account sync, instead of duplicating it. `own` lets a caller doing several
    adds in a row pass the same already-loaded list through repeatedly rather than
    re-reading every datadir file per call; omit it for a one-off call.

    Returns (entry, created) -- `created` is False when this detection matched an
    already-tracked game (additive update only), True when a new entry was minted."""
    own = _own_entries() if own is None else own
    kind = detection["kind"]
    external_id = detection.get("external_id")
    # The real identifying value isn't always the numeric external_id -- a service
    # like Epic Games Store identifies a game by a string AppName, kept in
    # extra[PLATFORM_ID_KINDS[kind]], with external_id always None (2026-08-03,
    # caught before it could duplicate every EGS game on every re-sync: the
    # platform_ids fallback below used to be gated on `external_id is not None`,
    # which a string-identified service can never satisfy).
    id_key = datadir.PLATFORM_ID_KINDS.get(kind)
    string_id = detection.get("extra", {}).get(id_key) if id_key else None

    # 2026-08-04: game_id is never a reused platform ID anymore, so matching an
    # already-tracked game for this detection is *only* ever done via
    # platform_ids[] membership, regardless of whether the platform identifies by
    # a numeric external_id or a string_id.
    match_value = external_id if external_id is not None else string_id
    entry = None
    if match_value is not None:
        entry = next(
            (
                e
                for e in own
                if any(p.get("platform") == kind and str(p.get("id")) == str(match_value) for p in e.get("platform_ids", []))
            ),
            None,
        )

    created = entry is None
    if entry is None:
        name = detection.get("name") or _resolve_name_via_metadata(kind, detection)
        entry = {
            "system_uuid": get_system_uuid(),
            "game_id": new_game_id(),
            "game_id_source": "local",
            "canonical_name": name or f"Unknown ({kind} {external_id})",
            "platform_ids": [],
            "ownership": [],
            "managed_externally": False,
            "version_management_enabled": True,
            "notes": "",
            "versions": [],
            "available_updates": [],
        }
        own.append(entry)
        datadir.add_timeline_event(entry, "ownership_recorded", f"via {kind}")

    _update_platform_ids_and_ownership(entry, detection)
    games_db.save_entry_and_sync_cache(entry)

    return entry, created


def sync_owned(plugin) -> dict:
    """Pull `plugin.list_owned()` (an account-level ownership listing, independent of
    any local install -- e.g. ItchLibraryPlugin's real API-key-based itch.io pull,
    2026-08-03) and fold it into this system's tracked games via record_ownership().
    Returns {"status": "ok", "linked": N, "created": N} or an error if the plugin has
    no real list_owned() implementation."""
    owned = plugin.list_owned()
    if owned is None:
        return {"status": "error", "message": f"'{plugin.id}' has no account connected (or doesn't support list_owned())."}

    own = _own_entries()
    linked = 0
    created = 0
    for detection in owned:
        _entry, was_created = record_ownership(detection, own)
        if was_created:
            created += 1
        else:
            linked += 1

    return {"status": "ok", "linked": linked, "created": created}


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
            "id": new_game_id()[:12],
            "mods": mods,
            "language": language,
            "path": path_str,
            "platforms": platforms,
            "source": source,
            "detected_at": now,
        }
    )
