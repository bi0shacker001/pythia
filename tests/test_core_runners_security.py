import io
import gzip
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pythia.core import runners
from pythia.core.runners import (
    _HTTPSOnlyRedirectHandler,
    _promote_file_no_replace,
    _safe_extract_gzip,
    _safe_extract_tar,
    _tool_download_filename,
)


def _tar(entries):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name, content in entries:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    raw.seek(0)
    return tarfile.open(fileobj=raw, mode="r"), raw


class CoreRunnerArchiveSecurityTests(unittest.TestCase):
    def test_extracts_regular_member(self):
        archive, raw = _tar((("runner/bin/tool", b"ok"),))
        with archive, raw, tempfile.TemporaryDirectory() as tmp:
            _safe_extract_tar(archive, Path(tmp))
            self.assertEqual(b"ok", (Path(tmp) / "runner" / "bin" / "tool").read_bytes())

    def test_rejects_traversal_before_writing(self):
        archive, raw = _tar((("safe", b"partial"), ("../escape", b"bad")))
        with archive, raw, tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises((tarfile.TarError, tarfile.FilterError)):
                _safe_extract_tar(archive, Path(tmp))
            self.assertEqual([], list(Path(tmp).iterdir()))

    def test_rejects_case_collisions_before_writing(self):
        archive, raw = _tar((("Runner/Tool", b"first"), ("runner/tool", b"second")))
        with archive, raw, tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(tarfile.TarError):
                _safe_extract_tar(archive, Path(tmp))
            self.assertEqual([], list(Path(tmp).iterdir()))

    def test_enforces_member_and_expansion_limits(self):
        cases = (
            ({"max_members": 1}, (("a", b"1"), ("b", b"2"))),
            ({"max_member_size": 1}, (("a", b"12"),)),
            ({"max_total_size": 1}, (("a", b"1"), ("b", b"1"))),
        )
        for limits, entries in cases:
            with self.subTest(limits=limits):
                archive, raw = _tar(entries)
                with archive, raw, tempfile.TemporaryDirectory() as tmp:
                    with self.assertRaises(tarfile.TarError):
                        _safe_extract_tar(archive, Path(tmp), **limits)
                    self.assertEqual([], list(Path(tmp).iterdir()))

    def test_gzip_limit_leaves_no_partial_or_final_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "tool.gz"
            with gzip.open(archive, "wb") as output:
                output.write(b"expanded")
            target = Path(tmp) / "tool"
            with self.assertRaises(ValueError):
                _safe_extract_gzip(archive, target, max_size=2)
            self.assertFalse(target.exists())
            self.assertFalse((Path(tmp) / ".tool.part").exists())

    def test_tool_download_limit_removes_partial_file(self):
        class Response:
            headers = {"Content-Length": "4"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                if getattr(self, "done", False):
                    return b""
                self.done = True
                return b"data"

        opener = mock.Mock()
        opener.open.return_value = Response()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runners, "MAX_TOOL_DOWNLOAD_BYTES", 2), mock.patch.object(
            runners, "_cache_dir", return_value=Path(tmp)
        ), mock.patch.object(runners.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                runners.download_and_install("https://example.invalid/tool.bin", Path(tmp) / "final.bin")
            self.assertFalse((Path(tmp) / "tool.bin.part").exists())
            self.assertFalse((Path(tmp) / "tool.bin").exists())

    def test_archive_failure_never_exposes_partial_install_directory(self):
        class Response:
            headers = {"Content-Length": "4"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                if getattr(self, "done", False):
                    return b""
                self.done = True
                return b"data"

        def fail_after_partial(_archive, destination, **_kwargs):
            (destination / "partial-tool").write_bytes(b"partial")
            raise ValueError("corrupt archive")

        opener = mock.Mock()
        opener.open.return_value = Response()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            runners, "_cache_dir", return_value=Path(tmp) / "cache"
        ), mock.patch.object(runners.urllib.request, "build_opener", return_value=opener), mock.patch.object(
            runners, "_extract_archive", side_effect=fail_after_partial
        ):
            (Path(tmp) / "cache").mkdir()
            destination = Path(tmp) / "tools" / "runner" / "1.0"
            with self.assertRaisesRegex(ValueError, "corrupt archive"):
                runners.download_and_install("https://example.invalid/tool.tar", destination, is_archive=True)
            self.assertFalse(destination.exists())
            self.assertEqual([], list(destination.parent.glob(".1.0-install-*")))

    def test_tool_urls_require_https_and_safe_filename(self):
        for url in (
            "http://example.invalid/tool.zip",
            "https://user:secret@example.invalid/tool.zip",
            "https://example.invalid/",
            "https://example.invalid/%2e%2e",
            "https://example.invalid/bad%5cname.zip",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _tool_download_filename(url)
        self.assertEqual("tool.tar.gz", _tool_download_filename("https://example.invalid/releases/tool.tar.gz?download=1"))

    def test_tool_redirect_rejects_https_downgrade(self):
        request = runners.urllib.request.Request("https://example.invalid/tool.zip")
        with self.assertRaises(runners.urllib.error.HTTPError):
            _HTTPSOnlyRedirectHandler().redirect_request(
                request, None, 302, "Found", {}, "http://example.invalid/tool.zip"
            )

    def test_runner_catalog_is_bounded_and_schema_filtered(self):
        class Response:
            def __init__(self, payload, declared=None):
                self.payload = payload
                self.headers = {"Content-Length": str(len(payload) if declared is None else declared)}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                return self.payload[:size]

        payload = b'{"versions":[{"version":"1","architecture":"x86_64","url":"https://example.invalid/tool"},"bad",{"version":2}]}'
        opener = mock.Mock()
        opener.open.return_value = Response(payload)
        with mock.patch.object(runners.urllib.request, "build_opener", return_value=opener):
            result = runners.fetch_lutris_catalog("wine")
        self.assertEqual(1, len(result))
        self.assertEqual("1", result[0]["version"])

        opener.open.return_value = Response(b"{}", declared=runners.MAX_RUNNER_CATALOG_BYTES + 1)
        with mock.patch.object(runners.urllib.request, "build_opener", return_value=opener):
            self.assertEqual([], runners.fetch_lutris_catalog("wine"))
        self.assertEqual([], runners.fetch_lutris_catalog("../escape"))

    def test_direct_binary_promotion_is_atomic_and_never_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "cache.bin"
            target = root / "tools" / "tool.bin"
            source.write_bytes(b"new")
            _promote_file_no_replace(source, target)
            self.assertEqual(b"new", target.read_bytes())
            self.assertFalse(source.exists())
            self.assertEqual([], list(target.parent.glob(".tool.bin-install-*")))

            second = root / "second-cache.bin"
            second.write_bytes(b"replacement")
            with self.assertRaises(FileExistsError):
                _promote_file_no_replace(second, target)
            self.assertEqual(b"new", target.read_bytes())
            self.assertTrue(second.exists())
            self.assertEqual([], list(target.parent.glob(".tool.bin-install-*")))


if __name__ == "__main__":
    unittest.main()
