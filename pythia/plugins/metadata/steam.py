"""
Steam metadata plugin -- pulls platform support and DLC listings from Steam's public
appdetails API, unauthenticated, no API key needed, works for a game the user doesn't
own. Deliberately separate from plugins/library/steam.py's local install detection
(2026-08-03: metadata and downloads are handled completely separately, and that
separation extends to detection too -- see MetadataPlugin's docstring).
"""

import json
import urllib.error
import urllib.request

from pythia.plugins import MetadataPlugin
from pythia.plugins.registry import register_builtin

_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={appid}"


class SteamMetadataPlugin(MetadataPlugin):
    id = "pythia.metadata.steam"
    display_name = "Steam"
    version = "1.0.0"
    author = "pythia"

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
        """2026-08-03, verified against a real appid from the user's library,
        Sunrider Academy/340730, before shipping: response platforms matched its
        actual on-disk lib folders exactly."""
        appid = platform_id.get("id")
        if not appid:
            return None
        data = self._appdetails(appid)
        if data is None:
            return None
        platforms = data.get("platforms") or {}
        return [p for p, supported in platforms.items() if supported]

    def list_dlc(self, platform_id: dict) -> list | None:
        """appdetails' "dlc" field is just a list of app IDs -- one appdetails call
        per DLC ID gets its real name. 2026-08-03, verified against a real appid
        from the user's library, Love and Sex: Second Base/1264710: found 5 real DLC
        IDs (For A Fistful Of Waifu, Lively World, Bree's story, Supporter Pack, For
        A Few Waifus More) with real names, used as the basis for this
        implementation."""
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


plugin = register_builtin(SteamMetadataPlugin())
