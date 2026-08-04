"""
Direct-launch handler -- the one deliberate, narrow exception to "Pythia never
launches a game's own executable" (2026-08-03, see pythia/plugins/__init__.py's
module docstring). Applies only to a variant the user has explicitly flagged
`direct: true` with an `executable` path set: a game with no runner/compatibility
layer needed at all, most relevantly a native Windows install that "just runs the
executable directly." This is not the same as untracked -- a direct variant keeps
its normal source/version/update/patch tracking, exactly like any other variant;
"direct" only describes how it launches. No runner selection, no compatibility
flags, no process tracking -- this does exactly one thing: start that one
executable path.

**Deliberately NOT a registered plugin** (2026-08-03 correction): it used to be
(`pythia.manager.direct`, via register_builtin()), which put a fake "Run Directly"
entry in the Plugins page and Managers settings page as if it were a real,
disable-able capability someone chose to install. There's nothing to enable or
disable here -- running a preconfigured path via the OS shell is the simplest
possible operation and isn't a meaningful extension point. This module still
exposes the same `plugin`-shaped object (id/display_name/is_available()/open())
for `pythia.plugins.manager.find_for_game()` to use directly, it's just never
added to the discoverable registry.
"""

import os
import subprocess
import sys

from pythia.plugins import ManagerPlugin


class DirectLaunchManagerPlugin(ManagerPlugin):
    id = "pythia.manager.direct"
    display_name = "Run Directly"
    version = "1.0.0"
    author = "pythia"
    compatible_sources: list = []  # never matched by kind -- see module docstring

    def is_available(self, ctx) -> bool:
        return True

    def open(self, detection: dict, ctx) -> bool:
        executable = (detection.get("extra") or {}).get("executable")
        if not executable or not os.path.exists(executable):
            return False
        try:
            if sys.platform == "win32":
                os.startfile(executable)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", executable])
            else:
                subprocess.Popen([executable])
        except OSError:
            return False
        return True


plugin = DirectLaunchManagerPlugin()  # not register_builtin() -- see module docstring
