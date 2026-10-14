"""Real local-HTTP-fixture tests for the optional Download Automator plugin
(2026-08-08 hardening pass) -- never a live host or a real premium account,
per the task's own requirement. A real `http.server.HTTPServer` on
127.0.0.1 serves each scenario (redirect, range/no-range resume, truncated
content, checksum mismatch); the plugin talks to it over real HTTP.
"""

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = REPO_ROOT / "optional_plugin_sources" / "download_automator" / "plugin.py"


def _load_plugin_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("test_download_automator_plugin", PLUGIN_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeServer:
    """A real local HTTP server, controlled per-test via a `responder`
    callable so each scenario (redirect, range support, truncated body,
    ...) is expressed as plain Python instead of a fixed fixture file."""

    def __init__(self, responder):
        self.responder = responder
        self.requests: list[dict] = []
        self._server = HTTPServer(("127.0.0.1", 0), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append({"path": self.path, "headers": dict(self.headers)})
                outer.responder(self)

            def log_message(self, *args):
                pass

        return Handler

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/file.bin"

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class DownloadAutomatorPluginTests(unittest.TestCase):
    def setUp(self):
        # Isolation is mandatory here, not optional: set_host_cookies() below
        # calls pythia.credentials.set_credential(), which resolves its
        # storage path via pythia.identity.datadir_path() -> app_data_dir() --
        # real %APPDATA%/Pythia on this machine if PYTHIA_DATA_DIR isn't
        # overridden. An earlier version of this file wrote real (fake, but
        # real-looking) credential files into the actual user's real Pythia
        # app-data directory; caught and cleaned up by hand once, fixed here
        # so it can't happen again.
        self._data_dir = tempfile.TemporaryDirectory()
        self._prior_data_dir = os.environ.get("PYTHIA_DATA_DIR")
        os.environ["PYTHIA_DATA_DIR"] = self._data_dir.name

        self.module = _load_plugin_module()
        self.plugin = self.module.DownloadAutomatorPlugin()
        self._tmp = tempfile.TemporaryDirectory()
        self._servers: list[_FakeServer] = []

    def tearDown(self):
        for server in self._servers:
            server.stop()
        self._tmp.cleanup()
        if self._prior_data_dir is None:
            os.environ.pop("PYTHIA_DATA_DIR", None)
        else:
            os.environ["PYTHIA_DATA_DIR"] = self._prior_data_dir
        self._data_dir.cleanup()

    def _start(self, responder) -> _FakeServer:
        server = _FakeServer(responder)
        self._servers.append(server)
        return server

    def test_identify_host_rejects_non_http(self):
        result = self.plugin.identify_host("ftp://example.invalid/file.bin")
        self.assertFalse(result["supported"])

    def test_identify_host_rejects_invalid_port_and_embedded_credentials(self):
        invalid_port = self.plugin.prepare_download("https://example.invalid:not-a-port/file.bin")
        self.assertEqual("unsupported", invalid_port["status"])
        self.assertIn("invalid host or port", invalid_port["reason"])

        embedded = self.plugin.prepare_download("https://user:secret@example.invalid/file.bin")
        self.assertEqual("unsupported", embedded["status"])
        self.assertIn("host sign-in flow", embedded["reason"])
        self.assertNotIn("secret", json.dumps(embedded))

    def test_full_download_succeeds_and_reports_real_total(self):
        body = b"x" * 5000

        def responder(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        server = self._start(responder)
        plan = self.plugin.prepare_download(server.url, "game.bin")
        received_events = []
        result = self.plugin.download(plan, self._tmp.name, progress=lambda r, t: received_events.append((r, t)))
        self.assertEqual("ok", result["status"])
        self.assertEqual(len(body), result["total"])
        self.assertEqual(len(body), result["received"])
        self.assertTrue(Path(result["path"]).is_file())
        self.assertEqual(body, Path(result["path"]).read_bytes())
        self.assertTrue(received_events)  # real progress callbacks fired

    def test_range_resume_appends_only_missing_bytes(self):
        full_body = b"A" * 2000 + b"B" * 2000
        already_have = full_body[:2000]

        def responder(handler):
            range_header = handler.headers.get("Range")
            if range_header == "bytes=2000-":
                remainder = full_body[2000:]
                handler.send_response(206)
                handler.send_header("Content-Range", f"bytes 2000-{len(full_body)-1}/{len(full_body)}")
                handler.send_header("Content-Length", str(len(remainder)))
                handler.end_headers()
                handler.wfile.write(remainder)
            else:
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(full_body)))
                handler.end_headers()
                handler.wfile.write(full_body)

        server = self._start(responder)
        plan = self.plugin.prepare_download(server.url, "game.bin")
        target = Path(self._tmp.name) / plan["filename"]
        partial = target.with_suffix(target.suffix + ".part")
        partial.write_bytes(already_have)
        partial.with_suffix(partial.suffix + ".json").write_text(
            json.dumps({"url_sha256": self.module._url_fingerprint(server.url)})
        )

        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("ok", result["status"])
        self.assertEqual(full_body, target.read_bytes())
        # Only the missing 2000 bytes were actually requested with a range.
        self.assertIn("bytes=2000-", [r["headers"].get("Range") for r in server.requests])

    def test_missing_range_support_falls_back_to_full_redownload(self):
        """Server ignores Range and always returns 200 + the full body --
        the plugin must fall back to a clean full download, not silently
        append the new full body onto the stale partial bytes."""
        full_body = b"C" * 3000
        stale_partial = b"WRONG-STALE-DATA"

        def responder(handler):
            handler.send_response(200)  # never honors Range
            handler.send_header("Content-Length", str(len(full_body)))
            handler.end_headers()
            handler.wfile.write(full_body)

        server = self._start(responder)
        plan = self.plugin.prepare_download(server.url, "game.bin")
        target = Path(self._tmp.name) / plan["filename"]
        partial = target.with_suffix(target.suffix + ".part")
        partial.write_bytes(stale_partial)
        partial.with_suffix(partial.suffix + ".json").write_text(
            json.dumps({"url_sha256": self.module._url_fingerprint(server.url)})
        )

        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("ok", result["status"])
        self.assertEqual(full_body, target.read_bytes())  # not stale_partial + full_body

    def test_cancellation_stops_mid_transfer_and_leaves_partial(self):
        chunk = b"D" * 70000  # bigger than the plugin's 64KiB read chunk

        def responder(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(chunk) * 3))
            handler.end_headers()
            for _ in range(3):
                handler.wfile.write(chunk)
                handler.wfile.flush()

        server = self._start(responder)
        plan = self.plugin.prepare_download(server.url, "game.bin")
        cancel_event = threading.Event()

        def progress(received, total):
            if received >= len(chunk):
                cancel_event.set()

        result = self.plugin.download(plan, self._tmp.name, progress=progress, cancel_event=cancel_event)
        self.assertEqual("cancelled", result["status"])
        target = Path(self._tmp.name) / plan["filename"]
        self.assertFalse(target.is_file())  # never promoted to the final visible file
        self.assertTrue(Path(result["path"]).is_file())  # the .part remains, for a future resume

    def test_html_response_reports_interactive_required(self):
        def responder(handler):
            body = b"<html>please log in</html>"
            handler.send_response(200)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        server = self._start(responder)
        plan = self.plugin.prepare_download(server.url, "game.bin")
        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("interactive_required", result["status"])

    def test_connection_failure_reports_error_without_leaking_headers(self):
        """2026-08-08 redaction requirement: the error message must never
        contain the Cookie header's secret value."""
        secret = "super-secret-session-token"
        self.plugin.set_host_cookies("127.0.0.1", {"session": secret})
        plan = self.plugin.prepare_download("http://127.0.0.1:1/file.bin", "game.bin")  # nothing listens on port 1
        self.assertIn(secret, plan["headers"]["Cookie"])  # confirms the secret really is in the outgoing plan

        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("error", result["status"])
        self.assertNotIn(secret, json.dumps(result))

    def test_resume_metadata_fingerprints_signed_url_instead_of_persisting_it(self):
        secret = "premium-query-token"
        body = b"downloaded"

        def responder(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body) + 1))
            handler.end_headers()
            handler.wfile.write(body)
            handler.close_connection = True

        server = self._start(responder)
        plan = self.plugin.prepare_download(f"{server.url}?access_token={secret}", "signed.bin")
        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("error", result["status"])
        metadata = (Path(self._tmp.name) / "signed.bin.part.json").read_text(encoding="utf-8")
        self.assertNotIn(secret, metadata)
        self.assertNotIn(plan["url"], metadata)
        self.assertEqual(self.module._url_fingerprint(plan["url"]), json.loads(metadata)["url_sha256"])

    def test_checksum_match_succeeds(self):
        body = b"E" * 1000
        digest = hashlib.sha256(body).hexdigest()

        def responder(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        server = self._start(responder)
        plan = self.plugin.prepare_download(server.url, "game.bin")
        plan["sha256"] = digest
        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("ok", result["status"])

    def test_checksum_mismatch_never_promotes_to_final_file(self):
        body = b"F" * 1000

        def responder(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        server = self._start(responder)
        plan = self.plugin.prepare_download(server.url, "game.bin")
        plan["sha256"] = "0" * 64  # deliberately wrong
        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("checksum_mismatch", result["status"])
        target = Path(self._tmp.name) / plan["filename"]
        self.assertFalse(target.is_file())  # a corrupt/mismatched file must never become the visible final artifact

    def test_cookies_make_a_premium_session(self):
        self.plugin.set_host_cookies("example.invalid", {"auth": "abc123"})
        plan = self.plugin.prepare_download("https://example.invalid/file.bin", "x.bin")
        self.assertTrue(plan["premium_session"])
        self.assertIn("Cookie", plan["headers"])

    def test_no_cookies_means_no_premium_session(self):
        plan = self.plugin.prepare_download("https://another-example.invalid/file.bin", "x.bin")
        self.assertFalse(plan["premium_session"])
        self.assertNotIn("Cookie", plan["headers"])

    def test_malformed_synced_cookies_are_filtered_before_header_construction(self):
        self.plugin.set_host_cookies(
            "example.invalid",
            {
                "valid_session": "abc123",
                "bad\r\nheader": "injected",
                "bad_value": "secret\r\nX-Evil: yes",
                "unicode": "snowman-☃",
                "non_string": 42,
            },
        )
        plan = self.plugin.prepare_download("https://example.invalid/file.bin", "x.bin")
        self.assertEqual("valid_session=abc123", plan["headers"]["Cookie"])
        self.assertTrue(plan["premium_session"])

    def test_declared_oversized_download_is_rejected_before_body_read(self):
        def responder(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(self.module.MAX_DOWNLOAD_BYTES + 1))
            handler.end_headers()

        server = self._start(responder)
        result = self.plugin.download(self.plugin.prepare_download(server.url, "huge.bin"), self._tmp.name)
        self.assertEqual("error", result["status"])
        self.assertIn("safety limit", result["message"])

    def test_truncated_response_never_promotes_final_file(self):
        body = b"short"

        def responder(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body) + 100))
            handler.end_headers()
            handler.wfile.write(body)
            handler.close_connection = True

        server = self._start(responder)
        plan = self.plugin.prepare_download(server.url, "truncated.bin")
        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("error", result["status"])
        self.assertFalse((Path(self._tmp.name) / "truncated.bin").exists())

    def test_cross_host_redirect_strips_credentials(self):
        body = b"safe"

        def destination(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        target = self._start(destination)

        def redirect(handler):
            handler.send_response(302)
            handler.send_header("Location", target.url.replace("127.0.0.1", "localhost"))
            handler.end_headers()

        source = self._start(redirect)
        self.plugin.set_host_cookies("127.0.0.1", {"premium": "secret"})
        plan = self.plugin.prepare_download(source.url, "redirect.bin")
        plan["headers"]["Authorization"] = "Bearer future-adapter-token"
        plan["headers"]["Proxy-Authorization"] = "Basic proxy-token"
        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("ok", result["status"])
        redirected_headers = target.requests[0]["headers"]
        self.assertNotIn("Cookie", redirected_headers)
        self.assertNotIn("Authorization", redirected_headers)
        self.assertNotIn("Proxy-Authorization", redirected_headers)

    def test_same_host_different_port_strips_authorization_but_preserves_cookie(self):
        body = b"safe"

        def destination(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        target = self._start(destination)

        def redirect(handler):
            handler.send_response(302)
            handler.send_header("Location", target.url)
            handler.end_headers()

        source = self._start(redirect)
        self.plugin.set_host_cookies("127.0.0.1", {"premium": "secret"})
        plan = self.plugin.prepare_download(source.url, "redirect-port.bin")
        plan["headers"]["Authorization"] = "Bearer origin-token"
        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("ok", result["status"])
        redirected_headers = target.requests[0]["headers"]
        self.assertIn("Cookie", redirected_headers)
        self.assertNotIn("Authorization", redirected_headers)

    def test_existing_completed_download_requires_review(self):
        target = Path(self._tmp.name) / "game.bin"
        target.write_bytes(b"keep")
        plan = self.plugin.prepare_download("https://example.invalid/game.bin", "game.bin")
        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("needs_review", result["status"])
        self.assertEqual(b"keep", target.read_bytes())

    def test_partial_from_different_url_is_not_resumed(self):
        body = b"fresh"

        def responder(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        server = self._start(responder)
        plan = self.plugin.prepare_download(server.url, "game.bin")
        partial = Path(self._tmp.name) / "game.bin.part"
        partial.write_bytes(b"stale")
        partial.with_suffix(".part.json").write_text(json.dumps({"url": "https://other.invalid/file"}))
        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("ok", result["status"])
        self.assertEqual(body, Path(result["path"]).read_bytes())
        self.assertNotIn("Range", server.requests[0]["headers"])

    def test_resume_rejects_wrong_content_range(self):
        def responder(handler):
            handler.send_response(206)
            handler.send_header("Content-Range", "bytes 0-3/4")
            handler.send_header("Content-Length", "4")
            handler.end_headers()
            handler.wfile.write(b"data")

        server = self._start(responder)
        plan = self.plugin.prepare_download(server.url, "game.bin")
        partial = Path(self._tmp.name) / "game.bin.part"
        partial.write_bytes(b"old")
        partial.with_suffix(".part.json").write_text(
            json.dumps({"url_sha256": self.module._url_fingerprint(server.url)})
        )
        result = self.plugin.download(plan, self._tmp.name)
        self.assertEqual("error", result["status"])
        self.assertIn("resume range", result["message"])


class SafeExtractionHandoffTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_plugin_module()
        self.plugin = self.module.DownloadAutomatorPlugin()
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def _make_zip(self, name="archive.zip", members=None):
        import zipfile

        path = Path(self._tmp.name) / name
        with zipfile.ZipFile(path, "w") as zf:
            for member_name, content in (members or {"game.exe": b"binary"}).items():
                zf.writestr(member_name, content)
        return path

    def test_extracts_into_empty_destination(self):
        archive = self._make_zip()
        destination = Path(self._tmp.name) / "extracted"
        result = self.plugin.extract_download(str(archive), str(destination))
        self.assertEqual("ok", result["status"])
        self.assertTrue((destination / "game.exe").is_file())

    def test_refuses_to_overwrite_non_empty_destination(self):
        archive = self._make_zip()
        destination = Path(self._tmp.name) / "existing"
        destination.mkdir()
        (destination / "already_here.txt").write_text("keep me")
        result = self.plugin.extract_download(str(archive), str(destination))
        self.assertEqual("needs_review", result["status"])
        # The pre-existing file must be untouched.
        self.assertEqual("keep me", (destination / "already_here.txt").read_text())
        self.assertFalse((destination / "game.exe").is_file())

    def test_rejects_traversal_archive(self):
        import zipfile

        archive = Path(self._tmp.name) / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../../evil.txt", b"pwned")
        destination = Path(self._tmp.name) / "safe_out"
        result = self.plugin.extract_download(str(archive), str(destination))
        self.assertEqual("error", result["status"])
        self.assertFalse((Path(self._tmp.name).parent / "evil.txt").exists())
        self.assertEqual([], list(Path(self._tmp.name).glob(".*-extract-*")))

    def test_rejects_duplicate_and_case_colliding_members(self):
        import zipfile

        for members in (
            (("game.exe", b"first"), ("game.exe", b"second")),
            (("Folder/Game.exe", b"first"), ("folder/game.EXE", b"second")),
        ):
            with self.subTest(members=[name for name, _ in members]):
                archive = Path(self._tmp.name) / f"collision-{len(list(Path(self._tmp.name).glob('collision-*')))}.zip"
                with zipfile.ZipFile(archive, "w") as zf:
                    for name, content in members:
                        zf.writestr(name, content)
                destination = archive.with_suffix("")
                result = self.plugin.extract_download(str(archive), str(destination))
                self.assertEqual("error", result["status"])
                self.assertIn("duplicate destination paths", result["message"])
                self.assertFalse(destination.exists())
                self.assertEqual([], list(Path(self._tmp.name).glob(".*-extract-*")))

    def test_successful_extraction_atomically_promotes_and_cleans_staging(self):
        archive = self._make_zip(members={"folder/game.exe": b"binary", "readme.txt": b"hello"})
        destination = Path(self._tmp.name) / "installed"
        result = self.plugin.extract_download(str(archive), str(destination))
        self.assertEqual("ok", result["status"])
        self.assertEqual(b"binary", (destination / "folder" / "game.exe").read_bytes())
        self.assertEqual([], list(Path(self._tmp.name).glob(".*-extract-*")))


if __name__ == "__main__":
    unittest.main()
