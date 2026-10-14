"""Build installable optional plugin ZIPs without making them protected built-ins."""

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "optional_plugin_sources"
OUTPUT_DIR = ROOT / "packaging" / "optional-plugins"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for plugin_dir in sorted(path.parent for path in SOURCE_DIR.glob("*/plugin.json")):
        manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
        target = OUTPUT_DIR / f"{manifest['id']}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(plugin_dir.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                    archive.write(path, path.relative_to(plugin_dir).as_posix())
        print(f"{manifest['id']} -> {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
