"""
CatSystem2 engine plugin. Signature verified against a real install in the user's
library (2026-08-02): cs2conf.dll sits directly in the game root, alongside the
engine's .int archive files (bgm.int, image.int, kcs.int, ...). Windows-native engine
with no official Linux port.
"""

from pathlib import Path

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin


class CatSystem2EnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.catsystem2"
    display_name = "CatSystem2"
    version = "1.0.0"
    author = "pythia"
    kind = "catsystem2"

    def _signature_present(self, game_root: Path) -> bool:
        return (game_root / "cs2conf.dll").is_file()


plugin = register_builtin(CatSystem2EnginePlugin())
