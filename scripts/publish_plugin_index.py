"""Publish the built-in plugin index for GitHub Pages (2026-08-07: "we need
to configure github sites to hold modules/plugin to allow for updates" --
"Keep the systems simple. Decouple plugins from the actual application
storage, so we just update ourselves when we need to.").

Simple by design, one mechanism only: copies every real, pre-built
pythia/plugins/builtin/*.zip (scripts/build_plugins.py's own output) into
docs/plugins/, and writes docs/plugins/index.json describing them (id,
display_name, version, sha256, categories, a plain relative download path).
GitHub Pages serves docs/ as static files -- no GitHub API, no auth, no
Release-asset dependency; a plugin's own update cycle is completely
independent of the app's own release cycle, which is the whole point of
decoupling them.

Requires the repo to actually be public with Pages enabled to be fetchable
by a real install -- currently private ("only private right now, to prevent
people from grabbing it while it's working. It'll be made public with
1.0"), so this can be run and committed now, verified once public.

Run after scripts/build_plugins.py, from the repo root:
    python scripts/publish_plugin_index.py
"""

import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILTIN_DIR = REPO_ROOT / "pythia" / "plugins" / "builtin"
DOCS_PLUGINS_DIR = REPO_ROOT / "docs" / "plugins"

sys.path.insert(0, str(REPO_ROOT))
from pythia.plugins import packaging  # noqa: E402


def main() -> None:
    if not BUILTIN_DIR.is_dir():
        raise SystemExit(f"No built plugins at {BUILTIN_DIR} -- run scripts/build_plugins.py first.")

    DOCS_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    for old_zip in DOCS_PLUGINS_DIR.glob("*.zip"):
        old_zip.unlink()

    entries = []
    for zip_path in sorted(BUILTIN_DIR.glob("*.zip")):
        manifest = packaging.read_manifest(zip_path)
        dest = DOCS_PLUGINS_DIR / zip_path.name
        shutil.copyfile(zip_path, dest)
        entries.append(
            {
                "id": manifest["id"],
                "display_name": manifest.get("display_name", manifest["id"]),
                "version": manifest.get("version", "0.0.0"),
                "categories": manifest.get("categories", []),
                "sha256": packaging.compute_hash(zip_path),
                "download": f"plugins/{zip_path.name}",
            }
        )
        print(f"{manifest['id']} -> docs/plugins/{zip_path.name}")

    index_path = DOCS_PLUGINS_DIR / "index.json"
    index_path.write_text(json.dumps({"plugins": entries}, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {index_path} ({len(entries)} plugin(s))")


if __name__ == "__main__":
    main()
