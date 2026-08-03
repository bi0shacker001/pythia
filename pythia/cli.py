"""
Pythia CLI: `python -m pythia <command> ...`

Commands:
  scan <path>      recursively discover and onboard every game root under <path>
  onboard <path>   identify and track a single game root, prompting interactively
                    if there's an ambiguous match against an already-tracked game
  list             show every game this system knows about (own + synced)
  status <path>    show engine platform support and detected patchers for one install
  enable <path>    run whatever engine plugin remediation is fixable for one install
  open <game_id>   hand this game off to a manager plugin (e.g. the Steam client)
  renpy-patch ...  the Ren'Py ARM/x86_64 lib patcher (see renpy_patch.patch.run_cli)
"""

import argparse
import sys
from pathlib import Path

from pythia import datadir, onboarding, scanning


def _print_result(result: dict):
    if result["status"] != "ok":
        print(f"  error: {result['message']}", file=sys.stderr)
        return
    print(f"  -> game_id {result['game_id']}  ({result['path']})")


def cmd_scan(args):
    print(f"Scanning '{args.path}' ...")
    results = scanning.scan_library(args.path)
    ok = [r for r in results if r["status"] == "ok"]
    errors = [r for r in results if r["status"] != "ok"]
    print(f"Found {len(results)} game install(s), {len(ok)} onboarded, {len(errors)} error(s).\n")
    for r in results:
        _print_result(r)
    return 0 if not errors else 1


def _prompt_decision(pv: dict):
    """Interactively ask the user how to resolve an ambiguous/no match. Returns a
    decision dict suitable for onboarding.onboard(), or None for "just use automatic"."""
    candidates = pv["candidates"]
    if candidates["certain"] is not None:
        # Unambiguous -- no need to ask.
        return None

    suggested = candidates["suggested"]
    if not suggested:
        return None  # nothing to link to; will be created as new automatically

    print(f"\n'{pv['path']}' looks like it might be: {pv['name']!r}")
    print("Possible existing matches on this system:")
    for i, entry in enumerate(suggested, start=1):
        print(f"  {i}. game_id {entry['game_id']}: {entry['canonical_name']!r}")
    print("  n. This is a new game (not one of the above)")

    choice = input("Which one is this? [1-{}/n, default n]: ".format(len(suggested))).strip().lower()
    if not choice or choice == "n":
        return {"action": "new"}

    try:
        idx = int(choice)
        if not (1 <= idx <= len(suggested)):
            raise ValueError
    except ValueError:
        print("  Not a valid choice, treating as new game.")
        return {"action": "new"}

    matched = suggested[idx - 1]
    import_choice = input("Import this into that game's existing library location? [y/N]: ").strip().lower()
    return {
        "action": "link",
        "game_id": matched["game_id"],
        "import_to_location": import_choice == "y",
    }


def cmd_onboard(args):
    pv = onboarding.preview(args.path)
    if pv["status"] != "ok":
        print(f"error: {pv['message']}", file=sys.stderr)
        return 1

    decision = None if args.non_interactive else _prompt_decision(pv)
    result = onboarding.onboard(args.path, decision=decision)
    _print_result(result)
    return 0 if result["status"] == "ok" else 1


def cmd_list(args):
    games = datadir.list_known_games()
    if not games:
        print("No games tracked yet. Use `scan` or `onboard` first.")
        return 0
    for game_id, entries in sorted(games.items()):
        name = entries[0].get("canonical_name", "?")
        print(f"[{game_id}] {name}")

        # ownership/platform_ids are additive per system entry (see the Milestone
        # 3-5 plan's schema v3 note) -- merge every system's view for display so
        # "owned on Steam and Epic" shows as one line, not split across systems.
        ownership = {o["platform"] for e in entries for o in e.get("ownership", [])}
        platform_ids = {
            (p["platform"], p.get("id")) for e in entries for p in e.get("platform_ids", [])
        }
        if ownership:
            print(f"    owned on: {', '.join(sorted(ownership))}")
        if platform_ids:
            ids_str = ", ".join(f"{plat}:{pid}" for plat, pid in sorted(platform_ids))
            print(f"    platform ids: {ids_str}")

        for entry in entries:
            for group in entry.get("versions", []):
                for variant in group.get("variants", []):
                    plats = ", ".join(variant.get("platforms", [])) or "?"
                    tags = ", ".join(m.get("name", "?") for m in variant.get("mods", []))
                    lang = variant.get("language")
                    suffix = "".join(f" [{t}]" for t in (tags, lang) if t)
                    print(
                        f"    {entry['system_uuid'][:8]}...  {group.get('version', '?')}{suffix}"
                        f"  ({plats})  {variant.get('path')}"
                    )
    return 0


def cmd_status(args):
    """Show what every enabled engine plugin thinks of `path` (identification,
    per-platform support/fixability) and what every enabled patcher plugin detects
    there -- the CLI surface for the plugin architecture's detect()/platform_status()/
    capabilities() and PatcherPlugin.detect(), which otherwise have no user-facing
    entry point at all."""
    from pythia.plugins import engine as engine_plugins
    from pythia.plugins import patcher as patcher_plugins
    from pythia.plugins.context import PluginContext

    path = Path(args.path).resolve()
    if not path.is_dir():
        print(f"error: '{path}' does not exist or is not a directory.", file=sys.stderr)
        return 1

    ctx = PluginContext(log=print)

    print(f"Engine status for '{path}':")
    found_engine = False
    for plugin in engine_plugins.enabled_plugins():
        detection = plugin.detect(path)
        if detection is None:
            continue
        found_engine = True
        print(f"  [{plugin.display_name}] detected: {detection}")
        print(f"    capabilities: {plugin.capabilities(ctx)}")
        for plat, info in plugin.platform_status(path, ctx).items():
            state = "ok" if info["supported"] else ("fixable" if info["fixable"] else "unsupported")
            requires = ", ".join(info.get("requires", [])) or "-"
            print(f"    {plat:14s} [{state:11s}] requires: {requires}")
    if not found_engine:
        print("  No engine plugin recognized this install.")

    print(f"\nPatchers for '{path}':")
    found_patch = False
    for plugin in patcher_plugins.enabled_plugins():
        detected = plugin.detect(path)
        if detected is None:
            continue
        found_patch = True
        print(f"  [{plugin.display_name}] detected: {detected}")
    if not found_patch:
        print("  None detected.")

    return 0


