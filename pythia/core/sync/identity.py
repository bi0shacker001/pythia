"""
Phase 1 network identity (see aisync/CHATGPT-INITIAL-HANDOFF.md and
aisync/CLAUDE-RESPONSE.md for the full design this implements).

Every Pythia installation gets, in addition to its existing
pythia.identity.get_system_uuid(): a private key, a self-signed X.509
certificate, and a Syncthing-compatible device ID derived from that
certificate (SHA-256 of the DER-encoded cert -> base32 -> Luhn mod 32 check
digits -> hyphenated groups of 7 -- the same derivation Syncthing itself
uses, since global/local discovery and relay control are meant to
interoperate with real Syncthing infrastructure later).

**Caveat, on record** (see aisync/CLAUDE-RESPONSE.md): the device-ID
derivation below is implemented from protocol knowledge, not verified
against a captured real-Syncthing test vector. It is internally consistent
(round-trips, check digits validate) but interop with a live Syncthing
discovery server is UNVERIFIED until tested against one.

Storage: app_data_dir()/network/identity.json (the local record) plus
cert.pem/key.pem next to it. This directory is never synchronized -- the
private key must never leave this system, same rule as
pythia.credentials's stored tokens.
"""

import base64
import datetime
import hashlib
import json
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from pythia.identity import app_data_dir, get_system_uuid

IDENTITY_VERSION = 1
_CERT_VALIDITY_DAYS = 20 * 365  # long-lived, matching Syncthing's own convention
_BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def _network_dir(base_dir: Path | None = None) -> Path:
    """`base_dir` (2026-08-08, connectivity milestone: "support two isolated
    Pythia identities in one test process/data sandbox") lets a caller point
    at an explicit directory instead of the process-global
    pythia.identity.app_data_dir() (which reads PYTHIA_DATA_DIR -- one value
    per process, so two identities can't both rely on it at once). Every
    real caller still omits this and gets the normal single-identity-per-
    process behavior; only tests simulating two systems in one process pass
    it."""
    d = (base_dir or app_data_dir()) / "network"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _identity_path(base_dir: Path | None = None) -> Path:
    return _network_dir(base_dir) / "identity.json"


def _cert_path(base_dir: Path | None = None) -> Path:
    return _network_dir(base_dir) / "cert.pem"


def _key_path(base_dir: Path | None = None) -> Path:
    return _network_dir(base_dir) / "key.pem"


def _luhn32_check_char(s: str) -> str:
    """Generic Luhn mod N check-digit generation over the base32 alphabet
    (n=32), the same algorithm Syncthing's lib/luhn package uses: iterate
    left to right, factor alternates 2/1 per character."""
    n = len(_BASE32_ALPHABET)
    factor = 2
    total = 0
    for ch in s:
        codepoint = _BASE32_ALPHABET.index(ch)
        addend = factor * codepoint
        factor = 1 if factor == 2 else 2
        addend = (addend // n) + (addend % n)
        total += addend
    remainder = total % n
    check_codepoint = (n - remainder) % n
    return _BASE32_ALPHABET[check_codepoint]


def device_id_from_der(cert_der: bytes) -> str:
    """Syncthing-format device ID: SHA-256(DER cert) -> base32 (no padding,
    52 chars for 32 bytes) -> split into 4x13-char chunks, each gets a Luhn
    check digit appended (-> 4x14 = 56 chars) -> hyphenated in groups of 7
    (8 groups)."""
    digest = hashlib.sha256(cert_der).digest()
    b32 = base64.b32encode(digest).decode("ascii").rstrip("=")
    if len(b32) != 52:
        raise ValueError(f"unexpected base32 length {len(b32)} for a 32-byte digest")
    chunks = [b32[i * 13 : (i + 1) * 13] for i in range(4)]
    luhnified = "".join(chunk + _luhn32_check_char(chunk) for chunk in chunks)
    groups = [luhnified[i * 7 : (i + 1) * 7] for i in range(8)]
    return "-".join(groups)


def validate_device_id(device_id: str) -> bool:
    """Re-derive each chunk's check digit from its 13 real characters and
    compare -- catches a mistyped/corrupted device ID entered manually
    (Phase 1's "manual device-ID entry" pairing exchange)."""
    stripped = device_id.replace("-", "")
    if len(stripped) != 56:
        return False
    for i in range(4):
        chunk = stripped[i * 14 : i * 14 + 13]
        check = stripped[i * 14 + 13]
        if any(c not in _BASE32_ALPHABET for c in chunk):
            return False
        if _luhn32_check_char(chunk) != check:
            return False
    return True


def _generate_keypair_and_cert(system_name: str):
    """Self-signed Ed25519 cert -- no CA, no chain; identity is the
    certificate itself (pinned by device ID / fingerprint on pairing), same
    trust model Syncthing uses. Ed25519 over RSA/ECDSA: smaller keys/certs,
    no parameter choices to get wrong."""
    key = ed25519.Ed25519PrivateKey.generate()
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, system_name or "Pythia")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=_CERT_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, algorithm=None)  # Ed25519 keys must not pass a hash algorithm
    )
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    return key_pem, cert_pem, cert_der


