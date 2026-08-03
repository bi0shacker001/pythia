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
  duplicates       list this system's tracked games that look like the same game
  merge <a> <b>    fold game_id b into game_id a (b is deleted)
  renpy-patch ...  the Ren'Py ARM/x86_64 lib patcher (see renpy_patch.patch.run_cli)
"""

import argparse
import sys
from pathlib import Path

from pythia import datadir, onboarding, reconciliation, scanning


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
    engine_matches = engine_plugins.identify_all(path)
    for plugin, detection in engine_matches:
        print(f"  [{plugin.display_name}] detected: {detection}")
        print(f"    capabilities: {plugin.capabilities(ctx)}")
        for plat, info in plugin.platform_status(path, ctx).items():
            state = "ok" if info["supported"] else ("fixable" if info["fixable"] else "unsupported")
            requires = ", ".join(info.get("requires", [])) or "-"
            print(f"    {plat:14s} [{state:11s}] requires: {requires}")
    if not engine_matches:
        print("  No engine plugin recognized this install.")

    print(f"\nPatchers for '{path}':")
    patcher_matches = patcher_plugins.identify_all(path)
    for plugin, detected in patcher_matches:
        print(f"  [{plugin.display_name}] detected: {detected}")
    if not patcher_matches:
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
    matches = engine_plugins.identify_all(path)
    if not matches:
        print(f"error: no engine plugin recognizes '{path}'.", file=sys.stderr)
        return 1
    matched, _detection = matches[0]

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

    candidates = manager_plugins.find_for_game(entries)
    if not candidates:
        print("No manager plugin knows how to launch this game.")
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


def cmd_duplicates(args):
    """List this system's own tracked games that look like the same game -- a
    suggestion queue, never auto-applied (see reconciliation.find_merge_candidates)."""
    candidates = reconciliation.find_merge_candidates()
    if not candidates:
        print("No likely duplicates found.")
        return 0
    for c in candidates:
        a, b = c["a"], c["b"]
        print(f"  {c['score']:.2f}  [{a['game_id']}] {a['name']!r} ({a['engine_kind']}, {a['version_count']} version(s))")
        print(f"          <-> [{b['game_id']}] {b['name']!r} ({b['engine_kind']}, {b['version_count']} version(s))")
        print(f"          a: {a['path']}")
        print(f"          b: {b['path']}")
    print(f"\n{len(candidates)} candidate pair(s). Use `pythia merge <a> <b>` to fold b into a.")
    return 0


def cmd_merge(args):
    """Fold game_id `retired` into game_id `surviving` (see reconciliation.merge)."""
    result = reconciliation.merge(args.surviving, args.retired)
    if result["status"] != "ok":
        print(f"error: {result['message']}", file=sys.stderr)
        return 1
    print(f"  -> game_id {result['retired_game_id']} merged into {result['game_id']}")
    return 0


def cmd_itch_connect(args):
    """Store an itch.io API key locally (never synced) for real account-level
    ownership pulls -- see pythia/plugins/library/itch.py's module docstring for
    where this flow came from (ported from Lutris's actual itchio.py service)."""
    from pythia.plugins.library import itch as itch_plugin_module

    itch_plugin_module.set_api_key(args.api_key)
    print("itch.io API key saved. Run `pythia itch-sync` to pull your owned library.")
    return 0


def cmd_itch_sync(args):
    """Pull the user's real itch.io ownership (via the API key set with
    `itch-connect`) and fold it into this system's tracked games."""
    from pythia.plugins.library.itch import plugin as itch_plugin

    result = onboarding.sync_owned(itch_plugin)
    if result["status"] != "ok":
        print(f"error: {result['message']}", file=sys.stderr)
        return 1
    print(f"  -> {result['linked']} game(s) linked, {result['created']} new game(s) recorded (ownership only).")
    return 0


def cmd_steam_connect(args):
    """Store a Steam Web API key locally (never synced, never Lutris's default key)
    for real account-level ownership pulls -- get one at
    https://steamcommunity.com/dev/apikey."""
    from pythia.plugins.library import steam as steam_plugin_module

    steam_plugin_module.set_api_key(args.api_key)
    if args.steamid64:
        steam_plugin_module.set_steamid64(args.steamid64)
    print("Steam API key saved. Run `pythia steam-sync` to pull your owned library.")
    return 0


def cmd_steam_sync(args):
    """Pull the user's real Steam ownership (via the API key set with
    `steam-connect`, and the most-recently-used local Steam account unless overridden)
    and fold it into this system's tracked games."""
    from pythia.plugins.library.steam import plugin as steam_plugin

    result = onboarding.sync_owned(steam_plugin)
    if result["status"] != "ok":
        print(f"error: {result['message']}", file=sys.stderr)
        return 1
    print(f"  -> {result['linked']} game(s) linked, {result['created']} new game(s) recorded (ownership only).")
    return 0


def cmd_gog_connect(args):
    """Step 1 of GOG's OAuth login: print the URL to open in a real browser (GOG has
    no self-serve API-key page like Steam/itch.io -- see plugins/library/gog.py)."""
    from pythia.plugins.library.gog import login_url

    print("Open this URL in your browser, log in, then copy the final redirected URL")
    print("(it may show a blank/error page -- the address bar is what matters):\n")
    print(f"  {login_url()}\n")
    print("Then run: pythia gog-token \"<pasted url>\"")
    return 0


def cmd_gog_token(args):
    """Step 2: exchange the pasted redirect URL's authorization code for a token,
    storing the (synced) refresh token."""
    from pythia.plugins.library.gog import connect_from_redirect_url

    if not connect_from_redirect_url(args.redirected_url):
        print("error: no authorization code found in that URL.", file=sys.stderr)
        return 1
    print("GOG connected. Run `pythia gog-sync` to pull your owned library.")
    return 0


def cmd_gog_sync(args):
    """Pull the user's real GOG ownership (product IDs only -- see
    plugins/library/gog.py for why names aren't resolved here) and fold it into this
    system's tracked games."""
    from pythia.plugins.library.gog import plugin as gog_plugin

    result = onboarding.sync_owned(gog_plugin)
    if result["status"] != "ok":
        print(f"error: {result['message']}", file=sys.stderr)
        return 1
    print(f"  -> {result['linked']} game(s) linked, {result['created']} new game(s) recorded (ownership only).")
    return 0


def cmd_epic_connect(args):
    """Step 1 of Epic's OAuth login: print the URL to open in a real browser."""
    from pythia.plugins.library.egs import login_url

    print("Open this URL in your browser and log in. Epic's redirect page shows raw")
    print("JSON text (not a normal page) -- copy that JSON text, then run:\n")
    print(f"  {login_url()}\n")
    print('  pythia epic-token \'<pasted JSON>\'')
    return 0


def cmd_epic_token(args):
    """Step 2: exchange the pasted JSON page content's authorizationCode for a
    token, storing the (synced) refresh token."""
    from pythia.plugins.library.egs import connect_from_redirect_json

    if not connect_from_redirect_json(args.pasted_json):
        print("error: no authorizationCode found in that JSON.", file=sys.stderr)
        return 1
    print("Epic Games Store connected. Run `pythia epic-sync` to pull your owned library.")
    return 0


def cmd_epic_sync(args):
    """Pull the user's real Epic Games Store ownership and fold it into this
    system's tracked games."""
    from pythia.plugins.library.egs import plugin as egs_plugin

    result = onboarding.sync_owned(egs_plugin)
    if result["status"] != "ok":
        print(f"error: {result['message']}", file=sys.stderr)
        return 1
    print(f"  -> {result['linked']} game(s) linked, {result['created']} new game(s) recorded (ownership only).")
    return 0


def cmd_f95_import(args):
    """One-shot import from a real F95Checker db.sqlite3 -- NOT an ongoing account
    sync (see plugins/library/f95.py's module docstring for why): datadir is
    canonical after this, safe to re-run (only ever links/adds, never overwrites),
    but there's no persistent "connection" the way Steam/GOG/Epic have."""
    from pythia.plugins.library import f95 as f95_module
    from pythia.plugins.library.f95 import plugin as f95_plugin

    if args.db_path:
        f95_module.set_db_path(args.db_path)

    result = onboarding.sync_owned(f95_plugin)
    if result["status"] != "ok":
        print(f"error: {result['message']}", file=sys.stderr)
        return 1
    print(f"  -> {result['linked']} game(s) linked, {result['created']} new game(s) recorded (watchlist import).")
    return 0


def cmd_f95_export(args):
    """Write a real, loadable F95Checker db.sqlite3 from Pythia's own tracked f95
    entries -- deliberately minimal-fidelity, see plugins/library/f95.py."""
    from pythia.plugins.library.f95 import export_to_sqlite

    count = export_to_sqlite(args.path)
    print(f"  -> wrote {count} game(s) to '{args.path}'")
    return 0


def cmd_f95_rpc_serve(args):
    """Run the F95Checker-browser-extension-compatible RPC server in the
    foreground (Ctrl+C to stop) -- see pythia/f95_rpc.py for the real protocol."""
    import time

    from pythia import f95_rpc

    f95_rpc.start()
    print(f"F95 RPC server listening on http://127.0.0.1:{f95_rpc.RPC_PORT} (the official")
    print("F95Checker browser extension can point at this directly). Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        f95_rpc.stop()
    return 0


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

    p_duplicates = sub.add_parser("duplicates", help="List tracked games that look like the same game.")
    p_duplicates.set_defaults(func=cmd_duplicates)

    p_merge = sub.add_parser("merge", help="Fold game_id b into game_id a (b is deleted).")
    p_merge.add_argument("surviving", type=int)
    p_merge.add_argument("retired", type=int)
    p_merge.set_defaults(func=cmd_merge)

    p_itch_connect = sub.add_parser("itch-connect", help="Save an itch.io API key locally for ownership sync.")
    p_itch_connect.add_argument("api_key")
    p_itch_connect.set_defaults(func=cmd_itch_connect)

    p_itch_sync = sub.add_parser("itch-sync", help="Pull real itch.io ownership via the saved API key.")
    p_itch_sync.set_defaults(func=cmd_itch_sync)

    p_steam_connect = sub.add_parser("steam-connect", help="Save a Steam Web API key locally for ownership sync.")
    p_steam_connect.add_argument("api_key")
    p_steam_connect.add_argument("--steamid64", help="Override the local-login-history guess of which account to query.")
    p_steam_connect.set_defaults(func=cmd_steam_connect)

    p_steam_sync = sub.add_parser("steam-sync", help="Pull real Steam ownership via the saved API key.")
    p_steam_sync.set_defaults(func=cmd_steam_sync)

    p_gog_connect = sub.add_parser("gog-connect", help="Step 1: print the GOG OAuth login URL.")
    p_gog_connect.set_defaults(func=cmd_gog_connect)

    p_gog_token = sub.add_parser("gog-token", help="Step 2: exchange the pasted redirect URL for a token.")
    p_gog_token.add_argument("redirected_url")
    p_gog_token.set_defaults(func=cmd_gog_token)

    p_gog_sync = sub.add_parser("gog-sync", help="Pull real GOG ownership via the connected account.")
    p_gog_sync.set_defaults(func=cmd_gog_sync)

    p_epic_connect = sub.add_parser("epic-connect", help="Step 1: print the Epic OAuth login URL.")
    p_epic_connect.set_defaults(func=cmd_epic_connect)

    p_epic_token = sub.add_parser("epic-token", help="Step 2: exchange the pasted redirect JSON for a token.")
    p_epic_token.add_argument("pasted_json")
    p_epic_token.set_defaults(func=cmd_epic_token)

    p_epic_sync = sub.add_parser("epic-sync", help="Pull real Epic Games Store ownership via the connected account.")
    p_epic_sync.set_defaults(func=cmd_epic_sync)

    p_f95_import = sub.add_parser(
        "f95-import", help="One-shot import from a real F95Checker db.sqlite3 (not an ongoing sync)."
    )
    p_f95_import.add_argument("--db-path", help="Override the default F95Checker db.sqlite3 location.")
    p_f95_import.set_defaults(func=cmd_f95_import)

    p_f95_export = sub.add_parser("f95-export", help="Write a real F95Checker-compatible db.sqlite3.")
    p_f95_export.add_argument("path")
    p_f95_export.set_defaults(func=cmd_f95_export)

    p_f95_rpc = sub.add_parser(
        "f95-rpc-serve", help="Run the F95Checker browser-extension-compatible RPC server."
    )
    p_f95_rpc.set_defaults(func=cmd_f95_rpc_serve)

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
