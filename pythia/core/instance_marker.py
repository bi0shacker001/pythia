"""
On-disk instance marker files (2026-08-04, Milestone 9's `game_uuid`-only slice
-- the full instance model/`instance_uuid` convergence stays deferred with the
rest of the mod-manager/instance-model work, per explicit user instruction).

Written by Pythia directly into a variant's own folder (not `datadir/`) so it
travels with the actual game files through *any* external sync mechanism
(OneDrive, rsync, a NAS mount, a second Syncthing pair pointed at the game
folder itself) -- that's the entire point: when the same marker turns up on
another system, it's real physical evidence the two systems are looking at
the same synced install, not just a plausible name match. See the plan's
Milestone 9 for the full design and its real, confirmed Syncthing caveats
(dotfiles sync by default, but users sometimes opt out via `.stignore`, and a
Unix dotfile isn't automatically given the Windows hidden attribute on
arrival).

Deliberately a hidden dotfile, plain JSON -- self-describing even before
cross-referencing the corresponding synced `datadir/*.json` entry, and safe to
read manually if something needs debugging.
"""

import ctypes
import datetime
import json
import sys
import uuid
from pathlib import Path

MARKER_FILENAME = ".pythia-instance"


def marker_path(variant_root: Path) -> Path:
    return Path(variant_root) / MARKER_FILENAME


def read_marker(variant_root: Path) -> dict | None:
    path = marker_path(variant_root)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_marker(variant_root: Path, game_uuid: str, version: str, mods: list, language, system_uuid: str) -> None:
    """Best-effort -- a write failure (e.g. a read-only or not-yet-existing
    path) should never block onboarding, so this never raises. Preserves the
    marker's own `instance_uuid`/`created_at` across repeat calls instead of
    re-minting them every time this variant is re-onboarded/rescanned."""
    path = marker_path(variant_root)
    if not Path(variant_root).is_dir():
        return
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    existing = read_marker(variant_root)
    data = {
        "game_uuid": game_uuid,
        "instance_uuid": (existing or {}).get("instance_uuid") or str(uuid.uuid4()),
        "version": version,
        "mods": mods,
        "language": language,
        "produced_by_system_uuid": system_uuid,
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _set_hidden(path)
    except OSError:
        pass


def _set_hidden(path: Path) -> None:
    """Best-effort Windows hidden-attribute set -- on POSIX the leading dot
    already makes it a dotfile by convention, nothing more to do here. Real,
    confirmed limitation (see Milestone 9 §D): this attribute is local-only,
    it doesn't travel through an external sync tool the way the dotfile
    convention itself does."""
    if sys.platform != "win32":
        return
    try:
        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except OSError:
        pass
