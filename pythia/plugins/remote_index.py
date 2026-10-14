"""Client for a remote plugin index (2026-08-07: "we need to configure
github sites to hold modules/plugin to allow for updates... Keep the systems
simple. Decouple plugins from the actual application storage, so we just
update ourselves when we need to.").

Deliberately simple, one real mechanism: fetch a JSON index (the exact shape
scripts/publish_plugin_index.py writes to docs/plugins/index.json, served
as static files by GitHub Pages -- no GitHub API, no auth), download a
chosen plugin's zip, verify its real sha256 against what the index declared,
and hand it to packaging.install_plugin_zip() -- the same install path a
user manually picking a local zip file already goes through. A plugin's own
update cycle is completely independent of the app's own version/release
cycle, which is the actual point of a separate index like this.

DEFAULT_INDEX_URL points at GitHub Pages for this repo -- not fetchable yet
(the repo is private until 1.0; Pages requires a public repo or a paid plan
otherwise), but the code is correct now and needs no changes once the repo
goes public.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from pythia.identity import app_data_dir
from pythia.plugins import packaging

DEFAULT_INDEX_URL = "https://bi0shacker001.github.io/pythia/plugins/index.json"
MAX_PLUGIN_DOWNLOAD = 32 * 1024 * 1024
MAX_INDEX_DOWNLOAD = 2 * 1024 * 1024
MAX_INDEX_ENTRIES = 1024
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def _require_https(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Plugin indexes and downloads must use HTTPS.")
    return url


def _validate_entry(entry: dict) -> dict:
    if not isinstance(entry, dict):
        raise ValueError("Every remote plugin index entry must be an object.")
    plugin_id = entry.get("id")
    if not isinstance(plugin_id, str) or not packaging.PLUGIN_ID_PATTERN.fullmatch(plugin_id):
        raise ValueError("Remote plugin entry has an invalid plugin id.")
    digest = entry.get("sha256")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise ValueError(f"Remote plugin {plugin_id!r} has an invalid SHA-256 digest.")
    download = entry.get("download")
    if not isinstance(download, str) or not download.strip():
        raise ValueError(f"Remote plugin {plugin_id!r} has no download path.")
    return entry


def fetch_index(index_url: str = DEFAULT_INDEX_URL, timeout: int = 15) -> list[dict]:
    """Every plugin the remote index currently lists -- [{"id",
    "display_name", "version", "categories", "sha256", "download"}, ...].
    Raises on a real network/format failure; callers decide how to surface
    that (see backend.py's Slot wrapper for the UI-facing version)."""
    req = urllib.request.Request(_require_https(index_url), headers={"User-Agent": "Pythia"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        _require_https(resp.geturl())
        payload = resp.read(MAX_INDEX_DOWNLOAD + 1)
        if len(payload) > MAX_INDEX_DOWNLOAD:
            raise ValueError("Remote plugin index exceeds the 2 MiB safety limit.")
        data = json.loads(payload.decode("utf-8"))
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise ValueError("Remote index has no 'plugins' list.")
    if len(plugins) > MAX_INDEX_ENTRIES:
        raise ValueError(f"Remote plugin index exceeds {MAX_INDEX_ENTRIES} entries.")
    return [_validate_entry(entry) for entry in plugins]


def install_from_index(entry: dict, index_url: str = DEFAULT_INDEX_URL, timeout: int = 60) -> dict:
    """Download `entry` (one dict from fetch_index()'s own return list),
    verify its real sha256 matches what the index declared, and install it
    via the normal zip-install path. Returns the same {"status": "ok"/"error", ...}
    shape packaging.install_plugin_zip() does."""
    try:
        entry = _validate_entry(entry)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    download_path = entry["download"]

    try:
        download_url = _require_https(urllib.parse.urljoin(_require_https(index_url), download_path))
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    tmp_dir = app_data_dir() / "plugins" / "_downloads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"{entry.get('id', 'plugin')}.zip"

    try:
        req = urllib.request.Request(download_url, headers={"User-Agent": "Pythia"})
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp_path, "wb") as f:
            _require_https(resp.geturl())
            declared_length = resp.headers.get("Content-Length")
            if declared_length:
                try:
                    if int(declared_length) > MAX_PLUGIN_DOWNLOAD:
                        return {"status": "error", "message": "Plugin download exceeds the 32 MiB safety limit."}
                except ValueError:
                    pass
            received = 0
            while chunk := resp.read(1024 * 256):
                received += len(chunk)
                if received > MAX_PLUGIN_DOWNLOAD:
                    return {"status": "error", "message": "Plugin download exceeded the 32 MiB safety limit."}
                f.write(chunk)

        actual_hash = packaging.compute_hash(tmp_path)
        if actual_hash != entry["sha256"]:
            return {
                "status": "error",
                "message": f"Downloaded file's hash ({actual_hash}) doesn't match the index's "
                f"declared hash ({entry['sha256']}) -- refusing to install a corrupted/tampered download.",
            }

        return packaging.install_plugin_zip(str(tmp_path))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"status": "error", "message": f"Couldn't download {download_url}: {e}"}
    finally:
        tmp_path.unlink(missing_ok=True)
