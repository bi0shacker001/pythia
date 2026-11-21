"""Start at sign-in: Pythia in the tray when the person signs in, so paired handhelds find it (docs/SPEC.md,
decision 12). Windows: the per-user Run key. Linux: an autostart desktop entry. macOS: a per-user launch
agent. Each starts `pythia --tray` (pythia.util.selfcmd.app_command)."""

import os
import shlex
import sys
from pathlib import Path

from pythia.util import selfcmd

NAME = "Pythia"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_DESKTOP = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "net.pythia.Pythia.desktop"
)
_AGENT = Path.home() / "Library" / "LaunchAgents" / "net.pythia.Pythia.plist"


def _command() -> list[str]:
    return selfcmd.app_command("--tray")


def _windows_line() -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in _command())


def enabled() -> bool:
    if sys.platform == "win32":
        import winreg  # noqa: PLC0415

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, NAME)
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return _AGENT.is_file()
    return _DESKTOP.is_file()


def set_enabled(on: bool) -> None:
    """Turns start at sign-in on or off for this person; raises OSError when the system refuses."""
    if sys.platform == "win32":
        import winreg  # noqa: PLC0415

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if on:
                winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, _windows_line())
            else:
                try:
                    winreg.DeleteValue(key, NAME)
                except FileNotFoundError:
                    pass
        return
    path = _AGENT if sys.platform == "darwin" else _DESKTOP
    if not on:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        args = "".join(f"<string>{part}</string>" for part in _command())
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict><key>Label</key><string>net.pythia.Pythia</string>'
            f"<key>ProgramArguments</key><array>{args}</array><key>RunAtLoad</key><true/></dict></plist>\n",
            encoding="utf-8",
        )
        return
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Pythia\nIcon=net.pythia.Pythia\n"
        f"Exec={shlex.join(_command())}\nX-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
