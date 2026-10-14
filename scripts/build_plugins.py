"""Real build step for every built-in plugin (2026-08-07, user: "Loose source
plus a real build script. We wanna be running executables on given operating
systems... We never run from the source (and we can't, we need to explicitly
break the software's ability to read 'loose' plugins, like it has been
doing.").

Each built-in's real, hand-authored, checked-in source lives as a loose
directory under pythia/plugin_sources/<category>/<name>/ -- plugin.json,
plugin.py, one type-<category>.py per category it declares, one os-*.py per
OS it supports, and (rarely) a real assets/ subfolder of binary tool files.
This script zips each of those directories, byte-for-byte, into a single
committed artifact at pythia/pythia/plugins/builtin/<id>.zip -- the exact
same zip format packaging.py already validates for a user-installed or
third-party package (plugin.json + plugin.py at the zip root, nothing
wrapped in a folder). registry.py's built-in discovery reads ONLY these
built .zip files, via the same zipimport path as any other package; it
never imports or reads plugin_sources/ at runtime. Re-run this script and
commit the result whenever plugin_sources/ changes -- the app itself does
not regenerate these.
"""

import json
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = REPO_ROOT / "pythia" / "plugin_sources"
OUT_DIR = REPO_ROOT / "pythia" / "plugins" / "builtin"


def build_one(plugin_dir: Path) -> str:
    manifest_path = plugin_dir / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plugin_id = manifest["id"]
    if not (plugin_dir / "plugin.py").is_file():
        raise SystemExit(f"{plugin_dir}: no plugin.py")

    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in plugin_id)
    zip_path = OUT_DIR / f"{safe_name}.zip"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(plugin_dir.rglob("*")):
            relative = path.relative_to(plugin_dir)
            if (
                path.is_file()
                and "__pycache__" not in relative.parts
                and path.suffix not in {".pyc", ".pyo"}
            ):
                zf.write(path, relative.as_posix())

    return f"{plugin_id} -> {zip_path.relative_to(REPO_ROOT)} ({zip_path.stat().st_size} bytes)"


def main() -> None:
    plugin_dirs = sorted(p.parent for p in SOURCES_DIR.glob("*/*/plugin.json"))
    if not plugin_dirs:
        raise SystemExit(f"No plugin sources found under {SOURCES_DIR}")

    # Stale zips (a plugin renamed/removed from plugin_sources/ since the last
    # build) must not linger and keep loading -- start from a clean directory.
    if OUT_DIR.is_dir():
        for old_zip in OUT_DIR.glob("*.zip"):
            old_zip.unlink()

    for plugin_dir in plugin_dirs:
        print(build_one(plugin_dir))
    print(f"\nBuilt {len(plugin_dirs)} plugin package(s) into {OUT_DIR.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
