"""
Patcher plugin registry -- a thin, category-filtered view over the central registry
(see pythia.plugins.registry). New category (2026-08-02): detects/manages file-level
patches (translation patches, other content patches) applied on top of an already-
identified game, tracked separately from mods/language. One real built-in ships this
round (bundled.py, grounded in MGQ Paradox's real "One-Click Patcher" folder) --
detection only, since where these patches come from and how they update hasn't been
researched yet.
"""

from pathlib import Path

from pythia.plugins.patcher.bundled import plugin as bundled_patcher_plugin  # noqa: F401
from pythia.plugins.registry import filter_enabled, for_category


def all_plugins() -> list:
    return for_category("patcher")


def enabled_plugins() -> list:
    return filter_enabled(all_plugins())


def identify_all(game_root: Path):
    """Every enabled patcher plugin whose detect() actually matches -- same
    per-plugin try/except isolation as library/engine's identify_all() (2026-08-03,
    defensive hardening after a real crash was found in the identical unguarded
    pattern -- see plugins/library/__init__.py's identify_all())."""
    results = []
    for plugin in enabled_plugins():
        try:
            result = plugin.detect(game_root)
        except Exception:
            continue
        if result is not None:
            results.append((plugin, result))
    return results
