"""
Source plugin registry -- a thin, category-filtered view over the central registry
(see pythia.plugins.registry). No built-in implementations ship this milestone (NAS/
Nextcloud/network-share integration needs testing against the user's actual setup
first -- see the Milestone 4 plan), but a third-party plugin that's *also* a
LibraryPlugin/DownloaderPlugin/ModPlugin (multi-category) would appear here too,
alongside every other category it implements, from one instance.
"""

from pythia.plugins.registry import filter_enabled, for_category


def all_plugins() -> list:
    return for_category("source")


def enabled_plugins() -> list:
    return filter_enabled(all_plugins())
