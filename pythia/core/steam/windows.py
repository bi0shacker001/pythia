"""
Windows-native Steam install/library/user discovery -- adapted from Lutris's
lutris/util/steam/config.py (2026-08-03), which is Unix-path-only (STEAM_DATA_DIRS is
a list of `~/.steam`-style paths -- meaningless here, since Pythia runs natively on
Windows, not Steam-for-Windows-under-Wine the way Lutris's own Linux-first code
assumes). Real logic kept: `loginusers.vdf` parsing for local-user detection (no API
key needed) and `libraryfolders.vdf` parsing for multi-drive library folders (a real
gap pythia/plugins/library/steam.py doesn't cover today -- it only sees whichever
single steamapps/ folder scanning.py's tree walk happens to reach). New: registry-
based Steam install path lookup, since Windows has no XDG-style fixed data dir.
"""

import winreg
from pathlib import Path

from pythia.core.steam.vdf import parse_vdf_file


def get_steam_install_path() -> Path | None:
    """Locate Steam's own install directory via the registry (how Steam itself
    registers its install location on Windows), falling back to the two standard
    Program Files locations if the registry key is missing."""
    for hive, subkey in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
    ):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _type = winreg.QueryValueEx(key, "SteamPath" if hive == winreg.HKEY_CURRENT_USER else "InstallPath")
                path = Path(value)
                if path.is_dir():
                    return path
        except OSError:
            continue

    for candidate in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        path = Path(candidate)
        if path.is_dir():
            return path
    return None


def get_steamapps_dirs() -> list[Path]:
    """Every Steam library's steamapps/ folder (the main install's own, plus any
    additional library folders on other drives, per libraryfolders.vdf) -- closes a
    real gap in pythia/plugins/library/steam.py, which today only ever sees whichever
    single steamapps/ folder the user happens to point `scan` at directly."""
    steam_dir = get_steam_install_path()
    if steam_dir is None:
        return []

    dirs = []
    main_steamapps = steam_dir / "steamapps"
    if main_steamapps.is_dir():
        dirs.append(main_steamapps)

    library_vdf = main_steamapps / "libraryfolders.vdf"
    if library_vdf.is_file():
        try:
            data = parse_vdf_file(library_vdf)
        except OSError:
            data = {}
        folders = data.get("libraryfolders", data)
        for entry in folders.values():
            if not isinstance(entry, dict):
                continue
            raw_path = entry.get("path")
            if not raw_path:
                continue
            candidate = Path(raw_path) / "steamapps"
            if candidate.is_dir() and candidate not in dirs:
                dirs.append(candidate)
    return dirs


def get_steam_users() -> list[dict]:
    """Every account that has logged into this Steam install (via config/loginusers.vdf),
    most-recently-used first -- local file, no API key or network access needed. Each
    dict has at least 'steamid64' and whatever loginusers.vdf itself stores (PersonaName,
    MostRecent, ...)."""
    steam_dir = get_steam_install_path()
    if steam_dir is None:
        return []
    login_vdf = steam_dir / "config" / "loginusers.vdf"
    if not login_vdf.is_file():
        return []
    try:
        data = parse_vdf_file(login_vdf)
    except OSError:
        return []
    users_block = data.get("users", data)
    users = []
    most_recent = None
    for steamid64, account in users_block.items():
        if not isinstance(account, dict):
            continue
        account = dict(account)
        account["steamid64"] = steamid64
        if str(account.get("MostRecent", account.get("mostrecent", "0"))) == "1":
            most_recent = account
        else:
            users.append(account)
    if most_recent:
        users = [most_recent] + users
    return users


def get_active_steamid64() -> str | None:
    """The most-recently-used local Steam account's SteamID64, or None if Steam has
    never been logged into on this machine. This is what list_owned() uses by default
    (falls back to asking the user for a SteamID if this is empty -- e.g. a fresh
    machine, or a headless/portable Steam install with no local login history)."""
    users = get_steam_users()
    return users[0]["steamid64"] if users else None
