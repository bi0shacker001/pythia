"""
CMVS engine plugin (2026-08-03) -- a real, distinct Japanese VN engine (Purple
Software/Light.vn's own "CMVS" runtime), discovered while investigating why
ChronoClock (previously tracked in the user's own hand-organized `adult/unknown/`
folder) never matched any existing engine plugin. Signature verified directly
against that real install: `cmvs32.exe`/`cmvs64.exe` (the engine's own
architecture-specific launchers) and `cmvs.cfg` sit directly in the game root --
distinct from Kirikiri/BGI/CatSystem2, which this user's library also has real
examples of. Windows-native, no known Linux port.
"""

from pathlib import Path

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin

_MARKER_NAMES = {"cmvs32.exe", "cmvs64.exe", "cmvs.cfg"}


class CmvsEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.cmvs"
    display_name = "CMVS"
    version = "1.0.0"
    author = "pythia"
    kind = "cmvs"

    def _signature_present(self, game_root: Path) -> bool:
        try:
            return any(p.name.lower() in _MARKER_NAMES for p in game_root.iterdir() if p.is_file())
        except OSError:
            return False


plugin = register_builtin(CmvsEnginePlugin())
