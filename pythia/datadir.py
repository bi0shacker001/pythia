"""
Read/write access to datadir/ -- the synced, single-writer-per-file metadata store.

Filename convention: {system-uuid}-{game_id}-{sanitized-canonical-name}.json
A system may only ever write files whose name starts with its own UUID; files from
other UUIDs are read-only synced data written by other systems. This is the whole
sync-safety model -- see the Milestone 1 plan for why.
"""

import datetime
import json
import logging
import re
import uuid
from pathlib import Path

from pythia.identity import datadir_path, get_system_uuid

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 4

# Which library `kind`s carry a real cross-platform ID worth recording in a game's
# top-level `platform_ids[]`/`ownership[]` (2026-08-03) -- an engine kind like
# "renpy"/"unity"/"unknown" is an *identification* of what the install is, not an
# account/store identity, so those never seed either list. Deliberately two separate
# lists, not one: `platform_ids` is "this game is known as X on platform Y" (useful
# for pulling metadata from that platform's public API even if the user doesn't own
# it there); `ownership` is "the user actually has a claim to it on platform Y"
# (seeded here from an install actually existing, which is strong evidence of
# ownership, but conceptually a different fact -- e.g. Patreon-subscribed but
# f95-downloaded is ownership without a platform_id, and a game's Steam AppID could
# be known/recorded for metadata purposes without the user owning it on Steam).
PLATFORM_ID_KINDS = {
    "steam": "steam_appid",
    "itch": "itch_game_id",
    "gog": "gog_id",
    "egs": "egs_app_name",
    "f95": "f95_thread_id",
}

LANGUAGE_TOKENS = {
    "eng": "en", "en": "en", "english": "en",
    "jp": "ja", "jpn": "ja", "japanese": "ja",
    "cn": "zh", "chs": "zh", "cht": "zh", "chinese": "zh",
    "kr": "ko", "kor": "ko", "korean": "ko",
    "mtl": "mtl", "machine-translated": "mtl", "machinetranslated": "mtl", "translated": "mtl",
}


def classify_variant_tokens(rest: str):
    """Split a trailing string (after name+version have been stripped) into a mods
    list (each `{"name": ..., "version": None}` -- naming alone can never tell us a
    mod's version) and an optional language code, using a small fixed set of language
    tokens. Best-effort -- shared by onboarding's naming heuristic and the v1->v2
    migration."""
    mods = []
    language = None
    for token in re.split(r"[-_ ]+", rest.strip(" -_")):
        if not token or not any(c.isalnum() for c in token):
            continue  # skip empty/punctuation-only leftovers (e.g. a bare "+")
        canonical = LANGUAGE_TOKENS.get(token.lower())
        if canonical and language is None:
            language = canonical
        else:
            mods.append({"name": token, "version": None})
    return mods, language


def _split_version_label(label: str):
    """Split an old schema-v1 version 'label' (e.g. '10.0-sancho') into
    (version, mods, language) -- no leading game-name prefix to strip here, unlike
    onboarding's full folder-name splitter."""
    m = re.match(r"^v?(\d+(?:\.\d+)*[a-zA-Z]*)(.*)$", label)
    if not m:
        return label, [], None
    version = m.group(1)
    mods, language = classify_variant_tokens(m.group(2))
    return version, mods, language


def _migrate_v1_to_v2(entry: dict) -> dict:
    """Old flat versions[] (one label+path per install) -> grouped
    version -> variants[] (mods/language split out of the old label)."""
    grouped: dict[str, dict] = {}
    for old in entry.get("versions", []):
        label = old.get("label", "")
        version, mods, language = _split_version_label(label)
        group = grouped.setdefault(version, {"version": version, "variants": []})
        group["variants"].append(
            {
                "mods": mods,
                "language": language,
                "path": old.get("path"),
                "platforms": old.get("platforms", []),
                "detected_at": old.get("detected_at"),
            }
        )

    entry["versions"] = list(grouped.values())
    entry.setdefault("available_updates", [])
    entry.setdefault("notes", "")
    entry["schema_version"] = SCHEMA_VERSION
    return entry


