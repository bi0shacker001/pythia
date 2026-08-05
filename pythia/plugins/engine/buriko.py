"""
Buriko General Interpreter (BGI/Ethornell) engine plugin. Signature verified against
a real install in the user's library (2026-08-02): BGI.gdb and BGI.hvl sit directly
in the game root -- distinctive, engine-specific filenames not shared with anything
else. Windows-native engine with no official Linux port.
"""

from pathlib import Path

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin


class BurikoEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.buriko"
    display_name = "Buriko General Interpreter (BGI/Ethornell)"
    version = "1.0.0"
    author = "pythia"
    kind = "buriko"

    def _signature_present(self, game_root: Path) -> bool:
        return (game_root / "BGI.gdb").is_file() or (game_root / "BGI.hvl").is_file()


plugin = register_builtin(BurikoEnginePlugin())
