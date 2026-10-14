import os
import tempfile
import unittest
import zipfile
from pathlib import Path

_APPDATA = tempfile.TemporaryDirectory()
os.environ["APPDATA"] = _APPDATA.name

from pythia.plugins import packaging


ROOT = Path(__file__).resolve().parent.parent


def tearDownModule():
    # Importing plugin packaging can initialize Pythia's rotating file handler
    # inside this module's temporary APPDATA. Close that owned handler before
    # deleting the directory; otherwise Windows reports a locked pythia.log at
    # interpreter shutdown even though every assertion passed.
    try:
        from pythia.util.log import LOG_FILENAME, shutdown_logging
    except ImportError:
        LOG_FILENAME = None
    if LOG_FILENAME and Path(LOG_FILENAME).is_relative_to(Path(_APPDATA.name)):
        shutdown_logging()
    _APPDATA.cleanup()


class PluginArtifactIntegrityTests(unittest.TestCase):
    def packages(self):
        yield from sorted((ROOT / "pythia" / "plugins" / "builtin").glob("*.zip"))
        yield from sorted((ROOT / "packaging" / "optional-plugins").glob("*.zip"))

    def test_every_runtime_package_is_clean_and_structurally_complete(self):
        packages = list(self.packages())
        self.assertTrue(packages)
        for path in packages:
            with self.subTest(package=path.name):
                manifest = packaging.read_manifest(path)
                self.assertEqual(path.stem, manifest["id"])
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                    self.assertFalse(any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names))
                    self.assertFalse(any(name.startswith("/") or "\\" in name for name in names))
                    plugin_source = archive.read("plugin.py")
                    self.assertNotIn(b"plugin_sources", plugin_source)
                for category in manifest["categories"]:
                    self.assertIsNotNone(packaging.load_type_config_schema(path, manifest["id"], category))
                self.assertTrue(packaging.declared_os_support(path))

    def test_download_automator_carries_extraction_helper_inside_zip(self):
        path = ROOT / "packaging" / "optional-plugins" / "pythia.downloader.automator.zip"
        with zipfile.ZipFile(path) as archive:
            self.assertIn("safe_archive.py", archive.namelist())
            self.assertNotIn(b"from pythia.util.safe_archive", archive.read("plugin.py"))


if __name__ == "__main__":
    unittest.main()
