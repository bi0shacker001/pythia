# PyInstaller spec for Pythia's Linux build (2026-08-07) -- feeds
# scripts/build_appimage.sh's AppDir assembly and scripts/build_linux_packages.sh's
# fpm-based rpm/deb. Must be run ON Linux (or in the ubuntu-latest CI job) --
# PyInstaller doesn't cross-compile. Build with:
#   pyinstaller packaging/linux/pythia.spec --distpath dist/linux --workpath build/linux

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821
REPO_ROOT = SPEC_DIR.parent.parent
SRC_ROOT = REPO_ROOT
PKG_DIR = REPO_ROOT / "pythia"
SHARE = REPO_ROOT / "share"

sys.path.insert(0, str(SRC_ROOT))

pyside6_datas, pyside6_binaries, pyside6_hiddenimports = collect_all("PySide6")

# 2026-08-07, external review (same finding applies to every frozen build,
# not just Windows) -- see packaging/windows/pythia.spec's own note for the
# full explanation of why these three specifically need this.
dynamic_imports = [
    *collect_submodules("pythia.migrations"),
    *collect_submodules("pythia.runners"),
    "moddb",
]

datas = [
    *pyside6_datas,
    (str(PKG_DIR / "plugins" / "builtin"), "pythia/plugins/builtin"),
    # Optional plugins (scripts/build_optional_plugins.py's committed output,
    # e.g. Download Automator) -- see the Windows spec's own comment for why
    # this must ship even though it's never auto-loaded/protected.
    (str(REPO_ROOT / "packaging" / "optional-plugins"), "pythia/plugins/optional"),
    (str(PKG_DIR / "ui" / "desktop" / "web"), "pythia/ui/desktop/web"),
    (str(PKG_DIR / "ui" / "bigpicture" / "qml"), "pythia/ui/bigpicture/qml"),
    (str(SHARE / "pythia" / "media"), "share/pythia/media"),
    (str(SHARE / "pythia" / "bin" / "pythia-wrapper"), "share/pythia/bin"),
    # Desktop integration assets -- read by pythia.util.xdg_install's
    # --xdg-install flag (AppImage self-integration), and by the rpm/deb
    # packaging step (scripts/build_linux_packages.sh installs these
    # system-wide directly rather than via xdg_install).
    (str(SHARE / "applications" / "net.pythia.Pythia.desktop"), "share/applications"),
    (str(SHARE / "icons" / "hicolor"), "share/icons/hicolor"),
    (str(SHARE / "mime" / "packages" / "application-x-pythia.xml"), "share/mime/packages"),
]

a = Analysis(
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(SRC_ROOT)],
    binaries=pyside6_binaries,
    datas=datas,
    hiddenimports=[
        *pyside6_hiddenimports,
        *dynamic_imports,
        "pythia.ui.backend",
        "pythia.ui.webview.app",
        "pythia.ui.desktop.app",
        "pythia.ui.bigpicture.app",
    ],
    hookspath=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pythia",
    debug=False,
    strip=False,
    upx=False,
    console=True,  # AppImages/rpm/deb keep a console -- no --windowed
                    # equivalent gap on Linux the way there is on Windows.
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="pythia",
)
