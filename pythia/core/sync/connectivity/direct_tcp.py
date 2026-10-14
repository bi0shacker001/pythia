"""
Direct TCP/TLS connectivity (connectivity milestone, accepted via
aisync/CLAUDE-NEXT-TASK.md 2026-08-08). Builds on identity.py, trust.py, and
Codex's protocol.py. In-process asyncio only -- no helper process, no
external tool.

**Mutual authentication design, and why it isn't plain TLS client-cert
auth** (recorded here and in aisync/WORKLOG.md for Codex to check):
Python's stdlib `ssl` module has no supported way to *request* a client
certificate on a server socket without also *requiring* it to chain to a
loaded CA -- `verify_mode=CERT_OPTIONAL` still fails the handshake if the
presented cert doesn't validate, and our certs are self-signed with no
shared CA. So instead of TLS client-cert auth:

- TLS runs with `CERT_NONE` on both sides (no built-in chain validation).
  The connecting side (TLS client) can and does read the listener's real
  certificate via `getpeercert(binary_form=True)` -- that direction is a
  normal, fully-supported part of any TLS handshake.
- All *identity* verification happens at the application layer, in the
  `HELLO` exchange below: each side sends its own certificate (DER) and
  device ID: the receiver re-derives the device ID from the certificate
  bytes itself (never trusts the claimed string alone) and checks it against
  the pinned trust record for that `system_uuid` (trust.get_trusted()).
- Private-key *possession* is proven two different ways per direction: the
  listener's, implicitly, by a successful TLS handshake (a TLS server must
  use its private key to complete one); the connector's, explicitly, via an
  Ed25519 signature over a listener-issued nonce carried in the HELLO
  exchange -- since the connector never gets a TLS-level proof opportunity
  under this verify_mode.
- As a defense-in-depth check, the connector also compares the listener's
  HELLO-declared certificate against the certificate TLS itself actually
  presented (`ssl_object.getpeercert(binary_form=True)`) -- the two must be
  byte-identical.

Open question for Codex/research (see aisync/WORKLOG.md): whether adding
pyOpenSSL (which exposes a real `set_verify()` callback, unlike stdlib
`ssl`) would be worth it later for symmetric TLS-layer mutual auth, or
whether this HELLO-layer challenge-response is fine long-term. Not resolved
here -- flagging rather than guessing.
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import socket
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from pythia.core.sync import identity, protocol, trust

HANDSHAKE_TIMEOUT = 10.0

# The sentinel meaning "listen on every local interface" -- when a caller
# passes this exact host to start_listener(), we also attempt a real IPv6
# wildcard bind (2026-08-09, "internal IPv6 direct connectivity and
# dual-stack route selection") rather than staying IPv4-only. Any other
# host string (a specific interface address, "::", "127.0.0.1", ...) is a
# real, explicit user choice and is passed through unchanged -- never
# second-guessed.
_IPV4_WILDCARD = "0.0.0.0"
_IPV6_WILDCARD = "::"

_ipv6_supported_cache: bool | None = None


def ipv6_supported() -> bool:
    """Whether this process can actually create and bind a real IPv6 socket
    right now -- socket.has_ipv6 is only a compile-time constant (some
    systems report it True but still can't bind, e.g. IPv6 disabled at the
    OS/kernel level), so this does a real bind attempt instead, once, and
    caches the honest result for the life of the process."""
    global _ipv6_supported_cache
    if _ipv6_supported_cache is not None:
        return _ipv6_supported_cache
    if not socket.has_ipv6:
        _ipv6_supported_cache = False
        return False
    probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        probe.bind(("::1", 0))
        _ipv6_supported_cache = True
    except OSError:
        _ipv6_supported_cache = False
    finally:
        probe.close()
    return _ipv6_supported_cache


def listener_families(server: asyncio.base_events.Server) -> list[str]:
    """Real address families the given listener actually bound, for honest
    UI diagnostics -- never guessed from configuration alone, since a
    dual-stack bind request can silently end up IPv4-only on a system where
    IPv6 isn't actually usable (see start_listener())."""
    families = []
    for sock in server.sockets or []:
        if sock.family == socket.AF_INET and "IPv4" not in families:
            families.append("IPv4")
        elif sock.family == socket.AF_INET6 and "IPv6" not in families:
            families.append("IPv6")
    return families


