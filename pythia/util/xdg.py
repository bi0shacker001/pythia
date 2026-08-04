"""Cross-platform user directory lookups -- replaces the handful of
GLib.get_user_*_dir() calls that used to pull PyGObject in just for path
strings (2026-08-04 GTK removal). Windows has no XDG convention of its own,
so the nearest real analogs (%APPDATA%/%LOCALAPPDATA%) are used there;
everything else follows the real XDG base directory spec.
"""

import os


def get_user_config_dir() -> str:
    if os.name == "nt":
        return os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def get_user_data_dir() -> str:
    if os.name == "nt":
        return os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def get_user_cache_dir() -> str:
    if os.name == "nt":
        return os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")


# GLib.UserDirectory special dirs (Desktop/Documents/Downloads/...) that a few
# call sites ask for by name -- real per-platform guesses, not GLib-backed
# (GLib reads these from ~/.config/user-dirs.dirs on Linux; we don't have
# that parser here, so this is a reasonable fallback, not a full port).
_SPECIAL_DIR_NAMES = {
    "DESKTOP": "Desktop",
    "DOCUMENTS": "Documents",
    "DOWNLOAD": "Downloads",
    "MUSIC": "Music",
    "PICTURES": "Pictures",
    "PUBLIC_SHARE": "Public",
    "TEMPLATES": "Templates",
    "VIDEOS": "Videos",
}


def get_user_special_dir(name: str) -> str:
    """name: one of the _SPECIAL_DIR_NAMES keys (matches GLib.UserDirectory's
    G_USER_DIRECTORY_* suffix, e.g. "DOWNLOAD" for G_USER_DIRECTORY_DOWNLOAD)."""
    folder = _SPECIAL_DIR_NAMES.get(name, name.title())
    return os.path.join(os.path.expanduser("~"), folder)
