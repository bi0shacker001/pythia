"""Content-addressed game transfer primitives for direct peer connections.

This module is deliberately transport-independent: discovery and relays may help
peers meet, but game bytes are represented as verified blocks which a direct
connection can request and deliver. Importing it starts no listener or worker.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath

DEFAULT_BLOCK_SIZE = 4 * 1024 * 1024
MAX_BLOCK_SIZE = 16 * 1024 * 1024
MAX_FILES = 250_000


class TransferError(ValueError):
    """A manifest, block, or materialization request is unsafe or invalid."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative(value: str) -> Path:
    posix = PurePosixPath(value)
    if not value or posix.is_absolute() or ".." in posix.parts or ":" in posix.parts[0]:
        raise TransferError(f"unsafe transfer path: {value!r}")
    return Path(*posix.parts)


def build_manifest(root: str | Path, block_size: int = DEFAULT_BLOCK_SIZE) -> dict:
    """Describe regular files below *root* as ordered SHA-256 blocks."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise TransferError(f"game root is not a directory: {root}")
    if not 1 <= block_size <= MAX_BLOCK_SIZE:
        raise TransferError(f"block_size must be between 1 and {MAX_BLOCK_SIZE}")

    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TransferError(f"symbolic links are not transferable: {path}")
        if not path.is_file():
            continue
        if len(files) >= MAX_FILES:
            raise TransferError(f"game contains more than {MAX_FILES} files")
        blocks = []
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(block_size):
                size += len(chunk)
                blocks.append(_sha256(chunk))
        files.append({
            "path": path.relative_to(root).as_posix(),
            "size": size,
            "blocks": blocks,
        })
    return {"version": 1, "block_size": block_size, "files": files}


def validate_manifest(manifest: dict) -> None:
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise TransferError("unsupported transfer manifest")
    block_size = manifest.get("block_size")
    files = manifest.get("files")
    if not isinstance(block_size, int) or not 1 <= block_size <= MAX_BLOCK_SIZE:
        raise TransferError("invalid manifest block size")
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise TransferError("invalid manifest file list")
    seen = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise TransferError("invalid manifest file entry")
        relative = _safe_relative(entry.get("path", "")).as_posix()
        if relative in seen:
            raise TransferError(f"duplicate manifest path: {relative}")
        seen.add(relative)
        size, blocks = entry.get("size"), entry.get("blocks")
        if not isinstance(size, int) or size < 0 or not isinstance(blocks, list):
            raise TransferError(f"invalid size or blocks for {relative}")
        if any(not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value) for value in blocks):
            raise TransferError(f"invalid block hash for {relative}")
        expected = 0 if size == 0 else (size + block_size - 1) // block_size
        if len(blocks) != expected:
            raise TransferError(f"block count does not match size for {relative}")


def block_path(cache: str | Path, digest: str) -> Path:
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise TransferError("invalid SHA-256 block id")
    return Path(cache) / digest[:2] / digest[2:]


def store_block(cache: str | Path, digest: str, data: bytes) -> Path:
    """Verify and atomically add a received block to the local cache."""
    if len(data) > MAX_BLOCK_SIZE or _sha256(data) != digest:
        raise TransferError("received block does not match its SHA-256 id")
    target = block_path(cache, digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return target
    fd, temporary = tempfile.mkstemp(prefix=".block-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def missing_blocks(manifest: dict, cache: str | Path) -> list[str]:
    validate_manifest(manifest)
    wanted = dict.fromkeys(block for entry in manifest["files"] for block in entry["blocks"])
    return [digest for digest in wanted if not block_path(cache, digest).is_file()]


def materialize(manifest: dict, cache: str | Path, destination: str | Path) -> dict:
    """Reconstruct verified files, replacing each destination file atomically."""
    validate_manifest(manifest)
    missing = missing_blocks(manifest, cache)
    if missing:
        raise TransferError(f"cannot materialize: {len(missing)} block(s) are missing")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    written = 0
    for entry in manifest["files"]:
        target = destination / _safe_relative(entry["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".transfer-", dir=target.parent)
        try:
            remaining = entry["size"]
            with os.fdopen(fd, "wb") as output:
                for digest in entry["blocks"]:
                    data = block_path(cache, digest).read_bytes()
                    if _sha256(data) != digest or len(data) > manifest["block_size"]:
                        raise TransferError(f"cached block failed verification: {digest}")
                    output.write(data)
                    remaining -= len(data)
                if remaining != 0:
                    raise TransferError(f"materialized size mismatch for {entry['path']}")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            written += 1
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    return {"status": "ok", "files": written}
