"""
F95zone library plugin -- reads a real, local F95Checker `db.sqlite3` and treats it
as the watchlist ownership source established earlier this session's design
discussion: "we own everything on f95, because we can download anything from there...
what matters is what the user has said they care about" (2026-08-03 Ownership
semantics section of the plan). Every row in F95Checker's own `games` table already
*is* that watchlist -- the user explicitly added it there -- so `list_owned()` simply
returns all of them, no separate ownership concept needed.

**This is a one-shot import, not an ongoing account sync** (2026-08-03 correction --
unlike Steam/GOG/Epic, where the platform's own account is the durable source of
truth worth re-polling for new purchases, F95Checker's db.sqlite3 is a local
snapshot file that only exists on whichever one machine happens to run F95Checker --
there's no "account" to keep re-checking). Once imported, Pythia's own datadir
entries are canonical: the CLI surface is `f95-import`, not `f95-connect`/
`f95-sync`, deliberately not implying a persistent connection the way the other
services' commands do. Safe to re-run (`onboarding.sync_owned()` only ever links or
adds, never overwrites an existing entry's `canonical_name`/notes), but it's meant
to be run when the user wants to pull in new watchlist entries, not on any
recurring schedule.

Schema confirmed directly against a real (user-copied, not the live production file)
db.sqlite3, cross-checked against F95Checker's actual source
(github.com/WillyJL/F95Checker, GPLv3 -- cloned to `f95checker/` as a reference, same
pattern as the Lutris clone, never imported from directly). `modules/db.py` itself is
too tightly coupled to F95Checker's whole async/GUI runtime (`aiosqlite`, imgui
globals, interlinked internal modules) to reuse directly -- same call as Lutris's
services -- so this reads the raw sqlite file independently with stdlib `sqlite3`,
opened read-only (`mode=ro`) so it never risks writing to or locking a file
F95Checker itself might have open.

No `match()`/`is_root()` -- F95 isn't a distribution format with a structural
signature (same reasoning as the `f95checker` DownloaderPlugin stub already has,
`compatible_library_kinds = ["*"]`): whatever engine actually built an F95-sourced
install (Ren'Py, RPG Maker, ...) is what identifies it locally, via the existing
engine plugins. This plugin only ever answers "what has the user told F95Checker
they're watching," account-level, independent of any local install.

`Type` values (F95Checker's own engine-tag enum, `common/structs.py`) are passed
through as a hint in `extra["f95_engine_hint"]` -- a real, useful cross-check against
Pythia's own file-signature-based engine detection, but never authoritative (a
mismatch just means "worth a look," not an error).
"""

import json
import sqlite3
from pathlib import Path

from pythia.plugins import LibraryPlugin
from pythia.plugins.registry import register_builtin

_DEFAULT_DB_PATH = Path.home() / "AppData" / "Roaming" / "f95checker" / "db.sqlite3"

# F95Checker's common/structs.py Type enum -> Pythia's own engine plugin `kind`
# strings, where a real, confident mapping exists. Anything not listed here (Flash,
# HTML, Unity, Unreal Eng, Godot, ...) either has no Pythia engine plugin yet or the
# names don't correspond closely enough to guess safely -- left unmapped rather than
# guessed.
_TYPE_TO_ENGINE_HINT = {
    14: "renpy",
    13: "rpgmaker_vxace",  # F95's "RPGM" doesn't distinguish MV/MZ/VX Ace itself
}


def set_db_path(path: str):
    from pythia import identity

    identity.set_setting("f95_db_path", path)


def get_db_path() -> Path:
    from pythia import identity

    override = identity.get_setting("f95_db_path")
    return Path(override) if override else _DEFAULT_DB_PATH


