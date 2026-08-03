"""
PluginContext -- the small internal API every plugin method gets instead of a bare
`log` callable, so a plugin needing a scratch/download cache or OS info doesn't
reinvent app_data_dir() plumbing itself. Deliberately minimal: this is not a general
dependency-injection framework, just the handful of things Milestone 4's plugins
actually need.
"""

import os
from pathlib import Path


class PluginContext:
    def __init__(self, log=print):
        self.log = log

    @property
    def current_os(self) -> str:
        """Coarse OS id matching the platform strings already used throughout
        datadir/schema (e.g. "windows-x86_64", "linux-aarch64") -- "windows" or
        "linux" (macOS support isn't a real target yet, falls through to "linux")."""
        return "windows" if os.name == "nt" else "linux"

    def cache_dir(self, plugin_id: str) -> Path:
        """A per-plugin scratch/download cache under the app-data dir, created on
        first use."""
        from pythia.identity import app_data_dir

        d = app_data_dir() / "plugin_cache" / plugin_id
        d.mkdir(parents=True, exist_ok=True)
        return d
