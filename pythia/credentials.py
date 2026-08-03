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

from pythia.identity import datadir_path, get_system_uuid


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
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get_credential(plugin_id: str, key: str) -> str | None:
    """This system's own value if it has one, else the first other system's synced
    value that has it (see module docstring for why this isn't a real merge)."""
    own = _read(_own_path())
    value = (own.get(plugin_id) or {}).get(key)
    if value:
        return value

    this_uuid = get_system_uuid()
    for path in sorted(_credentials_dir().glob("*.json")):
        if path.stem == this_uuid:
            continue
        other = _read(path)
        value = (other.get(plugin_id) or {}).get(key)
        if value:
            return value
    return None


def set_credential(plugin_id: str, key: str, value: str):
    path = _own_path()
    data = _read(path)
    data.setdefault(plugin_id, {})[key] = value
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
