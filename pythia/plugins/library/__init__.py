"""
Library plugin registry -- a thin, category-filtered view over the central registry
in pythia.plugins.registry (built-in + third-party, single instantiation, multi-
category plugins supported -- see that module's docstring). Multiple plugins matching
the same path is normal (e.g. two different Steam library plugins); identify_all() is
the real primitive, identify() is a single-winner convenience for callers that just
want "the" detection (onboarding.py's automatic/non-interactive path).

No Ren'Py library plugin exists here (2026-08-03 root-cause fix, removed): Ren'Py is
an engine, not a distribution source, so it never belonged in this category -- see
plugins/engine/renpy.py, which now owns both its own structural is_root() and its
engine identification, with no library-side duplicate.
"""

from pathlib import Path

from pythia.plugins.library.generic import plugin as generic_plugin
from pythia.plugins.library.itch import plugin as itch_plugin
from pythia.plugins.library.steam import plugin as steam_plugin
from pythia.plugins.registry import filter_enabled, for_category

# Built-in priority order, used only to pick a single winner in identify() when more
# than one plugin matches the same path. Generic is the unconditional catch-all, so
# it must always rank last.
_DEFAULT_PRIORITY = [steam_plugin.id, itch_plugin.id, generic_plugin.id]


def all_plugins() -> list:
    """Every discovered library plugin, built-in + external, regardless of enabled
    state."""
    return for_category("library")


def enabled_plugins() -> list:
    return filter_enabled(all_plugins())


def identify_all(path: Path):
    """Every enabled plugin that matches `path` -- multiple matches are normal, not
    an error (e.g. two Steam library plugins both recognizing the same install)."""
    results = []
    for plugin in enabled_plugins():
        result = plugin.match(path)
        if result is not None:
            results.append((plugin, result))
    return results


def identify(path: Path):
    """Single-winner convenience over identify_all(): ranks by built-in priority
    order, with any third-party plugin (unranked) sorting after every built-in one
    and generic always sorting last of all."""
    matches = identify_all(path)
    if not matches:
        return None

    def rank(item):
        plugin, _ = item
        try:
            return _DEFAULT_PRIORITY.index(plugin.id)
        except ValueError:
            return len(_DEFAULT_PRIORITY)

    matches.sort(key=rank)
    return matches[0][1]
