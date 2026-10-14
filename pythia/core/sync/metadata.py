"""Ownership-preserving metadata records and deterministic reconciliation.

Each system is authoritative only for records bearing its own ``system_uuid``.
Remote records can be mirrored locally but can never overwrite another system's
entry. Equal revisions with different hashes are surfaced as conflicts rather
than resolved by arrival order.
"""

from __future__ import annotations

import hashlib
import json
import time

MAX_RECORD_BYTES = 4 * 1024 * 1024


class MetadataError(ValueError):
    pass


def _canonical(value) -> bytes:
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MetadataError(f"metadata is not JSON serializable: {exc}") from exc
    if len(data) > MAX_RECORD_BYTES:
        raise MetadataError("metadata record exceeds the size limit")
    return data


def create_record(entry: dict, *, revision: int | None = None, updated_at: int | None = None) -> dict:
    if not isinstance(entry, dict):
        raise MetadataError("entry must be an object")
    owner, game_id = entry.get("system_uuid"), entry.get("game_id")
    if not isinstance(owner, str) or not owner or not isinstance(game_id, str) or not game_id:
        raise MetadataError("entry requires non-empty system_uuid and game_id")
    revision = entry.get("sync_revision", 0) if revision is None else revision
    if not isinstance(revision, int) or revision < 0:
        raise MetadataError("revision must be a non-negative integer")
    payload = json.loads(_canonical(entry))
    return {
        "kind": "record",
        "owner_system_uuid": owner,
        "game_id": game_id,
        "revision": revision,
        "updated_at": int(time.time() if updated_at is None else updated_at),
        "payload_hash": hashlib.sha256(_canonical(payload)).hexdigest(),
        "payload": payload,
    }


def create_tombstone(owner_system_uuid: str, game_id: str, revision: int, *, deleted_at: int | None = None) -> dict:
    value = {
        "kind": "tombstone",
        "owner_system_uuid": owner_system_uuid,
        "game_id": game_id,
        "revision": revision,
        "deleted_at": int(time.time() if deleted_at is None else deleted_at),
    }
    validate_item(value)
    return value


def validate_item(item: dict, *, expected_owner: str | None = None) -> None:
    if not isinstance(item, dict) or item.get("kind") not in {"record", "tombstone"}:
        raise MetadataError("metadata item has an invalid kind")
    owner, game_id, revision = item.get("owner_system_uuid"), item.get("game_id"), item.get("revision")
    if not isinstance(owner, str) or not owner or not isinstance(game_id, str) or not game_id:
        raise MetadataError("metadata item requires owner_system_uuid and game_id")
    if expected_owner is not None and owner != expected_owner:
        raise MetadataError("peer attempted to publish a record it does not own")
    if not isinstance(revision, int) or revision < 0:
        raise MetadataError("metadata revision must be a non-negative integer")
    if item["kind"] == "record":
        payload = item.get("payload")
        if not isinstance(payload, dict) or payload.get("system_uuid") != owner or payload.get("game_id") != game_id:
            raise MetadataError("record payload identity does not match its envelope")
        digest = hashlib.sha256(_canonical(payload)).hexdigest()
        if item.get("payload_hash") != digest:
            raise MetadataError("record payload hash mismatch")
        if not isinstance(item.get("updated_at"), int):
            raise MetadataError("record updated_at must be an integer")
    elif not isinstance(item.get("deleted_at"), int):
        raise MetadataError("tombstone deleted_at must be an integer")


def item_key(item: dict) -> str:
    return f"{item['owner_system_uuid']}:{item['game_id']}"


def summarize(items: list[dict]) -> dict:
    result = {}
    for item in items:
        validate_item(item)
        key = item_key(item)
        if key in result:
            raise MetadataError(f"duplicate metadata item: {key}")
        result[key] = {
            "kind": item["kind"],
            "revision": item["revision"],
            "payload_hash": item.get("payload_hash"),
        }
    return result


def reconcile(local_items: list[dict], incoming_items: list[dict], *, peer_system_uuid: str) -> dict:
    """Classify peer-owned updates without mutating storage."""
    local = {}
    for item in local_items:
        validate_item(item)
        key = item_key(item)
        if key in local:
            raise MetadataError(f"duplicate local metadata item: {key}")
        local[key] = item

    upserts, tombstones, ignored, conflicts = [], [], [], []
    seen = set()
    for incoming in incoming_items:
        validate_item(incoming, expected_owner=peer_system_uuid)
        key = item_key(incoming)
        if key in seen:
            raise MetadataError(f"peer sent duplicate metadata item: {key}")
        seen.add(key)
        current = local.get(key)
        if current is None or incoming["revision"] > current["revision"]:
            (tombstones if incoming["kind"] == "tombstone" else upserts).append(incoming)
        elif incoming["revision"] < current["revision"]:
            ignored.append(incoming)
        elif incoming["kind"] == current["kind"] and incoming.get("payload_hash") == current.get("payload_hash"):
            ignored.append(incoming)
        else:
            conflicts.append({"local": current, "incoming": incoming, "reason": "equal revision with different content"})
    return {"upserts": upserts, "tombstones": tombstones, "ignored": ignored, "conflicts": conflicts}
