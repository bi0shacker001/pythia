"""
Ren'Py engine plugin. Wraps renpy_patch/patch.py's existing, already-proven functions
unchanged for detect()/enable() -- no logic rewritten, since version/lib-prefix
detection and the ARM/x86_64 lib-add are already correct. platform_status() is new
composition logic (not a patch.py rewrite): it turns get_lib_naming_info()'s existing
per-platform folder check into the {supported, requires, fixable} shape every engine
plugin reports. Every gap Ren'Py has is fixable by construction -- closing exactly
that gap is the whole reason renpy_patch exists.
"""

import re
from pathlib import Path

from pythia.plugins import EnginePlugin
from pythia.plugins.registry import register_builtin
from pythia.renpy_patch import patch

# Every platform folder this plugin knows how to either confirm or add. Windows
# folders are never added by renpy_patch (see patch.py's module docstring -- purely
# additive to Linux support, existing Windows folders are left alone), so Windows
# is reported as supported-if-present but never fixable.
_KNOWN_PLATFORMS = {
    "linux-x86_64": {"kind": "sdk", "fixable": True},
    "linux-aarch64": {"kind": "sdkarm", "fixable": True},
    "windows-x86_64": {"kind": None, "fixable": False},
    "windows-i686": {"kind": None, "fixable": False},
}


def _installed_platforms(game_root: Path) -> list:
    """Every platform this specific install already has engine libs for, derived
    from whatever lib/<prefix><platform> folders actually exist on disk right now.
    Moved here from the library plugin (2026-08-03 root-cause fix, see
    plugins/library/renpy.py's module docstring) -- this is real engine-specific
    inspection (parsing Ren'Py's lib/ folder naming convention), not a distribution/
    storefront signal, so it belongs on the engine plugin, not the library one."""
    lib_dir = game_root / "lib"
    if not lib_dir.is_dir():
        return []
    platforms = []
    for entry in lib_dir.iterdir():
        if not entry.is_dir():
            continue
        m = re.match(r"^(?:py\d+-)?(windows|linux|mac)(?:-(.+))?$", entry.name)
        if not m:
            continue
        plat = m.group(1) if not m.group(2) else f"{m.group(1)}-{m.group(2)}"
        if plat not in platforms:
            platforms.append(plat)
    return platforms


class RenpyEnginePlugin(EnginePlugin):
    id = "pythia.engine.renpy"
    display_name = "Ren'Py"
    version = "1.0.0"
    author = "pythia"
    # Inherits refines_kinds = None (always tried, regardless of distribution kind --
    # 2026-08-03 root-cause fix). Was `["unknown"]` at first, then `[]`, each time
    # under a wrong assumption about which kinds a Ren'Py install could start as --
    # see EnginePlugin.refines_kinds's docstring and plugins/library/ (renpy.py
    # removed entirely) for the full history.

    def is_root(self, path: Path) -> bool:
        """Structural marker: a Ren'Py game root always has both a renpy/ and a
        game/ subfolder directly inside it (same rule patch.find_game_roots uses).
        Owned here, not by a library plugin (2026-08-03 root-cause fix) -- this is
        Ren'Py's own engine-level structural signature, nothing to do with how the
        game was distributed. Was previously duplicated on a separate
        RenpyLibraryPlugin, which has been removed entirely."""
        path = Path(path)
        return (path / "renpy").is_dir() and (path / "game").is_dir()

    def detect(self, game_root: Path) -> dict | None:
        """Identify a Ren'Py install: structural marker plus engine version,
        lib-naming convention, and installed platforms. Same detection renpy_patch
        already does, just exposed as an EnginePlugin."""
        game_root = Path(game_root)
        if not self.is_root(game_root):
            return None

        platforms = _installed_platforms(game_root)
        prefix, pyver, found = patch.get_lib_naming_info(game_root)
        if not found:
            return {"kind": "renpy", "version": None, "lib_prefix": None, "platforms": platforms}

        version = patch.get_renpy_engine_version(game_root, pyver)
        return {"kind": "renpy", "version": version, "lib_prefix": prefix, "platforms": platforms}

    def platform_status(self, game_root: Path, ctx) -> dict:
        """For each platform folder Ren'Py could ship, report whether it's already
        present (supported/native) and, if not, whether renpy_patch can add it."""
        game_root = Path(game_root)
        prefix, _pyver, found = patch.get_lib_naming_info(game_root)
        prefix = prefix or ""

        status = {}
        for platform, info in _KNOWN_PLATFORMS.items():
            folder_name = f"{prefix}{platform}"
            present = found and (game_root / "lib" / folder_name).is_dir()
            if present:
                status[platform] = {"supported": True, "native": True, "requires": [], "fixable": False}
            else:
                requires = [] if not info["fixable"] else ["renpy engine libs (auto-downloadable)"]
                status[platform] = {
                    "supported": False,
                    "native": False,
                    "requires": requires,
                    "fixable": info["fixable"] and found,
                }
        return status

    def enable(self, game_root: Path, target_platforms: list, ctx) -> dict:
        """Add whatever fixable platform gaps renpy_patch knows how to close.
        target_platforms is accepted for interface consistency but not selectively
        honored yet -- process_game_root() always attempts both linux-x86_64 and
        linux-aarch64 in one pass (see renpy_patch's ARCH_TARGETS), and this wraps
        that behavior unchanged rather than rewriting it to filter per-call."""
        game_root = Path(game_root)
        cache_root = ctx.cache_dir(self.id)
        resolve_cache = patch.ResolveCache(cache_root)
        summary = patch.process_game_root(game_root, game_root.name, cache_root, False, resolve_cache, ctx.log)
        return {"summary": summary}

    def capabilities(self, ctx) -> dict:
        """renpy_patch is stdlib-only with no separate install step -- it can always
        operate (network access permitting, which enable() itself already reports
        failures for). "installed" here means "this plugin is usable," not "Ren'Py
        itself is installed" -- there's no such thing as a system-wide Ren'Py runtime
        to check for."""
        return {"installed": True, "version": None}


plugin = register_builtin(RenpyEnginePlugin())
