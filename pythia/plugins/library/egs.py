"""
Epic Games Store library plugin.

1. `match()`/`is_root()` -- local-install detection via Epic's own manifest
   directory, `C:\\ProgramData\\Epic\\EpicGamesLauncher\\Data\\Manifests\\*.item`
   (real location, confirmed in Lutris's `util/egs/egs_launcher.py` -- it reads the
   same directory under a Wine prefix, since Lutris runs on Linux; on Windows it's
   just the real Program Data path directly, no prefix involved). Each `.item` file
   is JSON; Lutris's own code only ever reads `AppName`/`MainGameAppName` from it
   (confirmed), cross-referencing everything else against its already-synced online
   library data. This plugin follows the same shape: `AppName` is the one field
   trusted from the manifest; `DisplayName`/`InstallLocation` are read defensively.

2. `list_owned()` -- real account-level ownership, delegated to `legendary`
   (github.com/derrod/legendary, GPLv3) as a **downloaded standalone binary, shelled
   out to via subprocess** -- not a Python import (2026-08-03, second correction:
   "we can make it an optional dependency or downloadable dependency. Like the
   runners system"). The standalone `pythia.exe` build has no pip/Python environment
   a user could install `legendary-gl` into at runtime, so a `pip install`-based
   optional dependency only ever worked for the `python -m pythia` source-checkout
   path. `pythia.core.runners.ensure_tool("legendary")` downloads the real, official,
   architecture-matched prebuilt binary (confirmed against legendary's actual GitHub
   release assets -- x86_64 Windows/Linux only, no arm64 build exists, so this
   correctly reports "not available" on arm64 rather than downloading a mismatched
   binary) into a local cache, and this module runs it as a subprocess -- the same
   external-tool-binary model Lutris's own runners system uses for Wine/Proton, not
   an in-process Python import.

   legendary keeps its own local session file, pointed at
   `app_data_dir()/legendary` (local, NOT the synced `datadir/`) via the
   `LEGENDARY_CONFIG_PATH` env var passed to the subprocess -- syncing it directly
   would let two systems both write the same file, violating the single-writer-
   per-file rule everything else in datadir/ depends on. This doesn't lose the "log
   in once" benefit: the session itself doesn't need to sync, because
   `list_owned()`'s *output* already flows through `onboarding.sync_owned()` into
   the already-synced `datadir/*.json` -- a user only needs to run `epic-connect`/
   `epic-token` on one system, ever.

   The OAuth login itself still uses Epic's own official redirect flow (a plain
   browser tab + copy-pasted JSON, no embedded browser) -- unchanged.
"""

import json
import os
import subprocess
from pathlib import Path

from pythia.core import runners
from pythia.identity import app_data_dir
from pythia.plugins import LibraryPlugin
from pythia.plugins.registry import register_builtin

_MANIFESTS_DIR = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests")


def _config_dir() -> Path:
    return app_data_dir() / "legendary"


def _run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess | None:
    """Run the downloaded legendary binary with `args`, downloading it first if
    needed. Returns None (never raises) if no binary is available for this
    machine's architecture, the download failed, or the process itself couldn't be
    started -- every caller already treats None the same as "not connected"."""
    binary = runners.ensure_tool("legendary")
    if binary is None:
        return None
    env = {**os.environ, "LEGENDARY_CONFIG_PATH": str(_config_dir())}
    try:
        return subprocess.run(
            [str(binary), "-y", *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def login_url() -> str:
    return (
        "https://www.epicgames.com/id/login?redirectUrl="
        "https%3A//www.epicgames.com/id/api/redirect%3F"
        "clientId%3D34a02cf8f4414e29b15921876da36f9a%26responseType%3Dcode"
    )


def connect_from_redirect_json(pasted_json: str) -> bool:
    """Parse the authorizationCode out of the pasted JSON page content (Epic's
    redirect page renders raw JSON as its content, not a URL query param -- a real,
    different shape from GOG/Steam) and hand it to `legendary auth --code`."""
    try:
        content = json.loads(pasted_json)
    except json.JSONDecodeError:
        return False
    code = content.get("authorizationCode")
    if not code:
        return False
    result = _run("auth", "--code", code)
    return result is not None and result.returncode == 0


class EgsLibraryPlugin(LibraryPlugin):
    id = "pythia.library.egs"
    display_name = "Epic Games Store"
    version = "1.0.0"
    author = "pythia"

    def is_connected(self) -> bool:
        # legendary's own session file existing is a cheap, real signal -- avoids
        # spawning a subprocess just to answer "connected or not."
        return (_config_dir() / "user.json").is_file()

    def _manifest_for(self, path: Path) -> dict | None:
        if not _MANIFESTS_DIR.is_dir():
            return None
        resolved = str(path.resolve())
        for item_path in _MANIFESTS_DIR.glob("*.item"):
            try:
                manifest = json.loads(item_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            install_location = manifest.get("InstallLocation")
            if install_location and str(Path(install_location).resolve()) == resolved:
                return manifest
        return None

    def is_root(self, path: Path) -> bool:
        return self._manifest_for(path) is not None

    def match(self, path: Path):
        manifest = self._manifest_for(path)
        if manifest is None:
            return None
        app_name = manifest.get("AppName") or manifest.get("MainGameAppName")
        return {
            "kind": "egs",
            "name": manifest.get("DisplayName"),
            "version_label": None,
            "platforms": ["windows"],
            "external_id": None,  # AppName is a slug/string, not a numeric ID
            "extra": {"egs_app_name": app_name} if app_name else {},
        }

    def list_owned(self):
        if not self.is_connected():
            return None
        if not runners.is_available("legendary"):
            return None

        result = _run("list", "--json", "--third-party", timeout=60)
        if result is None or result.returncode != 0:
            return []

        try:
            games = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        results = []
        for game in games:
            app_name = game.get("app_name")
            if not app_name:
                continue
            results.append(
                {
                    "kind": "egs",
                    "name": game.get("app_title") or app_name,
                    "version_label": None,
                    "platforms": [],
                    "external_id": None,
                    "extra": {"egs_app_name": app_name},
                }
            )
        return results


plugin = register_builtin(EgsLibraryPlugin())
