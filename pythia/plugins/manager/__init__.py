"""
Manager plugin registry -- a thin, category-filtered view over the central registry
(see pythia.plugins.registry). find_for_source() is the primitive that answers "which
launcher(s) can run this game" from a library plugin's `kind`, kept deliberately
separate from *identifying* the game's source (2026-08-02: a Steam-sourced game and
"the Steam client launches it" are different, independently pluggable facts -- see
LibraryPlugin/ManagerPlugin's docstrings for why). Multiple managers can legitimately
apply to the same source (a GOG game playable via GOG Galaxy or Lutris or Heroic).
"""

from pythia.plugins.manager.direct import plugin as direct_manager  # noqa: F401
from pythia.plugins.registry import filter_enabled, for_category

# Steam and Epic's manager capability now live on their single merged plugin
# classes (pythia/plugins/library/steam.py, pythia/plugins/library/egs.py --
# 2026-08-03, see steam.py's module docstring for why) -- for_category("manager")
# picks them up automatically via isinstance, same as every other multi-category
# plugin, no explicit import needed here.


def all_plugins() -> list:
    return for_category("manager")


def enabled_plugins() -> list:
    return filter_enabled(all_plugins())


def find_for_source(kind: str):
    """Every enabled manager plugin that can launch library-source `kind` -- either
    it lists that kind explicitly, or it declared itself source-agnostic ("*")."""
    return [p for p in enabled_plugins() if kind in p.compatible_sources or "*" in p.compatible_sources]


def find_for_game(entries: list) -> list:
    """(plugin, detection) candidates for every source kind this game_id's tracked
    entries carry a variant of -- factored out of cli.py's cmd_open() (2026-08-03)
    so the desktop/big-picture UIs can offer the same "hand off to a launcher" action
    without duplicating the detection-gathering logic.

    Also surfaces the direct-launch manager (2026-08-03) whenever any variant is
    flagged `direct` with an `executable` set -- this is independent of the
    source-`kind` matching above (see DirectLaunchManagerPlugin's docstring for
    why): a direct variant's source kind (if any) still describes where it came
    from for update/patch purposes, but doesn't imply a manager plugin exists
    that knows how to launch it."""
    detections_by_kind: dict = {}
    direct_executable = None
    for entry in entries:
        for group in entry.get("versions", []):
            for variant in group.get("variants", []):
                source = variant.get("source") or {}
                kind = source.get("kind")
                if kind:
                    detections_by_kind[kind] = {"kind": kind, "extra": source.get("extra", {})}
                if variant.get("direct") and variant.get("executable") and direct_executable is None:
                    direct_executable = variant["executable"]

    candidates = [
        (plugin, detections_by_kind[kind]) for kind in detections_by_kind for plugin in find_for_source(kind)
    ]
    if direct_executable is not None:
        candidates.append((direct_manager, {"kind": "direct", "extra": {"executable": direct_executable}}))
    return candidates
