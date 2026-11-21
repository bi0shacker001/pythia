"""Pythia's entry point: the `pythia` command of an installed package, and
what bin/pythia runs once it has set up the search path (source tree, or the
meson and Debian layouts).

A bare `pythia`, `--ui <desktop|bigpicture|web>` picking a front end (see
pythia.ui.mode), or `--tray` (start in the tray, as at sign-in) starts the Qt UI
when PySide6 is installed, or shows the one already running (pythia.instance).
The droidtop commands are requests to that running Pythia, which they start when
none runs; the other flags (list, install, export, exec...) are the command line.
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


def _console_streams() -> None:
    """A standalone build with no console (the Windows program the Start menu opens) has no standard
    streams; what Pythia writes there would raise. They go nowhere instead; the log file still has it."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 (for the process's life)


def main() -> None:
    _console_streams()
    # A standalone build is one program: the copies of itself it starts come back here (pythia.util.selfcmd).
    multiprocessing.freeze_support()
    from pythia.util import selfcmd  # noqa: PLC0415

    if selfcmd.run_requested(sys.argv):
        return
    # Python 3.14 makes 'forkserver' the default; Pythia's subprocess handling
    # expects fork. Windows has no fork at all.
    if hasattr(os, "fork"):
        multiprocessing.set_start_method("fork", force=True)
    _set_up_locale()

    if len(sys.argv) == 1 or "--ui" in sys.argv or "--tray" in sys.argv:
        # One Pythia per person (pythia.instance): when one runs, it shows its window and this copy ends.
        from pythia import instance  # noqa: PLC0415

        if instance.request("ping") is not None:
            if "--tray" not in sys.argv:
                instance.request("show")
            return
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
