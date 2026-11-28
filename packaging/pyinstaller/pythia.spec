# PyInstaller spec for Pythia's standalone builds: what the Windows installer, the macOS disk image and the
# Linux deb and rpm carry (packaging/README.md). Built from an environment with Pythia's wheel and
# packaging/freeze_requirements.py's list installed, from the repository root:
#
#     pyinstaller packaging/pyinstaller/pythia.spec --distpath dist/standalone --workpath build/pyinstaller
#
# Windows: Pythia.exe (no console; the Start menu's) and pythia-cli.exe (the command line), one folder.
# macOS: Pythia.app. Linux: a pythia folder with the pythia program in it.
import importlib.util
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent.parent  # noqa: F821 (PyInstaller defines SPECPATH)
ICON = str(ROOT / "share" / "icons" / "hicolor" / "256x256" / "apps" / "net.pythia.Pythia.png")
WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
QT_FREE = os.environ.get("PYTHIA_QT_FREE") == "1"
LEGACY_UI = ("pythia.ui.desktop", "pythia.ui.bigpicture", "pythia.ui.webview", "pythia.ui.tray", "pythia.ui.mode", "pythia.ipc.qt")

# The compiled droidtop agent, from the installed wheel. Run from the repository root, the source tree's own
# pythia package (which has no compiled module) shadowed the wheel's and the build came out without it, so it
# is named here and the build stops when it is not the installed one. Run PyInstaller from outside the checkout.
NATIVE = importlib.util.find_spec("pythia._native")
if NATIVE is None or NATIVE.origin is None or ROOT in Path(NATIVE.origin).resolve().parents:
    raise SystemExit(
        f"pythia._native must come from the installed wheel, not {NATIVE and NATIVE.origin}: "
        "install the wheel into the build environment and run PyInstaller from outside the checkout"
    )

# Pythia imports much of itself lazily (plugins, the UI's places, the droidtop modules), so every module
# is collected.
hidden = collect_submodules("pythia", filter=lambda name: ".tests" not in name and
    (not QT_FREE or not any(name == old or name.startswith(old + ".") for old in LEGACY_UI))) + ["pythia._native"]
package_data = collect_data_files("pythia")
if QT_FREE:
    package_data = [item for item in package_data if not any(
        "/" + old.replace(".", "/") + "/" in Path(item[0]).as_posix() for old in LEGACY_UI)]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "pyinstaller" / "entry.py")],
    pathex=[],
    binaries=[(NATIVE.origin, "pythia")],
    # The package's own files (Qt designs, json, the Windows secure-screen helper) and share/pythia, which
    # pythia.util.datapath finds inside the bundle.
    datas=package_data + [(str(ROOT / "share" / "pythia"), "share/pythia")],
    hiddenimports=hidden,
    # GTK leftovers the Qt UI does not use (packaging/freeze_requirements.py).
    excludes=["gi", "dbus", "tkinter"] + (["PySide6", "PyQt6", "PyQt5", *LEGACY_UI] if QT_FREE else []),
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821


def program(name, console):
    return EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        console=console,
        icon=ICON,
        upx=False,
    )


if WINDOWS:
    programs = [program("Pythia", console=False), program("pythia-cli", console=True)]
    folder = "Pythia"
elif MAC:
    programs = [program("pythia", console=False)]
    folder = "pythia"
else:
    programs = [program("pythia", console=True)]
    folder = "pythia"

collected = COLLECT(*programs, a.binaries, a.datas, upx=False, name=folder)  # noqa: F821

if MAC:
    app = BUNDLE(  # noqa: F821
        collected,
        name="Pythia.app",
        icon=ICON,
        bundle_identifier="net.pythia.Pythia",
        info_plist={
            "CFBundleName": "Pythia",
            "CFBundleDisplayName": "Pythia",
            "NSHighResolutionCapable": True,
            "CFBundleURLTypes": [{"CFBundleURLName": "Pythia", "CFBundleURLSchemes": ["pythia"]}],
        },
    )
