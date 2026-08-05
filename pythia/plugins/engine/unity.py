"""
Unity engine plugin. Signature verified against real installs in the user's library
(2026-08-02): the player runtime sits alongside the game's own executable and a
"<Name>_Data/" folder -- this has been Unity's standalone-build signature since the
engine's early versions and is reliable enough to check alone.

Real bug fixed (2026-08-03): only `UnityPlayer.dll` (Windows) was checked, so a real
Linux export in this library (Erosphere-71.1-lin, which ships `UnityPlayer.so`
instead) silently fell through to "unidentified" -- confirmed by directly listing
that install's real files, not guessed. `.dylib` (macOS) is added on the same
well-established convention but hasn't been verified against a real Mac install in
this library.

A second, separate real bug fixed the same day: the check only ever looked in the
game root's own top level, but a real install in this library
(love_of_magic_book3_1_1_9f_win64) nests the actual player runtime three folders
deep (content/win64_build/book3/UnityPlayer.dll) -- an artifact of how it was
packaged, not an unusual Unity export shape. Now searches a few levels down, same
depth/reasoning as the generic library plugin's own `_os_looks_windows` heuristic
(plugins/library/generic.py), which already had to solve this exact "real
executable nested under a wrapper folder" problem.
"""

from pathlib import Path

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin

_PLAYER_FILENAMES = ("UnityPlayer.dll", "UnityPlayer.so", "UnityPlayer.dylib")


def _has_player_runtime(path: Path, max_depth: int = 3) -> bool:
    try:
        entries = list(path.iterdir())
    except OSError:
        return False
    if any(e.is_file() and e.name in _PLAYER_FILENAMES for e in entries):
        return True
    if max_depth <= 0:
        return False
    return any(e.is_dir() and _has_player_runtime(e, max_depth - 1) for e in entries)


class UnityEnginePlugin(DetectOnlyEnginePlugin):
    id = "pythia.engine.unity"
    display_name = "Unity"
    version = "1.0.0"
    author = "pythia"
    kind = "unity"

    def _signature_present(self, game_root: Path) -> bool:
        return _has_player_runtime(game_root)


plugin = register_builtin(UnityEnginePlugin())
