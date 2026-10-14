"""
Trusted-device store (Phase 1). Maps a paired remote system's Pythia
system_uuid to its network device ID / certificate fingerprint / known
addresses / friendly name. Local-only, never synchronized (same rule as
identity.py's private key -- a device's own trust decisions are its own).

"Pairing establishes trust; discovery merely locates the already-trusted
device" (aisync design): nothing in here ever adds a trust record on its
own -- only pairing.py's accept_pairing_package() does, after the user
explicitly completes an exchange.
"""

import datetime
import json
from pathlib import Path

from pythia.identity import app_data_dir


def _trust_path(base_dir: Path | None = None) -> Path:
    """`base_dir` (2026-08-08, connectivity milestone, same rationale as
    identity.py's _network_dir()): lets a test simulate a second isolated
    system's trust store in the same process. Real callers omit it."""
    d = (base_dir or app_data_dir()) / "network"
    d.mkdir(parents=True, exist_ok=True)
    return d / "trusted_devices.json"


def _load(base_dir: Path | None = None) -> dict:
    path = _trust_path(base_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict, base_dir: Path | None = None) -> None:
    path = _trust_path(base_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_trusted(base_dir: Path | None = None) -> list[dict]:
    return list(_load(base_dir).values())


def get_trusted(system_uuid: str, base_dir: Path | None = None) -> dict | None:
    return _load(base_dir).get(system_uuid)


def find_by_device_id(device_id: str, base_dir: Path | None = None) -> dict | None:
    for record in _load(base_dir).values():
        if record.get("device_id") == device_id:
            return record
    return None


def add_trusted(
    *,
    system_uuid: str,
    device_id: str,
    certificate_fingerprint: str,
    friendly_name: str,
    known_addresses: list[str] | None = None,
    base_dir: Path | None = None,
) -> dict:
    """Real, explicit trust grant -- called only after a pairing exchange
    completes (pairing.py), never automatically from a discovery hit. Trust
    is keyed by system_uuid: re-pairing the same system (e.g. after it
    regenerated its cert) updates the record in place rather than creating a
    duplicate, since system_uuid is the durable Pythia identity and
    device_id/fingerprint are the thing that can legitimately change."""
    data = _load(base_dir)
    record = {
        "system_uuid": system_uuid,
        "device_id": device_id,
        "certificate_fingerprint": certificate_fingerprint,
        "friendly_name": friendly_name,
        "known_addresses": list(known_addresses or []),
        "trusted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    data[system_uuid] = record
    _write(data, base_dir)
    return record


def remove_trusted(system_uuid: str, base_dir: Path | None = None) -> bool:
    data = _load(base_dir)
    if system_uuid not in data:
        return False
    del data[system_uuid]
    _write(data, base_dir)
    return True


def update_known_addresses(system_uuid: str, addresses: list[str], base_dir: Path | None = None) -> dict | None:
    """Discovery/connection results feed back here -- addresses are routing
    hints attached to an already-trusted record, never how trust itself is
    established (see this module's own docstring)."""
    data = _load(base_dir)
    record = data.get(system_uuid)
    if record is None:
        return None
    record["known_addresses"] = list(addresses)
    _write(data, base_dir)
    return record


def is_trusted(system_uuid: str, base_dir: Path | None = None) -> bool:
    return system_uuid in _load(base_dir)
