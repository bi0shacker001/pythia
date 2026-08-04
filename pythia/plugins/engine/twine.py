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

from pythia.plugins.engine._detect_only import DetectOnlyEnginePlugin
from pythia.plugins.registry import register_builtin

_READ_WINDOW = 512 * 1024
_MARKERS = (b"tw-storydata", b"twinejs", b"SugarCube", b"Harlowe")


def _looks_like_twine_export(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(_READ_WINDOW)
    except OSError:
        return False
    return any(marker in chunk for marker in _MARKERS)


class TwineEnginePlugin(DetectOnlyEnginePlugin):
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


plugin = register_builtin(TwineEnginePlugin())
