"""
Flash (packaged as an Adobe AIR captive-runtime app) engine plugin. Signature
verified against a real install in the user's library (2026-08-02): a META-INF/
folder plus a mimetype file directly in the game root -- the standard AIR
application-package signature, alongside a bundled "Adobe AIR" runtime folder.
Flash itself is dead upstream; Ruffle is the real remediation path, not yet wired up
here (needs its own inspection, same "don't fabricate" rule as everywhere else).
"""

from pathlib import Path

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin


class FlashAirEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.flash_air"
    display_name = "Flash (Adobe AIR package)"
    version = "1.0.0"
    author = "pythia"
    kind = "flash"
    non_windows_requirement = "Ruffle (not wired up yet) or Wine"

    def _signature_present(self, game_root: Path) -> bool:
        return (game_root / "META-INF").is_dir() and (game_root / "mimetype").is_file()


plugin = register_builtin(FlashAirEnginePlugin())
