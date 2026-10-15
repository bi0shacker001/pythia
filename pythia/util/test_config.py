import os

try:  # GTK 3 pinned before anything loads GTK 4; nothing to pin without PyGObject (Windows)
    import gi

    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
except (ImportError, ValueError):
    pass

from pythia.database import schema
from pythia.startup import init_pythia


def setup_test_environment():
    """Sets up a system to be able to run tests"""
    os.environ["PYTHIA_SKIP_INIT"] = "1"
    schema.syncdb()
    init_pythia()
