"""
Twine/HTML engine plugin (2026-08-03) -- closes a real, confirmed gap: 4 real
games in the user's own `adult/html/` folder (College Daze, Inheritance_B8,
LoveAndCorruption, Routes of Life) never matched any existing engine plugin,
because no HTML-engine plugin existed at all until now.

Signature verified directly against those real files: each ships as a single
large, self-contained .html file directly in the game root (Twine's standard
"compiled story" export -- story data, story format/engine JS, and often
base64-embedded media all inlined into one file), containing the literal
`<tw-storydata` element Twine's compiler always emits, alongside real
SugarCube/Harlowe story-format markers. Only reads the first 512KB of a
candidate file, not the whole thing -- real files in this library are
5-30MB+, but the marker was found consistently around the ~216KB mark in
both samples checked, well within that bound.

Needs only a modern browser to run -- no engine runtime to install/patch, so
this plugin is detection-only (matches the plan's "HTML/Twine-style" note in
the Milestone 4 reference list).
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

_READ_WINDOW = 512 * 1024
_MARKERS = (b"tw-storydata", b"twinejs", b"SugarCube", b"Harlowe")


def _looks_like_twine_export(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(_READ_WINDOW)
    except OSError:
        return False
    return any(marker in chunk for marker in _MARKERS)


class TwineEnginePlugin(_DetectOnlyEnginePlugin):
    id = "pythia.engine.twine"
    display_name = "Twine/HTML"
    version = "1.0.0"
    author = "pythia"
    kind = "twine"

    def _signature_present(self, game_root: Path) -> bool:
        try:
            html_files = [p for p in game_root.iterdir() if p.is_file() and p.suffix.lower() == ".html"]
        except OSError:
            return False
        return any(_looks_like_twine_export(p) for p in html_files)

    def platform_status(self, game_root: Path, ctx) -> dict:
        available = self._signature_present(Path(game_root))
        row = {"supported": available, "native": available,
               "requires": [] if available else ["a Twine HTML export"], "fixable": False}
        return {"windows": dict(row), "linux": dict(row)}


PLUGIN_CLASSES = [TwineEnginePlugin]
