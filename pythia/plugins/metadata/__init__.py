"""
Metadata plugin registry -- a thin, category-filtered view over the central registry
(see pythia.plugins.registry). Independently enable-able from library/downloader
plugins for the same platform (2026-08-03: "metadata and downloads are handled
completely separately," extended to detection too -- see MetadataPlugin's
docstring).
"""

from pythia.plugins.registry import filter_enabled, for_category

# GOG and Steam's metadata capability live on their single merged plugin classes
# (pythia/plugins/library/gog.py, pythia/plugins/library/steam.py -- 2026-08-03,
# see steam.py's module docstring for why) -- for_category("metadata") picks them
# up automatically via isinstance, same as every other multi-category plugin.


def all_plugins() -> list:
    return for_category("metadata")


def enabled_plugins() -> list:
    return filter_enabled(all_plugins())


def find_for_platform(platform: str):
    """Every enabled metadata plugin that could plausibly serve `platform` -- for
    now this is a naive id-substring match (see downloader/engine registries for the
    same pattern); a plugin can be more precise by declaring its own id clearly."""
    return [p for p in enabled_plugins() if platform in p.id]
