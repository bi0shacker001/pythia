"""Atomic mounted-folder staging for metadata snapshots.

The target may be a NAS share or a folder synchronized by a cloud provider. Pythia
uses only ordinary files and never starts or shells out to the provider's client.
Credentials and trust state are not accepted as metadata records and never staged.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from pythia.core.sync import metadata

MAX_ITEMS = 250_000
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024


class StagingError(RuntimeError):
    pass


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _repository(root: str | Path, repository_id: str) -> Path:
    if not repository_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in repository_id):
        raise StagingError("repository_id contains unsafe characters")
    return Path(root) / "pythia-sync" / repository_id


def _safe_name(value: str, field: str) -> str:
    if not value or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in value):
        raise StagingError(f"{field} contains unsafe characters")
    return value


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def export_snapshot(
    items: list[dict], root: str | Path, *, repository_id: str = "default",
    created_at: int | None = None, writer_id: str | None = None,
) -> dict:
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise StagingError("snapshot has an invalid item count")
    records = []
    seen = set()
    total = 0
    repo = _repository(root, repository_id)
    for item in items:
        metadata.validate_item(item)
    for item in sorted(items, key=metadata.item_key):
        key = metadata.item_key(item)
        if key in seen:
            raise StagingError(f"duplicate snapshot item: {key}")
        seen.add(key)
        data = _canonical(item)
        total += len(data)
        if total > MAX_SNAPSHOT_BYTES:
            raise StagingError("snapshot exceeds the metadata size limit")
        digest = hashlib.sha256(data).hexdigest()
        object_path = repo / "objects" / digest[:2] / f"{digest[2:]}.json"
        if not object_path.is_file():
            _atomic_write(object_path, data)
        records.append({"key": key, "sha256": digest, "size": len(data)})

    manifest = {
        "version": 1,
        "repository_id": repository_id,
        "created_at": int(time.time() if created_at is None else created_at),
        "records": records,
    }
    manifest_data = _canonical(manifest)
    snapshot_id = hashlib.sha256(manifest_data).hexdigest()
    _atomic_write(repo / "snapshots" / f"{snapshot_id}.json", manifest_data)
    _atomic_write(repo / "HEAD", (snapshot_id + "\n").encode("ascii"))
    if writer_id is not None:
        writer_id = _safe_name(writer_id, "writer_id")
        _atomic_write(repo / "heads" / f"{writer_id}.head", (snapshot_id + "\n").encode("ascii"))
    return {"status": "ok", "snapshot_id": snapshot_id, "items": len(records), "bytes": total}


def import_snapshot(root: str | Path, *, repository_id: str = "default", snapshot_id: str | None = None) -> dict:
    repo = _repository(root, repository_id)
    if snapshot_id is None:
        try:
            snapshot_id = (repo / "HEAD").read_text(encoding="ascii").strip()
        except OSError as exc:
            raise StagingError(f"could not read staged snapshot head: {exc}") from exc
    if len(snapshot_id) != 64 or any(c not in "0123456789abcdef" for c in snapshot_id):
        raise StagingError("staged snapshot head is invalid")
    try:
        manifest_data = (repo / "snapshots" / f"{snapshot_id}.json").read_bytes()
    except OSError as exc:
        raise StagingError(f"could not read staged snapshot: {exc}") from exc
    if hashlib.sha256(manifest_data).hexdigest() != snapshot_id:
        raise StagingError("staged snapshot manifest hash mismatch")
    try:
        manifest = json.loads(manifest_data)
    except json.JSONDecodeError as exc:
        raise StagingError("staged snapshot manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or manifest.get("repository_id") != repository_id:
        raise StagingError("unsupported or mismatched staged snapshot")
    records = manifest.get("records")
    if not isinstance(records, list) or len(records) > MAX_ITEMS:
        raise StagingError("staged snapshot record list is invalid")
    items, total, seen = [], 0, set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"key", "sha256", "size"}:
            raise StagingError("staged snapshot record descriptor is invalid")
        digest = record["sha256"]
        if not isinstance(digest, str) or len(digest) != 64:
            raise StagingError("staged object hash is invalid")
        try:
            data = (repo / "objects" / digest[:2] / f"{digest[2:]}.json").read_bytes()
        except OSError as exc:
            raise StagingError(f"could not read staged object: {exc}") from exc
        total += len(data)
        if total > MAX_SNAPSHOT_BYTES or len(data) != record["size"] or hashlib.sha256(data).hexdigest() != digest:
            raise StagingError("staged object size or hash mismatch")
        try:
            item = json.loads(data)
            metadata.validate_item(item)
        except (json.JSONDecodeError, metadata.MetadataError) as exc:
            raise StagingError(f"staged metadata object is invalid: {exc}") from exc
        key = metadata.item_key(item)
        if key != record["key"] or key in seen:
            raise StagingError("staged object key mismatch or duplicate")
        seen.add(key)
        items.append(item)
    return {"status": "ok", "snapshot_id": snapshot_id, "items": items, "created_at": manifest.get("created_at")}


def import_all_heads(root: str | Path, *, repository_id: str = "default") -> dict:
    """Load every device head and return a safe, conflict-aware merged view.

    Higher revisions win. Equal-revision divergent objects are reported and
    omitted from ``items`` so callers cannot silently choose a writer.
    """
    repo = _repository(root, repository_id)
    head_ids: dict[str, str] = {}
    heads_dir = repo / "heads"
    if heads_dir.is_dir():
        for path in sorted(heads_dir.glob("*.head")):
            writer = _safe_name(path.stem, "writer_id")
            try:
                head_ids[writer] = path.read_text(encoding="ascii").strip()
            except OSError as exc:
                raise StagingError(f"could not read staged head {writer}: {exc}") from exc
    if not head_ids:
        loaded = import_snapshot(root, repository_id=repository_id)
        return {"status": "ok", "heads": {"legacy": loaded["snapshot_id"]}, "items": loaded["items"], "conflicts": []}

    selected: dict[tuple, dict] = {}
    conflicted: set[tuple] = set()
    conflict_rows: list[dict] = []
    for writer, snapshot_id in head_ids.items():
        loaded = import_snapshot(root, repository_id=repository_id, snapshot_id=snapshot_id)
        for item in loaded["items"]:
            key = metadata.item_key(item)
            current = selected.get(key)
            if current is None or int(item["revision"]) > int(current["revision"]):
                selected[key] = item
                conflicted.discard(key)
                continue
            if int(item["revision"]) == int(current["revision"]) and _canonical(item) != _canonical(current):
                conflicted.add(key)
                conflict_rows.append({"key": key, "writer": writer, "revision": item["revision"]})
    return {
        "status": "conflict" if conflicted else "ok",
        "heads": head_ids,
        "items": [selected[key] for key in sorted(selected) if key not in conflicted],
        "conflicts": [row for row in conflict_rows if row["key"] in conflicted],
    }
