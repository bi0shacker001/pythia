import logging
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from pythia.util import log


class LoggingFallbackTests(unittest.TestCase):
    def test_writable_preferred_directory_gets_real_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            handler, filename = log._create_file_handler(Path(directory) / "logs")
            try:
                self.assertIsInstance(handler, logging.handlers.RotatingFileHandler)
                self.assertEqual(Path(directory) / "logs" / "pythia.log", Path(filename))
            finally:
                handler.close()

    def test_all_file_handler_failures_degrade_to_null_handler(self):
        with mock.patch.object(log.logging.handlers, "RotatingFileHandler", side_effect=PermissionError("denied")):
            handler, filename = log._create_file_handler(Path("unwritable"))
        self.assertIsInstance(handler, logging.NullHandler)
        self.assertIsNone(filename)

    def test_log_reader_tolerates_no_file_handler(self):
        with mock.patch.object(log, "LOG_FILENAME", None):
            self.assertEqual("", log.get_log_contents())

    def test_log_tail_reads_only_bounded_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.log"
            path.write_bytes(b"prefix-secret" * 10 + b"\nkept-tail\n")
            with mock.patch.object(log, "LOG_FILENAME", str(path)):
                text, truncated = log.get_log_tail(20)
        self.assertTrue(truncated)
        self.assertEqual(text, "kept-tail\n")
        self.assertNotIn("prefix-secret", text)

    def test_log_tail_discards_unrecognizable_partial_sensitive_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.log"
            path.write_bytes(b"Cookie: " + b"secret-value" * 20)
            with mock.patch.object(log, "LOG_FILENAME", str(path)):
                text, truncated = log.get_log_tail(32)
        self.assertTrue(truncated)
        self.assertEqual(text, "")


if __name__ == "__main__":
    unittest.main()
