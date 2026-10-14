import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

_APPDATA = tempfile.TemporaryDirectory()
os.environ["APPDATA"] = _APPDATA.name

from pythia.plugins import packaging, remote_index


class PluginArchiveSecurityTests(unittest.TestCase):
    def _archive(self, entries, manifest=None):
        handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        handle.close()
        path = Path(handle.name)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("plugin.json", json.dumps(manifest or {"id": "test.safe", "categories": ["library"]}))
            archive.writestr("plugin.py", "PLUGIN_CLASSES = []")
            for name, data in entries:
                archive.writestr(name, data)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_rejects_parent_traversal(self):
        path = self._archive([("../escape.py", "")])
        with self.assertRaisesRegex(packaging.PluginPackageError, "Unsafe path"):
            packaging.read_manifest(path)

    def test_rejects_windows_absolute_path(self):
        path = self._archive([("C:\\escape.py", "")])
        with self.assertRaisesRegex(packaging.PluginPackageError, "Unsafe path"):
            packaging.read_manifest(path)

    def test_rejects_case_insensitive_duplicate_paths(self):
        path = self._archive([("Helper.py", "a"), ("helper.py", "b")])
        with self.assertRaisesRegex(packaging.PluginPackageError, "duplicate"):
            packaging.read_manifest(path)

    def test_rejects_oversized_member_before_reading_it(self):
        path = self._archive([("large.bin", b"x" * (packaging.MAX_ARCHIVE_MEMBER + 1))])
        with self.assertRaisesRegex(packaging.PluginPackageError, "too large"):
            packaging.read_manifest(path)

    def test_remote_plugin_transport_requires_https(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            remote_index._require_https("http://example.invalid/plugins/index.json")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            remote_index._require_https("file:///tmp/index.json")
        self.assertEqual(
            "https://example.invalid/plugins/index.json",
            remote_index._require_https("https://example.invalid/plugins/index.json"),
        )

    def test_index_write_failure_preserves_existing_index(self):
        with tempfile.TemporaryDirectory() as root:
            index_path = Path(root) / "index.json"
            index_path.write_text('{"old": true}', encoding="utf-8")
            with mock.patch("pythia.plugins.packaging._index_path", return_value=index_path), \
                    mock.patch.object(Path, "replace", side_effect=OSError("disk failure")):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    packaging._write_index({"new": True})
            self.assertEqual('{"old": true}', index_path.read_text(encoding="utf-8"))
            self.assertEqual([], list(Path(root).glob(".index-*.tmp")))

    def test_rejects_noncanonical_plugin_id_before_path_sanitization(self):
        path = self._archive([], {"id": "../Same/Name", "categories": ["library"]})
        with self.assertRaises(packaging.PluginPackageError):
            packaging.read_manifest(path)

    def test_rejects_malformed_categories(self):
        path = self._archive([], {"id": "test.safe", "categories": "library"})
        with self.assertRaises(packaging.PluginPackageError):
            packaging.read_manifest(path)

    def test_uninstall_api_itself_protects_builtins(self):
        with mock.patch("pythia.plugins.registry.is_protected", return_value=True), \
                mock.patch("pythia.plugins.packaging._read_index") as read_index:
            result = packaging.uninstall_plugin("pythia.engine.renpy")
        self.assertEqual("error", result["status"])
        read_index.assert_not_called()

    def test_remote_entry_rejects_path_traversal_id_before_file_creation(self):
        result = remote_index.install_from_index(
            {"id": "../../escape", "download": "plugin.zip", "sha256": "0" * 64}
        )
        self.assertEqual("error", result["status"])
        self.assertIn("invalid plugin id", result["message"])

    def test_remote_entry_requires_exact_sha256(self):
        for digest in ("", "abc", "g" * 64, "0" * 65):
            with self.subTest(digest=digest):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    remote_index._validate_entry(
                        {"id": "test.safe", "download": "plugin.zip", "sha256": digest}
                    )


if __name__ == "__main__":
    unittest.main()
