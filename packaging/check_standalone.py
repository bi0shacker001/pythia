"""Run by CI inside a standalone build (``<program> --pythia-script packaging/check_standalone.py``): a copy
of Pythia started the way Pythia starts one (pythia.util.selfcmd) finds its data, its compiled droidtop
agent and, on Windows, the secure-screen helper."""

import os
import sys

import pythia._native as native
from pythia.util import datapath

data = datapath.get()
assert os.path.isfile(os.path.join(data, "bin", "pythia-wrapper")), data
if sys.platform == "win32":
    from pythia import secure_screen

    assert secure_screen.HELPER.is_file(), secure_screen.HELPER
# The droidtop-platforms data pythia.platform_systems reads is in the build: without it no platform maps to a
# droidtop system, and one of its own aliases resolves.
from pythia import platform_systems

assert platform_systems.PINNED.is_file(), platform_systems.PINNED
assert platform_systems.system_of("Super Nintendo Entertainment System")["id"] == "snes", "the aliases are missing"
assert platform_systems.system_of("Sony PlayStation 2")["id"] == "ps2"
print("pythia._native", native.__version__, "data", data, "platforms", len(platform_systems.load()))
