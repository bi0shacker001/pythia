"""Pythia's entry point: the `pythia` command of an installed package, and
what bin/pythia runs once it has set up the search path (source tree, or the
meson and Debian layouts).

A bare `pythia`, or `--ui <desktop|bigpicture|web>` picking a front end (see
pythia.ui.mode), starts the Qt UI when PySide6 is installed; any other flag
(list, install, export, exec, the droidtop commands...) is the headless CLI,
so scripting never needs a display.
"""

import gettext
import locale
import multiprocessing
import os
import sys


def _set_up_locale() -> None:
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error as ex:
        sys.stderr.write("Unsupported locale setting: %s\n" % ex)

    try:
        # optional_settings exists only in a meson build
        from pythia import optional_settings  # type: ignore[attr-defined]  # noqa: PLC0415
    except ImportError:
        return
    try:
        locale.bindtextdomain("pythia", optional_settings.LOCALE_DIR)
        gettext.bindtextdomain("pythia", optional_settings.LOCALE_DIR)
        locale.textdomain("pythia")
        gettext.textdomain("pythia")
    except Exception as ex:  # noqa: BLE001 -- translations are optional; say why they are off
        sys.stderr.write(
            "Couldn't bind gettext domain, translations won't work.\n"
            "LOCALE_DIR: %s\nError: %s\n" % (optional_settings.LOCALE_DIR, ex)
        )


def main() -> None:
    # Python 3.14 makes 'forkserver' the default; Pythia's subprocess handling
    # expects fork. Windows has no fork at all.
    if hasattr(os, "fork"):
        multiprocessing.set_start_method("fork", force=True)
    _set_up_locale()

    if len(sys.argv) == 1 or "--ui" in sys.argv:
        try:
            from pythia.ui.app import main as qt_main  # noqa: PLC0415
        except ImportError as ex:
            sys.stderr.write(f"Qt UI unavailable ({ex}), falling back to the headless CLI.\n")
        else:
            qt_main()
            return

    from pythia.app import main as cli_main  # noqa: PLC0415

    cli_main()


if __name__ == "__main__":
    main()
