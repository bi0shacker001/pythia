"""Games/variants query layer -- 2026-08-04 datadir rewrite.

datadir/*.json (see pythia.datadir) is the real, authoritative, sync-safe
store now -- not pga.db. This module keeps the exact function names/signatures
Lutris's game.py/services/*.py/installer/*.py/runners/*.py already call
(get_games, get_game_by_field, add_game, update_existing, delete_game, ...),
backed by cache.db (a rebuildable SQL index over datadir, using the same
storage-agnostic sql.py query engine pga.db used) instead of an authoritative
table. Every write here also writes through to datadir via
pythia.datadir.save_entry(), which is what actually persists/syncs;
rebuild_cache() reconstructs cache.db from datadir/*.json from scratch (call
once at startup, or any time cache.db is deleted -- it's derived, safe to
lose).

Identity model (2026-08-04, real user decision, not the Lutris default):
a game_id (16-char hex, pythia.identity.new_game_id()) replaces Lutris's
autoincrement int `id` one-for-one as a *game*'s identity -- "games" is the
external, one-row-per-logical-game view. Multiple *variants* (what a flat
Lutris row used to mean in full: one specific runner/directory/executable/
configpath install) can share a game_id -- that's how multiple versions of
the same game relate, which plain Lutris never modeled at all.
"""

import json
import time
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, TypeAlias

from pythia import datadir
from pythia.database import sql
from pythia.identity import get_system_uuid, new_game_id
from pythia.util.strings import slugify

DbGameDict: TypeAlias = dict[str, Any]

# Variant fields that came from a flat Lutris "games" row -- when a caller
# queries/updates by one of these, it means a specific install, not the
# owning game. See _split_game_and_variant_fields().
_VARIANT_FIELDS = {
    "version_label",
    "mods",
    "language",
    "platform",
    "runner",
    "executable",
    "directory",
    "configpath",
    "lastplayed",
    "installed",
    "installed_at",
    "detected_at",
    "year",
    "has_custom_banner",
    "has_custom_icon",
    "has_custom_coverart_big",
    "playtime",
    "service",
    "service_id",
}
_GAME_FIELDS = {
    "id",
    "system_uuid",
    "name",
    "sortname",
    "slug",
    "installer_slug",
    "parent_slug",
    "notes",
    "platform_ids",
    "ownership",
    "updated",
    "discord_id",
}


