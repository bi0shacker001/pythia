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
file for "key" "value" pairs is enough -- no need for a full recursive VDF parser
for match()/is_root() (unchanged from the original detectors/steam.py port).

`list_owned()` (added 2026-08-03) is new, real account-level ownership: the Steam Web
API's GetOwnedGames, using the user's own API key (never Lutris's -- see
pythia/core/settings.py's docstring for why) and, by default, the most-recently-used
local Steam account on this machine (pythia.core.steam.windows.get_active_steamid64,
ported from Lutris's lutris/util/steam/config.py, itself adapted for Windows since
Lutris's own version only knows Linux Steam data-dir layouts). This closes the
"authenticated Steam library import" gap flagged in the plan's roadmap -- it surfaces
owned titles that aren't installed locally at all, which no appmanifest scan could
ever find.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pythia.plugins import LibraryPlugin
from pythia.plugins.registry import register_builtin

_KV_RE = re.compile(r'"([^"]+)"\s+"([^"]*)"')


def set_api_key(api_key: str):
    """Thin CLI-facing wrapper over the plugin's generic credential storage (see
    PluginMeta.credential_fields). Get a key at
    https://steamcommunity.com/dev/apikey -- deliberately never defaults to Lutris's
    own hardcoded key (see pythia/core/settings.py)."""
    plugin.set_credential("api_key", api_key)


def get_api_key() -> str | None:
    return plugin.get_credential("api_key")


def set_steamid64(steamid64: str):
    """Override which Steam account list_owned() queries -- only needed if
    pythia.core.steam.windows.get_active_steamid64()'s local-login-history guess
    (most-recently-used account on this machine) picks the wrong one."""
    plugin.set_credential("steamid64", steamid64)


def get_steamid64() -> str | None:
    explicit = plugin.get_credential("steamid64")
    if explicit:
        return explicit
    from pythia.core.steam.windows import get_active_steamid64

    return get_active_steamid64()


class SteamLibraryPlugin(LibraryPlugin):
    id = "pythia.library.steam"
    display_name = "Steam"
    version = "1.0.0"
    author = "pythia"
    credential_fields = [
        {"key": "api_key", "label": "Steam Web API key", "secret": True},
        {"key": "steamid64", "label": "SteamID64 override (optional -- auto-detected otherwise)", "secret": False},
    ]

    def is_connected(self) -> bool:
        """Override PluginMeta's default (which requires every declared field) --
        steamid64 is an optional override with a real auto-detected fallback
        (pythia.core.steam.windows.get_active_steamid64), only the API key is
        actually required to connect."""
        return bool(self.get_credential("api_key"))

    def is_root(self, path: Path) -> bool:
        """A Steam-installed game always lives directly under steamapps/common/."""
        return path.parent.name.lower() == "common" and path.parent.parent.name.lower() == "steamapps"

    def list_owned(self):
        """Real account-level ownership via the Steam Web API (see module docstring).
        Returns match()-shaped dicts, or None if no API key is configured yet
        (LibraryPlugin.list_owned()'s documented "path-based detection only"
        sentinel) -- same not-connected-yet contract ItchLibraryPlugin uses."""
        api_key = get_api_key()
        steamid = get_steamid64()
        if not api_key or not steamid:
            return None

        url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/?" + urllib.parse.urlencode(
            {"key": api_key, "steamid": steamid, "format": "json", "include_appinfo": 1}
        )
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            return []

        games = (data.get("response") or {}).get("games") or []
        results = []
        for game in games:
            appid = game.get("appid")
            if appid is None:
                continue
            results.append(
                {
                    "kind": "steam",
                    "name": game.get("name"),
                    "version_label": None,
                    "platforms": [],
                    "external_id": int(appid),
                    "extra": {"steam_appid": str(appid)},
                }
            )
        return results

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