class DirectConnectError(Exception):
    """Base class for every handshake/connection failure below -- callers
    that don't care about the specific reason can catch just this."""


class UnknownDeviceError(DirectConnectError):
    pass


class CertificateMismatchError(DirectConnectError):
    pass


class SelfConnectionError(DirectConnectError):
    pass


class ProtocolVersionMismatchError(DirectConnectError):
    pass


class MalformedHelloError(DirectConnectError):
    pass


class HandshakeTimeoutError(DirectConnectError):
    pass


@dataclass
class PeerInfo:
    system_uuid: str
    device_id: str
    certificate_fingerprint: str
    system_name: str


@dataclass
class LocalNode:
    """This process's network identity plus which trust store to check
    against -- `base_dir` is the test-only two-identities-in-one-process
    override identity.py/trust.py already support; real callers build this
    with base_dir=None and get the normal single global identity."""

    identity_record: dict
    base_dir: Path | None = None
    _private_key: ed25519.Ed25519PrivateKey | None = field(default=None, repr=False, compare=False)

    @property
    def system_uuid(self) -> str:
        return self.identity_record["system_uuid"]

    @property
    def device_id(self) -> str:
        return self.identity_record["device_id"]

    def _load_private_key(self) -> ed25519.Ed25519PrivateKey:
        if self._private_key is None:
            key_bytes = Path(self.identity_record["private_key_path"]).read_bytes()
            key = serialization.load_pem_private_key(key_bytes, password=None)
            if not isinstance(key, ed25519.Ed25519PrivateKey):
                raise DirectConnectError("local private key is not Ed25519 -- identity.py should only ever generate one")
            self._private_key = key
        return self._private_key

    def sign(self, data: bytes) -> bytes:
        return self._load_private_key().sign(data)


def load_local_node(system_name: str | None = None, *, base_dir: Path | None = None, system_uuid: str | None = None) -> LocalNode:
    record = identity.get_or_create_identity(system_name, base_dir=base_dir, system_uuid=system_uuid)
    return LocalNode(record, base_dir)


def _build_server_ssl_context(node: LocalNode) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(node.identity_record["certificate_path"], node.identity_record["private_key_path"])
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _build_client_ssl_context(node: LocalNode) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _hello_payload(node: LocalNode, *, nonce: str | None = None, signature: str | None = None) -> dict:
    cert_der = Path(node.identity_record["certificate_path"]).read_bytes()
    # certificate_path holds PEM; re-encode to DER for the wire (DER is what
    # device_id_from_der()/certificate_fingerprint() operate on everywhere else).
    cert_der = x509.load_pem_x509_certificate(cert_der).public_bytes(serialization.Encoding.DER)
    payload = {
        "device_id": node.device_id,
        "system_uuid": node.system_uuid,
        "system_name": node.identity_record.get("system_name", ""),
        "certificate_der": base64.b64encode(cert_der).decode("ascii"),
        "protocol_version": protocol.PROTOCOL_VERSION,
    }
    if nonce is not None:
        payload["nonce"] = nonce
    if signature is not None:
        payload["signature"] = signature
    return payload


