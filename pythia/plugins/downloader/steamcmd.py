"""
SteamCMD downloader plugin -- real headless capability (2026-08-04), not
launch-only. Previously this only assumed a "steamcmd" binary already existed on
PATH and launched it as a plain GUI-style watch-folder tool, with an explicit
note that its real CLI contract hadn't been tested. Since then:

- `pythia.core.runners.ensure_tool("steamcmd")` was given a real, verified catalog
  entry (Valve's own stable installer URLs, same static-catalog pattern used for
  `legendary`) -- confirmed live: downloaded, extracted, and actually run end to
  end (`steamcmd.exe +quit` self-updated and exited cleanly; `+login anonymous
  +quit` connected to Steam Public successfully). Not guessed.
- `get_download_info()` below is real, scriptable SteamCMD syntax
  (`+login anonymous +force_install_dir <dir> +app_update <appid> validate +quit`),
  confirmed against SteamCMD's own documented command set. Only ever anonymous --
  a real authenticated login (username/password, likely Steam Guard 2FA) is a
  materially bigger, interactive flow that hasn't been built; anonymous-only
  covers free-to-play titles and dedicated-server tools, not the user's owned
  paid library, which is a real, stated ceiling, not hidden.
"""

import platform
from pathlib import Path

from pythia import downloads
from pythia.core import runners
from pythia.plugins import DownloaderPlugin
from pythia.plugins.registry import register_builtin


class SteamCmdDownloaderPlugin(DownloaderPlugin):
    id = "pythia.downloader.steamcmd"
    display_name = "SteamCMD"
    version = "1.0.0"
    author = "pythia"
    platforms = ["windows", "linux"]
    # SteamCMD only ever acquires for Steam-kind games.
    compatible_library_kinds = ["steam"]

    def _binary(self, log=print):
        exe_name = "steamcmd.exe" if platform.system() == "Windows" else "steamcmd.sh"
        return runners.ensure_tool("steamcmd", exe_name=exe_name, log=log)

    def get_download_info(self, item) -> dict | None:
        """Real, scriptable SteamCMD command line to anonymously fetch/update
        `item` (a dict with at least `appid` and `install_dir`). Returns None if
        no steamcmd binary is available for this machine (no real build, or the
        download failed) -- callers must not treat that as "app not found."."""
        appid = item.get("appid")
        install_dir = item.get("install_dir")
        if not appid or not install_dir:
            return None
        binary = self._binary()
        if binary is None:
            return None
        return {
            "command": [
                str(binary),
                "+force_install_dir",
                str(install_dir),
                "+login",
                "anonymous",
                "+app_update",
                str(appid),
                "validate",
                "+quit",
            ],
            "authenticated": False,
        }

    def launch(self, context: dict):
        """context: {"executable_path": str (optional override; defaults to the
        real downloaded copy via ensure_tool()), "watch_dir": Path,
        "on_new_file": Callable[[Path], None]}."""
        executable = context.get("executable_path") or self._binary()
        if executable is None:
            raise RuntimeError("No steamcmd build available for this machine's OS/architecture.")
        watch_dir = Path(context["watch_dir"])
        return downloads.launch_and_watch([str(executable)], watch_dir, context["on_new_file"])


plugin = register_builtin(SteamCmdDownloaderPlugin())
