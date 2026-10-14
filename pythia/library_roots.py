"""Persistent folders that Pythia can rescan as library sources."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path


class LibraryRootError(ValueError):
    pass


def _store_path(base_dir: Path | None = None) -> Path:
    if base_dir is None:
        from pythia.identity import app_data_dir

        base_dir = app_data_dir()
    return Path(base_dir) / "library_roots.json"


def _path_key(value: str) -> str:
    normalized = os.path.normpath(os.path.abspath(os.path.expanduser(value)))
    return os.path.normcase(normalized)


def _validate(root: dict) -> dict:
    if not isinstance(root, dict):
        raise LibraryRootError("library root must be an object")
    required = {"id", "path", "name", "enabled", "recursive", "added_at"}
    if not required.issubset(root):
        raise LibraryRootError("library root is missing required fields")
    if not all(isinstance(root[key], str) and root[key] for key in ("id", "path", "name")):
        raise LibraryRootError("library root id, path, and name must be non-empty strings")
    if not isinstance(root["enabled"], bool) or not isinstance(root["recursive"], bool):
        raise LibraryRootError("library root flags must be booleans")
    if not isinstance(root["added_at"], int):
        raise LibraryRootError("library root added_at must be an integer")
    source = root.get("source")
    if source is not None and (not isinstance(source, str) or not source):
        raise LibraryRootError("library root source must be null or a non-empty plugin ID")
    return dict(root)


def list_roots(*, base_dir: Path | None = None) -> list[dict]:
    path = _store_path(base_dir)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryRootError(f"could not read library roots: {exc}") from exc
    if not isinstance(raw, list):
        raise LibraryRootError("library roots file must contain a list")
    roots, ids, paths = [], set(), set()
    for value in raw:
        root = _validate(value)
        key = _path_key(root["path"])
        if root["id"] in ids or key in paths:
            raise LibraryRootError("library roots file contains duplicate ids or paths")
        ids.add(root["id"])
        paths.add(key)
        roots.append(root)
    return roots


def _write(roots: list[dict], *, base_dir: Path | None = None) -> None:
    path = _store_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".library-roots-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            json.dump(roots, output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def add_root(
    path: str,
    *,
    name: str | None = None,
    enabled: bool = True,
    recursive: bool = True,
    source: str | None = None,
    require_exists: bool = True,
    base_dir: Path | None = None,
) -> dict:
    if not isinstance(path, str) or not path.strip():
        raise LibraryRootError("library root path cannot be empty")
    resolved = os.path.normpath(os.path.abspath(os.path.expanduser(path.strip())))
    if require_exists and not Path(resolved).is_dir():
        raise LibraryRootError("library root is not an accessible directory")
    roots = list_roots(base_dir=base_dir)
    key = _path_key(resolved)
    if any(_path_key(root["path"]) == key for root in roots):
        raise LibraryRootError("that library root is already tracked")
    root = _validate({
        "id": str(uuid.uuid4()),
        "path": resolved,
        "name": (name or Path(resolved).name or resolved).strip(),
        "enabled": enabled,
        "recursive": recursive,
        "source": source,
        "added_at": int(time.time()),
    })
    roots.append(root)
    _write(roots, base_dir=base_dir)
    return root


def update_root(root_id: str, changes: dict, *, base_dir: Path | None = None) -> dict:
    allowed = {"name", "enabled", "recursive", "source", "path"}
    unknown = set(changes) - allowed
    if unknown:
        raise LibraryRootError(f"unknown library root field(s): {', '.join(sorted(unknown))}")
    roots = list_roots(base_dir=base_dir)
    index = next((i for i, root in enumerate(roots) if root["id"] == root_id), None)
    if index is None:
        raise LibraryRootError("library root was not found")
    updated = dict(roots[index])
    updated.update(changes)
    if "path" in changes:
        updated["path"] = os.path.normpath(os.path.abspath(os.path.expanduser(str(changes["path"]))))
        key = _path_key(updated["path"])
        if any(i != index and _path_key(root["path"]) == key for i, root in enumerate(roots)):
            raise LibraryRootError("that library root is already tracked")
    updated = _validate(updated)
    roots[index] = updated
    _write(roots, base_dir=base_dir)
    return updated


def remove_root(root_id: str, *, base_dir: Path | None = None) -> dict:
    roots = list_roots(base_dir=base_dir)
    kept = [root for root in roots if root["id"] != root_id]
    if len(kept) == len(roots):
        raise LibraryRootError("library root was not found")
    _write(kept, base_dir=base_dir)
    return {"status": "ok", "id": root_id}