def _extract_and_verify_identity(hello: dict, *, base_dir: Path | None) -> tuple[PeerInfo, bytes]:
    """Real checks against the HELLO a peer sent, in order: shape, protocol
    version, certificate/device-ID self-consistency, then trust-store
    presence and pin match. Never trusts a claimed field without deriving or
    cross-checking it."""
    required = ("device_id", "system_uuid", "certificate_der", "protocol_version")
    missing = [f for f in required if f not in hello]
    if missing:
        raise MalformedHelloError(f"HELLO is missing required field(s): {', '.join(missing)}")
    if not isinstance(hello["protocol_version"], int):
        raise MalformedHelloError("HELLO protocol_version must be an integer")
    if hello["protocol_version"] != protocol.PROTOCOL_VERSION:
        raise ProtocolVersionMismatchError(
            f"peer protocol version {hello['protocol_version']} != {protocol.PROTOCOL_VERSION}"
        )
    try:
        cert_der = base64.b64decode(hello["certificate_der"], validate=True)
    except (ValueError, TypeError) as e:
        raise MalformedHelloError(f"HELLO certificate_der is not valid base64: {e}") from e

    try:
        derived_device_id = identity.device_id_from_der(cert_der)
    except Exception as e:
        raise MalformedHelloError(f"HELLO certificate_der is not a usable certificate: {e}") from e
    if derived_device_id != hello["device_id"]:
        raise CertificateMismatchError("peer's declared device ID does not match the certificate it presented")

    trusted = trust.get_trusted(hello["system_uuid"], base_dir=base_dir)
    if trusted is None:
        raise UnknownDeviceError(f"system {hello['system_uuid']!r} is not a trusted device -- pair with it first")
    if trusted["device_id"] != derived_device_id:
        raise CertificateMismatchError(
            "peer's certificate does not match the device ID pinned for this trusted system "
            "(the remote system may have regenerated its identity -- re-pair to accept the new one)"
        )
    fingerprint = identity.certificate_fingerprint(cert_der)
    pinned_fingerprint = trusted.get("certificate_fingerprint")
    if pinned_fingerprint and pinned_fingerprint != fingerprint:
        raise CertificateMismatchError("peer's certificate fingerprint does not match the pinned trust record")

    peer = PeerInfo(
        system_uuid=hello["system_uuid"],
        device_id=derived_device_id,
        certificate_fingerprint=fingerprint,
        system_name=str(hello.get("system_name") or ""),
    )
    return peer, cert_der


def _proof_message(
    nonce: str,
    *,
    listener_system_uuid: str,
    listener_device_id: str,
    connector_system_uuid: str,
    connector_device_id: str,
) -> bytes:
    """Domain-separated, canonical proof transcript for connector authentication."""
    return json.dumps(
        {
            "domain": "pythia-direct-connect-proof-v1",
            "protocol_version": protocol.PROTOCOL_VERSION,
            "nonce": nonce,
            "listener_system_uuid": listener_system_uuid,
            "listener_device_id": listener_device_id,
            "connector_system_uuid": connector_system_uuid,
            "connector_device_id": connector_device_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _verify_nonce_signature(hello: dict, cert_der: bytes, proof: bytes) -> None:
    """Proves the connector actually holds the private key for the
    certificate it presented -- see this module's docstring for why this
    replaces TLS client-cert auth."""
    signature_b64 = hello.get("signature")
    if not signature_b64:
        raise MalformedHelloError("HELLO is missing the challenge-nonce signature")
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError) as e:
        raise MalformedHelloError(f"HELLO signature is not valid base64: {e}") from e
    public_key = x509.load_der_x509_certificate(cert_der).public_key()
    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        raise CertificateMismatchError("peer certificate does not use an Ed25519 key")
    try:
        public_key.verify(signature, proof)
    except InvalidSignature as e:
        raise CertificateMismatchError("peer failed to prove possession of its private key") from e


def _validate_capabilities(frame: protocol.Frame) -> dict:
    if frame.message_type != protocol.MessageType.CAPABILITIES:
        raise MalformedHelloError(f"expected CAPABILITIES, got {frame.message_type.name}")
    payload = frame.json()
    if not isinstance(payload, dict):
        raise MalformedHelloError("CAPABILITIES payload must be an object")
    if payload.get("protocol_version") != protocol.PROTOCOL_VERSION:
        raise ProtocolVersionMismatchError(
            f"peer capabilities protocol version {payload.get('protocol_version')!r} != {protocol.PROTOCOL_VERSION}"
        )
    if not isinstance(payload.get("features"), list) or not all(isinstance(item, str) for item in payload["features"]):
        raise MalformedHelloError("CAPABILITIES features must be a list of strings")
    return payload


class _FrameReader:
    """Wraps an asyncio.StreamReader + protocol.FrameDecoder so multiple
    frames arriving in one TCP read are queued rather than dropped --
    handshake code and DirectConnection share one instance per connection so
    nothing sent immediately after CAPABILITIES is ever lost."""

    def __init__(self, reader: asyncio.StreamReader, decoder: protocol.FrameDecoder):
        self._reader = reader
        self._decoder = decoder
        self._pending: list[protocol.Frame] = []

    async def read_frame(self, *, timeout: float | None = None) -> protocol.Frame:
        if self._pending:
            return self._pending.pop(0)
        while True:
            read_coro = self._reader.read(65536)
            chunk = await (asyncio.wait_for(read_coro, timeout) if timeout else read_coro)
            if not chunk:
                raise ConnectionError("peer closed the connection")
            frames = self._decoder.feed(chunk)
            if frames:
                self._pending = frames
                return self._pending.pop(0)


class DirectConnection:
    def __init__(self, writer: asyncio.StreamWriter, peer: PeerInfo, frame_reader: _FrameReader):
        self._writer = writer
        self.peer = peer
        self._frame_reader = frame_reader
        self.relayed = False

    async def send_frame(self, frame: protocol.Frame) -> None:
        self._writer.write(protocol.encode_frame(frame, relayed=self.relayed))
        await self._writer.drain()

    async def send_json(self, message_type: protocol.MessageType, value, *, stream_id: int = 0, flags: int = 0) -> None:
        self._writer.write(protocol.encode_json(message_type, value, stream_id=stream_id, flags=flags, relayed=self.relayed))
        await self._writer.drain()

    async def recv_frame(self, *, timeout: float | None = None) -> protocol.Frame:
        return await self._frame_reader.read_frame(timeout=timeout)

    async def ping(self, *, timeout: float = 5.0) -> protocol.Frame:
        await self.send_json(protocol.MessageType.PING, {})
        frame = await self.recv_frame(timeout=timeout)
        if frame.message_type != protocol.MessageType.PONG:
            raise protocol.ProtocolError(f"expected PONG, got {frame.message_type.name}")
        return frame

    async def close(self) -> None:
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError, ssl.SSLError):
            pass


