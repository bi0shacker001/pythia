"""
Manager plugin registry -- a thin, category-filtered view over the central registry
(see pythia.plugins.registry). find_for_source() is the primitive that answers "which
launcher(s) can run this game" from a library plugin's `kind`, kept deliberately
separate from *identifying* the game's source (2026-08-02: a Steam-sourced game and
"the Steam client launches it" are different, independently pluggable facts -- see
LibraryPlugin/ManagerPlugin's docstrings for why). Multiple managers can legitimately
apply to the same source (a GOG game playable via GOG Galaxy or Lutris or Heroic).
"""

from pythia.plugins.manager.steam_client import plugin as steam_client_manager  # noqa: F401
from pythia.plugins.registry import filter_enabled, for_category


def all_plugins() -> list:
    return for_category("manager")


def enabled_plugins() -> list:
    return filter_enabled(all_plugins())


def find_for_source(kind: str):
    """Every enabled manager plugin that can launch library-source `kind` -- either
    it lists that kind explicitly, or it declared itself source-agnostic ("*")."""
    return [p for p in enabled_plugins() if kind in p.compatible_sources or "*" in p.compatible_sources]
