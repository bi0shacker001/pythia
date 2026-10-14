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

One package, three classes (2026-08-07 correction: plugins aren't identified or
split apart by classname -- "we don't have to break things down too much when the
point is just to make it simple to run through stuff"). All three are enabled/
disabled together as this one plugin; engine detection just runs through every
class in PLUGIN_CLASSES and surfaces whichever one(s) actually match a given game
root -- more than one hit (or zero) is a normal, expected outcome, not an error.
"""

import stat
from pathlib import Path

from pythia.plugins import EnginePlugin


class _DetectOnlyEnginePlugin(EnginePlugin):
    """Self-contained per-plugin copy of the shared "detect from a real
    on-disk file signature, no remediation yet" shape (2026-08-07, user:
    "Plugins need to be completely self-contained. They cannot reference
    anything outside of themselves, besides stdlib and other basics" --
    every plugin needing this pattern carries its own copy rather than
    importing a shared pythia.plugins.engine._detect_only helper module
    that lives outside this plugin's own zip)."""

    kind = "base"
    non_windows_requirement = "Wine or Proton"

    def _signature_present(self, game_root: Path) -> bool:
        raise NotImplementedError

    def is_root(self, path: Path) -> bool:
        return self._signature_present(Path(path))

    def detect(self, game_root: Path):
        game_root = Path(game_root)
        if not game_root.is_dir() or not self._signature_present(game_root):
            return None
        return {"kind": self.kind}

    def platform_status(self, game_root: Path, ctx) -> dict:
        game_root = Path(game_root)
        try:
            files = [p for p in game_root.iterdir() if p.is_file()]
            has_windows_exe = any(p.suffix.lower() == ".exe" for p in files)
            has_linux_launcher = any(
                p.suffix.lower() == ".sh" or (p.suffix.lower() != ".exe" and bool(p.stat().st_mode & stat.S_IXUSR)) for p in files
            )
        except OSError:
            has_windows_exe = False
            has_linux_launcher = False
        return {
            "windows": {
                "supported": has_windows_exe,
                "native": has_windows_exe,
                "requires": [] if has_windows_exe else ["a Windows build"],
                "fixable": False,
            },
            "linux": {
                "supported": has_linux_launcher or has_windows_exe,
                "native": has_linux_launcher,
                "requires": [] if has_linux_launcher else ([self.non_windows_requirement] if has_windows_exe else ["a Linux or Windows build"]),
                "fixable": False,
            },
        }

    def enable(self, game_root: Path, target_platforms: list, ctx) -> dict:
        return {"summary": f"{self.display_name}: no automatic remediation available yet"}

    def capabilities(self, ctx) -> dict:
        return {"installed": True, "version": None}


def _has_core_script(game_root: Path, filename: str) -> bool:
    return (game_root / "js" / filename).is_file() or (game_root / "www" / "js" / filename).is_file()


class RpgMakerMvEnginePlugin(_DetectOnlyEnginePlugin):
    id = "pythia.engine.rpgmaker_mv"
    display_name = "RPG Maker MV"
    version = "1.0.0"
    author = "pythia"
    kind = "rpgmaker_mv"

    def _signature_present(self, game_root: Path) -> bool:
        return _has_core_script(game_root, "rpg_core.js")


class RpgMakerMzEnginePlugin(_DetectOnlyEnginePlugin):
    id = "pythia.engine.rpgmaker_mz"
    display_name = "RPG Maker MZ"
    version = "1.0.0"
    author = "pythia"
    kind = "rpgmaker_mz"

    def _signature_present(self, game_root: Path) -> bool:
        return _has_core_script(game_root, "rmmz_core.js")


class RpgMakerVxAceEnginePlugin(_DetectOnlyEnginePlugin):
    id = "pythia.engine.rpgmaker_vxace"
    display_name = "RPG Maker VX Ace"
    version = "1.0.0"
    author = "pythia"
    kind = "rpgmaker_vxace"

    def _signature_present(self, game_root: Path) -> bool:
        # Real bug fixed (2026-08-03): an exact ".rgss3a" suffix match missed
        # MGQ Paradox's own real install, where the bundled "One-Click Patcher"
        # (see plugins/patcher/bundled.py) renames the original archive to
        # "Game.rgss3a.old" as its own backup -- confirmed directly against
        # that real folder. Matching ".rgss3a" anywhere in the filename (not
        # just as the final suffix) catches this real-world renamed-backup
        # case too, not just a pristine unpatched install.
        try:
            return any(".rgss3a" in p.name.lower() for p in game_root.iterdir() if p.is_file())
        except OSError:
            return False


PLUGIN_CLASSES = [RpgMakerMvEnginePlugin, RpgMakerMzEnginePlugin, RpgMakerVxAceEnginePlugin]
