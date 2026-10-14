# PyInstaller spec for Pythia's Windows build (2026-08-07, revised same day
# to drop an earlier 4-executable design: "wait, nope. undo that. We're gonna
# use a config file on windows... We won't give a console, we'll write a log
# to the appdata folder's logs folder"). One executable, no console ever --
# UI selection and debug logging are controlled by a real config file (see
# pythia/util/local_config.py), not by which .exe you double-click.
#
# Build with:
#   pyinstaller packaging/windows/pythia.spec --distpath dist/windows --workpath build/windows
# (see scripts/build_windows.py, which wraps this + zips the result).

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821
REPO_ROOT = SPEC_DIR.parent.parent
# REPO_ROOT directly contains the `pythia` package dir (REPO_ROOT/pythia/__init__.py) --
# SRC_ROOT is what belongs on sys.path/pathex; PKG_DIR is the package itself.
SRC_ROOT = REPO_ROOT
PKG_DIR = REPO_ROOT / "pythia"
SHARE = REPO_ROOT / "share"

sys.path.insert(0, str(SRC_ROOT))

from pythia import __version__ as PYTHIA_VERSION  # noqa: E402

# PySide6 is heavy (WebEngine especially) -- collect_all() is the safe,
# guaranteed-complete option (icudtl.dat, qtwebengine_resources.pak,
# QtWebEngineProcess.exe, translations/, every plugin .dll) rather than
# hand-picking hidden imports and risking a silent missing-resource crash
# only visible once the frozen build actually runs. Costs bundle size, not
# correctness -- the right tradeoff for a first working build.
pyside6_datas, pyside6_binaries, pyside6_hiddenimports = collect_all("PySide6")

# 2026-08-07, external review against a clean install found this: PyInstaller's
# static import analysis can't see importlib.import_module("pythia.migrations.%s")
# (pythia/migrations/__init__.py) or __import__("pythia.runners.%s") (pythia/
# runners/__init__.py) -- both crash a genuinely fresh install (no local.db
# migration_version yet) with "No module named 'pythia.migrations.mess_to_mame'".
# pythia.util.moddb.py's importlib.import_module("moddb") is a real third-party
# package (not a pythia submodule), needs its own explicit hiddenimport instead
# of collect_submodules. Every other importlib/__import__ call site in the app
# was audited (see PLAN.md's own note) and doesn't need this treatment -- not a
# blind whole-repo collect.
dynamic_imports = [
    *collect_submodules("pythia.migrations"),
    *collect_submodules("pythia.runners"),
    "moddb",
]


def _windows_version_tuple(v: str) -> tuple:
    """Windows FileVersion/ProductVersion need a real 4-part integer tuple --
    "0.0.1" -> (0, 0, 1, 0)."""
    parts = [int("".join(c for c in seg if c.isdigit()) or 0) for seg in v.split(".")]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


_version_tuple = _windows_version_tuple(PYTHIA_VERSION)

# Real Pythia branding in the exe's own Windows version resource (2026-08-07,
# external review: "Do not retain Lutris product/version fields" -- there
# were none set at all before this, which is its own real gap: Explorer's
# file Properties dialog and Inno Setup's GetFileVersion() both had nothing
# authoritative to read).
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_version_tuple,
        prodvers=_version_tuple,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Pythia"),
                        StringStruct("FileDescription", "Pythia Game Manager"),
                        StringStruct("FileVersion", PYTHIA_VERSION),
                        StringStruct("InternalName", "pythia"),
                        StringStruct("OriginalFilename", "pythia.exe"),
                        StringStruct("ProductName", "Pythia"),
                        StringStruct("ProductVersion", PYTHIA_VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

datas = [
    *pyside6_datas,
    # The 23 real, pre-built built-in plugin packages (scripts/build_plugins.py's
    # committed output) -- registry._builtin_zip_dir() resolves this relative to
    # wherever `pythia.plugins` itself lands in the frozen bundle, so the
    # relative path must be preserved exactly.
    (str(PKG_DIR / "plugins" / "builtin"), "pythia/plugins/builtin"),
    # Optional plugins (scripts/build_optional_plugins.py's committed output,
    # e.g. Download Automator) -- never auto-loaded/protected like the
    # built-ins above, but must still ship with the app so
    # Backend.list_optional_plugins()/install_optional_plugin() can find them
    # without a network fetch. _optional_plugin_zip_dir() resolves this the
    # same way _builtin_zip_dir() does, relative to pythia.plugins' own
    # frozen location.
    (str(REPO_ROOT / "packaging" / "optional-plugins"), "pythia/plugins/optional"),
    # The web UI (index.html + assets) the QtWebEngine view loads.
    (str(PKG_DIR / "ui" / "desktop" / "web"), "pythia/ui/desktop/web"),
    # The big-picture QML UI's own .qml files (2026-08-07, external review's
    # smoke-test matrix caught this: --ui bigpicture exited silently within
    # seconds in the frozen build -- QQmlApplicationEngine logs a qWarning
    # and ends up with zero root objects when Main.qml can't be found, not a
    # Python exception, so neither crash hook saw anything to log). Resolved
    # relative to pythia.ui.bigpicture.app's own __file__, same as the web
    # UI's index.html above.
    (str(PKG_DIR / "ui" / "bigpicture" / "qml"), "pythia/ui/bigpicture/qml"),
    # Default cover-art/banner placeholders.
    (str(SHARE / "pythia" / "media"), "share/pythia/media"),
    # pythia-wrapper (monitored_command.py's process-tracking helper script) --
    # bundled so get_wrapper_script_location()'s candidate[0]
    # (dirname(sys.argv[0])/..) can find it; real Windows behavior for the
    # monitored-terminal-command feature this backs is unverified/out of
    # scope here, this only fixes it not being silently absent.
    (str(SHARE / "pythia" / "bin" / "pythia-wrapper"), "share/pythia/bin"),
]
# pythia.cfg is NOT listed here -- PyInstaller's `datas` always lands under
# _internal/ in onedir mode, but local_config.py's "next to the executable"
# candidate means truly next to pythia.exe (sibling, not nested). See
# scripts/build_windows.py's post-build copy step for that instead.

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
    excludes=[
        # Linux/GTK-only, never reachable on Windows (see pyproject.toml's own
        # sys_platform markers for the same audit).
        "gi",
        "dbus",
        "evdev",
    ],
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
    console=False,  # never a console -- pythia.cfg's debug=true + the log
                     # file under app_data_dir()/logs/ are the only way to
                     # get diagnostics out of this build.
    icon=str(SPEC_DIR / "icon.ico"),
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="pythia",
)
