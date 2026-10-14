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


class UnityEnginePlugin(_DetectOnlyEnginePlugin):
    id = "pythia.engine.unity"
    display_name = "Unity"
    version = "1.0.0"
    author = "pythia"
    kind = "unity"

    def _signature_present(self, game_root: Path) -> bool:
        return _has_player_runtime(game_root)


PLUGIN_CLASSES = [UnityEnginePlugin]
