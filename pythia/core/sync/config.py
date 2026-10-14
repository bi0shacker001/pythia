"""Persistent opt-in gate for core synchronization.

Reading the default state performs no writes. Identity, trust stores, listeners,
discovery, and transfer workers must only be created after ``enabled`` is true.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DEFAULTS = {
    "enabled": False,
    "listen": True,
    "lan_discovery": True,
    "global_discovery": False,
    "relay_discovery": False,
    "allow_game_transfer": True,
    "allow_metadata_sync": True,
    # 2026-08-08, "internal NAT traversal and direct-route publication" --
    # gates PCP/NAT-PMP/UPnP port-mapping attempts. Off by default, same
    # "explicit enable starts only selected capabilities" rule as every
    # other sync capability flag here.
    "nat_traversal": False,
}
_BOOL_KEYS = frozenset(DEFAULTS)


class SyncConfigError(ValueError):
    pass


def config_path(*, base_dir: Path | None = None) -> Path:
    if base_dir is None:
        from pythia.identity import app_data_dir

        base_dir = app_data_dir()
    return Path(base_dir) / "network" / "sync.json"


def load(*, base_dir: Path | None = None) -> dict:
    """Return validated settings; absence means disabled and creates nothing."""
    path = config_path(base_dir=base_dir)
    if not path.is_file():
        return dict(DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncConfigError(f"could not read sync configuration: {exc}") from exc
    if not isinstance(raw, dict):
        raise SyncConfigError("sync configuration must be a JSON object")
    unknown = set(raw) - _BOOL_KEYS
    if unknown:
        raise SyncConfigError(f"unknown sync setting(s): {', '.join(sorted(unknown))}")
    if any(not isinstance(value, bool) for value in raw.values()):
        raise SyncConfigError("all sync configuration values must be booleans")
    result = dict(DEFAULTS)
    result.update(raw)
    return result


def save(values: dict, *, base_dir: Path | None = None) -> dict:
    if not isinstance(values, dict):
        raise SyncConfigError("sync configuration must be a mapping")
    unknown = set(values) - _BOOL_KEYS
    if unknown:
        raise SyncConfigError(f"unknown sync setting(s): {', '.join(sorted(unknown))}")
    if any(not isinstance(value, bool) for value in values.values()):
        raise SyncConfigError("all sync configuration values must be booleans")
    merged = load(base_dir=base_dir)
    merged.update(values)
    path = config_path(base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".sync-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            json.dump(merged, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return merged


def is_enabled(*, base_dir: Path | None = None) -> bool:
    return load(base_dir=base_dir)["enabled"]
