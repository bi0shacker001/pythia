"""
Kirikiri/KAG3 engine plugin. Signature verified against a real install in the user's
library (2026-08-02): kirikiri games ship their data as .xp3 archives (the engine's
own container format) directly in the game root -- e.g. data.xp3, bgm.xp3, main.xp3.
Windows-native engine with no official Linux port.
"""

from pathlib import Path

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin


class KirikiriEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.kirikiri"
    display_name = "Kirikiri/KAG3"
    version = "1.0.0"
    author = "pythia"
    kind = "kirikiri"

    def _signature_present(self, game_root: Path) -> bool:
        try:
            return any(p.suffix.lower() == ".xp3" for p in game_root.iterdir() if p.is_file())
        except OSError:
            return False


plugin = register_builtin(KirikiriEnginePlugin())
