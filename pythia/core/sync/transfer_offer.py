"""Real offer/accept/reject negotiation in front of transfer_session.py's
block-level exchange (2026-08-08, "per-game transfer actions ... offer/send,
accept or reject"). transfer_session.send_game()/receive_game() start
exchanging MANIFEST/BLOCK_* immediately -- this module is what makes sure
the receiving side has explicitly agreed before a single byte moves,
using the real TRANSFER_OFFER/TRANSFER_ACCEPT/TRANSFER_REJECT message types
pythia.core.sync.protocol already defines for exactly this.
"""

from __future__ import annotations

from pathlib import Path

from pythia.core.sync import protocol, transfer_session


async def offer_and_send_game(
    connection, root: str | Path, *, game_id: str, display_name: str, stream_id: int, timeout: float = 30.0
) -> dict:
    """Sending side. Never calls transfer_session.send_game() (and so never
    sends a single block) until the peer has replied with a real
    TRANSFER_ACCEPT."""
    await connection.send_json(
        protocol.MessageType.TRANSFER_OFFER, {"game_id": game_id, "display_name": display_name}, stream_id=stream_id
    )
    reply = await connection.recv_frame(timeout=timeout)
    if reply.message_type == protocol.MessageType.TRANSFER_REJECT:
        return {"status": "rejected", "message": (reply.json() or {}).get("message", "")}
    if reply.message_type != protocol.MessageType.TRANSFER_ACCEPT:
        raise protocol.ProtocolError(f"expected TRANSFER_ACCEPT or TRANSFER_REJECT, got {reply.message_type.name}")
    result = await transfer_session.send_game(connection, root, stream_id=stream_id)
    result.setdefault("status", "ok")
    return result


async def wait_for_offer(connection, *, timeout: float | None = None) -> dict:
    """Receiving side, step 1: block until a real TRANSFER_OFFER arrives,
    and return it for the caller (a real person, via the native UI) to
    decide on -- never auto-accepted here."""
    frame = await connection.recv_frame(timeout=timeout)
    if frame.message_type != protocol.MessageType.TRANSFER_OFFER:
        raise protocol.ProtocolError(f"expected TRANSFER_OFFER, got {frame.message_type.name}")
    return frame.json() or {}


async def reject_offer(connection, *, message: str = "Declined.", stream_id: int) -> None:
    await connection.send_json(protocol.MessageType.TRANSFER_REJECT, {"message": message}, stream_id=stream_id)


async def accept_offer_and_receive_game(
    connection, destination: str | Path, cache: str | Path, *, stream_id: int
) -> dict:
    """Receiving side, step 2 -- only ever called after the caller has
    already decided to accept (see wait_for_offer() above). Sends the real
    TRANSFER_ACCEPT, then runs the real block-level receive
    (transfer_session.receive_game(), which is itself already resume/cache-
    reuse-aware)."""
    await connection.send_json(protocol.MessageType.TRANSFER_ACCEPT, {}, stream_id=stream_id)
    result = await transfer_session.receive_game(connection, destination, cache, stream_id=stream_id)
    result.setdefault("status", "ok")
    return result
