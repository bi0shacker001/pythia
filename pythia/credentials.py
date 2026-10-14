"""
Synced plugin credentials -- `datadir/credentials/{system_uuid}.json`.

2026-08-03: originally, `PluginMeta.get_credential`/`set_credential` wrote to
`local.db` (never synced), on the reasoning "secrets shouldn't go in the shared
sync folder." That was wrong for Pythia's actual use case: a login flow with real
friction (GOG's multi-step OAuth authorization-code dance, in particular) would have
to be independently repeated on every synced system -- exactly the tedium Pythia
exists to eliminate on awkward-input devices (the GPD Win 4/Retroid Pocket 5 case
this whole project started from). So credentials now live inside `datadir/`, the
same folder Syncthing already watches, and sync automatically.

**Real, accepted tradeoff, stated plainly rather than buried**: this means Steam Web
API keys, itch.io API keys, and GOG OAuth refresh tokens are all readable by anything
with read access to the synced folder -- another Syncthing peer, a cloud backup of
that folder, etc. There is no encryption at rest here. This is a conscious choice
(2026-08-03, explicit user instruction) favoring "log in once, works everywhere"
over defense against someone who already has access to your sync folder; it is not
defense against a hostile sync peer. Revisiting this with real encryption (e.g. a
per-system passphrase-derived key) is real, deferred future work, not implemented.

Sync-safety model: same single-writer-per-file rule as every other file in datadir/
(see datadir.py's own module docstring) -- a system only ever writes
`credentials/{its own system_uuid}.json`; every other system's credentials file is
read-only synced data. One file per system (not per plugin) bundling every
credential that system has ever set, keyed by plugin id then field key. A system
reading the merged view prefers its own file if present, otherwise the first other
system's file that has a value for that plugin/key -- "whoever logged in most
recently wins" is deliberately not attempted; that needs real conflict semantics
(see the plan's Milestone 6A "no deterministic merge semantics" gap) this module
doesn't try to solve.

Kept out of `datadir/`'s own top-level *.json glob on purpose (datadir.
list_all_entries() only globs the top level, not recursively) so this never gets
misparsed as a game entry.
"""

import json
import os
import tempfile
import threading

from pythia.identity import datadir_path, get_system_uuid


_WRITE_LOCK = threading.RLock()
MAX_CREDENTIAL_FILE_BYTES = 1024 * 1024


def _credentials_dir():
    d = datadir_path() / "credentials"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _own_path():
    return _credentials_dir() / f"{get_system_uuid()}.json"


def _read(path) -> dict:
    if not path.is_file():
        return {}
    try:
        if path.stat().st_size > MAX_CREDENTIAL_FILE_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        # Synced files are peer-controlled input.  Keep only the schema this
        # module writes so malformed values cannot make get_credential() call
        # `.get()` on a string/list or propagate non-string secrets.
        return {
            plugin_id: {key: value for key, value in fields.items() if isinstance(key, str) and isinstance(value, str)}
            for plugin_id, fields in data.items()
            if isinstance(plugin_id, str) and isinstance(fields, dict)
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def get_credential(plugin_id: str, key: str) -> str | None:
    """This system's own value if it has one, else the first other system's synced
    value that has it (see module docstring for why this isn't a real merge)."""
    own = _read(_own_path())
    own_fields = own.get(plugin_id) or {}
    if key in own_fields:
        return own_fields[key] or None

    this_uuid = get_system_uuid()
    for path in sorted(_credentials_dir().glob("*.json")):
        if path.stem == this_uuid:
            continue
        other = _read(path)
        other_fields = other.get(plugin_id) or {}
        if key in other_fields:
            return other_fields[key] or None
    return None


def set_credential(plugin_id: str, key: str, value: str):
    """Persist one credential without exposing a partially-written JSON file.

    The file may be observed by a sync engine at any moment, so write a complete
    sibling file, flush it, and atomically replace the prior version.  The lock
    also prevents two in-process login callbacks from losing each other's update.
    """
    if not all(isinstance(item, str) for item in (plugin_id, key, value)):
        raise TypeError("credential plugin id, key, and value must be strings")
    path = _own_path()
    with _WRITE_LOCK:
        data = _read(path)
        data.setdefault(plugin_id, {})[key] = value
        serialized = json.dumps(data, indent=2)
        if len(serialized.encode("utf-8")) > MAX_CREDENTIAL_FILE_BYTES:
            raise ValueError("credential store exceeds the maximum file size")
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(serialized)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
        finally:
            if temporary_name:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