def _migrate_v2_to_v3(entry: dict) -> dict:
    """Old single top-level `source` (2026-08-03: unconditionally overwritten on
    every re-onboard, unable to represent a game owned/gettable via more than one
    place -- e.g. Steam+Epic, or Steam-owned but f95-downloaded) -> per-variant
    `source` (what each specific on-disk install actually is) plus top-level
    `platform_ids[]`/`ownership[]` (see PLATFORM_ID_KINDS above for what each
    means)."""
    old_source = entry.pop("source", {}) or {}
    kind = old_source.get("kind")
    extra = {k: v for k, v in old_source.items() if k != "kind"}

    for group in entry.get("versions", []):
        for variant in group.get("variants", []):
            variant.setdefault("source", {"kind": kind, "extra": extra})

    platform_ids = entry.setdefault("platform_ids", [])
    id_key = PLATFORM_ID_KINDS.get(kind)
    if id_key and extra.get(id_key) and not any(p.get("platform") == kind for p in platform_ids):
        platform_ids.append({"platform": kind, "id": extra[id_key]})

    ownership = entry.setdefault("ownership", [])
    if kind in PLATFORM_ID_KINDS and not any(o.get("platform") == kind for o in ownership):
        ownership.append({"platform": kind})

    entry["schema_version"] = SCHEMA_VERSION
    return entry


def sanitize_name(name: str) -> str:
    """Turn a display name into a filesystem- and filename-safe token."""
    name = name.strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "unnamed"


def _filename(system_uuid: str, game_id: str, canonical_name: str) -> str:
    return f"{system_uuid}-{game_id}-{sanitize_name(canonical_name)}.json"


def find_entry_file(system_uuid: str, game_id: str) -> Path | None:
    """Locate this identity's existing datadir file, if any, regardless of what its
    sanitized-name portion currently says (the name can drift between onboard runs
    as detection improves; identity is (system_uuid, game_id), not the filename)."""
    d = datadir_path()
    if not d.is_dir():
        return None
    prefix = f"{system_uuid}-{game_id}-"
    matches = sorted(d.glob(f"{prefix}*.json"))
    return matches[0] if matches else None


def _ensure_variant_ids(entry: dict) -> bool:
    """Every variant needs a real, stable identity (2026-08-07, external UX
    review: "Stable instance identity" -- the action bar's instance selector
    was previously purely decorative, with nothing durable to actually
    select). Additive-only, no schema_version bump needed (same pattern as
    add_timeline_event's own note) -- an older entry with no variant "id"
    just gets one assigned here. Returns whether anything was actually
    added, so the caller can decide whether a resave is warranted."""
    changed = False
    for group in entry.get("versions", []):
        for variant in group.get("variants", []):
            if not variant.get("id"):
                variant["id"] = str(uuid.uuid4())
                changed = True
    return changed


def load_entry(path: Path) -> dict:
    entry = json.loads(path.read_text(encoding="utf-8"))
    version = entry.get("schema_version", 1)
    if version < 2:
        entry = _migrate_v1_to_v2(entry)
        version = 2
    if version < 3:
        entry = _migrate_v2_to_v3(entry)

    if _ensure_variant_ids(entry):
        # Only resave a file this system actually owns -- writing to another
        # system's synced entry would violate the single-writer-per-file
        # model this whole module is built on (see module docstring). A
        # peer system's own entries get ids assigned in-memory for this read
        # only, made durable once that system does the same load+resave.
        if entry.get("system_uuid") == get_system_uuid():
            save_entry(entry)

    return entry


def add_timeline_event(entry: dict, event_type: str, detail: str = ""):
    """Append a real, timestamped event to this entry's own history log
    (2026-08-03) -- closes a real gap the desktop UI's History tab had been openly
    showing as "not built yet." Directly inspired by F95Checker's own real
    `timeline_events` table (confirmed structure -- game_id, timestamp, type,
    arguments -- inspected against the user's real, copied db.sqlite3 while
    building the F95 import), not invented from nothing. Additive only: an older
    entry with no `timeline_events` key gets one created on first use, no schema
    migration needed. Caller is responsible for calling save_entry() afterward --
    this only mutates the in-memory dict."""
    events = entry.setdefault("timeline_events", [])
    events.append(
        {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "type": event_type,
            "detail": detail,
        }
    )


