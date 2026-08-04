import sys


def main():
    argv = sys.argv[1:]

    # `--relaunch` closes whatever UI instance is already running (via its PID
    # file, see pythia/ui/singleton.py) before starting this one -- strip it out
    # wherever it appears so it doesn't get mistaken for a CLI subcommand.
    relaunch = "--relaunch" in argv
    if relaunch:
        argv = [a for a in argv if a != "--relaunch"]

    # `--ui <mode>` alone (no real subcommand) still routes to the UI, just with an
    # explicit mode override instead of the auto-detected default.
    ui_hint = None
    if len(argv) == 2 and argv[0] == "--ui":
        ui_hint = argv[1]
        argv = []

    if not argv:
        from pythia.ui.mode import launch

        launch(hint=ui_hint, relaunch=relaunch)
        return 0

    from pythia.cli import run_cli

    return run_cli(argv)


if __name__ == "__main__":
    sys.exit(main())
