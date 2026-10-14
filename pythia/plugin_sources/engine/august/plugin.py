"""
AUGUST engine plugin (2026-08-03) -- a real, distinct Japanese VN engine used by
the developer AUGUST (Eiyuu Senki's actual publisher), discovered while
investigating why that title never matched any existing engine plugin despite
being hand-organized into the user's own `adult/unknown/` folder. Signature
verified directly against that real install: a consistent set of top-level
directories all prefixed "AUG" (AUGEvent/AUGGame/AUGSysGame/AUGSysMain), a real,
distinctive convention specific to this engine -- not shared with any other
engine already covered (Kirikiri, CMVS, BGI, CatSystem2). Windows-native.
"""

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
    #: shown in platform_status()'s "requires" list for every non-Windows platform.
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
                "supported": has_windows_exe,
                "native": False,
                "requires": [self.non_windows_requirement] if has_windows_exe else ["a Windows build"],
                "fixable": False,
            },
        }

    def enable(self, game_root: Path, target_platforms: list, ctx) -> dict:
        return {"summary": f"{self.display_name}: no automatic remediation available yet"}

    def capabilities(self, ctx) -> dict:
        return {"installed": True, "version": None}

_MARKER_PREFIX = "aug"
_MIN_MARKER_DIRS = 2  # more than one AUG* dir, to avoid a false positive on a coincidental single folder name


class AugustEnginePlugin(_DetectOnlyEnginePlugin):
    id = "pythia.engine.august"
    display_name = "AUGUST engine"
    version = "1.0.0"
    author = "pythia"
    kind = "august"

    def _signature_present(self, game_root: Path) -> bool:
        try:
            marker_dirs = [
                p for p in game_root.iterdir() if p.is_dir() and p.name.lower().startswith(_MARKER_PREFIX)
            ]
        except OSError:
            return False
        return len(marker_dirs) >= _MIN_MARKER_DIRS


PLUGIN_CLASSES = [AugustEnginePlugin]
