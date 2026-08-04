"""
Steam plugin -- one built-in integration spanning three capabilities (2026-08-03,
merged from three previously-separate classes after a real architecture bug was
confirmed: the Plugins page showed "Steam" three times, once per category, because
library/manager/metadata each instantiated their own independent class instead of
one plugin implementing all three interfaces). A single real product -- the Steam
client plus the user's own Steam account -- should be one toggleable thing in the
Plugins page; per-capability enable/disable still lives on the Sources/Managers/
Metadata pages exactly as before (isinstance-based category views are unaffected
by merging the underlying classes).

This does not remove the general architectural point that identification (Library)
and launch capability (Manager) are independently pluggable concepts (see
pythia/plugins/__init__.py's ManagerPlugin docstring -- a third-party plugin could
still add an alternative launcher for kind="steam" games, e.g. Heroic-for-Steam-ish).
It only means Pythia's own built-in Steam integration -- which will always offer its
own client as the natural default launcher for Steam-kind games -- doesn't need to
be split into three separately-toggleable pieces to preserve that extensibility.

**Local detection** -- identifies games living under a steamapps/common/ folder and
enriches them with metadata from the sibling appmanifest_<appid>.acf file Steam
itself already maintains there. ACF files are a simple nested key-value text format
(VDF); a regex scan over the whole file for "key" "value" pairs is enough -- no need
for a full recursive VDF parser here.

**Account sync** (`list_owned()`) -- real account-level ownership via the Steam Web
API's GetOwnedGames, using the user's own API key (never Lutris's -- see
pythia/core/settings.py's docstring for why) and, by default, the most-recently-used
local Steam account on this machine (pythia.core.steam.windows.get_active_steamid64,
ported from Lutris's lutris/util/steam/config.py, itself adapted for Windows since
Lutris's own version only knows Linux Steam data-dir layouts). Surfaces owned titles
that aren't installed locally at all, which no appmanifest scan could ever find.

**Launch hand-off** (`open()`) -- hands off to Steam's own client via its
"steam://rungameid/<appid>" URI; Steam's own client is what actually launches the
game, the same "open a door, don't walk through it ourselves" shape as launching an
external downloader in downloads.py.

**Metadata** (`platform_support()`/`list_dlc()`) -- pulls platform support and DLC
listings from Steam's public appdetails API, unauthenticated, no API key needed,
works for a game the user doesn't own. Verified 2026-08-03 against real appids from
the user's library (Sunrider Academy/340730 for platform_support; Love and Sex:
Second Base/1264710 for list_dlc, which found 5 real DLC IDs with real names).
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pythia.plugins import LibraryPlugin, ManagerPlugin, MetadataPlugin
from pythia.plugins.registry import register_builtin

_KV_RE = re.compile(r'"([^"]+)"\s+"([^"]*)"')
_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={appid}"


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


class SteamPlugin(LibraryPlugin, ManagerPlugin, MetadataPlugin):
    id = "pythia.library.steam"
    display_name = "Steam"
    version = "1.0.0"
    author = "pythia"
    credential_fields = [
        {"key": "api_key", "label": "Steam Web API key", "secret": True},
        {"key": "steamid64", "label": "SteamID64 override (optional -- auto-detected otherwise)", "secret": False},
    ]
    compatible_sources = ["steam"]

    # ---- Library: local detection + account sync -----------------------------
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

    # ---- Manager: hand-off to Steam's own client ------------------------------
    def is_available(self, ctx) -> bool:
        """No reliable cross-platform "is Steam installed" check without touching
        the registry/specific install paths, which hasn't been built yet -- reports
        unknown-as-unavailable (False) rather than guessing. open() still works
        regardless (the OS URI handler either resolves or the hand-off silently
        does nothing), this only affects whether a future UI shows Steam as a ready
        option."""
        return False

    def open(self, detection: dict, ctx) -> bool:
        """No-op (returns False) if `detection` has no Steam AppID to hand off to."""
        appid = (detection.get("extra") or {}).get("steam_appid")
        if not appid:
            return False

        uri = f"steam://rungameid/{appid}"
        try:
            if sys.platform == "win32":
                os.startfile(uri)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", uri])
            else:
                subprocess.Popen(["xdg-open", uri])
        except OSError:
            return False
        return True

    # ---- Metadata: public appdetails API ---------------------------------------
    def _appdetails(self, appid) -> dict | None:
        """Shared fetch for platform_support()/list_dlc(). Returns the "data"
        object for `appid`, or None on any failure (network, unexpected shape, or a
        genuinely unknown appid)."""
        url = _APPDETAILS_URL.format(appid=appid)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError):
            return None

        app_entry = data.get(str(appid))
        if not app_entry or not app_entry.get("success"):
            return None
        return app_entry.get("data") or {}

    def platform_support(self, platform_id: dict) -> list | None:
        appid = platform_id.get("id")
        if not appid:
            return None
        data = self._appdetails(appid)
        if data is None:
            return None
        platforms = data.get("platforms") or {}
        return [p for p, supported in platforms.items() if supported]

    def list_dlc(self, platform_id: dict) -> list | None:
        appid = platform_id.get("id")
        if not appid:
            return None
        data = self._appdetails(appid)
        if data is None:
            return None

        dlc_ids = data.get("dlc") or []
        result = []
        for dlc_id in dlc_ids:
            dlc_data = self._appdetails(dlc_id)
            name = dlc_data.get("name") if dlc_data else None
            result.append({"id": str(dlc_id), "name": name})
        return result


plugin = register_builtin(SteamPlugin())
