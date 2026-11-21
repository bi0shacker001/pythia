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
print("pythia._native", native.__version__, "data", data)