async def _server_handshake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, node: LocalNode, *, timeout: float) -> DirectConnection:
    decoder = protocol.FrameDecoder(relayed=False)
    frame_reader = _FrameReader(reader, decoder)

    nonce = secrets.token_hex(32)
    writer.write(protocol.encode_json(protocol.MessageType.HELLO, _hello_payload(node, nonce=nonce)))
    await writer.drain()

    frame = await frame_reader.read_frame(timeout=timeout)
    if frame.message_type != protocol.MessageType.HELLO:
        raise MalformedHelloError(f"expected HELLO, got {frame.message_type.name}")
    hello_in = frame.json()
    peer, cert_der = _extract_and_verify_identity(hello_in, base_dir=node.base_dir)
    if peer.system_uuid == node.system_uuid:
        raise SelfConnectionError("a system attempted to connect to itself")
    proof = _proof_message(
        nonce,
        listener_system_uuid=node.system_uuid,
        listener_device_id=node.device_id,
        connector_system_uuid=peer.system_uuid,
        connector_device_id=peer.device_id,
    )
    _verify_nonce_signature(hello_in, cert_der, proof)

    writer.write(protocol.encode_json(protocol.MessageType.CAPABILITIES, {"protocol_version": protocol.PROTOCOL_VERSION, "features": []}))
    await writer.drain()
    cap_frame = await frame_reader.read_frame(timeout=timeout)
    _validate_capabilities(cap_frame)

    return DirectConnection(writer, peer, frame_reader)


