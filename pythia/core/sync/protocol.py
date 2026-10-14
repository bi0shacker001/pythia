"""Pythia's bounded, transport-independent synchronization protocol.

The same frame format is used over direct and relayed connections.  Relay
connections are deliberately restricted to small control messages: bulk game,
save, archive, and block payloads are rejected before they can be encoded or
accepted.  This invariant belongs here rather than in UI or routing code so a
future caller cannot accidentally turn a public relay into a data plane.
"""

from __future__ import annotations

import enum
import json
import struct
from dataclasses import dataclass
from typing import Any


MAGIC = b"PYTH"
PROTOCOL_VERSION = 1
HEADER = struct.Struct("!4sBBHII")
MAX_CONTROL_PAYLOAD = 256 * 1024
MAX_DIRECT_PAYLOAD = 8 * 1024 * 1024


class ProtocolError(ValueError):
    """A malformed, unsupported, or policy-forbidden frame."""


class MessageType(enum.IntEnum):
    HELLO = 1
    CAPABILITIES = 2
    PING = 3
    PONG = 4
    CANDIDATE_OFFER = 5
    CANDIDATE_ANSWER = 6
    ROUTE_STATUS = 7
    REPOSITORY_SUMMARY = 8
    RECORD_REQUEST = 9
    RECORD_DATA = 10
    TOMBSTONE = 11
    SYNC_COMPLETE = 12
    TRANSFER_OFFER = 13
    TRANSFER_ACCEPT = 14
    TRANSFER_REJECT = 15
    TRANSFER_STATUS = 16
    METADATA_PULL_REQUEST = 17

    # These messages are valid only on an authenticated direct connection.
    MANIFEST = 32
    BLOCK_REQUEST = 33
    BLOCK_DATA = 34
    BLOCK_ACK = 35
    TRANSFER_COMPLETE = 36
    TRANSFER_CANCEL = 37
    SAVE_DATA = 38
    ARCHIVE_DATA = 39


RELAY_ALLOWED_TYPES = frozenset(
    {
        MessageType.HELLO,
        MessageType.CAPABILITIES,
        MessageType.PING,
        MessageType.PONG,
        MessageType.CANDIDATE_OFFER,
        MessageType.CANDIDATE_ANSWER,
        MessageType.ROUTE_STATUS,
        MessageType.REPOSITORY_SUMMARY,
        MessageType.RECORD_REQUEST,
        MessageType.RECORD_DATA,
        MessageType.TOMBSTONE,
        MessageType.SYNC_COMPLETE,
        MessageType.TRANSFER_OFFER,
        MessageType.TRANSFER_ACCEPT,
        MessageType.TRANSFER_REJECT,
        MessageType.TRANSFER_STATUS,
        MessageType.METADATA_PULL_REQUEST,
    }
)


@dataclass(frozen=True, slots=True)
class Frame:
    message_type: MessageType
    payload: bytes
    stream_id: int = 0
    flags: int = 0

    def json(self) -> Any:
        try:
            return json.loads(self.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("frame payload is not valid UTF-8 JSON") from exc


def _validate_frame(frame: Frame, *, relayed: bool) -> None:
    if not 0 <= frame.stream_id <= 0xFFFFFFFF:
        raise ProtocolError("stream_id is outside the unsigned 32-bit range")
    if not 0 <= frame.flags <= 0xFFFF:
        raise ProtocolError("flags is outside the unsigned 16-bit range")
    if relayed and frame.message_type not in RELAY_ALLOWED_TYPES:
        raise ProtocolError(f"{frame.message_type.name} is forbidden on relay connections")
    limit = MAX_CONTROL_PAYLOAD if relayed else MAX_DIRECT_PAYLOAD
    if len(frame.payload) > limit:
        raise ProtocolError(f"frame payload exceeds the {limit}-byte transport limit")


def encode_frame(frame: Frame, *, relayed: bool = False) -> bytes:
    """Encode one frame, enforcing the selected transport's policy."""
    _validate_frame(frame, relayed=relayed)
    return HEADER.pack(
        MAGIC,
        PROTOCOL_VERSION,
        int(frame.message_type),
        frame.flags,
        frame.stream_id,
        len(frame.payload),
    ) + frame.payload


def encode_json(
    message_type: MessageType,
    value: Any,
    *,
    stream_id: int = 0,
    flags: int = 0,
    relayed: bool = False,
) -> bytes:
    payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return encode_frame(Frame(message_type, payload, stream_id, flags), relayed=relayed)


class FrameDecoder:
    """Incrementally decode frames from arbitrary socket-sized chunks."""

    def __init__(self, *, relayed: bool = False):
        self.relayed = relayed
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        while len(self._buffer) >= HEADER.size:
            magic, version, raw_type, flags, stream_id, size = HEADER.unpack_from(self._buffer)
            if magic != MAGIC:
                raise ProtocolError("invalid frame magic")
            if version != PROTOCOL_VERSION:
                raise ProtocolError(f"unsupported protocol version {version}")
            try:
                message_type = MessageType(raw_type)
            except ValueError as exc:
                raise ProtocolError(f"unknown message type {raw_type}") from exc
            limit = MAX_CONTROL_PAYLOAD if self.relayed else MAX_DIRECT_PAYLOAD
            if size > limit:
                raise ProtocolError(f"declared payload exceeds the {limit}-byte transport limit")
            if self.relayed and message_type not in RELAY_ALLOWED_TYPES:
                raise ProtocolError(f"{message_type.name} is forbidden on relay connections")
            total = HEADER.size + size
            if len(self._buffer) < total:
                break
            payload = bytes(self._buffer[HEADER.size:total])
            del self._buffer[:total]
            frame = Frame(message_type, payload, stream_id, flags)
            _validate_frame(frame, relayed=self.relayed)
            frames.append(frame)
        return frames

