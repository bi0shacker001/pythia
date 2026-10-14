"""Frozen-build entry point for Windows (2026-08-07). Mirrors bin/pythia's GUI
branch -- bin/pythia's own sys.path surgery (finding the source checkout,
stripping home-dir entries) is meaningless once PyInstaller has already frozen
everything into one bundle, so this is a thin, direct call instead of trying
to reuse that script as-is.

UI selection and debug logging come from pythia_config.load_local_config()
(a real config file next to this exe in the portable bundle, or next to
local.db in an installed build -- see pythia/util/local_config.py) --
2026-08-07: "We're gonna use a config file on windows... It controls debug,
ui selection, and etc. We won't give a console." No console is ever attached
(pythia.spec builds this with console=False); the config file and the log
file under app_data_dir()/logs/ are the only ways to control/observe this
build."""

import sys


def main() -> None:
    # Installed before anything else is imported (2026-08-07, external
    # review): pythia.ui.app pulls in pythia.util.log, which opens a log
    # file at import time and can fail before its own excepthook exists.
    # See pythia.util.crash_handler's own docstring.
    from pythia.util.crash_handler import install_minimal_crash_handler

    install_minimal_crash_handler()

    from pythia.ui.app import main as qt_main

    qt_main()


if __name__ == "__main__":
    main()
