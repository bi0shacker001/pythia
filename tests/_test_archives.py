"""Adding a downloaded archive (Droidtop/tracker#538: a watched .zip could
not be added)."""

import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase

from pythia import archives


class UnpackTest(TestCase):
    def test_a_zip_unpacks_beside_itself_and_its_one_folder_is_the_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            zp = Path(tmp) / "Lost Game-1.2-pc.zip"
            with zipfile.ZipFile(zp, "w") as zf:
                zf.writestr("Lost Game-1.2-pc/game/script.rpy", "label start:\n")
                zf.writestr("Lost Game-1.2-pc/renpy/__init__.py", "")
            self.assertTrue(archives.is_archive(zp))
            folder = archives.unpack(zp)
            self.assertEqual(folder, Path(tmp) / "Lost Game-1.2-pc" / "Lost Game-1.2-pc")
            self.assertTrue((folder / "game" / "script.rpy").is_file())

    def test_loose_files_unpack_into_the_folder_named_after_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            zp = Path(tmp) / "Tiny.zip"
            with zipfile.ZipFile(zp, "w") as zf:
                zf.writestr("start.exe", "")
                zf.writestr("data/a.bin", "")
            self.assertEqual(archives.unpack(zp), Path(tmp) / "Tiny")

    def test_a_member_outside_the_folder_is_refused_and_nothing_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            zp = Path(tmp) / "Bad.zip"
            with zipfile.ZipFile(zp, "w") as zf:
                zf.writestr("../escaped.txt", "x")
            with self.assertRaisesRegex(archives.ArchiveError, "outside"):
                archives.unpack(zp)
            self.assertFalse((Path(tmp) / "escaped.txt").exists())

    def test_an_existing_folder_is_never_written_into(self):
        with tempfile.TemporaryDirectory() as tmp:
            zp = Path(tmp) / "Game.zip"
            with zipfile.ZipFile(zp, "w") as zf:
                zf.writestr("a.txt", "x")
            (Path(tmp) / "Game").mkdir()
            with self.assertRaisesRegex(archives.ArchiveError, "already exists"):
                archives.unpack(zp)

    def test_a_folder_is_not_an_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(archives.is_archive(Path(tmp)))
