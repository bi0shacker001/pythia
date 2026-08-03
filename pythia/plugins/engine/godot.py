"""
Godot engine plugin. Best-effort signature: a .pck file directly in the game root --
Godot's own packed-data container format. Known real limitation, not chased further
(2026-08-02): Godot also supports embedding the .pck directly inside the executable,
which this can't detect from the filesystem alone (confirmed against a real install
in the user's library that exports this way) -- an embedded-pck Godot game falls
through to "unknown" rather than misidentifying as something else, which is the
correct failure mode for a best-effort detector.
"""

from pathlib import Path

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin


class GodotEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.godot"
    display_name = "Godot"
    version = "1.0.0"
    author = "pythia"
    kind = "godot"

    def _signature_present(self, game_root: Path) -> bool:
        try:
            return any(p.suffix.lower() == ".pck" for p in game_root.iterdir() if p.is_file())
        except OSError:
            return False


plugin = register_builtin(GodotEnginePlugin())
