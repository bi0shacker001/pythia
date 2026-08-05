"""
Manager plugin registry -- a thin, category-filtered view over the central registry
(see pythia.plugins.registry). find_for_source() is the primitive that answers "which
launcher(s) can run this game" from a library plugin's `kind`, kept deliberately
separate from *identifying* the game's source (2026-08-02: a Steam-sourced game and
"the Steam client launches it" are different, independently pluggable facts -- see
LibraryPlugin/ManagerPlugin's docstrings for why). Multiple managers can legitimately
apply to the same source (a GOG game playable via GOG Galaxy or Lutris or Heroic).
"""

from pythia.plugins.registry import filter_enabled, for_category

# 2026-08-04, Lutris-base port: the old direct-launch manager plugin (a variant
# flagged `direct` with no runner at all) isn't ported -- Lutris's own "linux"
# runner already covers "just run this executable directly, no compatibility
# layer needed" (see pythia/runners/linux.py), so there's no reason to keep a
# second, narrower mechanism for the same thing.

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
    without duplicating the detection-gathering logic."""
    detections_by_kind: dict = {}
    for entry in entries:
        for group in entry.get("versions", []):
            for variant in group.get("variants", []):
                source = variant.get("source") or {}
                kind = source.get("kind")
                if kind:
                    detections_by_kind[kind] = {"kind": kind, "extra": source.get("extra", {})}

    return [(plugin, detections_by_kind[kind]) for kind in detections_by_kind for plugin in find_for_source(kind)]
