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

# Languages a folder name may carry, by name and code (UX review A14). Two-letter
# codes that are also common words ("it", "id", "no") are matched by name only.
LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "es": "Spanish",
    "ru": "Russian",
    "de": "German",
    "pt": "Portuguese",
    "fr": "French",
    "it": "Italian",
    "pl": "Polish",
    "tr": "Turkish",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "th": "Thai",
    "uk": "Ukrainian",
    "cs": "Czech",
    "nl": "Dutch",
    "ar": "Arabic",
    "hu": "Hungarian",
    "sv": "Swedish",
    "fi": "Finnish",
    "ro": "Romanian",
    "el": "Greek",
    "he": "Hebrew",
}
LANGUAGE_TOKENS = {
    **{name.lower(): code for code, name in LANGUAGE_NAMES.items()},
    "eng": "en",
    "en": "en",
    "jp": "ja",
    "jpn": "ja",
    "cn": "zh",
    "chs": "zh",
    "cht": "zh",
    "kr": "ko",
    "kor": "ko",
    "es": "es",
    "esp": "es",
    "spa": "es",
    "espanol": "es",
    "español": "es",
    "castellano": "es",
    "ru": "ru",
    "rus": "ru",
    "de": "de",
    "ger": "de",
    "deu": "de",
    "deutsch": "de",
    "pt": "pt",
    "ptbr": "pt",
    "por": "pt",
    "brazilian": "pt",
    "portugues": "pt",
    "português": "pt",
    "fr": "fr",
    "fra": "fr",
    "fre": "fr",
    "francais": "fr",
    "français": "fr",
    "ita": "it",
    "italiano": "it",
    "pl": "pl",
    "pol": "pl",
    "polski": "pl",
    "tr": "tr",
    "tur": "tr",
    "vi": "vi",
    "vn": "vi",
    "ukr": "uk",
    "cz": "cs",
    "cze": "cs",
    "nl": "nl",
}
# The kind of folder a word names (translation, patch, mod), checked in this order.
KIND_WORDS = {
    "translation": ("translation", "translated", "translate", "tl", "localization", "localisation"),
    "patch": ("patch", "patched", "fix", "hotfix", "bugfix"),
    "mod": ("mod", "mods", "modded", "modpack"),
}
KINDS = ("game", "mod", "patch", "translation")
# Words that are a fact about the folder, not a mod: machine translation only from
# "mtl" or "machine", and uncensoring.
FLAG_WORDS = {
    "mtl": "mtl",
    "machine": "mtl",
    "uncensored": "uncensored",
    "uncensor": "uncensored",
    "uncen": "uncensored",
    "unc": "uncensored",
}
# Release-platform words in a folder name: the engine plugin knows the platform; they are not mods.
PLATFORM_WORDS = {"pc", "win", "windows", "win64", "win32", "linux", "mac", "macos", "osx", "android", "x64", "x86"}


def classify_folder_tokens(rest: str) -> dict:
    """What a folder name says after its title and version: ``mods`` (each
    ``{"name": ..., "version": None}``; a name never tells a mod's version),
    ``language`` (a code, or None), ``kind`` (game, mod, patch or
    translation) and ``flags`` (mtl, uncensored). Brackets are stripped; kind,
    flag and platform words are never mods (UX review A14). Best-effort -- shared
    by onboarding's naming heuristic and the v1->v2 migration."""
    mods: list[dict] = []
    language = None
    kinds: set[str] = set()
    flags: list[str] = []
    text = re.sub(r"[\[\](){}]", " ", rest)
    for token in re.split(r"[-_ +,]+", text.strip(" -_")):
        if not token or not any(c.isalnum() for c in token):
            continue  # skip empty/punctuation-only leftovers (e.g. a bare "+")
        word = token.lower().strip(".")
        if word in PLATFORM_WORDS:
            continue
        kind = next((k for k, words in KIND_WORDS.items() if word in words), None)
        if kind:
            kinds.add(kind)
            continue
        flag = FLAG_WORDS.get(word)
        if flag:
            if flag not in flags:
                flags.append(flag)
            if flag == "mtl":
                kinds.add("translation")
            continue
        canonical = LANGUAGE_TOKENS.get(word)
        if canonical and language is None:
            language = canonical
            continue
        mods.append({"name": token, "version": None})
    kind = next((k for k in ("translation", "patch", "mod") if k in kinds), "game")
    return {"mods": mods, "language": language, "kind": kind, "flags": flags}


def classify_variant_tokens(rest: str):
    """classify_folder_tokens' mods and language, for callers that want only those."""
    found = classify_folder_tokens(rest)
    return found["mods"], found["language"]


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
    id_key = PLATFORM_ID_KINDS.get(kind) if kind else None
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


def load_entry(path: Path) -> dict:
    entry = json.loads(path.read_text())
    version = entry.get("schema_version", 1)
    if version < 2:
        entry = _migrate_v1_to_v2(entry)
        version = 2
    if version < 3:
        entry = _migrate_v2_to_v3(entry)
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


def list_known_games() -> dict[str, list[dict]]:
    """Group every datadir entry by game_id -- the merged, cross-system view of the
    whole library: which systems have which versions of which games, where."""
    games: dict[str, list[dict]] = {}
    for entry in list_all_entries():
        games.setdefault(entry["game_id"], []).append(entry)
    return games
