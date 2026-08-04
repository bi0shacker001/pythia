"""Toolkit-agnostic replacement for Gio.Application.get_default() (2026-08-04
GTK removal). GTK's Gio.Application maintained its own process-wide singleton;
core code (game.py) reached into it to call UI methods like
show_pythia_installer_window(). Same pattern, no GObject/Gio dependency: the
Qt PythiaApplication calls set_default(self) once at startup, and core code
calls get_default() to reach it.
"""

from typing import Any

_default: Any = None


def get_default() -> Any:
    """Returns the running application instance, or None if none has been
    set yet (e.g. running under the CLI with no UI at all)."""
    return _default


def set_default(application: Any) -> None:
    global _default
    _default = application
