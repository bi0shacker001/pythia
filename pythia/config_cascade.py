"""Real global -> game -> instance configuration cascade (2026-08-07,
Codex's "Universal Game Configuration and Compatibility System" proposal:
"Configuration follows the fixed cascade: global -> game -> instance. Every
effective value records its source, validation state, and reset target.").

Three real storage tiers, in override-precedence order:
  - instance: `variant["config"][option_key]` in this game's datadir entry
    (a NEW, generic sibling to the specific `executable`/`args`/
    `working_dir` fields Phase 1 already built into
    datadir.VARIANT_LAUNCH_FIELDS -- those stay as-is, already real and
    tested; this is for every OTHER option key the compat system needs).
  - game: `entry["config"][option_key]` in the same datadir entry.
  - global: `identity.get_setting(f"cfg_global:{option_key}")` -- local.db,
    not synced (matches how every other local UI preference is stored).

get_effective() is the one function every "what value should Play/the UI
actually use" question goes through -- it always reports which scope the
value actually came from (`source_scope`), never just a bare value, so the
UI can show real inheritance instead of guessing.
"""

import json

from pythia import datadir
from pythia.identity import get_setting, get_system_uuid, set_setting

_GLOBAL_KEY_PREFIX = "cfg_global:"


def get_global(option_key: str, default=None):
    raw = get_setting(_GLOBAL_KEY_PREFIX + option_key, None)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def set_global(option_key: str, value) -> None:
    set_setting(_GLOBAL_KEY_PREFIX + option_key, json.dumps(value))


def reset_global(option_key: str) -> None:
    set_setting(_GLOBAL_KEY_PREFIX + option_key, "null")


def _own_entry(game_id) -> dict | None:
    """This system's own datadir entry for `game_id` -- game/instance scope
    writes only ever touch this system's own file (same single-writer rule
    as everywhere else in datadir.py)."""
    path = datadir.find_entry_file(get_system_uuid(), game_id)
    if path is None:
        return None
    return datadir.load_entry(path)


def get_game(game_id, option_key: str, default=None):
    entry = _own_entry(game_id)
    if entry is None:
        return default
    return entry.get("config", {}).get(option_key, default)


def set_game(game_id, option_key: str, value) -> dict:
    entry = _own_entry(game_id)
    if entry is None:
        return {"status": "error", "message": f"No entry found for game {game_id!r} on this system."}
    config = entry.setdefault("config", {})
    if value is None:
        config.pop(option_key, None)
    else:
        config[option_key] = value
    datadir.add_timeline_event(entry, "game_configured", f"Updated game-wide config: {option_key}")
    datadir.save_entry(entry)
    return {"status": "ok"}


def get_instance(game_id, variant_id: str, option_key: str, default=None):
    entry = _own_entry(game_id)
    if entry is None:
        return default
    variant = datadir.find_variant(entry, variant_id)
    if variant is None:
        return default
    return variant.get("config", {}).get(option_key, default)


def set_instance(game_id, variant_id: str, option_key: str, value) -> dict:
    entry = _own_entry(game_id)
    if entry is None:
        return {"status": "error", "message": f"No entry found for game {game_id!r} on this system."}
    variant = datadir.find_variant(entry, variant_id)
    if variant is None:
        return {"status": "error", "message": f"No variant {variant_id!r} found for game {game_id!r}."}
    config = variant.setdefault("config", {})
    if value is None:
        config.pop(option_key, None)
    else:
        config[option_key] = value
    datadir.add_timeline_event(entry, "instance_configured", f"Updated instance config: {option_key}")
    datadir.save_entry(entry)
    return {"status": "ok"}


def get_effective(option_key: str, game_id=None, variant_id: str | None = None, default=None) -> dict:
    """{"value", "source_scope", "raw": {"global", "game", "instance"}} --
    the one real source of truth for "what value should actually be used,"
    reporting exactly which scope it came from rather than a bare value
    (2026-08-07: "Every effective value records its source" -- this is what
    lets the Configure UI show real "Inherited from Game" / "Instance
    override" labels instead of guessing)."""
    raw_instance = None
    raw_game = None
    if game_id is not None:
        raw_game = get_game(game_id, option_key, None)
        if variant_id:
            raw_instance = get_instance(game_id, variant_id, option_key, None)
    raw_global = get_global(option_key, None)

    if raw_instance is not None:
        return {"value": raw_instance, "source_scope": "instance", "raw": {"global": raw_global, "game": raw_game, "instance": raw_instance}}
    if raw_game is not None:
        return {"value": raw_game, "source_scope": "game", "raw": {"global": raw_global, "game": raw_game, "instance": raw_instance}}
    if raw_global is not None:
        return {"value": raw_global, "source_scope": "global", "raw": {"global": raw_global, "game": raw_game, "instance": raw_instance}}
    return {"value": default, "source_scope": "default", "raw": {"global": raw_global, "game": raw_game, "instance": raw_instance}}
