"""
On-disk instance marker files, read only (2026-08-04, Milestone 9's `game_uuid`-only slice
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

**Pythia no longer writes them (2026-10-16).** Owner direction for the
Windows test (Droidtop/tracker#538): Pythia never writes into a person's game
folders. An install's identity lives in Pythia's own data: the entry's
`game_uuid` and the variant's `path` in `datadir/`. A marker written by an
earlier Pythia is still read as evidence (read_marker), since reading writes
nothing; none is created or updated.
"""

import json
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
