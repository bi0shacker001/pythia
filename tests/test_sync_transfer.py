import tempfile
import unittest
from pathlib import Path

from pythia.core.sync.transfer import (
    TransferError,
    build_manifest,
    materialize,
    missing_blocks,
    store_block,
    validate_manifest,
)


class SyncTransferTests(unittest.TestCase):
    def test_round_trip_and_deduplicated_missing_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "source"
            root.mkdir()
            (root / "a.bin").write_bytes(b"same")
            (root / "sub").mkdir()
            (root / "sub" / "b.bin").write_bytes(b"same")
            manifest = build_manifest(root, block_size=4)
            cache = Path(td) / "cache"
            self.assertEqual(len(missing_blocks(manifest, cache)), 1)
            digest = manifest["files"][0]["blocks"][0]
            store_block(cache, digest, b"same")
            result = materialize(manifest, cache, Path(td) / "result")
            self.assertEqual(result, {"status": "ok", "files": 2})
            self.assertEqual((Path(td) / "result" / "sub" / "b.bin").read_bytes(), b"same")

    def test_rejects_traversal_and_bad_block(self):
        manifest = {"version": 1, "block_size": 4, "files": [{"path": "../escape", "size": 4, "blocks": ["0" * 64]}]}
        with self.assertRaises(TransferError):
            validate_manifest(manifest)
        with tempfile.TemporaryDirectory() as td, self.assertRaises(TransferError):
            store_block(td, "0" * 64, b"evil")

    def test_missing_preflight_does_not_create_files(self):
        manifest = {"version": 1, "block_size": 4, "files": [{"path": "game.dat", "size": 4, "blocks": ["0" * 64]}]}
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "out"
            with self.assertRaises(TransferError):
                materialize(manifest, Path(td) / "cache", destination)
            self.assertFalse((destination / "game.dat").exists())


if __name__ == "__main__":
    unittest.main()
