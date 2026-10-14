"""End-to-end game transfer over an authenticated ``DirectConnection``.

The relay protocol cannot carry any message used here. Callers must explicitly
enable synchronization and establish a trusted direct connection before invoking
these coroutines; importing this module has no side effects.
"""

from __future__ import annotations

from pathlib import Path

from pythia.core.sync import protocol
from pythia.core.sync.transfer import (
    TransferError,
    build_manifest,
    materialize,
    missing_blocks,
    store_block,
    validate_manifest,
)

REQUEST_BATCH = 512
HASH_BYTES = 64


def _json_frame(frame: protocol.Frame, expected: protocol.MessageType, stream_id: int) -> dict:
    if frame.message_type != expected or frame.stream_id != stream_id:
        raise TransferError(
            f"expected {expected.name} on stream {stream_id}, got "
            f"{frame.message_type.name} on stream {frame.stream_id}"
        )
    value = frame.json()
    if not isinstance(value, dict):
        raise TransferError(f"{expected.name} payload must be an object")
    return value


def _requested_blocks(root: Path, manifest: dict, requested: set[str]):
    """Yield requested blocks directly from disk without retaining game data."""
    block_size = manifest["block_size"]
    remaining = set(requested)
    for entry in manifest["files"]:
        path = root / Path(*entry["path"].split("/"))
        with path.open("rb") as source:
            for expected in entry["blocks"]:
                chunk = source.read(block_size)
                if expected in remaining:
                    # The file may have changed after manifest construction.
                    import hashlib

                    if hashlib.sha256(chunk).hexdigest() != expected:
                        raise TransferError(f"source changed during transfer: {entry['path']}")
                    yield expected, chunk
                    remaining.remove(expected)
    if remaining:
        raise TransferError(f"requested block(s) are absent from source: {len(remaining)}")


async def send_game(connection, root: str | Path, *, stream_id: int) -> dict:
    """Serve one game tree until the peer verifies and completes the transfer."""
    if stream_id <= 0:
        raise TransferError("game transfers require a non-zero stream id")
    root = Path(root).resolve()
    manifest = build_manifest(root)
    offered = {digest for entry in manifest["files"] for digest in entry["blocks"]}
    await connection.send_json(protocol.MessageType.MANIFEST, manifest, stream_id=stream_id)

    sent = 0
    while True:
        frame = await connection.recv_frame()
        if frame.stream_id != stream_id:
            raise TransferError("received a frame for another transfer stream")
        if frame.message_type == protocol.MessageType.TRANSFER_CANCEL:
            reason = frame.json().get("message", "peer cancelled")
            raise TransferError(str(reason))
        if frame.message_type == protocol.MessageType.TRANSFER_COMPLETE:
            result = _json_frame(frame, protocol.MessageType.TRANSFER_COMPLETE, stream_id)
            if result.get("status") != "ok":
                raise TransferError(str(result.get("message") or "peer did not complete transfer"))
            return {"status": "ok", "files": len(manifest["files"]), "blocks_sent": sent}
        request = _json_frame(frame, protocol.MessageType.BLOCK_REQUEST, stream_id)
        hashes = request.get("hashes")
        if not isinstance(hashes, list) or not hashes or len(hashes) > REQUEST_BATCH:
            raise TransferError("BLOCK_REQUEST hashes must be a non-empty bounded list")
        if any(not isinstance(digest, str) or digest not in offered for digest in hashes):
            raise TransferError("peer requested a block outside the offered manifest")
        unique = set(hashes)
        if len(unique) != len(hashes):
            raise TransferError("BLOCK_REQUEST contains duplicate hashes")
        for digest, data in _requested_blocks(root, manifest, unique):
            await connection.send_frame(
                protocol.Frame(protocol.MessageType.BLOCK_DATA, digest.encode("ascii") + data, stream_id)
            )
            sent += 1


async def receive_game(connection, destination: str | Path, cache: str | Path, *, stream_id: int) -> dict:
    """Request missing blocks, verify them, and atomically reconstruct a game."""
    if stream_id <= 0:
        raise TransferError("game transfers require a non-zero stream id")
    frame = await connection.recv_frame()
    manifest = _json_frame(frame, protocol.MessageType.MANIFEST, stream_id)
    validate_manifest(manifest)
    needed = missing_blocks(manifest, cache)
    received = 0
    try:
        for offset in range(0, len(needed), REQUEST_BATCH):
            batch = needed[offset : offset + REQUEST_BATCH]
            await connection.send_json(protocol.MessageType.BLOCK_REQUEST, {"hashes": batch}, stream_id=stream_id)
            pending = set(batch)
            while pending:
                block_frame = await connection.recv_frame()
                if block_frame.message_type == protocol.MessageType.TRANSFER_CANCEL:
                    raise TransferError(str(block_frame.json().get("message", "peer cancelled")))
                if block_frame.message_type != protocol.MessageType.BLOCK_DATA or block_frame.stream_id != stream_id:
                    raise TransferError("expected BLOCK_DATA for the active transfer")
                if len(block_frame.payload) < HASH_BYTES:
                    raise TransferError("BLOCK_DATA payload is missing its hash")
                try:
                    digest = block_frame.payload[:HASH_BYTES].decode("ascii")
                except UnicodeDecodeError as exc:
                    raise TransferError("BLOCK_DATA hash is not ASCII") from exc
                if digest not in pending:
                    raise TransferError("received an unrequested or duplicate block")
                store_block(cache, digest, block_frame.payload[HASH_BYTES:])
                pending.remove(digest)
                received += 1
        result = materialize(manifest, cache, destination)
    except Exception as exc:
        await connection.send_json(
            protocol.MessageType.TRANSFER_CANCEL,
            {"message": str(exc)},
            stream_id=stream_id,
        )
        raise
    await connection.send_json(
        protocol.MessageType.TRANSFER_COMPLETE,
        {"status": "ok", "files": result["files"]},
        stream_id=stream_id,
    )
    return {"status": "ok", "files": result["files"], "blocks_received": received}
