"""
itch.io library plugin.

Two independent capabilities, deliberately not conflated:

1. `match()`/`is_root()` -- local-install detection via the .itch/receipt.json.gz
   marker the itch app writes into a game's own install folder. 2026-08-03: this
   marker's existence/location is corroborated via web research only, never
   first-hand verified against a real local install (none exists in this library --
   the itch.io test case, Love and Sex: Second Base, is Steam-only on disk here).
   Kept deliberately defensive/best-effort.

2. `list_owned()` -- real account-level ownership, added 2026-08-03 after cloning
   Lutris's actual source (github.com/lutris/lutris, lutris/services/itchio.py) to
   see how it really does this. Lutris's own itch.io service does NOT read any local
   receipt file at all -- it authenticates with a user-supplied itch.io API key
   against itch's real REST API (`GET https://api.itch.io/profile/owned-keys`,
   paginated, Bearer auth), because there's no way to prove ownership from local
   files alone. This directly confirms and supersedes the 2026-08-03 research-agent
   finding noted above. `list_owned()` below ports that same real, working flow to
   stdlib `urllib` (no gi/GTK dependency, unlike Lutris's own code) -- it's the
   genuinely useful piece: it surfaces the user's real itch.io library even for
   titles that were never installed locally at all, which no local-file scan could
   ever find.
"""

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pythia.plugins import LibraryPlugin

_RECEIPT_REL_PATH = (".itch", "receipt.json.gz")
_API_URL = "https://api.itch.io"


def set_api_key(api_key: str):
    """Thin CLI-facing wrapper over the plugin's generic credential storage (see
    PluginMeta.credential_fields) -- kept as a module function so `pythia
    itch-connect` doesn't need to import the plugin instance directly. Obtained from
    https://itch.io/user/settings/api-keys, same source Lutris's own login flow
    points users to."""
    plugin.set_credential("api_key", api_key)


def get_api_key() -> str | None:
    return plugin.get_credential("api_key")


def _api_request(path: str, api_key: str, query: dict | None = None) -> dict:
    url = f"{_API_URL}/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


class ItchLibraryPlugin(LibraryPlugin):
    id = "pythia.library.itch"
    display_name = "itch.io"
    version = "1.0.0"
    author = "pythia"
    credential_fields = [{"key": "api_key", "label": "API key", "secret": True}]

    def is_root(self, path: Path) -> bool:
        return path.joinpath(*_RECEIPT_REL_PATH).is_file()

    def list_owned(self):
        """Real account-level ownership via the itch.io API (see module docstring --
        ported from Lutris's actual ItchIoService.get_owned_games). Returns a list of
        match()-shaped dicts (kind/name/version_label/platforms/external_id/extra),
        or None if no API key is configured (LibraryPlugin.list_owned()'s documented
        "path-based detection only" sentinel)."""
        api_key = get_api_key()
        if not api_key:
            return None

        owned_keys = []
        query = {"page": 1}
        safety = 1000  # same defensive pagination cap Lutris's own code uses
        while safety:
            try:
                response = _api_request("profile/owned-keys", api_key, query)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
                break
            keys = response.get("owned_keys")
            if not isinstance(keys, list):
                break
            owned_keys.extend(keys)
            if len(keys) != int(response.get("per_page", 0) or 0):
                break
            query["page"] += 1
            safety -= 1

        results = []
        for key in owned_keys:
            game = key.get("game") or {}
            game_id = game.get("id")
            if game_id is None:
                continue
            results.append(
                {
                    "kind": "itch",
                    "name": game.get("title"),
                    "version_label": None,
                    "platforms": [],
                    "external_id": int(game_id) if isinstance(game_id, int) else None,
                    "extra": {"itch_game_id": game_id},
                }
            )
        return results

    def _read_receipt(self, path: Path) -> dict | None:
        receipt_path = path.joinpath(*_RECEIPT_REL_PATH)
        try:
            with gzip.open(receipt_path, "rt", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, EOFError, json.JSONDecodeError):
            return None

    def match(self, path: Path):
        if not self.is_root(path):
            return None

        receipt = self._read_receipt(path) or {}
        game = receipt.get("game") or {}
        game_id = game.get("id")

        return {
            "kind": "itch",
            "name": game.get("title"),
            "version_label": None,
            # itch uploads are platform-specific but the receipt schema for which
            # platform *this* install is hasn't been verified -- leaving this empty
            # rather than guessing "windows" the way the Steam plugin's no-manifest
            # fallback does (that one's grounded in "this is the common case,"
            # this one isn't grounded in anything yet).
            "platforms": [],
            "external_id": int(game_id) if isinstance(game_id, int) else None,
            "extra": {"itch_game_id": game_id} if game_id is not None else {},
        }


PLUGIN_CLASSES = [ItchLibraryPlugin]
