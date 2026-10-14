"""Non-boolean sync network settings: listener host/port and the global
discovery server URL. Kept in a separate file from config.py's DEFAULTS on
purpose -- config.py is intentionally boolean-only (see its own strict
validation), and there's no canonical global discovery server configured
anywhere in this codebase, so a real URL is a real user-supplied setting,
never a hardcoded default. Reading the default state performs no writes,
matching config.py's own contract.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DEFAULTS = {
    "listen_host": "0.0.0.0",
    "listen_port": 24030,
    "global_discovery_server": "",
    # A real, user-supplied mounted-folder/NAS/cloud-synced path (2026-08-08,
    # Codex sync review, "Orchestrate staging.py as an optional metadata
    # fallback: explicit folder/NAS/cloud-synced directory ... Add
    # Settings/UI controls"). Empty means not configured -- never a guessed
    # default path.
    "staging_folder": "",
    # A real, user-supplied relay rendezvous server "host:port" (2026-08-08,
    # "relay-assisted rendezvous and direct-path candidate orchestration").
    # No canonical relay server exists anywhere in this codebase either --
    # same "real user setting, never a hardcoded default" rule as
    # global_discovery_server.
    "relay_server": "",
}


class NetworkSettingsError(ValueError):
    pass


def settings_path(*, base_dir: Path | None = None) -> Path:
    if base_dir is None:
        from pythia.identity import app_data_dir

        base_dir = app_data_dir()
    return Path(base_dir) / "network" / "network_settings.json"


def _validate(result: dict) -> None:
    if not isinstance(result["listen_host"], str) or not result["listen_host"]:
        raise NetworkSettingsError("listen_host must be a non-empty string")
    if not isinstance(result["listen_port"], int) or isinstance(result["listen_port"], bool) or not (
        0 < result["listen_port"] < 65536
    ):
        raise NetworkSettingsError("listen_port must be an integer between 1 and 65535")
    if not isinstance(result["global_discovery_server"], str):
        raise NetworkSettingsError("global_discovery_server must be a string")
    if not isinstance(result["staging_folder"], str):
        raise NetworkSettingsError("staging_folder must be a string")
    if not isinstance(result["relay_server"], str):
        raise NetworkSettingsError("relay_server must be a string")


def load(*, base_dir: Path | None = None) -> dict:
    path = settings_path(base_dir=base_dir)
    if not path.is_file():
        return dict(DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NetworkSettingsError(f"could not read network settings: {exc}") from exc
    if not isinstance(raw, dict):
        raise NetworkSettingsError("network settings must be a JSON object")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise NetworkSettingsError(f"unknown network setting(s): {', '.join(sorted(unknown))}")
    result = dict(DEFAULTS)
    result.update(raw)
    _validate(result)
    return result


def save(values: dict, *, base_dir: Path | None = None) -> dict:
    if not isinstance(values, dict):
        raise NetworkSettingsError("network settings must be a mapping")
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise NetworkSettingsError(f"unknown network setting(s): {', '.join(sorted(unknown))}")
    merged = load(base_dir=base_dir)
    merged.update(values)
    _validate(merged)
    path = settings_path(base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".network-settings-", dir=path.parent)
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
