"""
GOG library plugin.

Two independent capabilities, same split as itch.io/Steam:

1. `match()`/`is_root()` -- local-install detection via `goggame-{id}.info`, a real
   JSON file GOG places directly in a game's own install folder (confirmed in
   Lutris's actual source, `lutris/scanners/playtron.py` -- not a web guess, unlike
   the itch.io receipt-file precedent which stayed flagged "unverified" for exactly
   that reason). Contains a `playTasks` array Pythia doesn't need (that's launch
   information); the product ID is pulled straight from the filename.

2. `list_owned()` -- real account-level ownership via GOG's OAuth2 API. GOG has no
   self-serve third-party client registration (confirmed directly from GOG's own
   community forum: "As we have no way to register our own clients, this makes
   everything a lot more complicated") -- `_CLIENT_ID`/`_CLIENT_SECRET` below are the
   same values Lutris's `services/gog.py` uses, independently confirmed to be a
   de-facto shared community credential (the identical value is documented in the
   unofficial `gogapidocs` reference too), not something uniquely borrowed from one
   project. User-approved 2026-08-03: adopt the same shared credential, same
   real-world footing as every other established open-source GOG tool.

   The login flow is authorization-code OAuth2, normally completed via an embedded
   browser (Lutris's own approach) that intercepts the redirect. Pythia has no
   embedded browser by design (avoids a QtWebEngine dependency) -- instead the user
   opens the login URL in their own browser, logs in, and pastes the final redirected
   URL back (its query string carries the authorization code). See cli.py's
   `gog-connect`/`gog-token` commands.

   Library listing uses `GET https://embed.gog.com/user/data/games` (Bearer-token
   auth, returns bare owned product IDs, per the unofficial API reference) rather
   than Lutris's own `account/getFilteredProducts` (which needs browser session
   cookies Pythia has no way to capture without an embedded browser). This means
   `list_owned()` gets IDs but not names -- resolving a name from a product ID is a
   metadata-plugin's job (mirrors platform_support()/list_dlc() already being split
   out of SteamLibraryPlugin into its own MetadataPlugin), not implemented here.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from pythia.plugins import LibraryPlugin
from pythia.plugins.registry import register_builtin

_CLIENT_ID = "46899977096215655"
_CLIENT_SECRET = "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"
_REDIRECT_URI = "https://embed.gog.com/on_login_success?origin=client"
_AUTH_URL = "https://auth.gog.com/auth"
_TOKEN_URL = "https://auth.gog.com/token"
_LIBRARY_URL = "https://embed.gog.com/user/data/games"


def login_url() -> str:
    """The URL to open in a real browser -- log in there, then pass the final
    redirected URL (it 404s or shows a blank GOG page, but the address bar carries
    the authorization code) to gog_token_from_url()."""
    params = {
        "client_id": _CLIENT_ID,
        "layout": "client2",
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
    }
    return _AUTH_URL + "?" + urllib.parse.urlencode(params)


def _exchange(params: dict) -> dict:
    url = _TOKEN_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def connect_from_redirect_url(redirected_url: str) -> bool:
    """Parse the authorization code out of the pasted redirect URL, exchange it for
    an access+refresh token pair, and store the refresh token (synced, see
    pythia.credentials). Returns False if the URL has no ?code= (user pasted the
    wrong thing, or the login was cancelled)."""
    query = dict(parse_qsl(urlparse(redirected_url).query))
    code = query.get("code")
    if not code:
        return False
    try:
        token = _exchange(
            {
                "client_id": _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _REDIRECT_URI,
            }
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return False
    if "refresh_token" not in token:
        return False
    plugin.set_credential("refresh_token", token["refresh_token"])
    plugin.set_credential("access_token", token["access_token"])
    plugin.set_credential("access_token_fetched_at", str(int(time.time())))
    return True


def _fresh_access_token() -> str | None:
    refresh_token = plugin.get_credential("refresh_token")
    if not refresh_token:
        return None

    fetched_at = plugin.get_credential("access_token_fetched_at")
    age = time.time() - float(fetched_at) if fetched_at else float("inf")
    if age < 2600:  # same margin Lutris's own code uses (tokens last ~1hr)
        cached = plugin.get_credential("access_token")
        if cached:
            return cached

    try:
        token = _exchange(
            {
                "client_id": _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None
    if "access_token" not in token:
        return None
    plugin.set_credential("access_token", token["access_token"])
    plugin.set_credential("access_token_fetched_at", str(int(time.time())))
    # A refreshed token pair sometimes carries a new refresh_token too -- keep it if so.
    if token.get("refresh_token"):
        plugin.set_credential("refresh_token", token["refresh_token"])
    return token["access_token"]


class GogLibraryPlugin(LibraryPlugin):
    id = "pythia.library.gog"
    display_name = "GOG"
    version = "1.0.0"
    author = "pythia"
    credential_fields = [{"key": "refresh_token", "label": "Connected (via gog-connect)", "secret": True}]

    def is_connected(self) -> bool:
        return bool(self.get_credential("refresh_token"))

    def is_root(self, path: Path) -> bool:
        return any(path.glob("goggame-*.info")) or self._registry_entry_for(path) is not None

    def _registry_entry_for(self, path: Path) -> dict | None:
        """A Windows-only *bonus* signal, not the primary detection path (2026-08-03
        -- Pythia needs to run on Linux too, per the project's own long-term vision,
        so `goggame-*.info` below stays the real, cross-platform signature every
        platform can rely on). Added after finding real entries under
        HKLM\\SOFTWARE\\WOW6432Node\\GOG.com\\Games\\<id> on a real Windows machine --
        GOG's offline/standalone installer writes one subkey per game here, with
        real `path`/`ver`/`gameName` values, independent of whether the GOG Galaxy
        client is even installed. When it's available it's richer (a real version
        string and display name the file-marker alone can't give), but `winreg`
        doesn't exist on Linux at all -- this returns None there immediately, and
        match() falls straight through to the file-based check, unaffected."""
        try:
            import winreg
        except ImportError:
            return None
        resolved = str(path.resolve())
        try:
            games_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games")
        except OSError:
            return None
        try:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(games_key, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(games_key, subkey_name) as subkey:
                        game_path, _ = winreg.QueryValueEx(subkey, "path")
                        if str(Path(game_path).resolve()) != resolved:
                            continue
                        entry = {"gog_id": subkey_name}
                        for value_name in ("gameName", "ver"):
                            try:
                                entry[value_name], _ = winreg.QueryValueEx(subkey, value_name)
                            except OSError:
                                pass
                        return entry
                except OSError:
                    continue
        finally:
            games_key.Close()
        return None

    def match(self, path: Path):
        registry_entry = self._registry_entry_for(path)
        if registry_entry is not None:
            gog_id = registry_entry["gog_id"]
            return {
                "kind": "gog",
                "name": registry_entry.get("gameName"),
                "version_label": registry_entry.get("ver"),
                "platforms": ["windows"],
                "external_id": int(gog_id) if gog_id.isdigit() else None,
                "extra": {"gog_id": gog_id},
            }

        info_files = list(path.glob("goggame-*.info"))
        if not info_files:
            return None
        stem = info_files[0].stem  # "goggame-1207658691"
        gog_id = stem[len("goggame-"):]
        return {
            "kind": "gog",
            "name": None,
            "version_label": None,
            "platforms": ["windows"],
            "external_id": int(gog_id) if gog_id.isdigit() else None,
            "extra": {"gog_id": gog_id},
        }

    def list_owned(self):
        access_token = _fresh_access_token()
        if not access_token:
            return None

        req = urllib.request.Request(_LIBRARY_URL, headers={"Authorization": f"Bearer {access_token}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            return []

        owned_ids = data.get("owned") or []
        return [
            {
                "kind": "gog",
                "name": None,
                "version_label": None,
                "platforms": [],
                "external_id": int(gog_id),
                "extra": {"gog_id": str(gog_id)},
            }
            for gog_id in owned_ids
            if isinstance(gog_id, int)
        ]


plugin = register_builtin(GogLibraryPlugin())
