"""Frozen-build entry point for Linux (2026-08-07). Mirrors bin/pythia's GUI
branch -- see packaging/windows/entry.py for why this is a thin wrapper
rather than reusing bin/pythia's own sys.path surgery directly."""

import sys


def main() -> None:
    from pythia.util.crash_handler import install_minimal_crash_handler

    install_minimal_crash_handler()

    from pythia.ui.app import main as qt_main

    qt_main()


if __name__ == "__main__":
    sys.exit(main())