async def _client_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    node: LocalNode,
    *,
    expected_system_uuid: str | None,
    timeout: float,
) -> DirectConnection:
    decoder = protocol.FrameDecoder(relayed=False)
    frame_reader = _FrameReader(reader, decoder)

    frame = await frame_reader.read_frame(timeout=timeout)
    if frame.message_type != protocol.MessageType.HELLO:
        raise MalformedHelloError(f"expected HELLO, got {frame.message_type.name}")
    hello_in = frame.json()
    peer, cert_der = _extract_and_verify_identity(hello_in, base_dir=node.base_dir)
    if peer.system_uuid == node.system_uuid:
        raise SelfConnectionError("a system attempted to connect to itself")
    if expected_system_uuid is not None and peer.system_uuid != expected_system_uuid:
        raise CertificateMismatchError(
            f"connected to system {peer.system_uuid!r}, expected {expected_system_uuid!r}"
        )

    ssl_object = writer.get_extra_info("ssl_object")
    tls_peer_der = ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
    if tls_peer_der is not None and tls_peer_der != cert_der:
        raise CertificateMismatchError("peer's HELLO certificate does not match the certificate TLS actually presented")

    nonce = hello_in.get("nonce")
    if not nonce or not isinstance(nonce, str):
        raise MalformedHelloError("listener HELLO is missing its challenge nonce")

    proof = _proof_message(
        nonce,
        listener_system_uuid=peer.system_uuid,
        listener_device_id=peer.device_id,
        connector_system_uuid=node.system_uuid,
        connector_device_id=node.device_id,
    )
    signature = base64.b64encode(node.sign(proof)).decode("ascii")
    writer.write(protocol.encode_json(protocol.MessageType.HELLO, _hello_payload(node, signature=signature)))
    await writer.drain()

    cap_frame = await frame_reader.read_frame(timeout=timeout)
    _validate_capabilities(cap_frame)
    writer.write(protocol.encode_json(protocol.MessageType.CAPABILITIES, {"protocol_version": protocol.PROTOCOL_VERSION, "features": []}))
    await writer.drain()

    return DirectConnection(writer, peer, frame_reader)


async def _close_quietly(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError, ssl.SSLError):
        pass


async def start_listener(
    host: str,
    port: int,
    node: LocalNode,
    on_connected: Callable[[DirectConnection], Awaitable[None]],
    *,
    on_reject: Callable[[Exception], None] | None = None,
    handshake_timeout: float = HANDSHAKE_TIMEOUT,
) -> asyncio.base_events.Server:
    """Listen for direct connections and hand each successfully-authenticated
    one to `on_connected`. A single bad/malicious/unauthorized connection
    attempt is rejected and closed -- it never takes the listener down
    (mirrors pythia.plugins.registry's "one broken package never breaks
    everything else" rule).

    Dual-stack (2026-08-09, "internal IPv6 direct connectivity and
    dual-stack route selection"): when `host` is the real "every local
    interface" wildcard (`0.0.0.0`), also attempts a real IPv6 wildcard
    bind (`::`) alongside it -- two separate sockets (one AF_INET, one
    AF_INET6 with IPV6_V6ONLY explicitly set), the same portable approach
    `asyncio.start_server()` itself already uses internally for a
    multi-host bind, which gracefully skips a family that genuinely isn't
    available (EADDRNOTAVAIL) rather than failing the whole listener. A
    specific host the caller actually chose (a real interface address,
    `::`, `127.0.0.1`, ...) is never second-guessed -- passed through as a
    single bind, exactly as given."""
    ssl_ctx = _build_server_ssl_context(node)
    bind_hosts: str | list[str] = host
    if host == _IPV4_WILDCARD:
        bind_hosts = [_IPV4_WILDCARD, _IPV6_WILDCARD] if ipv6_supported() else [_IPV4_WILDCARD]

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            conn = await _server_handshake(reader, writer, node, timeout=handshake_timeout)
        except asyncio.TimeoutError as e:
            if on_reject:
                on_reject(HandshakeTimeoutError(f"handshake timed out: {e}"))
            await _close_quietly(writer)
            return
        except Exception as e:  # noqa: BLE001 -- a single bad peer must never crash the listener
            if on_reject:
                on_reject(e)
            await _close_quietly(writer)
            return
        await on_connected(conn)

    return await asyncio.start_server(_handle, bind_hosts, port, ssl=ssl_ctx)


async def connect(
    host: str,
    port: int,
    node: LocalNode,
    *,
    expected_system_uuid: str | None = None,
    handshake_timeout: float = HANDSHAKE_TIMEOUT,
) -> DirectConnection:
    ssl_ctx = _build_client_ssl_context(node)
    reader, writer = await asyncio.open_connection(host, port, ssl=ssl_ctx)
    try:
        return await _client_handshake(
            reader, writer, node, expected_system_uuid=expected_system_uuid, timeout=handshake_timeout
        )
    except asyncio.TimeoutError as e:
        await _close_quietly(writer)
        raise HandshakeTimeoutError(f"handshake with peer timed out: {e}") from e
    except DirectConnectError:
        await _close_quietly(writer)
        raise
    except Exception:
        await _close_quietly(writer)
        raise
