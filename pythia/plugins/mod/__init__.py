"""
Mod plugin registry -- a thin, category-filtered view over the central registry (see
pythia.plugins.registry). No built-in implementations ship this milestone (real mod
sources -- Nexus-style sites, F95 mod threads, a modder's own itch.io/Patreon --
haven't been inspected yet), but a downloader plugin that's *also* a ModPlugin
(2026-08-02: "a single plugin that can do downloads ... handle mods") would appear
here too, from the same instance registered in the downloader category.
"""

from pythia.plugins.registry import filter_enabled, for_category


def all_plugins() -> list:
    return for_category("mod")


def enabled_plugins() -> list:
    return filter_enabled(all_plugins())