def _split_game_and_variant_fields(params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    game_fields: dict[str, Any] = {}
    variant_fields: dict[str, Any] = {}
    for key, value in params.items():
        if key in _VARIANT_FIELDS:
            variant_fields[key] = value
        else:
            game_fields[key] = value
    return game_fields, variant_fields


# ---- datadir entry <-> cache.db row shape ----------------------------------


def _entry_path(game_id: str, system_uuid: str | None = None) -> Any:
    return datadir.find_entry_file(system_uuid or get_system_uuid(), game_id)


def _load_entry(game_id: str, system_uuid: str | None = None) -> dict[str, Any] | None:
    path = _entry_path(game_id, system_uuid)
    if path is None:
        return None
    return datadir.load_entry(path)


def _new_entry(game_id: str, name: str, slug: str) -> dict[str, Any]:
    return {
        "system_uuid": get_system_uuid(),
        "game_id": game_id,
        "canonical_name": name,
        "sortname": "",
        "slug": slug,
        "installer_slug": "",
        "parent_slug": "",
        "notes": "",
        "discord_id": None,
        "platform_ids": [],
        "ownership": [],
        "versions": [],
    }


def _iter_variants(entry: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Flattens the real datadir shape (versions[] groups, each with its own
    variants[]) into variant dicts -- the "versions" wrapper groups multiple
    variants that share the same label (different mods/language/platform of
    the very same version); the SQL index and the merged-row view below both
    work with the flat form, so each yielded variant is a shallow copy with
    its group's label injected as "version_label" rather than staying nested."""
    for group in entry.get("versions") or []:
        for variant in group.get("variants") or []:
            flat = dict(variant)
            flat["version_label"] = group.get("version")
            yield flat


def _find_or_create_version_group(entry: dict[str, Any], version_label: str | None) -> dict[str, Any]:
    versions = entry.setdefault("versions", [])
    group = next((g for g in versions if g.get("version") == version_label), None)
    if group is None:
        group = {"version": version_label, "variants": []}
        versions.append(group)
    return group


def _upsert_cache_from_entry(entry: dict[str, Any]) -> None:
    """Write (or overwrite) this entry's game row + all its variant rows in
    cache.db. Safe to call repeatedly -- deletes this game_id's old variant
    rows first so a variant removed from the entry disappears from the index
    too."""
    from pythia import settings

    game_id = entry["game_id"]
    system_uuid = entry["system_uuid"]

    with sql.db_cursor(settings.DB_PATH) as cursor:
        sql.cursor_execute(
            cursor,
            "INSERT INTO games (id, system_uuid, name, sortname, slug, installer_slug, parent_slug, notes, "
            "platform_ids, ownership, updated, discord_id, game_uuid, game_id_source, managed_externally, "
            "version_management_enabled, available_updates) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET system_uuid=excluded.system_uuid, name=excluded.name, "
            "sortname=excluded.sortname, slug=excluded.slug, installer_slug=excluded.installer_slug, "
            "parent_slug=excluded.parent_slug, notes=excluded.notes, platform_ids=excluded.platform_ids, "
            "ownership=excluded.ownership, updated=excluded.updated, discord_id=excluded.discord_id, "
            "game_uuid=excluded.game_uuid, game_id_source=excluded.game_id_source, "
            "managed_externally=excluded.managed_externally, "
            "version_management_enabled=excluded.version_management_enabled, "
            "available_updates=excluded.available_updates",
            (
                game_id,
                system_uuid,
                entry.get("canonical_name"),
                entry.get("sortname") or "",
                entry.get("slug") or "",
                entry.get("installer_slug") or "",
                entry.get("parent_slug") or "",
                entry.get("notes") or "",
                json.dumps(entry.get("platform_ids") or []),
                json.dumps(entry.get("ownership") or []),
                entry.get("updated_at"),
                entry.get("discord_id"),
                entry.get("game_uuid"),
                entry.get("game_id_source"),
                entry.get("managed_externally"),
                entry.get("version_management_enabled"),
                json.dumps(entry.get("available_updates") or []),
            ),
        )
        sql.cursor_execute(cursor, "DELETE FROM variants WHERE game_id = ?", (game_id,))
        for variant in _iter_variants(entry):
            sql.cursor_execute(
                cursor,
                "INSERT INTO variants (id, game_id, system_uuid, version_label, mods, language, platform, "
                "runner, executable, directory, configpath, path, platforms, source, lastplayed, installed, "
                "installed_at, detected_at, year, has_custom_banner, has_custom_icon, has_custom_coverart_big, "
                "playtime, service, service_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    variant["id"],
                    game_id,
                    system_uuid,
                    variant.get("version_label"),
                    json.dumps(variant.get("mods") or []),
                    variant.get("language"),
                    variant.get("platform"),
                    variant.get("runner"),
                    variant.get("executable"),
                    variant.get("directory"),
                    variant.get("configpath"),
                    variant.get("path"),
                    json.dumps(variant.get("platforms") or []),
                    json.dumps(variant.get("source") or {}),
                    variant.get("lastplayed"),
                    variant.get("installed"),
                    variant.get("installed_at"),
                    variant.get("detected_at"),
                    variant.get("year"),
                    variant.get("has_custom_banner"),
                    variant.get("has_custom_icon"),
                    variant.get("has_custom_coverart_big"),
                    variant.get("playtime"),
                    variant.get("service"),
                    variant.get("service_id"),
                ),
            )


def save_entry_and_sync_cache(entry: dict[str, Any]) -> Path:
    """The one shared primitive for "persist this raw datadir entry dict,
    then keep cache.db in sync" -- add_game()/update_existing() below use it
    internally, and onboarding.py (which builds up richer entry dicts of its
    own -- version groups, platform_ids, ownership, timeline_events -- rather
    than flat per-variant kwargs) calls it directly too, so there's exactly
    one code path that ever writes a datadir entry, not two that could drift
    out of sync with each other. Returns the path save_entry() wrote."""
    saved_path = datadir.save_entry(entry)
    _upsert_cache_from_entry(entry)
    return saved_path


def rebuild_cache() -> None:
    """Reconstruct cache.db from datadir/*.json from scratch. cache.db is
    fully derived -- safe to delete or call this any time (e.g. at startup)."""
    from pythia import settings

    with sql.db_cursor(settings.DB_PATH) as cursor:
        sql.cursor_execute(cursor, "DELETE FROM games")
        sql.cursor_execute(cursor, "DELETE FROM variants")
    for entry in datadir.list_all_entries():
        _upsert_cache_from_entry(entry)


def _row_to_game_dict(row: dict[str, Any]) -> DbGameDict:
    row = dict(row)
    for key in ("platform_ids", "ownership", "mods"):
        if key in row and isinstance(row[key], str):
            try:
                row[key] = json.loads(row[key])
            except (TypeError, ValueError):
                row[key] = []
    return row


def _primary_variant(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The variant a flat "one row per game" query represents this game by:
    the most recently played/installed variant, falling back to the most
    recently detected one. Real games with only one variant (the common
    case) always get that one, unambiguously."""
    variants = list(_iter_variants(entry))
    if not variants:
        return None
    installed = [v for v in variants if v.get("installed")]
    pool = installed or variants
    return max(pool, key=lambda v: (v.get("lastplayed") or 0, v.get("installed_at") or 0, v.get("detected_at") or ""))


def _merged_row(entry: dict[str, Any], variant: dict[str, Any] | None) -> DbGameDict:
    row: DbGameDict = {
        "id": entry["game_id"],
        "system_uuid": entry["system_uuid"],
        "name": entry.get("canonical_name"),
        "sortname": entry.get("sortname") or "",
        "slug": entry.get("slug") or "",
        "installer_slug": entry.get("installer_slug") or "",
        "parent_slug": entry.get("parent_slug") or "",
        "notes": entry.get("notes") or "",
        "platform_ids": entry.get("platform_ids") or [],
        "ownership": entry.get("ownership") or [],
        "updated": entry.get("updated_at"),
        "discord_id": entry.get("discord_id"),
        "version_count": len({v.get("version_label") for v in _iter_variants(entry) if v.get("version_label")}),
    }
    for field in _VARIANT_FIELDS:
        row[field] = (variant or {}).get(field)
    if variant is not None:
        row["variant_id"] = variant.get("id")
    return row


# ---- reads (query cache.db, matching pga.db's old query engine exactly) ---


def get_games(
    searches: dict[str, str] | None = None,
    filters: sql.DBConditionsDict | None = None,
    excludes: sql.DBConditionsDict | None = None,
    sorts: Sequence[Any] | None = None,
) -> list[DbGameDict]:
    """One row per game (the external view), each carrying its *primary*
    variant's install fields flattened in -- see _primary_variant(). Filters
    naming a variant-only field (runner, installed, ...) are honored by
    joining against variants and returning one row per matching variant
    instead (so `filters={"installed": 1}` still finds every installed
    variant, not just each game's primary one)."""
    from pythia import settings

    variant_filter_keys = {k.split("__")[0] for k in (filters or {})} | {k.split("__")[0] for k in (excludes or {})}
    if variant_filter_keys & _VARIANT_FIELDS:
        return _get_games_via_variant_filter(searches, filters, excludes, sorts)

    rows = sql.filtered_query(
        settings.DB_PATH, "games", searches=searches, filters=filters, excludes=excludes, sorts=sorts
    )
    games = []
    for row in rows:
        row = _row_to_game_dict(row)
        entry = _load_entry(row["id"], row["system_uuid"])
        variant = _primary_variant(entry) if entry else None
        games.append(_merged_row(entry, variant) if entry else row)
    return games


def _get_games_via_variant_filter(
    searches: dict[str, Any] | None,
    filters: dict[str, Any] | None,
    excludes: dict[str, Any] | None,
    sorts: Sequence[Any] | None,
) -> list[DbGameDict]:
    from pythia import settings

    # filtered_query() defaults to "ORDER BY slug" when no sorts are given --
    # the "variants" table has no slug column (that's a game-level field), so
    # a variant-scoped query always needs an explicit sort.
    sorts = sorts or [("id", "ASC")]
    rows = sql.filtered_query(
        settings.DB_PATH, "variants", searches=searches, filters=filters, excludes=excludes, sorts=sorts
    )
    games = []
    for row in rows:
        entry = _load_entry(row["game_id"], row["system_uuid"])
        if not entry:
            continue
        variant = next((v for v in _iter_variants(entry) if v["id"] == row["id"]), None)
        games.append(_merged_row(entry, variant))
    return games


def get_games_where(**conditions: Any) -> list[DbGameDict]:
    """Same field__lessthan / __isnull / __not / __in conditions as before,
    against the merged games+primary-variant view (or per-variant rows, for
    variant-scoped fields -- see get_games())."""
    filters: dict[str, Any] = {}
    excludes: dict[str, Any] = {}
    below: dict[str, Any] = {}
    for field, value in conditions.items():
        base_field, _, op = field.partition("__")
        if op == "isnull":
            (filters if value else excludes)[base_field] = None
        elif op == "not":
            excludes[base_field] = value
        elif op in ("", "in"):
            filters[base_field] = value
        elif op == "lessthan":
            below[base_field] = value
        else:
            raise ValueError(f"unknown condition {field}")
    games = get_games(filters=filters, excludes=excludes)
    for field, limit in below.items():
        games = [g for g in games if g.get(field) is not None and g[field] < limit]
    return games


def get_games_by_ids(game_ids: Iterable[str]) -> list[DbGameDict]:
    game_ids = list(game_ids)
    return get_games(filters={"id": game_ids}) if game_ids else []


def get_game_for_service(service: str, appid: str) -> DbGameDict | None:
    existing = get_games(filters={"service_id": appid, "service": service})
    return existing[0] if existing else None


def get_all_installed_game_for_service(service: str) -> dict[str, DbGameDict]:
    db_games = get_games(filters={"service": service, "installed": 1})
    return {g["service_id"]: g for g in db_games}


def get_service_games(service: str) -> list[str]:
    return [g["service_id"] for g in get_games(filters={"service": service, "installed": 1})]


def get_game_by_field(value: Any, field: str = "slug") -> DbGameDict | None:
    if field not in ("slug", "installer_slug", "id", "configpath", "name"):
        raise ValueError("Can't query by field '%s'" % field)
    results = get_games(filters={field: value})
    return results[0] if results else None


def get_games_by_runner(runner: str) -> list[DbGameDict]:
    return get_games(filters={"runner": runner})


def get_games_by_slug(slug: str) -> list[DbGameDict]:
    return get_games(filters={"slug": slug})


# ---- writes (through to datadir, cache.db kept in sync) --------------------


def _find_owning_game_id(game_fields: dict[str, Any], variant_fields: dict[str, Any]) -> str | None:
    """Real match heuristic, same shape as Lutris's own get_matching_game():
    an explicit id wins outright; otherwise match by slug plus (for an
    installed variant) configpath, or (for an uninstalled one) runner."""
    if game_fields.get("id"):
        existing = get_game_by_field(game_fields["id"], "id")
        if existing:
            return str(existing["id"])
    slug = game_fields.get("slug") or (slugify(game_fields["name"]) if game_fields.get("name") else None)
    if not slug:
        return None
    for game in get_games_by_slug(slug):
        if game.get("installed"):
            if game.get("configpath") == variant_fields.get("configpath"):
                return str(game["id"])
        elif game.get("runner") == variant_fields.get("runner") or not all(
            [variant_fields.get("runner"), game.get("runner")]
        ):
            return str(game["id"])
    return None


def add_game(**game_data: Any) -> str:
    """Add a game (and its first variant) to datadir. Returns the game_id."""
    game_fields, variant_fields = _split_game_and_variant_fields(game_data)
    variant_fields.setdefault("installed_at", int(time.time()))
    version_label = variant_fields.pop("version_label", None)
    name = game_fields.get("name") or "Unknown"
    slug = game_fields.get("slug") or slugify(name)

    game_id = new_game_id()
    entry = _new_entry(game_id, name, slug)
    entry.update({k: v for k, v in game_fields.items() if k != "id"})
    entry["canonical_name"] = name
    entry["slug"] = slug

    variant = dict(variant_fields)
    variant["id"] = new_game_id()[:12]
    entry["versions"] = [{"version": version_label, "variants": [variant]}]

    save_entry_and_sync_cache(entry)
    return game_id


def add_games_bulk(games: list[DbGameDict]) -> list[str]:
    return [add_game(**game) for game in games]


def add_or_update(**params: Any) -> str:
    game_id = update_existing(**params)
    if game_id:
        return game_id
    return add_game(**params)


def update_existing(**params: Any) -> str | None:
    """Updates a game/variant, but never creates one. Returns the game_id if
    a match was found, else None."""
    game_fields, variant_fields = _split_game_and_variant_fields(params)
    game_id = _find_owning_game_id(game_fields, variant_fields)
    if not game_id:
        return None

    entry = _load_entry(game_id)
    if entry is None:
        return None

    entry.update({k: v for k, v in game_fields.items() if k not in ("id",)})

    version_label = variant_fields.pop("version_label", None)
    target_configpath = variant_fields.get("configpath")
    if target_configpath:
        # A specific install was named -- match it exactly, or (since the
        # owning game was already found) treat an unmatched configpath as a
        # new variant/version of that same game, never someone else's row.
        existing = next((v for v in _iter_variants(entry) if v.get("configpath") == target_configpath), None)
    else:
        # No configpath given at all -- too ambiguous to know which variant
        # is meant, so fall back to whichever one this game already has
        # (matches Lutris's own single-row update_existing() behavior).
        existing = next(iter(_iter_variants(entry)), None)

    if existing is not None:
        # Found in some version group -- update it in place there, moving it
        # to the new group if its version_label changed.
        old_group = _find_or_create_version_group(entry, existing["version_label"])
        variant = next(v for v in old_group["variants"] if v["id"] == existing["id"])
        if version_label is not None and version_label != existing["version_label"]:
            old_group["variants"].remove(variant)
            _find_or_create_version_group(entry, version_label)["variants"].append(variant)
    else:
        variant = {"id": new_game_id()[:12]}
        _find_or_create_version_group(entry, version_label)["variants"].append(variant)
    variant.update(variant_fields)

    save_entry_and_sync_cache(entry)
    return game_id


def get_matching_game(params: dict[str, Any]) -> str | None:
    game_fields, variant_fields = _split_game_and_variant_fields(params)
    return _find_owning_game_id(game_fields, variant_fields)


def delete_game(game_id: str) -> None:
    """Delete a game -- all its variants go with it. This system's own entry
    only (can't delete another synced system's file, see the datadir
    single-writer-per-file rule)."""
    from pythia import settings

    entry = _load_entry(game_id)
    if entry is not None:
        path = datadir.find_entry_file(entry["system_uuid"], game_id)
        if path is not None and entry["system_uuid"] == get_system_uuid():
            try:
                path.unlink()
            except OSError:
                pass
    with sql.db_cursor(settings.DB_PATH) as cursor:
        sql.cursor_execute(cursor, "DELETE FROM games WHERE id = ?", (game_id,))
        sql.cursor_execute(cursor, "DELETE FROM variants WHERE game_id = ?", (game_id,))


def get_used_runners() -> list[str]:
    from pythia import settings

    with sql.db_cursor(settings.DB_PATH) as cursor:
        rows = sql.cursor_execute(
            cursor, "select distinct runner from variants where runner is not null order by runner"
        ).fetchall()
    return [r[0] for r in rows if r[0]]


def get_used_platforms() -> list[str]:
    from pythia import settings

    with sql.db_cursor(settings.DB_PATH) as cursor:
        rows = sql.cursor_execute(
            cursor,
            "select distinct platform from variants "
            "where platform is not null and platform is not '' order by platform",
        ).fetchall()
    return [r[0] for r in rows if r[0]]


def get_game_count(param: str, value: Any) -> int | None:
    return len(get_games(filters={param: value}))
