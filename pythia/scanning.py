"""
scan_library(root) -- walk a folder tree and discover every candidate game root,
across every *enabled* library AND engine plugin (not a hardcoded list -- see the
Milestone 4 plan for why plugins are dynamically discovered/enable-able), calling
onboarding.onboard() on each one found.

Structural boundary detection (is_root()/shortcut_roots()) isn't library-plugin-only
(2026-08-03 root-cause fix): an engine can have its own structural marker independent
of any distribution source (Ren'Py's renpy/+game/ folders say nothing about whether
the game came from Steam, F95, or nowhere at all -- see plugins/engine/renpy.py's
is_root()), so this walk consults both categories' plugins uniformly, not just
library ones. See the Milestone 1 plan's "Detection + scanning" section for the
algorithm: a structural-marker shortcut (e.g. Steam's steamapps/common/), each
plugin's own is_root() rule, and a generic fallback restricted to direct children of
the scan root (so the generic plugin doesn't fire on arbitrary deeply-nested folders
in trees it doesn't understand).
"""

from pathlib import Path

from pythia import onboarding
from pythia.plugins import engine, library
from pythia.util.log import logger


def _shortcut_roots(path: Path, plugins):
    """The first plugin that recognizes `path` as one of its own "container, don't
    recurse into it further" shapes wins (see LibraryPlugin.shortcut_roots).
    Returns a list[Path] (even if empty) or None if no plugin recognizes it."""
    for plugin in plugins:
        roots = plugin.shortcut_roots(path)
        if roots is not None:
            return roots
    return None


def _marker_roots(path: Path, plugins):
    """Recursively find every structural root any enabled library plugin recognizes,
    not descending further once one is found (mirrors renpy_patch.find_game_roots,
    generalized to any plugin's is_root()/shortcut_roots())."""
    shortcut = _shortcut_roots(path, plugins)
    if shortcut is not None:
        yield from shortcut
        return

    for plugin in plugins:
        if plugin.is_root(path):
            yield path
            return

    try:
        children = sorted(p for p in path.iterdir() if p.is_dir())
    except OSError:
        children = []
    for child in children:
        yield from _marker_roots(child, plugins)


def _generic_roots(path: Path, generic):
    """Apply the generic plugin to `path`, but first descend through any pure
    organizational wrapper folders (see GenericLibraryPlugin.looks_like_pure_container
    -- e.g. Manual/Extracted/ containing unrelated games Flashpoint and yansim,
    neither of which is itself a wrapper) rather than onboarding the wrapper as one
    bogus "game". A real install with no files at its own top level but real engine-
    structure subfolders (bin/, logs/, ...) is never treated as a wrapper -- see that
    method's docstring for why both signals are required."""
    if generic.looks_like_pure_container(path):
        try:
            children = sorted(p for p in path.iterdir() if p.is_dir())
        except OSError:
            children = []
        for child in children:
            yield from _generic_roots(child, generic)
        return

    if generic.match(path) is not None:
        yield path


def _discover_roots(scan_root: Path, plugins, generic):
    """Full discovery: marker-based roots anywhere in the tree, plus a generic
    fallback -- but *only* for direct children of `scan_root` that had no marker-
    based root anywhere inside them. See the plan's Detection + scanning section for
    why generic is restricted this way."""
    if _shortcut_roots(scan_root, plugins) is not None or any(p.is_root(scan_root) for p in plugins):
        yield from _marker_roots(scan_root, plugins)
        return

    try:
        children = sorted(p for p in scan_root.iterdir() if p.is_dir())
    except OSError:
        children = []

    for child in children:
        found_any = False
        for marker_root in _marker_roots(child, plugins):
            found_any = True
            yield marker_root
        if not found_any and generic is not None:
            yield from _generic_roots(child, generic)


def scan_library(root: Path | str):
    """Discover and onboard every game root under `root`. Returns the list of
    onboarding result dicts (see onboarding.onboard)."""
    root = Path(root).resolve()
    # Library AND engine plugins can both contribute structural boundaries (see
    # module docstring) -- is_root()/shortcut_roots() are the same shape on both
    # categories, so the walk itself doesn't need to know or care which category
    # found a given root.
    enabled = library.enabled_plugins() + engine.enabled_plugins()
    # The generic catch-all step is handled separately below (it has no is_root()/
    # shortcut_roots() of its own -- see GenericLibraryPlugin), and only applies if
    # the user hasn't disabled it.
    generic = library.generic_plugin if library.generic_plugin in library.enabled_plugins() else None

    roms = _rom_folders(root)
    results = []
    for discovered in _discover_roots(root, enabled, generic):
        rom = roms.get(discovered)
        if rom is not None:
            results.append(
                {
                    "status": "skipped",
                    "game_path": str(discovered),
                    "message": f"{discovered.name} is a ROM folder ({', '.join(rom['systems'])}: "
                    f"{rom['roms']} ROM{'' if rom['roms'] == 1 else 's'}), "
                    "not a PC game; the droidtop agent lists its ROMs.",
                }
            )
            continue
        results.append(onboarding.onboard(discovered))
    return results


def _rom_folders(root: Path) -> dict[Path, dict]:
    """The ROM system folders under `root`, by droidtop-agent's scanner rules
    (pythia.droidtop.scan): Pythia's own walk would track `psx/` or `3ds/` as
    a game. Without the agent module, none are known and a warning says so."""
    from pythia import droidtop  # noqa: PLC0415
    from pythia.droidtop import AgentError  # noqa: PLC0415

    if not droidtop.available():
        logger.warning("ROM folders are scanned as games: %s", droidtop.why_unavailable())
        return {}
    from pythia.droidtop import scan  # noqa: PLC0415

    try:
        return scan.rom_folders(root)
    except AgentError as ex:
        logger.warning("ROM folders are scanned as games: the droidtop agent's scanner failed: %s", ex)
        return {}
