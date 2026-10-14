"""Authenticated LAN discovery announcements for explicitly enabled sync.

This module only creates and verifies packets. It opens no sockets and performs no
discovery merely by being imported. Announcements from unpaired devices are
ignored; pairing remains the sole trust-establishment path.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from pythia.core.sync import identity, trust

DISCOVERY_VERSION = 1
MAX_PACKET_SIZE = 32 * 1024
MAX_ADDRESSES = 16
MAX_AGE_SECONDS = 120
MAX_FUTURE_SKEW_SECONDS = 30


class DiscoveryError(ValueError):
    pass


def _canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def create_announcement(node, port: int, addresses: list[str], *, now: int | None = None) -> bytes:
    if not 1 <= port <= 65535:
        raise DiscoveryError("discovery port is outside 1..65535")
    if not isinstance(addresses, list) or not addresses or len(addresses) > MAX_ADDRESSES:
        raise DiscoveryError("addresses must be a non-empty bounded list")
    if any(not isinstance(value, str) or not value or len(value) > 255 for value in addresses):
        raise DiscoveryError("discovery addresses must be non-empty strings")
    certificate = x509.load_pem_x509_certificate(Path(node.identity_record["certificate_path"]).read_bytes())
    cert_der = certificate.public_bytes(serialization.Encoding.DER)
    body = {
        "domain": "pythia-lan-discovery-v1",
        "version": DISCOVERY_VERSION,
        "system_uuid": node.system_uuid,
        "device_id": node.device_id,
        "system_name": str(node.identity_record.get("system_name") or ""),
        "certificate_der": base64.b64encode(cert_der).decode("ascii"),
        "port": port,
        "addresses": addresses,
        "timestamp": int(time.time() if now is None else now),
        "nonce": secrets.token_hex(16),
    }
    packet = {"body": body, "signature": base64.b64encode(node.sign(_canonical(body))).decode("ascii")}
    encoded = _canonical(packet)
    if len(encoded) > MAX_PACKET_SIZE:
        raise DiscoveryError("discovery announcement exceeds the packet limit")
    return encoded


def verify_announcement(packet: bytes, *, base_dir: Path | None = None, now: int | None = None) -> dict:
    if not isinstance(packet, bytes) or len(packet) > MAX_PACKET_SIZE:
        raise DiscoveryError("invalid discovery packet size")
    try:
        envelope = json.loads(packet.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoveryError("discovery packet is not valid UTF-8 JSON") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"body", "signature"}:
        raise DiscoveryError("invalid discovery envelope")
    body = envelope["body"]
    if not isinstance(body, dict) or body.get("domain") != "pythia-lan-discovery-v1":
        raise DiscoveryError("invalid discovery domain")
    required = {"version", "system_uuid", "device_id", "certificate_der", "port", "addresses", "timestamp", "nonce"}
    if not required.issubset(body) or body["version"] != DISCOVERY_VERSION:
        raise DiscoveryError("unsupported or incomplete discovery announcement")
    current = int(time.time() if now is None else now)
    timestamp = body["timestamp"]
    if not isinstance(timestamp, int) or timestamp < current - MAX_AGE_SECONDS or timestamp > current + MAX_FUTURE_SKEW_SECONDS:
        raise DiscoveryError("discovery announcement is expired or too far in the future")
    if not isinstance(body["port"], int) or not 1 <= body["port"] <= 65535:
        raise DiscoveryError("invalid discovery port")
    addresses = body["addresses"]
    if not isinstance(addresses, list) or not addresses or len(addresses) > MAX_ADDRESSES or any(not isinstance(a, str) or not a or len(a) > 255 for a in addresses):
        raise DiscoveryError("invalid discovery addresses")
    trusted = trust.get_trusted(body["system_uuid"], base_dir=base_dir)
    if trusted is None:
        raise DiscoveryError("discovery announcement is from an unpaired device")
    try:
        cert_der = base64.b64decode(body["certificate_der"], validate=True)
        certificate = x509.load_der_x509_certificate(cert_der)
        signature = base64.b64decode(envelope["signature"], validate=True)
    except Exception as exc:
        raise DiscoveryError("invalid discovery certificate or signature encoding") from exc
    derived_id = identity.device_id_from_der(cert_der)
    if derived_id != body["device_id"] or derived_id != trusted.get("device_id"):
        raise DiscoveryError("discovery certificate does not match the trusted device ID")
    fingerprint = identity.certificate_fingerprint(cert_der)
    if trusted.get("certificate_fingerprint") and fingerprint != trusted["certificate_fingerprint"]:
        raise DiscoveryError("discovery certificate fingerprint does not match trust")
    public_key = certificate.public_key()
    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        raise DiscoveryError("discovery certificate is not Ed25519")
    try:
        public_key.verify(signature, _canonical(body))
    except InvalidSignature as exc:
        raise DiscoveryError("invalid discovery announcement signature") from exc
    return {
        "system_uuid": body["system_uuid"],
        "device_id": derived_id,
        "system_name": str(body.get("system_name") or ""),
        "addresses": list(addresses),
        "port": body["port"],
        "timestamp": timestamp,
    }
