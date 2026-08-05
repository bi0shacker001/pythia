from typing import Any, TypeAlias

from pythia import settings
from pythia.database import sql
from pythia.util.log import logger

DBSchema: TypeAlias = list[dict[str, Any]]

DATABASE: dict[str, DBSchema] = {
    # 2026-08-04 datadir rewrite: this table is now a rebuildable SQL index over
    # datadir/*.json (real source of truth), not the authoritative store pga.db
    # used to be -- see pythia/database/games.py's module docstring. "games" is
    # the external, one-row-per-logical-game view (id = a 16-char game_id,
    # replacing Lutris's autoincrement int one-for-one); per-install detail
    # (what used to be this row's own runner/directory/executable/etc.) now
    # lives in the sibling "variants" table, keyed by game_id -- a game_id can
    # own more than one variant (different versions/installs of the same game),
    # which plain Lutris never modeled.
    "games": [
        {"name": "id", "type": "TEXT", "indexed": True},
        {"name": "system_uuid", "type": "TEXT"},
        {"name": "name", "type": "TEXT"},
        {"name": "sortname", "type": "TEXT"},
        {"name": "slug", "type": "TEXT"},
        {"name": "installer_slug", "type": "TEXT"},
        {"name": "parent_slug", "type": "TEXT"},
        {"name": "notes", "type": "TEXT"},
        {"name": "platform_ids", "type": "TEXT"},  # JSON list, see datadir.PLATFORM_ID_KINDS
        {"name": "ownership", "type": "TEXT"},  # JSON list
        {"name": "updated", "type": "DATETIME"},
        {"name": "discord_id", "type": "TEXT"},
        # From onboarding.py (folder-scan tracking, ported 2026-08-04):
        {"name": "game_uuid", "type": "TEXT"},  # real UUID, Milestone 9 marker-file convergence
        {"name": "game_id_source", "type": "TEXT"},
        {"name": "managed_externally", "type": "INTEGER"},
        {"name": "version_management_enabled", "type": "INTEGER"},
        {"name": "available_updates", "type": "TEXT"},  # JSON list
    ],
    # One row per install (what a Lutris "games" row used to mean in full:
    # runner/directory/executable/configpath/installed/playtime/...), plus
    # Pythia's own version-tracking fields (version_label/mods/language) that
    # Lutris never had a place for. Multiple variants can share a game_id --
    # that's the "different versions of the same game" relationship.
    "variants": [
        {"name": "id", "type": "TEXT", "indexed": True},
        {"name": "game_id", "type": "TEXT"},
        {"name": "system_uuid", "type": "TEXT"},
        {"name": "version_label", "type": "TEXT"},
        {"name": "mods", "type": "TEXT"},  # JSON list
        {"name": "language", "type": "TEXT"},
        {"name": "platform", "type": "TEXT"},
        {"name": "runner", "type": "TEXT"},
        {"name": "executable", "type": "TEXT"},
        {"name": "directory", "type": "TEXT"},
        {"name": "configpath", "type": "TEXT"},
        {"name": "path", "type": "TEXT"},  # from onboarding.py: the raw on-disk install folder
        {"name": "platforms", "type": "TEXT"},  # JSON list, from onboarding.py
        {"name": "source", "type": "TEXT"},  # JSON {"kind", "extra"}, from onboarding.py
        {"name": "lastplayed", "type": "INTEGER"},
        {"name": "installed", "type": "INTEGER"},
        {"name": "installed_at", "type": "INTEGER"},
        {"name": "detected_at", "type": "DATETIME"},
        {"name": "year", "type": "INTEGER"},
        {"name": "has_custom_banner", "type": "INTEGER"},
        {"name": "has_custom_icon", "type": "INTEGER"},
        {"name": "has_custom_coverart_big", "type": "INTEGER"},
        {"name": "playtime", "type": "REAL"},
        {"name": "service", "type": "TEXT"},
        {"name": "service_id", "type": "TEXT"},
    ],
    "service_games": [
        {"name": "id", "type": "INTEGER", "indexed": True},
        {"name": "service", "type": "TEXT"},
        {"name": "appid", "type": "TEXT"},
        {"name": "name", "type": "TEXT"},
        {"name": "slug", "type": "TEXT"},
        {"name": "icon", "type": "TEXT"},
        {"name": "logo", "type": "TEXT"},
        {"name": "url", "type": "TEXT"},
        {"name": "details", "type": "TEXT"},
        {"name": "pythia_slug", "type": "TEXT"},
    ],
    "sources": [
        {"name": "id", "type": "INTEGER", "indexed": True},
        {"name": "uri", "type": "TEXT UNIQUE"},
    ],
    "categories": [
        {"name": "id", "type": "INTEGER", "indexed": True},
        {"name": "name", "type": "TEXT", "unique": True},
    ],
    "games_categories": [
        {"name": "game_id", "type": "TEXT", "indexed": False},  # game_id, not an autoincrement int -- see games table
        {"name": "category_id", "type": "INTEGER", "indexed": False},
    ],
    "saved_searches": [
        {"name": "id", "type": "INTEGER", "indexed": True},
        {"name": "name", "type": "TEXT", "unique": True},
        {"name": "search", "type": "TEXT", "unique": False},
    ],
}


def get_schema(tablename: str) -> DBSchema:
    """
    Fields:
        - position
        - name
        - type
        - not null
        - default
        - indexed
    """
    tables = []
    query = "pragma table_info('%s')" % tablename
    with sql.db_cursor(settings.DB_PATH) as cursor:
        for row in cursor.execute(query).fetchall():
            field = {
                "name": row[1],
                "type": row[2],
                "not_null": row[3],
                "default": row[4],
                "indexed": row[5],
            }
            tables.append(field)
    return tables


def field_to_string(name: str = "", type: str = "", indexed: bool = False, unique: bool = False) -> str:  # pylint: disable=redefined-builtin
    """Converts a python based table definition to it's SQL statement"""
    field_query = "%s %s" % (name, type)
    if indexed:
        field_query += " PRIMARY KEY"
    if unique:
        field_query += " UNIQUE"
    return field_query


def create_table(name: str, schema: DBSchema) -> None:
    """Creates a new table in the database"""
    fields = ", ".join([field_to_string(**f) for f in schema])
    query = "CREATE TABLE IF NOT EXISTS %s (%s)" % (name, fields)
    logger.debug("[Query] %s", query)
    with sql.db_cursor(settings.DB_PATH) as cursor:
        cursor.execute(query)


def migrate(table: str, schema: DBSchema) -> list[str]:
    """Compare a database table with the reference model and make necessary changes

    This is very basic and only the needed features have been implemented (adding columns)

    Args:
        table (str): Name of the table to migrate
        schema (dict): Reference schema for the table

    Returns:
        list: The list of column names that have been added
    """

    existing_schema = get_schema(table)
    migrated_fields = []
    if existing_schema:
        columns = [col["name"] for col in existing_schema]
        for field in schema:
            if field["name"] not in columns:
                logger.info("Migrating %s field %s", table, field["name"])
                migrated_fields.append(field["name"])
                sql.add_field(settings.DB_PATH, table, field)
    else:
        create_table(table, schema)
    return migrated_fields


def syncdb() -> None:
    """Update the database to the current version, making necessary changes
    for backwards compatibility."""
    for table_name, table_data in DATABASE.items():
        migrate(table_name, table_data)
