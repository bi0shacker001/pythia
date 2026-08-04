"""
Rebuildable index/cache over datadir/, stored in cache.db (SQLite).

cache.db is a second, separate database from local.db, living in the same folder.
Everything in it is derived from datadir/*.json -- it's safe to delete at any time
and rebuild by rescanning, which is exactly why it's kept out of local.db.
"""

import sqlite3
from pathlib import Path

from pythia.identity import app_data_dir


def _cache_db_path() -> Path:
    return app_data_dir() / "cache.db"


def _connect() -> sqlite3.Connection:
    path = _cache_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS games (
            game_id TEXT NOT NULL,
            system_uuid TEXT NOT NULL,
            canonical_name TEXT,
            source_kind TEXT,
            version_count INTEGER,
            filename TEXT NOT NULL,
            updated_at TEXT,
            PRIMARY KEY (game_id, system_uuid)
        )
        """
    )
    conn.commit()
    return conn


def _primary_source_kind(entry: dict):
    """Best-effort single kind for the index's source_kind column -- source now lives
    per-variant (2026-08-03, see onboarding.py's _merge_version), since a game can
    have variants from more than one place (a Steam install and an Epic install of
    the same game_id). This just takes the first variant's kind found; the real,
    complete picture is entry["versions"][*]["variants"][*]["source"]."""
    for group in entry.get("versions") or []:
        for variant in group.get("variants") or []:
            kind = (variant.get("source") or {}).get("kind")
            if kind:
                return kind
    return None


def upsert_entry(entry: dict, filename: str):
    """Update the cache row for one datadir entry (this system's own, or a synced one)."""
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO games (game_id, system_uuid, canonical_name, source_kind, version_count, filename, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_id, system_uuid) DO UPDATE SET
                canonical_name = excluded.canonical_name,
                source_kind = excluded.source_kind,
                version_count = excluded.version_count,
                filename = excluded.filename,
                updated_at = excluded.updated_at
            """,
            (
                entry["game_id"],
                entry["system_uuid"],
                entry.get("canonical_name"),
                _primary_source_kind(entry),
                sum(len(v.get("variants") or []) for v in entry.get("versions") or []),
                filename,
                entry.get("updated_at"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def remove_entry(system_uuid: str, game_id: str):
    """Drop one cache row -- used by reconciliation.merge() when a retired game_id's
    datadir file is deleted, so the index doesn't keep pointing at a file that no
    longer exists."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM games WHERE game_id = ? AND system_uuid = ?", (game_id, system_uuid))
        conn.commit()
    finally:
        conn.close()


def rebuild(datadir_dir: Path):
    """Drop and repopulate the whole cache by rescanning every datadir/*.json file."""
    import json

    conn = _connect()
    try:
        conn.execute("DELETE FROM games")
        conn.commit()
    finally:
        conn.close()

    if not datadir_dir.is_dir():
        return

    for jsonfile in sorted(datadir_dir.glob("*.json")):
        try:
            entry = json.loads(jsonfile.read_text())
        except (OSError, ValueError):
            continue
        upsert_entry(entry, jsonfile.name)


def list_cached_games():
    """Return cached rows as a list of dicts -- fast lookup, may lag a live datadir/ scan."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT game_id, system_uuid, canonical_name, source_kind, version_count, filename, updated_at "
            "FROM games ORDER BY game_id, system_uuid"
        ).fetchall()
        return [
            {
                "game_id": r[0],
                "system_uuid": r[1],
                "canonical_name": r[2],
                "source_kind": r[3],
                "version_count": r[4],
                "filename": r[5],
                "updated_at": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()
