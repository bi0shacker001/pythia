"""Scan decisions waiting survive a restart (UX review 2026-10-16, A16)."""

import tempfile
from pathlib import Path
from unittest import TestCase, mock

from pythia import intake_store


class IntakeStoreTest(TestCase):
    def test_decisions_are_kept_and_read_back_without_gone_folders(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(intake_store.settings, "DATA_DIR", tmp):
            here = Path(tmp) / "UX Test Novel v1.0"
            here.mkdir()
            intake_store.save([{"path": str(here), "name": "UX Test Novel"}, {"path": str(Path(tmp) / "gone")}])
            self.assertEqual(intake_store.load(), [{"path": str(here), "name": "UX Test Novel"}])

    def test_nothing_kept_or_a_damaged_file_is_an_empty_queue(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(intake_store.settings, "DATA_DIR", tmp):
            self.assertEqual(intake_store.load(), [])
            (Path(tmp) / intake_store.FILE_NAME).write_text("{not json")
            self.assertEqual(intake_store.load(), [])
