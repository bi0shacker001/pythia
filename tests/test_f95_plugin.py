import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "pythia" / "plugin_sources" / "library" / "f95" / "f95_update_check.py"


def load_update_module():
    spec = importlib.util.spec_from_file_location("test_f95_update_check", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class F95UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        self.module = load_update_module()

    def test_fast_check_batches_and_decodes_ids(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            ids = url.split("ids=", 1)[1].split(",")
            return json.dumps({thread_id: int(thread_id) * 10 for thread_id in ids}).encode()

        with mock.patch.object(self.module, "_fetch", fake_fetch):
            result = self.module.fast_check(list(range(1, 13)))

        self.assertEqual(2, len(calls))
        self.assertEqual(120, result[12])

    def test_update_check_persists_available_update(self):
        entry = {
            "system_uuid": "system-a",
            "game_id": "game-a",
            "canonical_name": "Example",
            "platform_ids": [{"platform": "f95", "id": "42"}],
            "versions": [{"version": "1.0", "variants": [{"source": {"kind": "renpy"}}]}],
            "available_updates": [],
        }
        saved = []
        result_data = {
            "version": "2.0",
            "url": "https://f95zone.to/threads/42",
            "downloads": (("Windows", (("Mirror", "https://example.invalid/file"),)),),
            "changelog": "New release",
        }

        os.environ.setdefault("PYTHIA_DATA_DIR", tempfile.gettempdir())
        with (
            mock.patch("pythia.datadir.list_all_entries", return_value=[entry]),
            mock.patch("pythia.datadir.add_timeline_event"),
            mock.patch("pythia.identity.get_system_uuid", return_value="system-a"),
            mock.patch("pythia.database.games.save_entry_and_sync_cache", side_effect=lambda item: saved.append(item)),
            mock.patch.object(self.module, "fast_check", return_value={42: 100}),
            mock.patch.object(self.module, "full_check", return_value=result_data),
        ):
            result = self.module.run_update_check(game_ids=["game-a"])

        self.assertEqual({"checked": 1, "updated": 1, "errors": []}, result)
        self.assertEqual(1, len(saved))
        update = entry["available_updates"][0]
        self.assertEqual("2.0", update["version"])
        self.assertEqual(42, update["thread_id"])
        self.assertEqual("New release", update["changelog"])


if __name__ == "__main__":
    unittest.main()
