"""Wire-level metadata sync over an authenticated direct connection
(2026-08-08, "wire repository summaries/record requests/data/tombstones
over authenticated direct connections. Apply only peer-owned records and
surface equal-revision conflicts for review."). pythia.core.sync.metadata
is pure data functions with no I/O -- this is the real exchange protocol
layered on top, using REPOSITORY_SUMMARY/RECORD_REQUEST/RECORD_DATA/
TOMBSTONE/SYNC_COMPLETE frames.

Deliberately does NOT write anything to storage itself -- reconcile()
(imported from metadata.py) only classifies upserts/tombstones/conflicts;
the caller (Backend, in this app) decides what to actually persist to
datadir, keeping "what did the wire protocol receive" and "what did we
choose to keep" as two separate, auditable steps -- the same "no false
success" separation the rest of this app already applies everywhere else.
"""

from __future__ import annotations

from pythia.core.sync import metadata, protocol


async def send_summary_and_records(connection, local_items: list, *, stream_id: int, timeout: float = 30.0) -> dict:
    """Offering side: publish our own summary, then send only the specific
    records the peer actually requests. Never pushes anything unrequested."""
    summary = metadata.summarize(local_items)
    await connection.send_json(protocol.MessageType.REPOSITORY_SUMMARY, summary, stream_id=stream_id)

    frame = await connection.recv_frame(timeout=timeout)
    if frame.message_type != protocol.MessageType.RECORD_REQUEST:
        raise protocol.ProtocolError(f"expected RECORD_REQUEST, got {frame.message_type.name}")
    requested_keys = set((frame.json() or {}).get("keys") or [])

    by_key = {metadata.item_key(item): item for item in local_items}
    sent = 0
    for key in requested_keys:
        item = by_key.get(key)
        if item is None:
            continue
        message_type = protocol.MessageType.TOMBSTONE if item["kind"] == "tombstone" else protocol.MessageType.RECORD_DATA
        await connection.send_json(message_type, item, stream_id=stream_id)
        sent += 1
    await connection.send_json(protocol.MessageType.SYNC_COMPLETE, {}, stream_id=stream_id)
    return {"status": "ok", "sent": sent}


async def receive_summary_and_reconcile(
    connection, local_items: list, *, peer_system_uuid: str, stream_id: int, timeout: float = 30.0
) -> dict:
    """Receiving side: read the peer's summary, request only the records it
    actually owns (namespace-checked -- never requests a peer's claim about
    a THIRD system's data through this exchange) that are newer than what
    we already know, verify every incoming item's real ownership
    (metadata.validate_item(expected_owner=...) -- a peer can never publish
    a record it doesn't own, enforced here, not just hoped for), then
    return the real classification from metadata.reconcile() -- including
    real equal-revision conflicts, surfaced, never auto-resolved."""
    frame = await connection.recv_frame(timeout=timeout)
    if frame.message_type != protocol.MessageType.REPOSITORY_SUMMARY:
        raise protocol.ProtocolError(f"expected REPOSITORY_SUMMARY, got {frame.message_type.name}")
    remote_summary = frame.json() or {}
    return await reconcile_from_summary(
        connection, remote_summary, local_items, peer_system_uuid=peer_system_uuid, stream_id=stream_id, timeout=timeout
    )


async def reconcile_from_summary(
    connection, remote_summary: dict, local_items: list, *, peer_system_uuid: str, stream_id: int, timeout: float = 30.0
) -> dict:
    """The receiving-role continuation of receive_summary_and_reconcile(),
    factored out so a caller that has already read the REPOSITORY_SUMMARY
    frame off the wire (e.g. SyncService's passive listener, which must peek
    a frame's message type before it knows which exchange is starting) can
    reuse the exact same request/receive/reconcile logic without a second,
    conflicting recv_frame() call."""
    local_summary = metadata.summarize(local_items)

    prefix = f"{peer_system_uuid}:"

    def _worth_requesting(remote_meta: dict, local_meta: dict | None) -> bool:
        if local_meta is None or remote_meta["revision"] > local_meta["revision"]:
            return True
        if remote_meta["revision"] == local_meta["revision"]:
            # Same revision: only worth fetching if content actually differs,
            # so a real equal-revision conflict can be surfaced below --
            # skipping this would silently hide the conflict instead of
            # detecting it.
            return remote_meta.get("payload_hash") != local_meta.get("payload_hash")
        return False

    requested_keys = [
        key
        for key, remote_meta in remote_summary.items()
        if key.startswith(prefix) and _worth_requesting(remote_meta, local_summary.get(key))
    ]
    await connection.send_json(protocol.MessageType.RECORD_REQUEST, {"keys": requested_keys}, stream_id=stream_id)

    incoming = []
    while True:
        frame = await connection.recv_frame(timeout=timeout)
        if frame.message_type == protocol.MessageType.SYNC_COMPLETE:
            break
        if frame.message_type not in (protocol.MessageType.RECORD_DATA, protocol.MessageType.TOMBSTONE):
            raise protocol.ProtocolError(f"unexpected message during metadata sync: {frame.message_type.name}")
        item = frame.json()
        metadata.validate_item(item, expected_owner=peer_system_uuid)
        incoming.append(item)

    result = metadata.reconcile(local_items, incoming, peer_system_uuid=peer_system_uuid)
    result["requested"] = len(requested_keys)
    result["received"] = len(incoming)
    return result


async def pull_and_reconcile(
    connection, local_items: list, *, peer_system_uuid: str, stream_id: int, timeout: float = 30.0
) -> dict:
    """Puller side (2026-08-08, Codex sync review, "make one user action
    genuinely bidirectional ... or provide explicit separate Push/Pull
    actions"): explicitly asks the peer to publish ITS OWN real state
    (`METADATA_PULL_REQUEST`), then completes the identical receiving-role
    exchange `receive_summary_and_reconcile()` already implements. Without
    this explicit request, both sides would default to receiving first
    (see `SyncService._on_connected()`'s own receiving-role listener) and
    deadlock -- a real, previously-unaddressed limitation of the one-way
    push primitive this module shipped with initially."""
    await connection.send_json(protocol.MessageType.METADATA_PULL_REQUEST, {}, stream_id=stream_id)
    return await receive_summary_and_reconcile(
        connection, local_items, peer_system_uuid=peer_system_uuid, stream_id=stream_id, timeout=timeout
    )
