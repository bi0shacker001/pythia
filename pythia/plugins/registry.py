"""
Central plugin registry. Every plugin -- built-in or third-party, single-category or
multi-category -- is discovered and instantiated exactly *once* here; each category's
own module (pythia.plugins.library, .engine, .downloader, .source, .mod) is a thin
filtered view over this single list, split by isinstance(). This is what makes a
multi-category plugin (2026-08-02: "a single plugin that can do downloads, manage
library sources, handle mods") work correctly: `class MyPlugin(DownloaderPlugin,
ModPlugin)` gets built once and appears in both the downloader and mod registries
from that one instance, not two independently-constructed ones -- important if the
plugin holds shared state (an authenticated session, a connection to one service).

Built-in plugins self-register via register_builtin() at import time, right next to
their own class definition, instead of a big central list that drifts out of sync as
plugins are added. Third-party plugins declare a single `importlib.metadata` entry
point under the "pythia.plugins" group -- one entry point per plugin however many
categories it spans, not one per category.
"""

import json

from pythia.plugins import (
    DownloaderPlugin,
    EnginePlugin,
    LibraryPlugin,
    ManagerPlugin,
    MetadataPlugin,
    ModPlugin,
    PatcherPlugin,
    SourcePlugin,
)

ENTRY_POINT_GROUP = "pythia.plugins"

CATEGORY_CLASSES = {
    "library": LibraryPlugin,
    "manager": ManagerPlugin,
    "engine": EnginePlugin,
    "downloader": DownloaderPlugin,
    "metadata": MetadataPlugin,
    "source": SourcePlugin,
    "mod": ModPlugin,
    "patcher": PatcherPlugin,
}

_builtin_instances = []
_external_cache = None


def register_builtin(instance):
    """Called once per built-in plugin instance, at the bottom of the module that
    defines it (see e.g. plugins/library/renpy.py). Returns the instance unchanged
    so it can be assigned to a module-level name in the same line."""
    _builtin_instances.append(instance)
    return instance


def categories_of(plugin) -> list:
    """Every category this plugin instance actually implements -- a multi-category
    plugin appears in more than one."""
    return [name for name, cls in CATEGORY_CLASSES.items() if isinstance(plugin, cls)]


def discover_external(log=print) -> list:
    """Instantiate every third-party plugin registered under the single
    "pythia.plugins" entry-point group. A single broken/incompatible plugin is
    logged and skipped, never allowed to take the whole registry down -- this is the
    actual point of dynamic discovery: the app must keep working when someone's
    third-party plugin doesn't. Cached after the first successful call in a process
    (entry points don't change without a restart)."""
    global _external_cache
    if _external_cache is not None:
        return _external_cache

    from importlib.metadata import entry_points

    found = []
    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except Exception as e:
        log(f"plugin discovery: could not query entry points for {ENTRY_POINT_GROUP!r}: {e}")
        return found

    for ep in eps:
        try:
            cls = ep.load()
            found.append(cls())
        except Exception as e:
            log(f"plugin discovery: failed to load plugin {ep.name!r}: {e}")

    _external_cache = found
    return found


def all_plugins() -> list:
    """Every discovered plugin instance, built-in + external, instantiated exactly
    once regardless of how many categories it spans."""
    return list(_builtin_instances) + discover_external()


def for_category(category: str) -> list:
    """Every discovered plugin (any enabled state) that implements `category`'s
    interface -- one instance can appear under several categories."""
    cls = CATEGORY_CLASSES[category]
    return [p for p in all_plugins() if isinstance(p, cls)]


def get_enabled_plugin_ids() -> list | None:
    """The `enabled_plugins` allowlist from settings, or None if unset/empty
    (meaning "no restriction -- everything is enabled"). Enablement is per plugin id,
    not per category -- a multi-category plugin is on or off as a whole."""
    from pythia.identity import get_setting

    raw = get_setting("enabled_plugins", None)
    if not raw:
        return None
    try:
        ids = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return ids or None


def set_enabled_plugin_ids(ids: list) -> None:
    """Set the allowlist. Pass an empty list to clear it back to "everything
    enabled" -- the debug-mode reset path."""
    from pythia.identity import set_setting

    set_setting("enabled_plugins", json.dumps(list(ids)))


def filter_enabled(plugins: list) -> list:
    """Apply the enabled_plugins allowlist to a list of plugin instances. No
    allowlist set -> every plugin passed in is enabled."""
    allowed = get_enabled_plugin_ids()
    if allowed is None:
        return list(plugins)
    allowed = set(allowed)
    return [p for p in plugins if p.id in allowed]
