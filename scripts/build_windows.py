"""Build Pythia's Windows portable distribution (2026-08-07): runs PyInstaller
against packaging/windows/pythia.spec, then zips the onedir output into
Pythia-<version>-win-<arch>-portable.zip. Run from the repo root:

    python scripts/build_windows.py

PyInstaller doesn't cross-compile -- this always builds for whatever
architecture it's actually running on (x86_64 or arm64, read from
platform.machine() so the output is correctly labeled either way; see
.github/workflows/build.yml's windows job matrix for how both get built in
CI, one real machine per architecture).

The Inno Setup installer (packaging/windows/pythia.iss) is a separate step --
compile it with Inno Setup's own ISCC.exe once this has produced
dist/windows/pythia/.
"""

import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from pythia import __version__ as VERSION  # noqa: E402

SPEC_PATH = REPO_ROOT / "packaging" / "windows" / "pythia.spec"
DIST_DIR = REPO_ROOT / "dist" / "windows"
BUILD_DIR = REPO_ROOT / "build" / "windows"
CONFIG_SRC = REPO_ROOT / "packaging" / "windows" / "pythia.cfg"


def _arch_label() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "x64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return machine


def main() -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(SPEC_PATH),
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(BUILD_DIR),
            "--noconfirm",
        ],
        cwd=REPO_ROOT,
        check=True,
    )

    onedir = DIST_DIR / "pythia"
    if not onedir.is_dir():
        raise SystemExit(f"Expected PyInstaller output at {onedir}, not found.")

    # pythia.cfg has to be a real sibling of pythia.exe, not nested under
    # _internal/ -- PyInstaller's own `datas` mechanism always nests in
    # onedir mode, so this is a plain post-build copy instead (see
    # pythia.spec's own note on why it isn't listed there).
    shutil.copyfile(CONFIG_SRC, onedir / "pythia.cfg")
    print(f"Copied {CONFIG_SRC} -> {onedir / 'pythia.cfg'}")

    zip_name = f"Pythia-{VERSION}-win-{_arch_label()}-portable.zip"
    zip_path = DIST_DIR / zip_name
    if zip_path.is_file():
        zip_path.unlink()

    print(f"Zipping {onedir} -> {zip_path} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in onedir.rglob("*"):
            if path.is_file():
                zf.write(path, Path("pythia") / path.relative_to(onedir))

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"Done: {zip_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
