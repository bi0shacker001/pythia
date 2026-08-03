"""
Epic Games Launcher manager plugin -- hands off via Epic's own real, documented URI
protocol (`com.epicgames.launcher://apps/<AppName>?action=launch&silent=true`,
confirmed against Epic's own developer docs, dev.epicgames.com/docs/epic-games-store/
protocol-activation -- not guessed). Same "open a door, don't walk through it" shape
as the Steam manager plugin: Pythia opens the URI, the Epic client (if installed and
registered as the URI handler) is what actually launches the game.

`AppName` is the same identifier `EgsLibraryPlugin.match()`/`list_owned()` already
put in `extra["egs_app_name"]` -- no separate lookup needed.
"""

import os
import subprocess
import sys
import urllib.parse

from pythia.plugins import ManagerPlugin
from pythia.plugins.registry import register_builtin


class EpicClientManagerPlugin(ManagerPlugin):
    id = "pythia.manager.epic_client"
    display_name = "Epic Games Launcher"
    version = "1.0.0"
    author = "pythia"
    compatible_sources = ["egs"]

    def is_available(self, ctx) -> bool:
        """Same honest "unknown" as the Steam manager plugin -- no reliable
        cross-platform "is the Epic client installed" check built yet."""
        return False

    def open(self, detection: dict, ctx) -> bool:
        """No-op (returns False) if `detection` has no Epic AppName to hand off to."""
        app_name = (detection.get("extra") or {}).get("egs_app_name")
        if not app_name:
            return False

        query = urllib.parse.urlencode({"action": "launch", "silent": "true"})
        uri = f"com.epicgames.launcher://apps/{app_name}?{query}"
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


plugin = register_builtin(EpicClientManagerPlugin())
