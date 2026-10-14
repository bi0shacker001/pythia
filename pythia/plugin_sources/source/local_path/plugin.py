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

Configured roots are a plain list of path strings in this plugin's own
generic per-plugin config (self.get_config/self.set_config, inherited from
PluginMeta -- 2026-08-07: "Plugins need to be completely self-contained...
They have their own configurations to give them what they need") -- local
to whichever machine mounted it, same as it always was; only the storage
mechanism changed, from a bespoke identity.get_setting call to the standard
per-plugin config channel every plugin now uses.
"""

import os
import shutil
from pathlib import Path

from pythia.plugins import SourcePlugin


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

    def get_roots(self) -> list[str]:
        roots = self.get_config("roots", [])
        return roots if isinstance(roots, list) else []

    def add_root(self, path: str) -> None:
        roots = self.get_roots()
        if path not in roots:
            roots.append(path)
            self.set_config("roots", roots)

    def remove_root(self, path: str) -> None:
        self.set_config("roots", [r for r in self.get_roots() if r != path])

    def list_available(self):
        results = []
        for root in self.get_roots():
            results.extend(self.browse(root))
        return results

    def browse(self, path=None):
        if path is None:
            return [_entry_dict(Path(r)) for r in self.get_roots() if Path(r).is_dir()]
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
        for root in self.get_roots():
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


PLUGIN_CLASSES = [LocalPathSourcePlugin]
