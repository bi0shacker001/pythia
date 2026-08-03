"""
Read/write access to datadir/ -- the synced, single-writer-per-file metadata store.

Filename convention: {system-uuid}-{game_id}-{sanitized-canonical-name}.json
A system may only ever write files whose name starts with its own UUID; files from
other UUIDs are read-only synced data written by other systems. This is the whole
sync-safety model -- see the Milestone 1 plan for why.
"""

import datetime
import json
import re
from pathlib import Path

from pythia.identity import datadir_path

SCHEMA_VERSION = 3

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


def _filename(system_uuid: str, game_id: int, canonical_name: str) -> str:
    return f"{system_uuid}-{game_id}-{sanitize_name(canonical_name)}.json"


def find_entry_file(system_uuid: str, game_id: int) -> Path | None:
    """Locate this identity's existing datadir file, if any, regardless of what its
    sanitized-name portion currently says (the name can drift between onboard runs
    as detection improves; identity is (system_uuid, game_id), not the filename)."""
    d = datadir_path()
    if not d.is_dir():
        return None
    prefix = f"{system_uuid}-{game_id}-"
    matches = sorted(d.glob(f"{prefix}*.json"))
    return matches[0] if matches else None


def load_entry(path: Path) -> dict:
    entry = json.loads(path.read_text())
    version = entry.get("schema_version", 1)
    if version < 2:
        entry = _migrate_v1_to_v2(entry)
        version = 2
    if version < 3:
        entry = _migrate_v2_to_v3(entry)
    return entry


def save_entry(entry: dict) -> Path:
    """Write an entry, replacing any prior file for the same (system_uuid, game_id)
    even if its sanitized-name portion changed since the last write."""
    d = datadir_path()
    d.mkdir(parents=True, exist_ok=True)

    entry["schema_version"] = SCHEMA_VERSION
    entry["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    old_path = find_entry_file(entry["system_uuid"], entry["game_id"])
    new_filename = _filename(entry["system_uuid"], entry["game_id"], entry["canonical_name"])
    new_path = d / new_filename

    new_path.write_text(json.dumps(entry, indent=2))

    if old_path is not None and old_path != new_path:
        try:
            old_path.unlink()
        except OSError:
            pass

    return new_path


def list_all_entries() -> list[dict]:
    """Every entry in datadir/ -- this system's own writes plus every other synced UUID's."""
    d = datadir_path()
    if not d.is_dir():
        return []
    entries = []
    for jsonfile in sorted(d.glob("*.json")):
        try:
            entries.append(load_entry(jsonfile))
        except (OSError, ValueError):
            continue
    return entries


def list_known_games() -> dict[int, list[dict]]:
    """Group every datadir entry by game_id -- the merged, cross-system view of the
    whole library: which systems have which versions of which games, where."""
    games: dict[int, list[dict]] = {}
    for entry in list_all_entries():
        games.setdefault(entry["game_id"], []).append(entry)
    return games
