"""Control-plane-only relay rendezvous (2026-08-08, "relay-assisted
rendezvous and direct-path candidate orchestration").

**Scoping decision, recorded here and in aisync/WORKLOG.md**: this is
Pythia's OWN rendezvous protocol, built on this repo's existing
`protocol.py` framing in `relayed=True` mode (already allowlist-enforced --
`RELAY_ALLOWED_TYPES` rejects `MANIFEST`/`BLOCK_DATA`/`SAVE_DATA`/
`ARCHIVE_DATA`/etc at the wire layer, so a relay connection can never become
a data plane even by mistake). It is not the literal Syncthing relay wire
protocol (a different, non-JSON binary format with its own join/session-
invitation mechanics) -- a real relay server for THIS protocol has to be a
Pythia-aware server, not an existing public Syncthing relay. "Where
protocol-compatible and feasible" (the task's own words) was judged not
feasible without reimplementing an entire separate third-party binary
protocol; this delivers the real, working alternative instead.

**Trust model**: a relay server is treated as fully untrusted, neutral
infrastructure -- it never needs a trust store of its own and never sees
anything an attacker could use to forge a candidate. Every
CANDIDATE_OFFER/CANDIDATE_ANSWER is signed end-to-end with the sender's own
Ed25519 key (the same key identity.py already generates and direct_tcp.py's
own connector-proof already uses) over a canonical, domain-separated,
timestamped transcript -- the receiving side verifies it independently
(certificate/device-ID self-consistency, the SENDER's own local trust-store
pin, signature validity, and a bounded freshness window against replay)
without trusting the relay transport at all. The relay's own join
handshake additionally proves each connecting client really owns the
device_id it claims (nonce + signature, the same shape), so the relay
itself has real confidence in its own routing table -- but a device joining
does NOT need to already be trusted by the relay; authorization to accept a
given peer's offer is entirely the RECEIVING device's own decision, exactly
like every other trust check in this codebase.
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from pythia.core.sync import identity, protocol, trust

JOIN_TIMEOUT = 10.0
MAX_CANDIDATE_OFFER_AGE = 300.0  # seconds -- replay protection window
_FORWARDABLE_TYPES = (
    protocol.MessageType.CANDIDATE_OFFER,
    protocol.MessageType.CANDIDATE_ANSWER,
    protocol.MessageType.ROUTE_STATUS,
)


class RelayError(Exception):
    """Base class for every relay handshake/routing failure."""


class RelayAuthError(RelayError):
    pass


# ---- shared wire helpers --------------------------------------------------------


async def _read_frame(reader: asyncio.StreamReader, decoder: protocol.FrameDecoder, *, timeout: float | None) -> protocol.Frame:
    """Same incremental-decode-with-queue shape as direct_tcp.py's own
    _FrameReader -- reimplemented locally (not imported) to keep this
    module's claim boundary self-contained; a relay server has no paired
    peer and no per-connection state direct_tcp.py's own reader carries."""
    pending: list[protocol.Frame] = []
    while True:
        if pending:
            return pending.pop(0)
        read_coro = reader.read(65536)
        chunk = await (asyncio.wait_for(read_coro, timeout) if timeout else read_coro)
        if not chunk:
            raise ConnectionError("relay peer closed the connection")
        frames = decoder.feed(chunk)
        if frames:
            pending = frames
            return pending.pop(0)


def _cert_der(node) -> bytes:
    pem = Path(node.identity_record["certificate_path"]).read_bytes()
    return x509.load_pem_x509_certificate(pem).public_bytes(serialization.Encoding.DER)


