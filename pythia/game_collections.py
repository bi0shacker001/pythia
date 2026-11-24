"""The person's collections of games: named lists they make ("Co-op", "To finish"), kept with the library.

Like favourites (pythia.favourites) they belong to the person and follow them to every computer sharing the
library folder, under datadir's rule that a computer writes only its own files. Each computer keeps one file,
``datadir/collections/<system-uuid>.json`` (a folder of its own, so the games' ``*.json`` readers never see it),
holding every change it made, each stamped with the time it was made:

    {"schema_version": 1, "system_uuid": "...", "collections": {
        "<collection id>": {"created_at": "...",
                            "name": {"value": "Co-op", "at": "..."},
                            "deleted": {"on": false, "at": "..."},
                            "members": {"<game id>": {"on": true, "at": "..."}}}}}

The collections as the person sees them are the merge of every computer's file: per collection the newest name
and the newest deleted mark win, and per game the newest member mark, so a game removed on one computer and added
on another ends where the later change put it, and removing travels as surely as adding.
"""

import datetime
import json
import os
import tempfile
import threading
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pythia.identity import datadir_path, get_system_uuid
from pythia.util.log import logger

FOLDER = "collections"
SCHEMA_VERSION = 1
NAME_LIMIT = 100
_write_lock = threading.Lock()


class CollectionError(Exception):
    """A change that cannot be made; ``code`` is the contract answer's code, the message words for a screen."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def now() -> str:
    """A change's time: UTC to the millisecond, one format so times compare as text (as favourites)."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def _folder() -> Path:
    return datadir_path() / FOLDER


def _own_path() -> Path:
    return _folder() / f"{get_system_uuid()}.json"


def _read(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as ex:
        logger.warning("Could not read the collections of %s: %s", path.name, ex)
        return {}
    found = data.get("collections") if isinstance(data, dict) else None
    return found if isinstance(found, dict) else {}


def _write_own(collections: dict[str, dict]) -> None:
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        {"schema_version": SCHEMA_VERSION, "system_uuid": get_system_uuid(), "collections": collections},
        indent=2,
        sort_keys=True,
    )
    handle, temp = tempfile.mkstemp(prefix=".collections-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.replace(temp, _own_path())
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _newest(marks: Sequence[dict | None]) -> dict | None:
    marks = [m for m in marks if isinstance(m, dict) and m.get("at")]
    return max(marks, key=lambda m: m["at"]) if marks else None


def merged() -> list[dict[str, Any]]:
    """Every collection that is not deleted, from every computer's file: ``id``, ``name``, ``game_ids`` (sorted),
    ``count``, ``created_at`` and ``updated_at`` (the newest change to it), ordered by name."""
    folder = _folder()
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    by_id: dict[str, list[dict]] = {}
    for path in files:
        for cid, record in _read(path).items():
            if isinstance(record, dict):
                by_id.setdefault(str(cid), []).append(record)
    found: list[dict[str, Any]] = []
    for cid, records in by_id.items():
        deleted = _newest([r.get("deleted") for r in records])
        if deleted and deleted.get("on"):
            continue
        name = _newest([r.get("name") for r in records])
        if not name or not str(name.get("value") or "").strip():
            continue
        marks: dict[str, list[dict]] = {}
        stamps = [name["at"]]
        for record in records:
            for game_id, mark in (record.get("members") or {}).items():
                if isinstance(mark, dict) and mark.get("at"):
                    marks.setdefault(str(game_id), []).append(mark)
                    stamps.append(mark["at"])
        members = sorted(g for g, m in marks.items() if (_newest(m) or {}).get("on"))
        created = min((r["created_at"] for r in records if r.get("created_at")), default=name["at"])
        found.append(
            {
                "id": cid,
                "name": str(name["value"]),
                "game_ids": members,
                "count": len(members),
                "created_at": created,
                "updated_at": max(stamps),
            }
        )
    return sorted(found, key=lambda c: (c["name"].casefold(), c["id"]))


def version(collections: list[dict[str, Any]] | None = None) -> str:
    """A short text that changes when any collection, its name or its members do."""
    import hashlib  # noqa: PLC0415

    text = json.dumps(merged() if collections is None else collections, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]  # noqa: S324 -- a change marker, not a secret


def _clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())
    if not text:
        raise CollectionError("empty-name", "A collection needs a name.")
    if len(text) > NAME_LIMIT:
        raise CollectionError("name-too-long", f"A collection's name is at most {NAME_LIMIT} characters.")
    return text


def _find(collections: list[dict], collection_id: str) -> dict:
    found = next((c for c in collections if c["id"] == collection_id), None)
    if found is None:
        raise CollectionError("no-such-collection", f"No collection {collection_id}.")
    return found


def _taken(collections: list[dict], name: str, except_id: str | None = None) -> bool:
    return any(c["name"].casefold() == name.casefold() and c["id"] != except_id for c in collections)


def create(name: str) -> dict[str, Any]:
    clean = _clean_name(name)
    with _write_lock:
        if _taken(merged(), clean):
            raise CollectionError("name-taken", f"There is already a collection called {clean}.")
        own = _read(_own_path())
        cid = uuid.uuid4().hex[:12]
        stamp = now()
        own[cid] = {
            "created_at": stamp,
            "name": {"value": clean, "at": stamp},
            "deleted": {"on": False, "at": stamp},
            "members": {},
        }
        _write_own(own)
    return _find(merged(), cid)


def rename(collection_id: str, name: str) -> dict[str, Any]:
    clean = _clean_name(name)
    with _write_lock:
        current = merged()
        _find(current, collection_id)
        if _taken(current, clean, collection_id):
            raise CollectionError("name-taken", f"There is already a collection called {clean}.")
        own = _read(_own_path())
        record = own.setdefault(collection_id, {"created_at": now(), "members": {}})
        record["name"] = {"value": clean, "at": now()}
        _write_own(own)
    return _find(merged(), collection_id)


def delete(collection_id: str) -> None:
    with _write_lock:
        _find(merged(), collection_id)
        own = _read(_own_path())
        record = own.setdefault(collection_id, {"created_at": now(), "members": {}})
        record["deleted"] = {"on": True, "at": now()}
        _write_own(own)


def _mark(collection_id: str, game_ids: list[str], on: bool) -> tuple[dict[str, Any], list[str]]:
    """Adds or removes ``game_ids``; returns the collection and the ids that actually changed it."""
    ids = list(dict.fromkeys(str(g) for g in game_ids if g))
    with _write_lock:
        collection = _find(merged(), collection_id)
        have = set(collection["game_ids"])
        changed = [g for g in ids if (g in have) != on]
        if changed:
            own = _read(_own_path())
            record = own.setdefault(collection_id, {"created_at": now(), "members": {}})
            members = record.setdefault("members", {})
            stamp = now()
            for game_id in changed:
                members[game_id] = {"on": on, "at": stamp}
            _write_own(own)
    return _find(merged(), collection_id), changed


def add_games(collection_id: str, game_ids: list[str]) -> tuple[dict[str, Any], list[str]]:
    return _mark(collection_id, game_ids, True)


def remove_games(collection_id: str, game_ids: list[str]) -> tuple[dict[str, Any], list[str]]:
    return _mark(collection_id, game_ids, False)
