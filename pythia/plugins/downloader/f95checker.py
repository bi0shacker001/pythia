"""
f95checker downloader plugin -- launches f95checker as its own window and watches a
folder for new files, per downloads.launch_and_watch. StdoutWatcher-based progress
parsing stays unimplemented (f95checker's real CLI output format hasn't been
inspected -- see the Milestone 2 plan's deferral); the watch-folder path already
covers its browser-driven downloads in the meantime.
"""

from pathlib import Path

from pythia import downloads
from pythia.plugins import DownloaderPlugin
from pythia.plugins.registry import register_builtin


class F95CheckerDownloaderPlugin(DownloaderPlugin):
    id = "pythia.downloader.f95checker"
    display_name = "F95Checker"
    version = "1.0.0"
    author = "pythia"
    platforms = ["windows", "linux"]
    # Not tied to any storefront library kind -- it tracks/downloads updates for
    # adult VN/RPGM/Unity titles regardless of how they were originally onboarded
    # (most of this user's library is "unknown"/generic or "renpy").
    compatible_library_kinds = ["*"]

    def launch(self, context: dict):
        """context: {"executable_path": str (defaults to "f95checker" on PATH),
        "watch_dir": Path, "on_new_file": Callable[[Path], None]}."""
        executable = context.get("executable_path") or "f95checker"
        watch_dir = Path(context["watch_dir"])
        return downloads.launch_and_watch([executable], watch_dir, context["on_new_file"])


plugin = register_builtin(F95CheckerDownloaderPlugin())
