import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from pythia.util.safe_archive import UnsafeArchiveError, safe_extract_zip


def _zip_bytes(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files:
            zf.writestr(name, content)
    out.seek(0)
    return out


class SafeArchiveTests(unittest.TestCase):
    def test_extracts_regular_nested_files(self):
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(_zip_bytes([("folder/tool.txt", b"ok")])) as zf:
            files = safe_extract_zip(zf, tmp)
            self.assertEqual(b"ok", (Path(tmp) / "folder" / "tool.txt").read_bytes())
            self.assertEqual([Path(tmp).resolve() / "folder" / "tool.txt"], files)

    def test_rejects_traversal_before_writing(self):
        for name in ("../escape", "folder/../../escape", "/absolute", "C:/drive", "..\\escape"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(_zip_bytes([("safe", b"partial"), (name, b"bad")])) as zf:
                with self.assertRaises(UnsafeArchiveError):
                    safe_extract_zip(zf, tmp)
                self.assertEqual([], list(Path(tmp).iterdir()))

    def test_enforces_bounded_expansion(self):
        cases = (({"max_members": 1}, [("a", b"1"), ("b", b"2")]), ({"max_total_size": 2}, [("a", b"123")]), ({"max_member_size": 2}, [("a", b"123")]), ({"max_compression_ratio": 2}, [("a", b"0" * 1000)]))
        for limits, files in cases:
            with self.subTest(limits=limits), tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(_zip_bytes(files)) as zf:
                with self.assertRaises(UnsafeArchiveError):
                    safe_extract_zip(zf, tmp, **limits)

    def test_rejects_symbolic_links(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "target")
        out.seek(0)
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(out) as zf:
            with self.assertRaises(UnsafeArchiveError):
                safe_extract_zip(zf, tmp)


if __name__ == "__main__":
    unittest.main()
