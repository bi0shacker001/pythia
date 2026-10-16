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
from pathlib import Path

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin

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


class GodotEnginePlugin(DetectOnlyEnginePlugin):
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
        return any(p.suffix.lower() in _EXECUTABLE_SUFFIXES and _has_embedded_pck_trailer(p) for p in candidates)


plugin = register_builtin(GodotEnginePlugin())
