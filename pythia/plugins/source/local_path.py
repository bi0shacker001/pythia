"""
Local/mounted-path source plugin (2026-08-03) -- Pythia's first real SourcePlugin,
closing the one plugin category with zero implementations until now.

Deliberately the simplest possible real source: the user points Pythia at a path
that's already a normal filesystem location as far as this OS is concerned -- a
UNC network share on Windows (`\\\\nas\\share`), an already-mounted network location
on Linux (`/mnt/nas`, via an existing fstab cifs/nfs mount the user set up
themselves), or just another local drive. No SMB/WebDAV/NFS protocol code lives
here at all: the OS's own mount/UNC layer already did that work, so this only ever
does plain filesystem I/O (os.scandir, shutil.copy) -- which is what makes it
genuinely cross-platform for free, no new dependency, real today rather than
aspirational. A real protocol-level client (connecting to a share directly without
an OS-level mount already in place) is legitimate future work, but needs the
user's actual NAS setup confirmed first, same "don't build against an unconfirmed
real-world detail" rule as everywhere else in this project.

Driving use case (2026-08-02 plan discussion): a NAS-hosted ROM collection too
large to sync everywhere -- list what's there, fetch one item on demand, without
ever needing the whole collection mirrored locally.

Configured roots are a plain list of path strings in local.db (via
identity.get_setting/set_setting, same pattern as f95_db_path/ui_theme) --
per-system, not synced, since a mount point/UNC path is inherently local to
whichever machine mounted it.
"""

import json
import os
import shutil
from pathlib import Path

from pythia.identity import get_setting, set_setting
from pythia.plugins import SourcePlugin
from pythia.plugins.registry import register_builtin

_SETTING_KEY = "source_local_path_roots"


def get_roots() -> list[str]:
    raw = get_setting(_SETTING_KEY)
    if not raw:
        return []
    try:
        roots = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return roots if isinstance(roots, list) else []


def add_root(path: str):
    roots = get_roots()
    if path not in roots:
        roots.append(path)
        set_setting(_SETTING_KEY, json.dumps(roots))


def remove_root(path: str):
    roots = [r for r in get_roots() if r != path]
    set_setting(_SETTING_KEY, json.dumps(roots))


def _entry_dict(p: Path) -> dict:
    is_dir = p.is_dir()
    size = None
    if not is_dir:
        try:
            size = p.stat().st_size
        except OSError:
            size = None
    return {"name": p.name, "path": str(p), "is_dir": is_dir, "size": size}


class LocalPathSourcePlugin(SourcePlugin):
    id = "pythia.source.local_path"
    display_name = "Local/mounted path"
    version = "1.0.0"
    author = "pythia"

    def list_available(self):
        results = []
        for root in get_roots():
            results.extend(self.browse(root))
        return results

    def browse(self, path=None):
        if path is None:
            return [_entry_dict(Path(r)) for r in get_roots() if Path(r).is_dir()]
        base = Path(path)
        if not base.is_dir():
            return []
        try:
            entries = sorted(base.iterdir(), key=lambda e: e.name.lower())
        except OSError:
            return []
        return [_entry_dict(e) for e in entries]

    def search(self, query: str) -> list:
        query_lower = query.lower()
        matches = []
        for root in get_roots():
            try:
                for dirpath, _dirnames, filenames in os.walk(root):
                    for name in filenames:
                        if query_lower in name.lower():
                            matches.append(_entry_dict(Path(dirpath) / name))
            except OSError:
                continue
        return matches

    def fetch(self, item, dest: Path, log):
        src = Path(item["path"] if isinstance(item, dict) else item)
        dest = Path(dest)
        log(f"Copying {src} -> {dest}...")
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        log("Done.")


plugin = register_builtin(LocalPathSourcePlugin())
