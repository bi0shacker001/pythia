"""
Engine plugin registry -- a thin, category-filtered view over the central registry
(see pythia.plugins.registry). onboarding.py always consults this after a library
plugin's structural match(), regardless of what (if anything) the library plugin
found -- engine identity and distribution identity are independent facts (2026-08-03
root-cause fix), so a Steam-distributed Ren'Py game still needs Ren'Py's engine
plugin to run, not just a plain "unknown" install. Plugins that declare an explicit
`refines_kinds` list restrict themselves to those starting kinds; the default (None)
means no restriction, always tried.
"""

from pathlib import Path

from pythia.plugins.engine.august import plugin as august_engine  # noqa: F401
from pythia.plugins.engine.buriko import plugin as buriko_engine  # noqa: F401
from pythia.plugins.engine.catsystem2 import plugin as catsystem2_engine  # noqa: F401
from pythia.plugins.engine.cmvs import plugin as cmvs_engine  # noqa: F401
from pythia.plugins.engine.flash_air import plugin as flash_air_engine  # noqa: F401
from pythia.plugins.engine.godot import plugin as godot_engine  # noqa: F401
from pythia.plugins.engine.kirikiri import plugin as kirikiri_engine  # noqa: F401
from pythia.plugins.engine.renpy import plugin as renpy_engine  # noqa: F401
from pythia.plugins.engine.rpgmaker import mv_plugin as rpgmaker_mv_engine  # noqa: F401
from pythia.plugins.engine.rpgmaker import mz_plugin as rpgmaker_mz_engine  # noqa: F401
from pythia.plugins.engine.rpgmaker import vxace_plugin as rpgmaker_vxace_engine  # noqa: F401
from pythia.plugins.engine.twine import plugin as twine_engine  # noqa: F401
from pythia.plugins.engine.unity import plugin as unity_engine  # noqa: F401
from pythia.plugins.engine.unreal import plugin as unreal_engine  # noqa: F401
from pythia.plugins.registry import filter_enabled, for_category


def all_plugins() -> list:
    return for_category("engine")


def enabled_plugins() -> list:
    return filter_enabled(all_plugins())


def identify_all(game_root: Path, current_kind: str | None = None):
    """Every enabled plugin willing to try against `current_kind` whose detect()
    actually matches -- multiple matches are normal, not an error. A plugin with
    refines_kinds=None (the default) is always willing. Each plugin's detect() is
    isolated in its own try/except (2026-08-03, defensive hardening after finding
    the identical unguarded-loop pattern actually crashed every onboard/scan call
    once one library plugin didn't override match() -- see
    plugins/library/__init__.py's identify_all() for the real incident this
    mirrors): a broken third-party engine plugin must never take every other
    plugin's identification down with it."""
    results = []
    for plugin in enabled_plugins():
        if plugin.refines_kinds is not None and current_kind not in plugin.refines_kinds:
            continue
        try:
            result = plugin.detect(game_root)
        except Exception:
            continue
        if result is not None:
            results.append((plugin, result))
    return results


def identify(game_root: Path, current_kind: str | None = None):
    """Single-winner convenience over identify_all(): first enabled, willing,
    matching plugin in registry order. Returns (plugin, detection_dict) or
    (None, None)."""
    matches = identify_all(game_root, current_kind)
    if not matches:
        return None, None
    return matches[0]


def find_for_kind(kind: str):
    """Every enabled engine plugin that could plausibly apply to a game already
    identified as `kind` -- either it has no restriction, it explicitly declares
    willingness to try that kind, or it's the plugin whose own id/detect naturally
    produced that kind in the first place (e.g. the Ren'Py engine plugin for kind
    "renpy")."""
    return [p for p in enabled_plugins() if p.refines_kinds is None or kind in p.refines_kinds or kind in p.id]
