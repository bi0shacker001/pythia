"""The web UI's own JavaScript tests (tests/web/*.test.mjs), run with node's
test runner when node is installed (CI's runners have it)."""

import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import TestCase

WEB_TESTS = sorted((Path(__file__).parent / "web").glob("*.test.mjs"))


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class WebJsTest(TestCase):
    def test_node_tests_pass(self):
        self.assertTrue(WEB_TESTS)
        done = subprocess.run(
            ["node", "--test", *map(str, WEB_TESTS)], capture_output=True, text=True, timeout=120, check=False
        )
        self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-2000:])
