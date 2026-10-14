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


def _discover_roots_shallow(scan_root: Path, plugins, generic):
    """Honor a configured non-recursive root: root/container/direct children only."""
    shortcut = _shortcut_roots(scan_root, plugins)
    if shortcut is not None:
        yield from shortcut
        return
    if any(plugin.is_root(scan_root) for plugin in plugins):
        yield scan_root
        return
    try:
        children = sorted(path for path in scan_root.iterdir() if path.is_dir())
    except OSError:
        return
    for child in children:
        shortcut = _shortcut_roots(child, plugins)
        if shortcut is not None:
            yield from shortcut
        elif any(plugin.is_root(child) for plugin in plugins):
            yield child
        elif generic is not None and generic.match(child) is not None:
            yield child


def scan_library(root: Path, *, recursive: bool = True):
    """Discover and onboard every game root under `root`. Returns the list of
    onboarding result dicts (see onboarding.onboard)."""
    root = Path(root).resolve()
    # Library AND engine plugins can both contribute structural boundaries (see
    # module docstring) -- is_root()/shortcut_roots() are the same shape on both
    # categories, so the walk itself doesn't need to know or care which category
    # found a given root.
    enabled_library = library.enabled_plugins()
    enabled = enabled_library + engine.enabled_plugins()
    # The generic catch-all step is handled separately below (it has no is_root()/
    # shortcut_roots() of its own -- see GenericLibraryPlugin), and only applies if
    # the user hasn't disabled it. Looked up by id, not a module-level name (2026-08-06:
    # the old `library.generic_plugin` attribute was a side effect of an eager
    # import-time `register_builtin()` call that no longer exists -- see registry.py's
    # real lazy-loader rewrite; every plugin is found through the registry now, not a
    # per-file module attribute).
    generic = next((p for p in enabled_library if p.id == "pythia.library.generic"), None)

    results = []
    discovered_roots = _discover_roots(root, enabled, generic) if recursive else _discover_roots_shallow(root, enabled, generic)
    seen = set()
    for discovered in discovered_roots:
        key = str(discovered.resolve())
        if key in seen:
            continue
        seen.add(key)
        results.append(onboarding.onboard(discovered))
    return results


def scan_configured_roots(progress=None):
    """Scan every enabled persisted root and report each real failure independently."""
    from pythia import library_roots

    roots = library_roots.list_roots()
    scanned = []
    games = []
    errors = []
    disabled = 0
    for root in roots:
        if not root["enabled"]:
            disabled += 1
            continue
        path = Path(root["path"])
        if not path.is_dir():
            errors.append({"id": root["id"], "path": str(path), "message": "Library root is offline or inaccessible."})
            if progress:
                progress(root, {"status": "error", "message": errors[-1]["message"]})
            continue
        try:
            results = scan_library(path, recursive=root["recursive"])
        except Exception as exc:
            errors.append({"id": root["id"], "path": str(path), "message": str(exc)})
            if progress:
                progress(root, {"status": "error", "message": str(exc)})
            continue
        scanned.append(root["id"])
        games.extend(results)
        if progress:
            progress(root, {"status": "ok", "games": len(results)})
    return {
        "status": "ok" if not errors else "partial",
        "roots": len(scanned),
        "disabled": disabled,
        "games": games,
        "game_count": len(games),
        "errors": errors,
    }
