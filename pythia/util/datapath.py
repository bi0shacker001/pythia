"""Utility to get the path of Pythia assets"""

# Standard Library
import os
import sys

# Pythia Modules
from pythia.util import system


def get() -> str:
    """Return the path for the resources."""
    launch_path = os.path.realpath(sys.path[0])
    if launch_path.startswith("/usr/local") and os.path.isdir("/usr/local/share/pythia"):
        data_path = "/usr/local/share/pythia"
    elif launch_path.startswith("/usr") and os.path.isdir("/usr/share/pythia"):
        data_path = "/usr/share/pythia"
    elif system.path_exists(os.path.normpath(os.path.join(sys.path[0], "share"))):
        data_path = os.path.normpath(os.path.join(sys.path[0], "share/pythia"))
    elif system.path_exists(os.path.normpath(os.path.join(launch_path, "../../share/pythia"))):
        data_path = os.path.normpath(os.path.join(launch_path, "../../share/pythia"))
    elif system.path_exists(os.path.join(sys.prefix, "share", "pythia")):
        # An installed package (pip, a venv): its data files land under the prefix.
        data_path = os.path.join(sys.prefix, "share", "pythia")
    else:
        import pythia

        pythia_module = pythia.__file__
        data_path = os.path.join(os.path.dirname(os.path.dirname(pythia_module)), "share/pythia")
    if not system.path_exists(data_path):
        raise IOError("data_path can't be found at : %s" % data_path)
    return data_path
