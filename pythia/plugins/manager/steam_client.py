"""
Steam client manager plugin -- hands off to Steam's own client via its
"steam://rungameid/<appid>" URI (2026-08-02). Pythia opens the URI; Steam's own
client is what actually launches the game, the same "open a door, don't walk through
it ourselves" shape as launching an external downloader in downloads.py. Kept
separate from the Steam *library* plugin (plugins/library/steam.py) on purpose --
identifying a game as Steam-sourced and knowing how to launch it are different
things; someone could in principle hand a Steam-sourced game off to a different
manager instead (not built here, but the split is what makes that representable).
"""

import os
import subprocess
import sys

from pythia.plugins import ManagerPlugin
from pythia.plugins.registry import register_builtin


class SteamClientManagerPlugin(ManagerPlugin):
    id = "pythia.manager.steam_client"
    display_name = "Steam"
    version = "1.0.0"
    author = "pythia"
    compatible_sources = ["steam"]

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


plugin = register_builtin(SteamClientManagerPlugin())
