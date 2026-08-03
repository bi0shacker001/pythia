"""
RPG Maker MV/MZ/VX Ace engine plugins. Signatures verified against real installs in
the user's library (2026-08-02): MV/MZ ship as NW.js apps with the engine's core
script at js/rpg_core.js (MV) or js/rmmz_core.js (MZ), either directly or wrapped in a
www/ folder depending on export settings. VX Ace (the older, Ruby/RGSS-based,
Windows-only variant) was initially assumed absent from the library -- wrong; MGQ
Paradox's real files (Game.rgss3a) turned up on closer inspection after the user
flagged "other rpgmaker types" existing. No RPG Maker XP samples found, so that
variant still isn't implemented -- would need a real sample first, same rule as
everywhere else in this plugin set.
"""

from pathlib import Path

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin


def _has_core_script(game_root: Path, filename: str) -> bool:
    return (game_root / "js" / filename).is_file() or (game_root / "www" / "js" / filename).is_file()


class RpgMakerMvEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.rpgmaker_mv"
    display_name = "RPG Maker MV"
    version = "1.0.0"
    author = "pythia"
    kind = "rpgmaker_mv"

    def _signature_present(self, game_root: Path) -> bool:
        return _has_core_script(game_root, "rpg_core.js")


class RpgMakerMzEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.rpgmaker_mz"
    display_name = "RPG Maker MZ"
    version = "1.0.0"
    author = "pythia"
    kind = "rpgmaker_mz"

    def _signature_present(self, game_root: Path) -> bool:
        return _has_core_script(game_root, "rmmz_core.js")


class RpgMakerVxAceEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.rpgmaker_vxace"
    display_name = "RPG Maker VX Ace"
    version = "1.0.0"
    author = "pythia"
    kind = "rpgmaker_vxace"

    def _signature_present(self, game_root: Path) -> bool:
        try:
            return any(p.suffix.lower() == ".rgss3a" for p in game_root.iterdir() if p.is_file())
        except OSError:
            return False


mv_plugin = register_builtin(RpgMakerMvEnginePlugin())
mz_plugin = register_builtin(RpgMakerMzEnginePlugin())
vxace_plugin = register_builtin(RpgMakerVxAceEnginePlugin())
