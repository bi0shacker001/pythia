"""
Steam library plugin -- identifies games living under a steamapps/common/ folder and
enriches them with metadata from the sibling appmanifest_<appid>.acf file Steam
itself already maintains there. Local detection only -- pulling metadata from
Steam's public web API (platform support, DLC) is a separate, independently
enable-able plugin: see plugins/metadata/steam.py (2026-08-03: "metadata and
downloads are handled completely separately," and that separation extends to local
detection too -- a user should be able to disable Steam metadata lookups without
losing local Steam install detection, or vice versa).

ACF files are a simple nested key-value text format (VDF). We only need a few
flat fields (appid, name, installdir, buildid), so a regex scan over the whole
file for "key" "value" pairs is enough -- no need for a full recursive VDF parser.

Moved verbatim from detectors/steam.py, adapted to the LibraryPlugin interface only.
"""

import re
from pathlib import Path

from pythia.plugins import LibraryPlugin
from pythia.plugins.registry import register_builtin

_KV_RE = re.compile(r'"([^"]+)"\s+"([^"]*)"')


class SteamLibraryPlugin(LibraryPlugin):
    id = "pythia.library.steam"
    display_name = "Steam"
    version = "1.0.0"
    author = "pythia"

    def is_root(self, path: Path) -> bool:
        """A Steam-installed game always lives directly under steamapps/common/."""
        return path.parent.name.lower() == "common" and path.parent.parent.name.lower() == "steamapps"

    def shortcut_roots(self, path: Path):
        """Once a steamapps/ folder is found, every relevant child already lives
        directly under common/ -- no need for scanning.py to walk into each
        install's own internals looking for nested roots, same optimization the
        original hardcoded scanning.py logic had, just owned by this plugin now."""
        if not (path / "steamapps").is_dir():
            return None
        common = path / "steamapps" / "common"
        if not common.is_dir():
            return []
        try:
            children = sorted(p for p in common.iterdir() if p.is_dir())
        except OSError:
            children = []
        return [c for c in children if self.is_root(c)]

    def _parse_acf(self, text: str) -> dict:
        return dict(_KV_RE.findall(text))

    def _find_manifest_for(self, steamapps_dir: Path, installdir_name: str):
        for acf_path in steamapps_dir.glob("appmanifest_*.acf"):
            try:
                text = acf_path.read_text(errors="replace")
            except OSError:
                continue
            fields = self._parse_acf(text)
            if fields.get("installdir") == installdir_name:
                return fields
        return None

    def match(self, path: Path):
        if not self.is_root(path):
            return None

        steamapps_dir = path.parent.parent
        fields = self._find_manifest_for(steamapps_dir, path.name)

        if fields is None:
            # Positioned like a Steam game but no matching manifest found (unusual --
            # maybe installed by other means). Still identify as Steam-kind, just
            # without an external ID to reuse.
            return {
                "kind": "steam",
                "name": None,
                "version_label": None,
                "platforms": ["windows"],
                "external_id": None,
                "extra": {},
            }

        appid = fields.get("appid")
        return {
            "kind": "steam",
            "name": fields.get("name"),
            "version_label": fields.get("buildid"),
            # Steam's own on-disk layout doesn't cheaply expose which platform depot is
            # installed; default to windows (the common case) until depot-level
            # inspection is added.
            "platforms": ["windows"],
            "external_id": int(appid) if appid and appid.isdigit() else None,
            "extra": {"steam_appid": appid, "buildid": fields.get("buildid")},
        }


plugin = register_builtin(SteamLibraryPlugin())