def cmd_enable(args):
    """Generalizes renpy-patch's fix-what's-fixable behavior to any engine plugin,
    not just Ren'Py -- finds whichever enabled engine plugin recognizes `path` and
    runs its enable() for every platform platform_status() marked fixable."""
    from pythia.plugins import engine as engine_plugins
    from pythia.plugins.context import PluginContext

    path = Path(args.path).resolve()
    if not path.is_dir():
        print(f"error: '{path}' does not exist or is not a directory.", file=sys.stderr)
        return 1

    ctx = PluginContext(log=print)
    matched = next((p for p in engine_plugins.enabled_plugins() if p.detect(path) is not None), None)
    if matched is None:
        print(f"error: no engine plugin recognizes '{path}'.", file=sys.stderr)
        return 1

    status = matched.platform_status(path, ctx)
    target_platforms = [plat for plat, info in status.items() if info.get("fixable")]
    if not target_platforms:
        print(f"[{matched.display_name}] nothing fixable for '{path}'.")
        return 0

    if args.dry_run:
        print(f"[{matched.display_name}] would attempt to enable: {', '.join(target_platforms)}")
        return 0

    result = matched.enable(path, target_platforms, ctx)
    print(f"[{matched.display_name}] {result}")
    return 0


def cmd_open(args):
    """Hand `game_id` off to a manager plugin capable of launching it (see
    ManagerPlugin) -- Pythia never launches the game itself, it opens a door
    belonging to whatever tool (Steam client, ...) actually runs it. Prompts for
    confirmation before doing so, since this launches an external application."""
    from pythia.plugins import manager as manager_plugins
    from pythia.plugins.context import PluginContext

    games = datadir.list_known_games()
    entries = games.get(args.game_id)
    if not entries:
        print(f"error: no tracked game with id {args.game_id}.", file=sys.stderr)
        return 1

    detections_by_kind = {}
    for entry in entries:
        for group in entry.get("versions", []):
            for variant in group.get("variants", []):
                source = variant.get("source") or {}
                kind = source.get("kind")
                if kind:
                    detections_by_kind[kind] = {"kind": kind, "extra": source.get("extra", {})}

    candidates = [
        (plugin, detections_by_kind[kind])
        for kind in detections_by_kind
        for plugin in manager_plugins.find_for_source(kind)
    ]
    if not candidates:
        seen = sorted(detections_by_kind) or ["none"]
        print(f"No manager plugin knows how to launch this game (sources seen: {', '.join(seen)}).")
        return 1

    manager_plugin, detection = candidates[0]
    print(f"This will hand off to {manager_plugin.display_name} to launch the game.")
    if not args.yes:
        answer = input("Proceed? [y/N]: ").strip().lower()
        if answer != "y":
            print("Cancelled.")
            return 0

    ctx = PluginContext(log=print)
    ok = manager_plugin.open(detection, ctx)
    print("Hand-off attempted." if ok else "Hand-off failed or not supported for this game.")
    return 0 if ok else 1


def build_parser():
    parser = argparse.ArgumentParser(prog="pythia")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Discover and track every game under a folder tree.")
    p_scan.add_argument("path")
    p_scan.set_defaults(func=cmd_scan)

    p_onboard = sub.add_parser("onboard", help="Identify and track a single game folder.")
    p_onboard.add_argument("path")
    p_onboard.add_argument(
        "--non-interactive", action="store_true", help="Never prompt; use the same automatic rules as `scan`."
    )
    p_onboard.set_defaults(func=cmd_onboard)

    p_list = sub.add_parser("list", help="List every game this system knows about.")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="Show engine platform support and detected patchers for one install.")
    p_status.add_argument("path")
    p_status.set_defaults(func=cmd_status)

    p_enable = sub.add_parser("enable", help="Run whatever engine plugin remediation is fixable for one install.")
    p_enable.add_argument("path")
    p_enable.add_argument("--dry-run", action="store_true", help="Report what would be fixed without changing anything.")
    p_enable.set_defaults(func=cmd_enable)

    p_open = sub.add_parser("open", help="Hand a tracked game off to a manager plugin (e.g. the Steam client).")
    p_open.add_argument("game_id", type=int)
    p_open.add_argument("--yes", action="store_true", help="Don't prompt for confirmation before handing off.")
    p_open.set_defaults(func=cmd_open)

    # renpy-patch is *not* registered as a normal subparser: it owns its own
    # argparse setup (patch.run_cli) with flags like --library/--game that clash
    # with how nested subparsers handle argparse.REMAINDER. run_cli() dispatches to
    # it directly, before argparse ever sees its arguments.
    sub.add_parser("renpy-patch", help="Add native Linux x86_64/ARM64 Ren'Py engine libs to game installs.")

    return parser


def run_cli(argv):
    if argv and argv[0] == "renpy-patch":
        from pythia.renpy_patch import patch

        return patch.run_cli(argv[1:])

    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
