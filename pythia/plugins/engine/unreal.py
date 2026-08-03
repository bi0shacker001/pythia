"""
Unreal Engine plugin. Signature verified against a real install in the user's library
(2026-08-02): an Engine/Binaries/ folder sits directly in the game root alongside the
game's own top-level .exe -- the standard Unreal packaged-build layout.
"""

from pathlib import Path

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin


class UnrealEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.unreal"
    display_name = "Unreal Engine"
    version = "1.0.0"
    author = "pythia"
    kind = "unreal"

    def _signature_present(self, game_root: Path) -> bool:
        return (game_root / "Engine" / "Binaries").is_dir()


plugin = register_builtin(UnrealEnginePlugin())
