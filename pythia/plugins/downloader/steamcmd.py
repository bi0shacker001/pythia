"""
SteamCMD downloader plugin -- launch-only this milestone. SteamCMD has a real
scriptable CLI (login, app_update, workshop downloads), which would let this plugin
do real version-query/update-check work later, but that integration needs testing
against a real steamcmd install this session doesn't have -- don't fabricate its exact
CLI contract. For now this just launches it as its own window/console and watches a
folder, same shape as every other downloader plugin.
"""

from pathlib import Path

from pythia import downloads
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

    def launch(self, context: dict):
        """context: {"executable_path": str (defaults to "steamcmd" on PATH),
        "watch_dir": Path, "on_new_file": Callable[[Path], None]}."""
        executable = context.get("executable_path") or "steamcmd"
        watch_dir = Path(context["watch_dir"])
        return downloads.launch_and_watch([executable], watch_dir, context["on_new_file"])


plugin = register_builtin(SteamCmdDownloaderPlugin())
