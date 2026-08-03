"""
Unity engine plugin. Signature verified against a real install in the user's library
(2026-08-02): UnityPlayer.dll sits directly in the game root alongside the game's own
.exe and a "<Name>_Data/" folder -- this has been Unity's standalone-build signature
since the engine's early versions and is reliable enough to check alone.
"""

from pathlib import Path

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin


class UnityEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.unity"
    display_name = "Unity"
    version = "1.0.0"
    author = "pythia"
    kind = "unity"

    def _signature_present(self, game_root: Path) -> bool:
        return (game_root / "UnityPlayer.dll").is_file()


plugin = register_builtin(UnityEnginePlugin())
