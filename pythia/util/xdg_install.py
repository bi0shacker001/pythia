"""Real XDG desktop integration for a self-contained Linux distribution
(AppImage, or any other portable Linux bundle) -- 2026-08-07, "with extra
stuff in the relevant xdg folders." An AppImage doesn't automatically appear
in an application launcher or register a MIME type the way a real system
package (rpm/deb) does; this is the standard "self-integrate" pattern AppImages
use instead of requiring AppImageLauncher. Reusable outside the AppImage too
(any Linux install of Pythia can run `pythia --xdg-install`).

Deliberately explicit/opt-in (a real CLI flag, not something that runs
automatically on every launch) -- registering files in the user's real
~/.local/share on their behalf is the kind of side effect that should be
asked for, not assumed.
"""

import os
import shutil
import subprocess
from pathlib import Path

from pythia.util.log import logger
from pythia.util.xdg import get_user_data_dir

APP_ID = "net.pythia.Pythia"


def _repo_share_dir() -> Path | None:
    """Where the checked-in share/ tree (desktop file, icons, mime XML)
    actually lives for THIS running instance -- the PyInstaller-frozen
    onedir bundle carries its own copy under _internal/share (via the
    (source, "share/...") datas entries in packaging/linux/pythia.spec),
    resolved relative to the running executable; a source checkout instead
    resolves it relative to this module's own file. None if neither is
    found (e.g. a stripped-down install)."""
    import sys

    candidates = [
        Path(sys.executable).resolve().parent / "_internal" / "share",  # frozen bundle
        Path(sys.executable).resolve().parent / "share",  # frozen bundle, alt layout
        Path(__file__).resolve().parent.parent.parent / "share",  # source checkout
    ]
    for candidate in candidates:
        if (candidate / "applications" / f"{APP_ID}.desktop").is_file():
            return candidate
    return None


def _resolve_exec_path() -> str:
    """The real, absolute path this instance is actually running from --
    what a generated .desktop file's Exec= must point at (an AppImage has
    no fixed system install location, so this can't be hardcoded)."""
    import sys

    if getattr(sys, "frozen", False):
        return sys.executable
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return appimage
    return sys.executable


def xdg_install(log=logger.info) -> bool:
    """Copy the .desktop file (with Exec= rewritten to this instance's real
    running path), the hicolor icon set, and the .pythia MIME association
    into the current user's real XDG data dirs. Returns True on success."""
    if os.name == "nt":
        log("xdg-install is a Linux-only operation; nothing to do on Windows.")
        return False

    share_dir = _repo_share_dir()
    if share_dir is None:
        log("Couldn't locate this install's own share/ tree -- nothing to integrate.")
        return False

    data_dir = Path(get_user_data_dir())
    exec_path = _resolve_exec_path()

    # .desktop file, Exec= rewritten to the real running path.
    desktop_src = share_dir / "applications" / f"{APP_ID}.desktop"
    desktop_dst_dir = data_dir / "applications"
    desktop_dst_dir.mkdir(parents=True, exist_ok=True)
    desktop_text = desktop_src.read_text(encoding="utf-8")
    desktop_text = desktop_text.replace("Exec=pythia %U", f'Exec="{exec_path}" %U')
    (desktop_dst_dir / f"{APP_ID}.desktop").write_text(desktop_text, encoding="utf-8")
    log(f"Installed {desktop_dst_dir / f'{APP_ID}.desktop'}")

    # Icon set.
    icons_src = share_dir / "icons" / "hicolor"
    icons_dst = data_dir / "icons" / "hicolor"
    copied = 0
    if icons_src.is_dir():
        for src_file in icons_src.rglob(f"{APP_ID}.*"):
            rel = src_file.relative_to(icons_src)
            dst_file = icons_dst / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_file, dst_file)
            copied += 1
    log(f"Installed {copied} icon file(s) under {icons_dst}")

    # MIME type association (the existing .pythia installer-script file type).
    mime_src = share_dir / "mime" / "packages" / "application-x-pythia.xml"
    if mime_src.is_file():
        mime_dst_dir = data_dir / "mime" / "packages"
        mime_dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(mime_src, mime_dst_dir / "application-x-pythia.xml")
        log(f"Installed {mime_dst_dir / 'application-x-pythia.xml'}")

    # Refresh the desktop/mime databases if the tools exist -- best-effort,
    # a missing tool (e.g. a minimal DE without update-mime-database) isn't
    # a real failure, the files themselves are still correctly in place.
    for cmd, args in (
        ("update-desktop-database", [str(desktop_dst_dir)]),
        ("update-mime-database", [str(data_dir / "mime")]),
    ):
        if shutil.which(cmd):
            try:
                subprocess.run([cmd, *args], check=False, capture_output=True)
            except OSError:
                pass

    log("XDG integration complete.")
    return True