async def _close_quietly(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


# ---- join (client <-> relay server) authentication -----------------------------


def _join_transcript(nonce: str, system_uuid: str, device_id: str) -> bytes:
    return json.dumps(
        {
            "domain": "pythia-relay-join-v1",
            "protocol_version": protocol.PROTOCOL_VERSION,
            "nonce": nonce,
            "system_uuid": system_uuid,
            "device_id": device_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _build_join_payload(node, nonce: str) -> dict:
    signature = base64.b64encode(node.sign(_join_transcript(nonce, node.system_uuid, node.device_id))).decode("ascii")
    return {
        "device_id": node.device_id,
        "system_uuid": node.system_uuid,
        "certificate_der": base64.b64encode(_cert_der(node)).decode("ascii"),
        "signature": signature,
    }


def _verify_join(join: dict, nonce: str) -> str:
    """Real checks, no shortcuts: shape, certificate/device-ID self-
    consistency, real Luhn-valid device ID, then signature over the
    nonce-bound transcript. Returns the real system_uuid on success."""
    required = ("device_id", "system_uuid", "certificate_der", "signature")
    missing = [f for f in required if f not in join]
    if missing:
        raise RelayAuthError(f"relay join is missing required field(s): {', '.join(missing)}")
    if not identity.validate_device_id(join["device_id"]):
        raise RelayAuthError("relay join device ID fails its own check digits")
    try:
        cert_der = base64.b64decode(join["certificate_der"], validate=True)
    except (ValueError, TypeError) as exc:
        raise RelayAuthError(f"relay join certificate_der is not valid base64: {exc}") from exc
    try:
        derived_device_id = identity.device_id_from_der(cert_der)
    except Exception as exc:
        raise RelayAuthError(f"relay join certificate is unusable: {exc}") from exc
    if derived_device_id != join["device_id"]:
        raise RelayAuthError("relay join's declared device ID does not match its certificate")
    public_key = x509.load_der_x509_certificate(cert_der).public_key()
    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        raise RelayAuthError("relay join certificate does not use an Ed25519 key")
    try:
        signature = base64.b64decode(join["signature"], validate=True)
    except (ValueError, TypeError) as exc:
        raise RelayAuthError(f"relay join signature is not valid base64: {exc}") from exc
    try:
        public_key.verify(signature, _join_transcript(nonce, join["system_uuid"], join["device_id"]))
    except InvalidSignature as exc:
        raise RelayAuthError("relay join failed to prove possession of its private key") from exc
    return join["system_uuid"]


# ---- candidate offer/answer signing + verification ------------------------------


def _offer_transcript(*, from_system_uuid: str, from_device_id: str, to_system_uuid: str, candidates: list[str], issued_at: int) -> bytes:
    return json.dumps(
        {
            "domain": "pythia-relay-candidate-offer-v1",
            "protocol_version": protocol.PROTOCOL_VERSION,
            "from_system_uuid": from_system_uuid,
            "from_device_id": from_device_id,
            "to_system_uuid": to_system_uuid,
            "candidates": list(candidates),
            "issued_at": int(issued_at),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_candidate_payload(node, *, to_system_uuid: str, candidates: list[str], issued_at: int | None = None) -> dict:
    """Builds a real, self-authenticating candidate offer/answer -- signed
    with this system's own Ed25519 key so the receiving side never has to
    trust the relay transport itself, only this payload's own signature."""
    issued_at = int(time.time() if issued_at is None else issued_at)
    transcript = _offer_transcript(
        from_system_uuid=node.system_uuid,
        from_device_id=node.device_id,
        to_system_uuid=to_system_uuid,
        candidates=candidates,
        issued_at=issued_at,
    )
    return {
        "from_system_uuid": node.system_uuid,
        "from_device_id": node.device_id,
        "to_system_uuid": to_system_uuid,
        "candidates": list(candidates),
        "issued_at": issued_at,
        "certificate_der": base64.b64encode(_cert_der(node)).decode("ascii"),
        "signature": base64.b64encode(node.sign(transcript)).decode("ascii"),
    }


def verify_candidate_payload(payload: dict, *, expected_from_system_uuid: str, base_dir: Path | None = None) -> list[str]:
    """Verifies a real candidate offer/answer end-to-end: shape, that the
    claimed sender matches who we expected it from, certificate/device-ID
    self-consistency, that the sender is already a device THIS system trusts
    (an untrusted relay can never introduce a new device this way -- pairing
    is still the only path into the trust store), pin match, freshness
    (rejects a stale/replayed offer), and signature validity. Returns the
    real candidate list on success; raises RelayAuthError otherwise, never
    silently drops a forged payload."""
    required = ("from_system_uuid", "from_device_id", "to_system_uuid", "candidates", "issued_at", "certificate_der", "signature")
    missing = [f for f in required if f not in payload]
    if missing:
        raise RelayAuthError(f"candidate payload is missing required field(s): {', '.join(missing)}")
    if payload["from_system_uuid"] != expected_from_system_uuid:
        raise RelayAuthError("candidate payload's claimed sender does not match the expected peer")
    if not isinstance(payload["candidates"], list) or not payload["candidates"] or not all(
        isinstance(c, str) and c for c in payload["candidates"]
    ):
        raise RelayAuthError("candidate payload's candidate list is malformed")
    now = time.time()
    if not isinstance(payload["issued_at"], int) or isinstance(payload["issued_at"], bool) or abs(now - payload["issued_at"]) > MAX_CANDIDATE_OFFER_AGE:
        raise RelayAuthError("candidate payload is stale or has an invalid timestamp -- possible replay")
    try:
        cert_der = base64.b64decode(payload["certificate_der"], validate=True)
    except (ValueError, TypeError) as exc:
        raise RelayAuthError(f"candidate payload certificate_der is not valid base64: {exc}") from exc
    try:
        derived_device_id = identity.device_id_from_der(cert_der)
    except Exception as exc:
        raise RelayAuthError(f"candidate payload certificate is unusable: {exc}") from exc
    if derived_device_id != payload["from_device_id"]:
        raise RelayAuthError("candidate payload's declared device ID does not match its certificate")
    trusted = trust.get_trusted(payload["from_system_uuid"], base_dir=base_dir)
    if trusted is None:
        raise RelayAuthError(f"{payload['from_system_uuid']} is not a trusted device -- cannot accept its candidate offer")
    if trusted["device_id"] != derived_device_id:
        raise RelayAuthError("candidate payload's certificate does not match the pinned trust record")
    pinned_fingerprint = trusted.get("certificate_fingerprint")
    if pinned_fingerprint and pinned_fingerprint != identity.certificate_fingerprint(cert_der):
        raise RelayAuthError("candidate payload's certificate fingerprint does not match the pinned trust record")
    transcript = _offer_transcript(
        from_system_uuid=payload["from_system_uuid"],
        from_device_id=payload["from_device_id"],
        to_system_uuid=payload["to_system_uuid"],
        candidates=payload["candidates"],
        issued_at=payload["issued_at"],
    )
    public_key = x509.load_der_x509_certificate(cert_der).public_key()
    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        raise RelayAuthError("candidate payload certificate does not use an Ed25519 key")
    try:
        signature = base64.b64decode(payload["signature"], validate=True)
    except (ValueError, TypeError) as exc:
        raise RelayAuthError(f"candidate payload signature is not valid base64: {exc}") from exc
    try:
        public_key.verify(signature, transcript)
    except InvalidSignature as exc:
        raise RelayAuthError("candidate payload signature verification failed") from exc
    return list(payload["candidates"])


# ---- client -----------------------------------------------------------------------


class RelayClient:
    """One connection to one relay server. Real, cancellable background
    listen loop dispatches CANDIDATE_OFFER/CANDIDATE_ANSWER/ROUTE_STATUS
    frames addressed to us to the caller's own callbacks -- this class never
    interprets or persists them itself (same "classify, caller persists"
    separation as metadata_session.py)."""

    def __init__(self, node, *, on_candidate_offer=None, on_candidate_answer=None, on_route_status=None):
        self.node = node
        self._on_candidate_offer = on_candidate_offer
        self._on_candidate_answer = on_candidate_answer
        self._on_route_status = on_route_status
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._decoder: protocol.FrameDecoder | None = None
        self._listen_task: asyncio.Task | None = None
        self.connected = False

    async def connect(self, host: str, port: int, *, timeout: float = JOIN_TIMEOUT) -> None:
        reader, writer = await asyncio.open_connection(host, port)
        decoder = protocol.FrameDecoder(relayed=True)
        try:
            frame = await _read_frame(reader, decoder, timeout=timeout)
            if frame.message_type != protocol.MessageType.HELLO:
                raise RelayError(f"expected a relay join challenge, got {frame.message_type.name}")
            nonce = (frame.json() or {}).get("nonce")
            if not nonce or not isinstance(nonce, str):
                raise RelayError("relay server sent a join challenge with no real nonce")

            writer.write(protocol.encode_json(protocol.MessageType.HELLO, _build_join_payload(self.node, nonce), relayed=True))
            await writer.drain()

            ack = await _read_frame(reader, decoder, timeout=timeout)
            if ack.message_type != protocol.MessageType.CAPABILITIES:
                raise RelayAuthError(f"relay server rejected our join: {ack.message_type.name}")
        except Exception:
            await _close_quietly(writer)
            raise

        self._reader, self._writer, self._decoder = reader, writer, decoder
        self.connected = True
        self._listen_task = asyncio.ensure_future(self._listen_loop())

    async def _listen_loop(self) -> None:
        try:
            while True:
                frame = await _read_frame(self._reader, self._decoder, timeout=None)
                if frame.message_type == protocol.MessageType.CANDIDATE_OFFER and self._on_candidate_offer:
                    self._on_candidate_offer(frame.json() or {})
                elif frame.message_type == protocol.MessageType.CANDIDATE_ANSWER and self._on_candidate_answer:
                    self._on_candidate_answer(frame.json() or {})
                elif frame.message_type == protocol.MessageType.ROUTE_STATUS and self._on_route_status:
                    self._on_route_status(frame.json() or {})
        except asyncio.CancelledError:
            raise
        except (ConnectionError, protocol.ProtocolError):
            pass
        finally:
            self.connected = False

    async def _send(self, message_type: protocol.MessageType, payload: dict) -> None:
        if not self.connected or self._writer is None:
            raise RelayError("not connected to a relay server")
        self._writer.write(protocol.encode_json(message_type, payload, relayed=True))
        await self._writer.drain()

    async def send_candidate_offer(self, to_system_uuid: str, candidates: list[str]) -> None:
        await self._send(protocol.MessageType.CANDIDATE_OFFER, sign_candidate_payload(self.node, to_system_uuid=to_system_uuid, candidates=candidates))

    async def send_candidate_answer(self, to_system_uuid: str, candidates: list[str]) -> None:
        await self._send(protocol.MessageType.CANDIDATE_ANSWER, sign_candidate_payload(self.node, to_system_uuid=to_system_uuid, candidates=candidates))

    async def send_route_status(self, to_system_uuid: str, status: str, *, detail: str = "") -> None:
        await self._send(
            protocol.MessageType.ROUTE_STATUS,
            {"from_system_uuid": self.node.system_uuid, "to_system_uuid": to_system_uuid, "status": status, "detail": detail},
        )

    async def close(self) -> None:
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._writer is not None:
            await _close_quietly(self._writer)
            self._writer = None
        self.connected = False


# ---- reference server -------------------------------------------------------------


class RelayServer:
    """A minimal, real reference relay server: routes CANDIDATE_OFFER/
    CANDIDATE_ANSWER/ROUTE_STATUS control frames by `to_system_uuid`,
    verifying each connecting client's own join proof but otherwise holding
    no trust store and never inspecting payload authenticity itself -- that
    is the RECEIVING device's own job (verify_candidate_payload()). Used
    directly by this codebase's own tests as the real counterpart to
    RelayClient; deploying one for real users is a separate, later
    operational concern, same as global_discovery.py's own server side."""

    def __init__(self):
        self._routes: dict[str, asyncio.StreamWriter] = {}
        self._server: asyncio.base_events.Server | None = None

    async def start(self, host: str, port: int) -> asyncio.base_events.Server:
        self._server = await asyncio.start_server(self._handle, host, port)
        return self._server

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        decoder = protocol.FrameDecoder(relayed=True)
        system_uuid: str | None = None
        try:
            nonce = secrets.token_hex(32)
            writer.write(protocol.encode_json(protocol.MessageType.HELLO, {"nonce": nonce}, relayed=True))
            await writer.drain()

            frame = await _read_frame(reader, decoder, timeout=JOIN_TIMEOUT)
            if frame.message_type != protocol.MessageType.HELLO:
                await _close_quietly(writer)
                return
            system_uuid = _verify_join(frame.json() or {}, nonce)
            self._routes[system_uuid] = writer
            writer.write(
                protocol.encode_json(
                    protocol.MessageType.CAPABILITIES, {"protocol_version": protocol.PROTOCOL_VERSION, "features": []}, relayed=True
                )
            )
            await writer.drain()

            while True:
                frame = await _read_frame(reader, decoder, timeout=None)
                if frame.message_type == protocol.MessageType.PING:
                    writer.write(protocol.encode_json(protocol.MessageType.PONG, {}, relayed=True))
                    await writer.drain()
                    continue
                if frame.message_type not in _FORWARDABLE_TYPES:
                    continue
                payload = frame.json() or {}
                target_writer = self._routes.get(payload.get("to_system_uuid"))
                if target_writer is not None:
                    target_writer.write(protocol.encode_json(frame.message_type, payload, relayed=True))
                    await target_writer.drain()
        except asyncio.CancelledError:
            raise
        except (ConnectionError, RelayAuthError, protocol.ProtocolError, asyncio.TimeoutError):
            pass
        finally:
            if system_uuid is not None and self._routes.get(system_uuid) is writer:
                self._routes.pop(system_uuid, None)
            await _close_quietly(writer)

    async def close(self) -> None:
        """Closes active connections BEFORE awaiting the listening socket's
        own shutdown -- real bug found via a hang in this module's own
        tests: Python 3.13 changed `Server.wait_closed()` to block until
        every still-running connection handler actually returns, so closing
        route writers only after `wait_closed()` deadlocks forever whenever
        a `_handle()` coroutine is still blocked reading (which it always
        is, between frames)."""
        for writer in list(self._routes.values()):
            await _close_quietly(writer)
        self._routes.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
