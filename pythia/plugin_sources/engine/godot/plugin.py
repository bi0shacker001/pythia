"""
Godot engine plugin. Two real signatures, both filesystem/binary-only (never
launches anything -- see pythia/plugins/__init__.py's module docstring on why
reading binary content for detection is fair game):

1. A loose .pck file directly in the game root -- Godot's own packed-data
   container format.
2. An embedded-pck export (2026-08-03, real gap closed): Godot can also embed
   the .pck directly inside the executable, which the loose-file check above
   can't see. Verified directly against a real install in the user's library
   that exports this way (Goodbye Eternity's Windows .exe) -- the file's
   literal last 4 bytes are the ASCII magic b"GDPC", preceded by an 8-byte
   little-endian offset into the file where the embedded pack actually starts.
   Checking this only ever reads the last 4 bytes of a candidate file (a
   single seek+read, not a full-file scan), so it stays cheap even against the
   multi-gigabyte executables real Godot exports produce.
"""

import struct
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

_EMBEDDED_PCK_MAGIC = b"GDPC"
_EXECUTABLE_SUFFIXES = {".exe", ".x86_64", ".x86", ""}


def _has_embedded_pck_trailer(path: Path) -> bool:
    try:
        size = path.stat().st_size
        if size < 12:
            return False
        with path.open("rb") as f:
            f.seek(size - 4)
            if f.read(4) != _EMBEDDED_PCK_MAGIC:
                return False
            f.seek(size - 12)
            (offset,) = struct.unpack("<Q", f.read(8))
        return 0 < offset < size
    except OSError:
        return False


class GodotEnginePlugin(_DetectOnlyEnginePlugin):
    id = "pythia.engine.godot"
    display_name = "Godot"
    version = "1.0.0"
    author = "pythia"
    kind = "godot"

    def _signature_present(self, game_root: Path) -> bool:
        try:
            candidates = [p for p in game_root.iterdir() if p.is_file()]
        except OSError:
            return False
        if any(p.suffix.lower() == ".pck" for p in candidates):
            return True
        return any(
            p.suffix.lower() in _EXECUTABLE_SUFFIXES and _has_embedded_pck_trailer(p)
            for p in candidates
        )


PLUGIN_CLASSES = [GodotEnginePlugin]
