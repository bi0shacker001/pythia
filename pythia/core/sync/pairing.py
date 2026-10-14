"""
Pairing package format (Phase 1). "Pairing establishes trust; discovery
merely locates the already-trusted device" -- this module is the only path
that ever calls trust.add_trusted().

A pairing package carries enough for the *receiving* side to recognize and
trust the initiating system without needing a live connection to it yet:
repository ID, system UUID, device ID, certificate fingerprint, friendly
name, any addresses the initiator already knows about itself, and a
short-lived secret that proves this package came from a real, current
pairing attempt (not a stale QR code or invitation file found later).

Four exchange shapes share this one package shape underneath:
- QR code / invitation file: encode_pairing_text() output, carried verbatim.
- Copyable invitation text: same encode_pairing_text() output.
- Manual device-ID entry: no package at all -- pair_by_manual_entry() below,
  weaker (no fingerprint/secret proof), explicitly named as such.
"""

import base64
import datetime
import json
import secrets

from pythia.core.sync import identity, trust

DEFAULT_TTL_SECONDS = 600  # 10 minutes -- long enough to scan/paste, short enough to matter
_SECRET_BYTES = 18  # -> 24-char urlsafe-base64 secret


def create_pairing_package(
    *,
    friendly_name: str | None = None,
    known_addresses: list[str] | None = None,
    repository_id: str = "default",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict:
    """Build this system's own pairing package -- what gets shown as a QR
    code / invitation text / invitation file for another system to accept.
    `repository_id` is a placeholder scope identifier until Phase 7's real
    repository/metadata-sync design lands; "default" until callers have a
    reason to pass something else."""
    record = identity.get_or_create_identity(friendly_name)
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "repository_id": repository_id,
        "system_uuid": record["system_uuid"],
        "device_id": record["device_id"],
        "certificate_fingerprint": record["certificate_fingerprint"],
        "friendly_name": friendly_name or record["system_name"],
        "known_addresses": list(known_addresses or []),
        "secret": secrets.token_urlsafe(_SECRET_BYTES),
        "created_at": now.isoformat(),
        "expires_at": (now + datetime.timedelta(seconds=ttl_seconds)).isoformat(),
    }


def encode_pairing_text(package: dict) -> str:
    """Compact, copy-pasteable / QR-encodable form: urlsafe-base64 of the
    package's JSON, prefixed so a pasted string is recognizable at a
    glance."""
    raw = json.dumps(package, separators=(",", ":")).encode("utf-8")
    return "pythia-pair:" + base64.urlsafe_b64encode(raw).decode("ascii")


def decode_pairing_text(text: str) -> dict:
    prefix = "pythia-pair:"
    if not text.startswith(prefix):
        raise ValueError("Not a Pythia pairing invitation.")
    try:
        raw = base64.urlsafe_b64decode(text[len(prefix) :].encode("ascii"))
        package = json.loads(raw)
    except Exception as e:
        raise ValueError(f"Couldn't read this pairing invitation: {e}") from e
    if not isinstance(package, dict):
        raise ValueError("Pairing invitation is malformed.")
    return package


def _validate_package_shape(package: dict) -> None:
    required = (
        "repository_id",
        "system_uuid",
        "device_id",
        "certificate_fingerprint",
        "expires_at",
        "secret",
    )
    missing = [f for f in required if not package.get(f)]
    if missing:
        raise ValueError(f"Pairing invitation is missing required field(s): {', '.join(missing)}.")
    if not identity.validate_device_id(package["device_id"]):
        raise ValueError("Pairing invitation's device ID fails its own check digits -- likely corrupted or mistyped.")


def is_expired(package: dict) -> bool:
    try:
        expires_at = datetime.datetime.fromisoformat(package["expires_at"])
    except (KeyError, ValueError):
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) > expires_at


def accept_pairing_package(package: dict) -> dict:
    """Validate and commit trust for a received pairing package -- the one
    real path into trust.add_trusted(). Real checks, not cosmetic: the
    package must have every required field, its device ID must pass its own
    Luhn check digits, and it must not be expired. Does NOT verify a live
    connection's certificate against `certificate_fingerprint` -- that
    happens at actual connection time (Phase 4), against this stored trust
    record; a pairing package alone can only assert what its sender claims
    about itself."""
    _validate_package_shape(package)
    if is_expired(package):
        raise ValueError("This pairing invitation has expired -- ask the other system to generate a new one.")
    my_uuid = identity.get_system_uuid()
    if package["system_uuid"] == my_uuid:
        raise ValueError("Can't pair a system with itself.")

    return trust.add_trusted(
        system_uuid=package["system_uuid"],
        device_id=package["device_id"],
        certificate_fingerprint=package["certificate_fingerprint"],
        friendly_name=package.get("friendly_name") or package["system_uuid"],
        known_addresses=package.get("known_addresses"),
    )


def pair_by_manual_entry(*, system_uuid: str, device_id: str, friendly_name: str) -> dict:
    """The weakest of the four exchanges (Phase 1 design explicitly lists
    "manual device-ID entry" as supported): no certificate fingerprint or
    secret proof, just a device ID the user typed in themselves. Trust is
    still real (added to the same store), but `certificate_fingerprint` is
    left empty -- callers/UI should visibly flag a manually-paired record as
    unverified until a live connection's cert actually matches the device ID
    it derives to."""
    if not identity.validate_device_id(device_id):
        raise ValueError("That device ID's check digits don't match -- please re-check it.")
    if system_uuid == identity.get_system_uuid():
        raise ValueError("Can't pair a system with itself.")
    return trust.add_trusted(
        system_uuid=system_uuid,
        device_id=device_id,
        certificate_fingerprint="",
        friendly_name=friendly_name or system_uuid,
        known_addresses=[],
    )
