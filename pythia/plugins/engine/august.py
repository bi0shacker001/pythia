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

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin

_MARKER_PREFIX = "aug"
_MIN_MARKER_DIRS = 2  # more than one AUG* dir, to avoid a false positive on a coincidental single folder name


class AugustEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.august"
    display_name = "AUGUST engine"
    version = "1.0.0"
    author = "pythia"
    kind = "august"

    def _signature_present(self, game_root: Path) -> bool:
        try:
            marker_dirs = [p for p in game_root.iterdir() if p.is_dir() and p.name.lower().startswith(_MARKER_PREFIX)]
        except OSError:
            return False
        return len(marker_dirs) >= _MIN_MARKER_DIRS


plugin = register_builtin(AugustEnginePlugin())