def _write_entry_file(entry: dict) -> Path:
    """Low-level write shared by save_entry() (this system's own writes) and
    save_mirrored_entry() (a peer's writes, mirrored locally) -- replacing
    any prior file for the same (system_uuid, game_id) even if its
    sanitized-name portion changed since the last write. Callers are
    responsible for setting sync_revision themselves before calling this;
    it never touches that field."""
    d = datadir_path()
    d.mkdir(parents=True, exist_ok=True)

    entry["schema_version"] = SCHEMA_VERSION
    entry["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    old_path = find_entry_file(entry["system_uuid"], entry["game_id"])
    new_filename = _filename(entry["system_uuid"], entry["game_id"], entry["canonical_name"])
    new_path = d / new_filename

    new_path.write_text(json.dumps(entry, indent=2), encoding="utf-8")

    if old_path is not None and old_path != new_path:
        try:
            old_path.unlink()
        except OSError:
            pass

    return new_path


def save_entry(entry: dict) -> Path:
    """Write this system's OWN entry. Real, monotonically-increasing
    per-entry counter (2026-08-08, "optional core sync service and native
    device/transfer workflow" -- "Add real revision/tombstone persistence if
    missing"). Additive-only field, same "an older entry without it just
    starts at 0" rule add_timeline_event()'s own docstring already
    established. pythia.core.sync.metadata's equal-revision-conflict
    detection depends on this actually incrementing on every real write, not
    staying pinned at a fake constant.

    Never use this for a peer's mirrored entry -- it always increments,
    which would silently discard the peer's own authoritative revision.
    Use save_mirrored_entry() for that (2026-08-08, Codex sync review:
    "correctness repair required" -- the original mirroring path called this
    for received records too, which re-incremented a peer's revision on our
    own local counter, corrupting future reconciliation)."""
    entry["sync_revision"] = int(entry.get("sync_revision", 0)) + 1
    return _write_entry_file(entry)


def save_mirrored_entry(payload: dict, *, revision: int) -> Path:
    """Persist a peer-owned entry exactly as reconciled -- pins
    `sync_revision` to the peer's own real, authoritative revision (the one
    that arrived over the wire) instead of incrementing our own local
    counter. This is what makes a later, older/stale push from the same
    peer correctly recognized as stale on the next reconciliation, including
    after a restart (see list_mirrored_records())."""
    entry = dict(payload)
    entry["sync_revision"] = int(revision)
    return _write_entry_file(entry)


def find_variant(entry: dict, variant_id: str) -> dict | None:
    for group in entry.get("versions", []):
        for variant in group.get("variants", []):
            if variant.get("id") == variant_id:
                return variant
    return None


# Real, persisted launch-configuration fields a variant can carry (2026-08-07,
# external UX review, Phase 1 "restore semantic trust": the Configure dialog's
# Executable/Arguments/Working directory fields previously had NO backing
# schema at all -- Save just cleared the modal and showed a toast, an outright
# lie, "the interface says a choice was saved... [when it wasn't]"). All
# optional/nullable -- an unset field means "not explicitly configured,"
# launch.py falls back to its own auto-detection (find_primary_executable) for
# any field the user hasn't set, same "declared data overrides inference"
# shape as everywhere else in this project.
VARIANT_LAUNCH_FIELDS = ("executable", "args", "working_dir")


def update_variant_launch_config(system_uuid: str, game_id: str, variant_id: str, **fields) -> dict:
    """Persist real launch-config overrides for one variant -- only
    `system_uuid`'s own entry file may be written (single-writer-per-file,
    same rule as everywhere else in this module). `fields` may set any of
    VARIANT_LAUNCH_FIELDS to a value, or to None to clear an override back to
    "not configured, use auto-detection." Returns {"status": "ok"} or
    {"status": "error", "message"} -- never raises for an ordinary not-found
    case, so a caller (a UI Slot) can show a real, specific failure instead of
    a blanket success."""
    unknown = set(fields) - set(VARIANT_LAUNCH_FIELDS)
    if unknown:
        return {"status": "error", "message": f"Unknown launch config field(s): {sorted(unknown)}"}

    path = find_entry_file(system_uuid, game_id)
    if path is None:
        return {"status": "error", "message": f"No entry found for game {game_id!r} on this system."}

    entry = load_entry(path)
    variant = find_variant(entry, variant_id)
    if variant is None:
        return {"status": "error", "message": f"No variant {variant_id!r} found for game {game_id!r}."}

    for key, value in fields.items():
        if value is None:
            variant.pop(key, None)
        else:
            variant[key] = value

    add_timeline_event(entry, "variant_configured", f"Updated launch configuration for variant {variant_id}")
    save_entry(entry)
    return {"status": "ok"}


def _tombstones_path() -> Path:
    return datadir_path().parent / "sync_tombstones.json"


def _load_tombstones() -> list[dict]:
    path = _tombstones_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def list_tombstones() -> list[dict]:
    """This system's own record of entries it has actually deleted --
    {system_uuid, game_id, revision, deleted_at} -- so a metadata push to a
    peer (see pythia.ui.sync_bridge.sync_metadata_with_device) can propagate
    a real deletion, not just current entries. Only ever contains this
    system's own deletions (single-writer-per-file rule applies here too)."""
    return list(_load_tombstones())


def remove_entry(system_uuid: str, game_id: str) -> bool:
    """Delete this system's own tracked entry for `game_id` -- respects the
    same single-writer-per-file rule as save_entry(): only ever deletes a
    file that belongs to `system_uuid`, never another system's synced copy
    of the same game_id (that system will still have its own real record;
    deleting a foreign file here would violate the whole sync-safety model
    this module's docstring describes). Returns whether a file was actually
    deleted. Records a real, persisted tombstone (2026-08-08, "Add real
    revision/tombstone persistence if missing") only for this system's own
    entries -- a peer's mirrored copy of a foreign game being removed here
    is a local decision to stop tracking it, not a real deletion of that
    system's own record, so no tombstone is recorded for a foreign entry."""
    path = find_entry_file(system_uuid, game_id)
    if path is None:
        return False
    entry = load_entry(path) if system_uuid == get_system_uuid() else None
    try:
        path.unlink()
    except OSError:
        return False
    if entry is not None:
        tombstones = _load_tombstones()
        tombstones.append(
            {
                "system_uuid": system_uuid,
                "game_id": game_id,
                "revision": int(entry.get("sync_revision", 0)) + 1,
                "deleted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        )
        _tombstones_path().parent.mkdir(parents=True, exist_ok=True)
        _tombstones_path().write_text(json.dumps(tombstones, indent=2), encoding="utf-8")
    return True


def list_all_entries() -> list[dict]:
    """Every entry in datadir/ -- this system's own writes plus every other synced UUID's."""
    d = datadir_path()
    if not d.is_dir():
        return []
    entries = []
    for jsonfile in sorted(d.glob("*.json")):
        try:
            entries.append(load_entry(jsonfile))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable datadir entry %s: %s", jsonfile, exc)
            continue
    return entries


def _mirrored_tombstones_path() -> Path:
    return datadir_path().parent / "sync_mirrored_tombstones.json"


def _load_mirrored_tombstones() -> list[dict]:
    path = _mirrored_tombstones_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def list_mirrored_tombstones(owner_system_uuid: str | None = None) -> list[dict]:
    """This system's persisted record of a PEER's own deletions
    (2026-08-08, Codex sync review: "remote tombstones are not persisted,
    allowing stale records to resurrect after deletion/restart"). Kept
    separate from list_tombstones() (this system's OWN deletions, republished
    to peers) -- a mirrored tombstone is never republished as ours; it exists
    only so a later, older/stale push of the same key from that peer is
    correctly recognized as stale by metadata.reconcile(), including after a
    restart."""
    items = _load_mirrored_tombstones()
    if owner_system_uuid is not None:
        items = [t for t in items if t.get("system_uuid") == owner_system_uuid]
    return items


def apply_mirrored_tombstone(owner_system_uuid: str, game_id: str, revision: int, deleted_at) -> None:
    """Real deletion of a peer-owned mirrored entry, with the revision it
    was deleted at persisted so a later, older/stale push for the same key
    is correctly rejected on the next reconciliation -- even after a
    restart. Only ever operates on `owner_system_uuid`'s own file, same
    single-writer-per-file rule as everywhere else in this module."""
    path = find_entry_file(owner_system_uuid, game_id)
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass
    tombstones = [
        t
        for t in _load_mirrored_tombstones()
        if not (t.get("system_uuid") == owner_system_uuid and t.get("game_id") == game_id)
    ]
    tombstones.append(
        {"system_uuid": owner_system_uuid, "game_id": game_id, "revision": int(revision), "deleted_at": deleted_at}
    )
    _mirrored_tombstones_path().parent.mkdir(parents=True, exist_ok=True)
    _mirrored_tombstones_path().write_text(json.dumps(tombstones, indent=2), encoding="utf-8")


def list_mirrored_records(owner_system_uuid: str) -> list[dict]:
    """Every currently-mirrored entry actually owned by `owner_system_uuid`,
    with its real, pinned `sync_revision` -- the missing piece from the
    original metadata-sync delivery (2026-08-08, Codex sync review): without
    this, a receiver's reconciliation state never included what it had
    already mirrored from that same peer, so `metadata.reconcile()` always
    saw an incoming key as brand new regardless of its real revision."""
    return [entry for entry in list_all_entries() if entry.get("system_uuid") == owner_system_uuid]


def list_known_games() -> dict[int, list[dict]]:
    """Group every datadir entry by game_id -- the merged, cross-system view of the
    whole library: which systems have which versions of which games, where."""
    games: dict[int, list[dict]] = {}
    for entry in list_all_entries():
        games.setdefault(entry["game_id"], []).append(entry)
    return games
