"""
Source plugin registry -- a thin, category-filtered view over the central registry
(see pythia.plugins.registry). First real built-in (2026-08-03): a local/mounted-
path source (see plugins/source/local_path.py) -- a real protocol-level NAS client
(SMB/WebDAV/Nextcloud) is still real, deferred future work needing the user's
actual setup confirmed first. A third-party plugin that's *also* a
LibraryPlugin/DownloaderPlugin/ModPlugin (multi-category) would appear here too,
alongside every other category it implements, from one instance.
"""

from pythia.plugins.registry import filter_enabled, for_category
from pythia.plugins.source.local_path import plugin as local_path_source  # noqa: F401


def all_plugins() -> list:
    return for_category("source")


def enabled_plugins() -> list:
    return filter_enabled(all_plugins())