def _decode_json_field(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


class F95LibraryPlugin(LibraryPlugin):
    id = "pythia.library.f95"
    display_name = "F95zone"
    version = "1.0.0"
    author = "pythia"

    def is_connected(self) -> bool:
        return get_db_path().is_file()

    def list_owned(self):
        db_path = get_db_path()
        if not db_path.is_file():
            return None

        try:
            # Read-only URI connection -- never risks writing to or locking a file
            # F95Checker itself might have open.
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.OperationalError:
            return []

        try:
            cur = conn.execute(
                "SELECT id, name, version, installed, type, url, executables FROM games"
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

        results = []
        for row in rows:
            game_id = row["id"]
            if game_id is None or game_id < 0:  # F95Checker uses negative ids for custom/local-only entries
                continue
            executables = _decode_json_field(row["executables"], [])
            extra = {
                "f95_thread_id": str(game_id),
                "f95_url": row["url"],
            }
            engine_hint = _TYPE_TO_ENGINE_HINT.get(row["type"])
            if engine_hint:
                extra["f95_engine_hint"] = engine_hint
            if executables:
                extra["f95_executables"] = executables
            results.append(
                {
                    "kind": "f95",
                    "name": row["name"],
                    "version_label": row["installed"] or row["version"],
                    "platforms": [],
                    "external_id": int(game_id),
                    "extra": extra,
                }
            )
        return results


_GAMES_TABLE_COLUMNS = (
    "id", "custom", "name", "version", "developer", "type", "status", "url",
    "added_on", "last_updated", "last_full_check", "last_check_version", "last_launched",
    "score", "votes", "rating", "finished", "installed", "updated", "archived",
    "executables", "description", "changelog", "tags", "unknown_tags", "unknown_tags_flag",
    "labels", "tab", "notes", "image_url", "downloads", "previews_urls", "reviews_total", "reviews",
)


def export_to_sqlite(path: str):
    """Write a real, loadable F95Checker db.sqlite3 (`games` table only, schema
    confirmed against a real file -- see module docstring) from Pythia's own tracked
    f95-platform_id entries. Deliberately minimal-fidelity: Pythia never captured
    F95Checker-only fields (tags, score, votes, developer, ...), so those write as
    honest empty/zero defaults rather than fabricated values -- name, thread id, url,
    and whatever version/install info Pythia has are the only real data carried over.
    Lets a user hop back to F95Checker's own UI, or share/migrate, without losing
    that fidelity gap silently -- it's a real, stated limitation, not hidden."""
    from pythia import datadir

    conn = sqlite3.connect(path)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS games ({', '.join(_GAMES_TABLE_COLUMNS)})"
    )

    rows = []
    seen_ids = set()
    for game_id, entries in datadir.list_known_games().items():
        for entry in entries:
            f95_id = next(
                (p.get("id") for p in entry.get("platform_ids", []) if p.get("platform") == "f95"),
                None,
            )
            if f95_id is None or f95_id in seen_ids:
                continue
            seen_ids.add(f95_id)

            installed_version = ""
            for group in entry.get("versions", []):
                for variant in group.get("variants", []):
                    if (variant.get("source") or {}).get("kind") == "f95":
                        installed_version = group.get("version") or ""

            rows.append(
                {
                    "id": int(f95_id) if str(f95_id).lstrip("-").isdigit() else 0,
                    "custom": 0,
                    "name": entry.get("canonical_name") or "",
                    "version": installed_version,
                    "developer": "",
                    "type": 0,
                    "status": 5,  # Unchecked -- Pythia doesn't track F95Checker's own status
                    "url": f"https://f95zone.to/threads/{f95_id}/",
                    "added_on": 0,
                    "last_updated": 0,
                    "last_full_check": 0,
                    "last_check_version": "",
                    "last_launched": 0,
                    "score": 0.0,
                    "votes": 0,
                    "rating": 0,
                    "finished": "",
                    "installed": installed_version,
                    "updated": 0,
                    "archived": 0,
                    "executables": "[]",
                    "description": "",
                    "changelog": "",
                    "tags": "[]",
                    "unknown_tags": "[]",
                    "unknown_tags_flag": 0,
                    "labels": "[]",
                    "tab": 0,
                    "notes": entry.get("notes") or "",
                    "image_url": "missing",
                    "downloads": "[]",
                    "previews_urls": "[]",
                    "reviews_total": 0,
                    "reviews": "[]",
                }
            )

    placeholders = ", ".join("?" for _ in _GAMES_TABLE_COLUMNS)
    conn.executemany(
        f"INSERT INTO games ({', '.join(_GAMES_TABLE_COLUMNS)}) VALUES ({placeholders})",
        [tuple(row[c] for c in _GAMES_TABLE_COLUMNS) for row in rows],
    )
    conn.commit()
    conn.close()
    return len(rows)


plugin = register_builtin(F95LibraryPlugin())
