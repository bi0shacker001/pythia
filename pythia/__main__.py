import sys


def main():
    argv = sys.argv[1:]

    # `--ui <mode>` alone (no real subcommand) still routes to the UI, just with an
    # explicit mode override instead of the auto-detected default.
    ui_hint = None
    if len(argv) == 2 and argv[0] == "--ui":
        ui_hint = argv[1]
        argv = []

    if not argv:
        from pythia.ui.mode import launch

        launch(hint=ui_hint)
        return 0

    from pythia.cli import run_cli

    return run_cli(argv)


if __name__ == "__main__":
    sys.exit(main())
