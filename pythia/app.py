"""Pythia's headless entry point (2026-08-04 GTK removal) -- real port of
pythia/gui/application.py's do_command_line()/do_handle_local_options(),
using argparse instead of GLib.OptionContext, and calling the exact same
underlying core functions (pythia.database.games, pythia.services,
pythia.installer, pythia.runners, ...) those handlers did.

What's NOT ported here: anything that fundamentally needs a window (showing
an installer dialog to resolve an ambiguous install, the bare "no action"
fallback that used to show the main window, --submit-issue's IssueReportWindow).
Those print a clear "not available without a UI" message instead of silently
no-opping or crashing -- the real Qt UI replaces gui/ entirely, this doesn't
try to fake it.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

from pythia import settings
from pythia.api import parse_installer_url
from pythia.database import games as games_db
from pythia.database.services import ServiceGameCollection
from pythia.delegates import CommandLineUIDelegate
from pythia.game import Game, export_game, import_game
from pythia.installer import get_installers
from pythia.migrations import migrate
from pythia.monitored_command import exec_command
from pythia.runners import InvalidRunnerError, RunnerInstallationError, get_runner_names, import_runner
from pythia.services import get_enabled_services
from pythia.startup import init_pythia, run_all_checks
from pythia.util import resources, system
from pythia.util.http import HTTPError, Request
from pythia.util.log import logger, set_debug_logging
from pythia.util.standalone_scripts import generate_script
from pythia.util.steam.appmanifest import AppManifest, get_appmanifests
from pythia.util.steam.config import get_steamapps_dirs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pythia", description="Pythia game library manager")
    parser.add_argument("-v", "--version", action="store_true", help="Print the version of Pythia and exit")
    parser.add_argument("-d", "--debug", action="store_true", help="Show debug messages")
    parser.add_argument("-i", "--install", metavar="FILE_OR_URL", help="Install a game from a yml file")
    parser.add_argument("-f", "--force", action="store_true", help="Force updates")
    parser.add_argument(
        "-b", "--output-script", metavar="GAME", help="Generate a bash script to run a game without the client"
    )
    parser.add_argument("-e", "--exec", metavar="COMMAND", help="Execute a program with the Pythia Runtime")
    parser.add_argument("-l", "--list-games", action="store_true", help="List all games in database")
    parser.add_argument("-o", "--installed", action="store_true", help="Only list installed games")
    parser.add_argument("-s", "--list-steam-games", action="store_true", help="List available Steam games")
    parser.add_argument("--list-steam-folders", action="store_true", help="List all known Steam library folders")
    parser.add_argument("--list-runners", action="store_true", help="List all known runners")
    parser.add_argument("--list-wine-versions", action="store_true", help="List all known Wine versions")
    parser.add_argument(
        "-a", "--list-all-service-games", action="store_true", help="List all games for all services in database"
    )
    parser.add_argument("--list-service-games", metavar="SERVICE", help="List all games for provided service in database")
    parser.add_argument("-r", "--install-runner", metavar="RUNNER", help="Install a Runner")
    parser.add_argument("-u", "--uninstall-runner", metavar="RUNNER", help="Uninstall a Runner")
    parser.add_argument("--export", metavar="SLUG", help="Export a game")
    parser.add_argument("--import", dest="import_", metavar="FILE", help="Import a game")
    parser.add_argument("--dest", metavar="PATH", help="Destination path for export/import")
    parser.add_argument("-j", "--json", action="store_true", help="Display the list of games in JSON format")
    parser.add_argument("--reinstall", action="store_true", help="Reinstall game")
    parser.add_argument(
        "--xdg-install",
        action="store_true",
        help="Register this install's .desktop file, icons, and MIME type under ~/.local/share "
        "(Linux only -- the self-integration step an AppImage needs since it isn't a system package)",
    )
    parser.add_argument(
        "uri",
        nargs="?",
        help="A pythia:rungame/<slug>, pythia:rungameid/<id>, or pythia:install/<slug> URI to act on",
    )
    return parser


def _print_game_list(game_list: list) -> None:
    for game in game_list:
        print(
            "{:4} | {:<40} | {:<40} | {:<15} | {:<64}".format(
                game["id"], game["name"][:40], game["slug"][:40], game["runner"] or "-", game["directory"] or "-"
            )
        )


def _print_game_json(game_list: list) -> None:
    games = []
    for game in game_list:
        playtime = timedelta(hours=game["playtime"]) if game["playtime"] else None
        cover_path = resources.get_cover_path(game["slug"]) if game["slug"] else None
        if cover_path and not os.path.exists(cover_path):
            cover_path = None
        games.append(
            {
                "id": game["id"],
                "slug": game["slug"],
                "name": game["name"],
                "runner": game["runner"],
                "platform": game["platform"] or None,
                "year": game["year"] or None,
                "directory": game["directory"] or None,
                "playtime": str(playtime) if playtime else None,
                "playtimeSeconds": playtime.total_seconds() if playtime else None,
                "lastplayed": (str(datetime.fromtimestamp(game["lastplayed"])) if game["lastplayed"] else None),
                "coverPath": cover_path,
            }
        )
    print(json.dumps(games, indent=2))


def _print_service_game_list(game_list: list) -> None:
    for game in game_list:
        print(
            "{:4} | {:<40} | {:<40} | {:<15} | {:<15}".format(
                game["id"],
                str(game["name"])[:40],
                str(game["slug"])[:40],
                str(game["service"])[:15],
                "true" if game["installed"] else "false",
            )
        )


def _print_service_game_json(game_list: list) -> None:
    games = [
        {
            "id": game["id"],
            "slug": game["slug"],
            "name": game["name"],
            "service": game["service"],
            "appid": game["appid"],
            "installed": game["installed"],
            "details": game["details"],
        }
        for game in game_list
    ]
    print(json.dumps(games, indent=2))


def _print_steam_list() -> None:
    for path in get_steamapps_dirs() or []:
        for appmanifest_file in get_appmanifests(path):
            appmanifest = AppManifest(os.path.join(path, appmanifest_file))
            print(" {:8} | {:<60} | {}".format(appmanifest.steamid, appmanifest.name or "-", ", ".join(appmanifest.states)))


def _print_runners() -> None:
    for name in sorted(get_runner_names(), key=str.lower):
        print(name)


def _install_runner(runner_name: str) -> None:
    from pythia.delegates import InstallUIDelegate  # noqa: PLC0415

    if runner_name.startswith("wine"):
        wine_dir = os.path.join(settings.RUNNER_DIR, "wine")
        runner_path = os.path.join(wine_dir, f"{runner_name}{'' if '-x86_64' in runner_name else '-x86_64'}")
        if os.path.isdir(runner_path):
            print(f"Wine version '{runner_name}' is already installed.")
            return
        try:
            runner = import_runner("wine")
            runner().install(InstallUIDelegate(), version=runner_name)
            print(f"Wine version '{runner_name}' has been installed.")
        except (InvalidRunnerError, RunnerInstallationError) as ex:
            print(ex.message)
        return

    try:
        runner = import_runner(runner_name)()
        if runner.is_installed():
            print(f"'{runner_name}' is already installed.")
        else:
            runner.install(InstallUIDelegate(), version=None, callback=None)
            print(f"'{runner_name}' has been installed")
    except (InvalidRunnerError, RunnerInstallationError) as ex:
        print(ex.message)


def _uninstall_runner(runner_name: str) -> None:
    if "wine" in runner_name:
        answer = input("Are you sure you want to delete Wine and all of the installed runners?[Y/N] ")
        if answer.lower() not in ("y", "yes"):
            print("Not removing Wine")
            return
        wine_dir = os.path.join(settings.RUNNER_DIR, "wine")
        version = f"{runner_name}{'' if '-x86_64' in runner_name else '-x86_64'}"
        runner_path = os.path.join(wine_dir, version)
        if os.path.isdir(runner_path):
            system.remove_folder(runner_path)
            print(f"Wine version '{version}' has been removed.")
        else:
            print(f"Specified version of Wine is not installed: {version}.")
        return

    try:
        runner_class = import_runner(runner_name)
        runner = runner_class()
    except InvalidRunnerError:
        logger.error("Failed to import Runner: %s", runner_name)
        return
    if not runner.is_installed():
        print(f"Runner '{runner_name}' is not installed.")
        return
    if runner.can_uninstall():
        runner.uninstall()
        print(f"'{runner_name}' has been uninstalled.")
    else:
        print(f"Runner '{runner_name}' cannot be uninstalled.")


def _execute_command(command: str) -> None:
    """Runs an arbitrary command in the Pythia runtime and blocks until it
    exits. Used to be GLib.MainLoop().run(); MonitoredCommand no longer needs
    a GLib loop at all (see monitored_command.py's own GTK-removal note), so
    this just polls until it's done."""
    logger.info("Running command '%s'", command)
    monitored_command = exec_command(command)
    try:
        while monitored_command.is_running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        monitored_command.stop()


def _install_excepthook() -> None:
    """See pythia.ui.app's own copy of this -- same reasoning, the headless
    CLI path deserves the same guarantee that an uncaught exception is
    actually logged somewhere durable, not just printed to a console that
    may not exist (e.g. invoked from a Windows shortcut with no console
    attached) (2026-08-07)."""

    def _hook(exc_type, exc_value, exc_tb):
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def run(argv: list[str] | None = None) -> int:
    _install_excepthook()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"pythia-{settings.VERSION}")
        return 0

    from pythia.util.local_config import load_local_config  # noqa: PLC0415

    if args.debug or load_local_config()["debug"]:
        set_debug_logging(True)

    if args.xdg_install:
        from pythia.util.xdg_install import xdg_install  # noqa: PLC0415

        return 0 if xdg_install(log=print) else 1

    logger.info("Starting Pythia %s", settings.VERSION)
    init_pythia()
    migrate()
    run_all_checks(async_ops=False)

    if args.output_script:
        game = (
            games_db.get_game_by_field(args.output_script, "id")
            or games_db.get_game_by_field(args.output_script, "slug")
            or games_db.get_game_by_field(args.output_script, "installer_slug")
        )
        if not game or not game["id"]:
            logger.warning("No valid game (%s) provided to generate the script. Use -l to find suitable games!", args.output_script)
            return 1
        generate_script(logger, CommandLineUIDelegate(None), game, f"{game['slug']}.sh")
        return 0

    if args.list_games:
        game_list = games_db.get_games(filters=({"installed": 1} if args.installed else None))
        (_print_game_json if args.json else _print_game_list)(game_list)
        return 0

    if args.list_service_games:
        service = args.list_service_games
        game_list = games_db.get_games(filters={"installed": 1, "service": service})
        service_game_list = ServiceGameCollection.get_for_service(service)
        for db_game in service_game_list:
            db_game["installed"] = any(("service_id", db_game["appid"]) in item.items() for item in game_list)
        if args.installed:
            service_game_list = [d for d in service_game_list if d["installed"]]
        (_print_service_game_json if args.json else _print_service_game_list)(service_game_list)
        return 0

    if args.list_all_service_games:
        game_list = games_db.get_games(filters={"installed": 1})
        service_game_list = ServiceGameCollection.get_service_games()
        for db_game in service_game_list:
            db_game["installed"] = any(
                ("service_id", db_game["appid"]) in item.items() for item in game_list if item["service"] == db_game["service"]
            )
        if args.installed:
            service_game_list = [d for d in service_game_list if d["installed"]]
        (_print_service_game_json if args.json else _print_service_game_list)(service_game_list)
        return 0

    if args.list_steam_games:
        _print_steam_list()
        return 0

    if args.list_steam_folders:
        for path in get_steamapps_dirs() or []:
            print(path)
        return 0

    if args.list_runners:
        _print_runners()
        return 0

    if args.list_wine_versions:
        from pythia.api import get_runners  # noqa: PLC0415

        for entry in get_runners("wine")["versions"]:
            if entry["version"]:
                print(entry)
        return 0

    if args.install_runner:
        _install_runner(args.install_runner)
        return 0

    if args.uninstall_runner:
        _uninstall_runner(args.uninstall_runner)
        return 0

    if args.export:
        if not args.dest:
            print("No destination dir given")
        else:
            export_game(args.export, args.dest)
        return 0

    if args.import_:
        if not args.dest:
            print("No destination dir given")
        else:
            import_game(args.import_, args.dest)
        return 0

    if args.exec:
        _execute_command(args.exec)
        return 0

    installer_info: dict = {
        "game_slug": None,
        "revision": None,
        "action": None,
        "service": None,
        "appid": None,
        "launch_config_name": None,
    }
    if args.uri:
        try:
            installer_info = parse_installer_url(args.uri)
        except ValueError:
            print(f"{args.uri!r} is not a valid URI")
            return 1

    game_slug = installer_info["game_slug"]
    action = installer_info["action"]
    service = installer_info["service"]
    appid = installer_info["appid"]
    launch_config_name = installer_info["launch_config_name"]
    revision = installer_info["revision"]

    launch_ui_delegate = CommandLineUIDelegate(launch_config_name)

    installer_file = args.install
    if installer_file:
        if installer_file.startswith(("http:", "https:")):
            try:
                request = Request(installer_file).get()
            except HTTPError:
                print(f"Failed to download {installer_file}")
                return 1
            try:
                headers = dict(request.response_headers or {})
                file_name = headers["Content-Disposition"].split("=", 1)[-1]
            except (KeyError, IndexError):
                file_name = os.path.basename(installer_file)
            file_path = os.path.join(tempfile.gettempdir(), file_name)
            print(f"download {installer_file} to {file_path} started")
            with open(file_path, "wb") as dest_file:
                dest_file.write(request.content)
            installer_file = file_path
        else:
            if not os.path.isabs(installer_file):
                installer_file = os.path.join(os.getcwd(), installer_file)
            installer_file = os.path.abspath(installer_file)
        if not os.path.isfile(installer_file):
            print(f"No such file: {installer_file}")
            return 1
        action = "install"

    db_game = None
    if game_slug and not service:
        if action == "rungameid":
            db_game = games_db.get_game_by_field(game_slug, "id")
        elif action == "rungame":
            db_game = games_db.get_game_by_field(game_slug, "slug")
        elif action == "install":
            db_game = games_db.get_game_by_field(game_slug, "slug") or games_db.get_game_by_field(
                game_slug, "installer_slug"
            )
        else:
            db_game = (
                games_db.get_game_by_field(game_slug, "id")
                or games_db.get_game_by_field(game_slug, "slug")
                or games_db.get_game_by_field(game_slug, "installer_slug")
            )

    if args.reinstall:
        action = "install"

    if not action:
        if db_game and db_game["installed"]:
            print(f"'{db_game['name']}' is already installed. Use --reinstall to reinstall it, or pass a URI to run it.")
            return 0
        if game_slug or installer_file or service:
            action = "install"
        else:
            print("No action specified. Run with --help to see available commands.")
            return 0

    if service and appid:
        service_game = ServiceGameCollection.get_game(service, appid)
        if service_game:
            get_enabled_services()[service]().install(service_game)
            return 0

    if action == "install":
        installers = get_installers(game_slug=game_slug, installer_file=installer_file, revision=revision)
        if not installers:
            print("No installers found.")
            return 1
        if len(installers) > 1:
            print(
                f"{len(installers)} installers found for {game_slug!r}; picking installers interactively needs the "
                "UI, which isn't built yet. Pass a specific installer file with --install instead."
            )
            return 1
        try:
            from pythia.installer.interpreter import ScriptInterpreter  # noqa: PLC0415
        except ImportError as ex:
            print(f"Can't run installers yet: {ex}. ScriptInterpreter still subclasses GObject.Object (real, structural -- not yet ported off GTK/GObject).")
            return 1

        ScriptInterpreter(installers[0], launch_ui_delegate).launch_install(launch_ui_delegate)
        return 0

    if action in ("rungame", "rungameid"):
        if not db_game or not db_game["id"]:
            logger.warning("No game found in library")
            return 0

        def on_error(error: BaseException) -> None:
            logger.exception("Unable to launch game: %s", error)

        game = Game(str(db_game["id"]))
        game.game_error.register(on_error)
        game.launch(launch_ui_delegate)
        while game.state != game.STATE_STOPPED:
            time.sleep(0.5)
        return 0

    print("Nothing to do without a UI (this action needs the desktop interface, which isn't built yet).")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
