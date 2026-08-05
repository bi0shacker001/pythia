"""
Shared base for engine plugins that can *detect* an engine from its real on-disk file
signature (2026-08-02: signatures below were verified against the user's actual
library files, not guessed) but don't yet have any real remediation to offer. Most of
these are Windows-native JP visual novel engines with no Linux port; Pythia doesn't
manage Wine prefixes yet, so platform_status() honestly reports what's missing
without claiming to be able to fix it -- same "report the gap, don't fabricate a fix"
spirit as everywhere else in the plugin architecture.
"""

from pathlib import Path

from pythia.plugins import EnginePlugin


class DetectOnlyEnginePlugin(EnginePlugin):
    # Inherits refines_kinds = None (always tried) -- a kirikiri/Unity/etc. game can
    # equally be sold on Steam and still needs to be identified as that engine
    # (2026-08-03 root-cause fix, see EnginePlugin.refines_kinds's docstring).
    kind = "base"
    #: shown in platform_status()'s "requires" list for every non-Windows platform.
    non_windows_requirement = "Wine (not managed by Pythia yet)"

    def _signature_present(self, game_root: Path) -> bool:
        raise NotImplementedError

    def detect(self, game_root: Path):
        game_root = Path(game_root)
        if not game_root.is_dir() or not self._signature_present(game_root):
            return None
        return {"kind": self.kind}

    def platform_status(self, game_root: Path, ctx) -> dict:
        game_root = Path(game_root)
        try:
            has_windows_exe = any(p.suffix.lower() == ".exe" for p in game_root.iterdir() if p.is_file())
        except OSError:
            has_windows_exe = False
        return {
            "windows": {
                "supported": has_windows_exe,
                "native": has_windows_exe,
                "requires": [] if has_windows_exe else ["a Windows build"],
                "fixable": False,
            },
            "linux": {
                "supported": False,
                "native": False,
                "requires": [self.non_windows_requirement],
                "fixable": False,
            },
        }

    def enable(self, game_root: Path, target_platforms: list, ctx) -> dict:
        return {"summary": f"{self.display_name}: no automatic remediation available yet"}

    def capabilities(self, ctx) -> dict:
        """Pure file-signature detection has no external dependency of its own --
        "installed" here means "this plugin is usable," same reasoning as the Ren'Py
        engine plugin's capabilities()."""
        return {"installed": True, "version": None}
