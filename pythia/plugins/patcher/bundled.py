"""
Bundled-patcher-tool detector -- recognizes a game that ships its own translation/
patch tool directly inside the install folder. Grounded in a real example
(2026-08-02): MGQ Paradox (RPG Maker VX Ace) ships a "One-Click Patcher" folder
containing a VXAceTranslator-based fan translation tool with its own
"Start Patcher.bat". Detection only -- see PatcherPlugin's docstring for why this
deliberately has no apply(); check_updates()/fetch() aren't implemented either, since
where these patches actually come from and how they publish updates hasn't been
researched yet (same "don't fabricate" rule as the other real-inspection-needed
stubs elsewhere -- f95checker's StdoutWatcher, steamcmd's real CLI contract).
"""

from pathlib import Path

from pythia.plugins import PatcherPlugin
from pythia.plugins.registry import register_builtin

_BUNDLED_PATCHER_FOLDER_NAME = "one-click patcher"


class BundledPatcherPlugin(PatcherPlugin):
    id = "pythia.patcher.bundled"
    display_name = "Bundled patcher tool (generic)"
    version = "1.0.0"
    author = "pythia"

    def detect(self, game_root: Path) -> dict | None:
        game_root = Path(game_root)
        try:
            subdirs = {p.name.lower() for p in game_root.iterdir() if p.is_dir()}
        except OSError:
            return None
        if _BUNDLED_PATCHER_FOLDER_NAME not in subdirs:
            return None
        return {"kind": "bundled", "folder": "One-Click Patcher"}


plugin = register_builtin(BundledPatcherPlugin())
