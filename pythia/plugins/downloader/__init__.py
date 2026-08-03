"""
Downloader plugin registry -- a thin, category-filtered view over the central
registry (see pythia.plugins.registry). find_for_kind() is the primitive that lets
the app answer "which downloaders work with this game" using each plugin's declared
compatible_library_kinds, instead of hardcoding per-plugin knowledge anywhere else --
this is what makes "multiple downloaders may work with multiple library sources"
(2026-08-02) an ordinary case instead of a special one.
"""

from pythia.plugins.downloader.f95checker import plugin as f95checker_plugin  # noqa: F401
from pythia.plugins.downloader.steamcmd import plugin as steamcmd_plugin  # noqa: F401
from pythia.plugins.registry import filter_enabled, for_category


def all_plugins() -> list:
    return for_category("downloader")


def enabled_plugins() -> list:
    return filter_enabled(all_plugins())


def find_for_kind(kind: str):
    """Every enabled downloader plugin compatible with library kind `kind` -- either
    it lists that kind explicitly, or it declared itself library-agnostic ("*")."""
    return [p for p in enabled_plugins() if kind in p.compatible_library_kinds or "*" in p.compatible_library_kinds]
