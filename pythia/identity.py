"""
This system's durable identity/config, stored in local.db (SQLite).

local.db is deliberately kept simple and lean: system_uuid (generated once, never
regenerated), a handful of local settings, and a counter for minting local game
IDs. Nothing here is derived/rebuildable -- that's cache.py's job. local.db lives
outside datadir/ and is never synced.
"""

import os
import sqlite3
import uuid
from pathlib import Path


def app_data_dir() -> Path:
    override = os.environ.get("PYTHIA_DATA_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "Pythia"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "pythia"


def datadir_path() -> Path:
    """Where datadir/ actually lives -- independently overridable from the rest of
    app_data_dir() via PYTHIA_DATADIR_OVERRIDE. This is what makes a local debug-mode
    multi-system simulation possible without a second real machine or a real sync
    tool (2026-08-02): point two separate PYTHIA_DATA_DIR instances (each with its
    own local.db, so its own system_uuid) at the same PYTHIA_DATADIR_OVERRIDE path,
    and they behave exactly as if Syncthing were keeping that shared folder in sync
    between two real devices -- because as far as either instance can tell, it is."""
    override = os.environ.get("PYTHIA_DATADIR_OVERRIDE")
    if override:
        return Path(override)
    return app_data_dir() / "datadir"


def _local_db_path() -> Path:
    return app_data_dir() / "local.db"


def _connect() -> sqlite3.Connection:
    path = _local_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.commit()
    return conn


def _get(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def get_system_uuid() -> str:
    """Return this system's persistent UUID, generating and storing one on first run.

    Never regenerated once set -- the datadir single-writer-per-file safety model
    depends on this staying stable across runs.
    """
    conn = _connect()
    try:
        existing = _get(conn, "system_uuid")
        if existing:
            return existing
        new_uuid = str(uuid.uuid4())
        _set(conn, "system_uuid", new_uuid)
        return new_uuid
    finally:
        conn.close()


def get_setting(key: str, default=None):
    conn = _connect()
    try:
        value = _get(conn, f"setting:{key}")
        return value if value is not None else default
    finally:
        conn.close()


def set_setting(key: str, value: str):
    conn = _connect()
    try:
        _set(conn, f"setting:{key}", value)
    finally:
        conn.close()


def next_game_id() -> int:
    """Mint and persist the next local game ID (for games with no external ID to reuse)."""
    conn = _connect()
    try:
        current = _get(conn, "next_local_game_id")
        current = int(current) if current is not None else 1
        _set(conn, "next_local_game_id", str(current + 1))
        return current
    finally:
        conn.close()