def certificate_fingerprint(cert_der: bytes) -> str:
    """SHA-256 of the DER cert, hex -- distinct from the device ID (which is
    the same hash, differently encoded); kept as its own field because the
    pairing package format calls for a "certificate fingerprint" separately
    from the device ID, matching how the two are independently checkable."""
    return hashlib.sha256(cert_der).hexdigest()


def _load_record(base_dir: Path | None = None) -> dict | None:
    path = _identity_path(base_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_record(record: dict, base_dir: Path | None = None) -> None:
    path = _identity_path(base_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(path)


def get_or_create_identity(
    system_name: str | None = None, *, base_dir: Path | None = None, system_uuid: str | None = None
) -> dict:
    """This system's real network identity: generates a keypair/cert on
    first call and persists the record, or returns the existing one
    unchanged (a device ID must stay stable, same "never regenerated" rule
    as pythia.identity.get_system_uuid()). `system_name` is only used the
    first time (cert CN + record's friendly name); pass a real value on
    first run or it defaults to "Pythia". `base_dir`/`system_uuid` are the
    test-only two-identities-in-one-process overrides described in
    _network_dir()'s docstring -- real callers never pass them, and get
    pythia.identity.app_data_dir()/get_system_uuid() as before."""
    existing = _load_record(base_dir)
    if existing is not None and _cert_path(base_dir).is_file() and _key_path(base_dir).is_file():
        return existing

    key_pem, cert_pem, cert_der = _generate_keypair_and_cert(system_name or "Pythia")
    _key_path(base_dir).write_bytes(key_pem)
    try:
        import os

        os.chmod(_key_path(base_dir), 0o600)
    except OSError:
        pass  # best-effort on platforms without POSIX permission bits (Windows)
    _cert_path(base_dir).write_bytes(cert_pem)

    record = {
        "system_uuid": system_uuid or get_system_uuid(),
        "device_id": device_id_from_der(cert_der),
        "certificate_path": str(_cert_path(base_dir)),
        "private_key_path": str(_key_path(base_dir)),
        "certificate_fingerprint": certificate_fingerprint(cert_der),
        "system_name": system_name or "Pythia",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "identity_version": IDENTITY_VERSION,
    }
    _write_record(record, base_dir)
    return record


def set_system_name(name: str, *, base_dir: Path | None = None) -> dict:
    """Rename does not touch the certificate/device ID -- only the friendly
    label. Re-issuing the cert for a rename would needlessly break every
    peer's pinned fingerprint."""
    record = get_or_create_identity(name, base_dir=base_dir)
    record["system_name"] = name
    _write_record(record, base_dir)
    return record
