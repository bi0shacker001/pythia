"""XDG shortcuts handling"""

import os
import shlex
import shutil
import stat
from textwrap import dedent

from pythia.api import format_installer_url
from pythia.settings import CACHE_DIR
from pythia.util import system
from pythia.util import xdg
from pythia.util.linux import LINUX_SYSTEM
from pythia.util.log import logger

# GTK removal (2026-08-04): GLib.UserDirectory.DIRECTORY_* -> pythia.util.xdg's
# own real-per-platform special-dir guesses (see that module's docstring for
# what "real" means here on Windows vs Linux).
_SPECIAL_DIRS = ("DESKTOP", "MUSIC", "PICTURES", "VIDEOS", "DOCUMENTS", "DOWNLOADS", "TEMPLATES")
_SPECIAL_DIR_ALIASES = {"DOWNLOADS": "DOWNLOAD"}  # this module's public names vs xdg.py's GLib-matching ones


def get_pythia_executable() -> str:
    if LINUX_SYSTEM.is_flatpak():
        return "flatpak run net.pythia.Pythia"
    return "pythia"


def get_xdg_entry(directory: str) -> str | None:
    """Return the path for specific user folders"""
    directory = directory.upper()
    if directory not in _SPECIAL_DIRS:
        raise ValueError(directory + " not supported. Only those folders are supported: " + ", ".join(_SPECIAL_DIRS))
    return xdg.get_user_special_dir(_SPECIAL_DIR_ALIASES.get(directory, directory))


def get_xdg_basename(game_slug: str, game_id: str, base_dir: str | None = None) -> str:
    """Return the filename for .desktop shortcuts"""
    if base_dir:
        # When base dir is provided, lookup possible combinations
        # and return the first match
        for path in [
            "net.pythia.{}-{}.desktop".format(game_slug, game_id),
            "{}-{}.desktop".format(game_slug, game_id),
            "{}.desktop".format(game_slug),
        ]:
            if system.path_exists(os.path.join(base_dir, path)):
                return path

    return "net.pythia.{}-{}.desktop".format(game_slug, game_id)


def create_launcher(
    game_slug: str,
    game_id: str,
    game_name: str,
    launch_config_name: str | None = None,
    desktop: bool = False,
    menu: bool = False,
) -> None:
    """Create a .desktop file."""
    desktop_dir = xdg.get_user_special_dir("DESKTOP")
    if not desktop_dir:
        raise RuntimeError("Creating launcher: unable to find directory for .desktop files")
    pythia_executable = get_pythia_executable()

    url = format_installer_url({"action": "rungameid", "game_slug": game_id, "launch_config_name": launch_config_name})

    # Quote URL for the shell but *also* quote %, which indicates a desktop file
    # field code in the Exec key.
    command = f"{pythia_executable} {shlex.quote(url)}".replace("%", "%%")

    try_exec = "" if LINUX_SYSTEM.is_flatpak() else "TryExec=pythia"

    launcher_content = dedent(
        """
        [Desktop Entry]
        Type=Application
        Name={}
        Icon={}
        Exec=env PYTHIA_SKIP_INIT=1 {}
        Categories=Game
        {}
        """.format(game_name, f"pythia_{game_slug}", command, try_exec)
    )

    launcher_filename = get_xdg_basename(game_slug, game_id)
    tmp_launcher_path = os.path.join(CACHE_DIR, launcher_filename)
    with open(tmp_launcher_path, "w", encoding="utf-8") as tmp_launcher:
        tmp_launcher.write(launcher_content)
        tmp_launcher.close()
    os.chmod(
        tmp_launcher_path,
        stat.S_IREAD | stat.S_IWRITE | stat.S_IRGRP | stat.S_IWGRP,
    )

    if desktop:
        os.makedirs(desktop_dir, exist_ok=True)
        launcher_path = os.path.join(desktop_dir, launcher_filename)
        logger.debug("Creating Desktop icon in %s", launcher_path)
        shutil.copy(tmp_launcher_path, launcher_path)
    if menu:
        user_dir = os.path.expanduser("~/.local/share") if LINUX_SYSTEM.is_flatpak() else xdg.get_user_data_dir()
        menu_path = os.path.join(user_dir, "applications")
        os.makedirs(menu_path, exist_ok=True)
        launcher_path = os.path.join(menu_path, launcher_filename)
        logger.debug("Creating menu launcher in %s", launcher_path)
        shutil.copy(tmp_launcher_path, launcher_path)
    os.remove(tmp_launcher_path)


def get_launcher_path(game_slug: str, game_id: str) -> str:
    """Return the path of a XDG game launcher.
    When legacy is set, it will return the old path with only the slug,
    otherwise it will return the path with slug + id
    """
    desktop_dir = xdg.get_user_special_dir("DESKTOP")
    if not desktop_dir:
        raise RuntimeError("Unable to find directory for .desktop files")

    return os.path.join(desktop_dir, get_xdg_basename(game_slug, game_id, base_dir=desktop_dir))


def get_menu_launcher_path(game_slug: str, game_id: str) -> str:
    """Return the path to a XDG menu launcher, prioritizing legacy paths if
    they exist
    """
    menu_dir = os.path.join(xdg.get_user_data_dir(), "applications")
    return os.path.join(menu_dir, get_xdg_basename(game_slug, game_id, base_dir=menu_dir))


def desktop_launcher_exists(game_slug: str, game_id: str) -> bool:
    """Return True if there is an existing desktop icon for a game"""
    return system.path_exists(get_launcher_path(game_slug, game_id))


def menu_launcher_exists(game_slug: str, game_id: str) -> bool:
    """Return True if there is an existing application menu entry for a game"""
    return system.path_exists(get_menu_launcher_path(game_slug, game_id))


def remove_launcher(game_slug: str, game_id: str, desktop: bool = False, menu: bool = False) -> None:
    """Remove existing .desktop file."""
    if desktop:
        launcher_path = get_launcher_path(game_slug, game_id)
        if system.path_exists(launcher_path):
            os.remove(launcher_path)

    if menu:
        menu_path = get_menu_launcher_path(game_slug, game_id)
        if system.path_exists(menu_path):
            os.remove(menu_path)
