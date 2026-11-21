"""pythia.instance: the channel a `pythia` command uses to reach the running Pythia (docs/SPEC.md decision 12)."""

import os
import secrets
import tempfile
import uuid
from unittest import TestCase

from pythia import instance


class InstanceTest(TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.key = secrets.token_bytes(32)
        if os.name == "nt":
            self.where = r"\\.\pipe\pythia-test-" + uuid.uuid4().hex
        else:
            self.where = os.path.join(self.dir.name, "instance.sock")
        self.running = None

    def tearDown(self):
        if self.running is not None:
            self.running.stop()
        self.dir.cleanup()

    def start(self, handlers):
        self.running = instance.Instance(handlers, where=self.where, key=self.key)
        self.assertTrue(self.running.start())

    def test_nothing_running_is_none(self):
        self.assertIsNone(instance.request("ping", where=self.where, key=self.key))

    def test_a_request_gets_its_lines_and_exit_code(self):
        def pair(args, say):
            say("Address: 10.0.0.2:47612")
            say("Code: " + args["code"])
            return 0

        self.start({"droidtop-pair": pair})
        lines = []
        code = instance.request(
            "droidtop-pair", {"code": "860495"}, on_line=lines.append, where=self.where, key=self.key
        )
        self.assertEqual(0, code)
        self.assertEqual(["Address: 10.0.0.2:47612", "Code: 860495"], lines)

    def test_ping_and_unknown_requests(self):
        self.start({})
        self.assertEqual(0, instance.request("ping", where=self.where, key=self.key))
        lines = []
        self.assertEqual(2, instance.request("nope", on_line=lines.append, where=self.where, key=self.key))
        self.assertIn("does not know", lines[0])

    def test_a_second_instance_does_not_start(self):
        self.start({})
        other = instance.Instance({}, where=self.where, key=self.key)
        self.assertFalse(other.start())

    def test_a_failing_handler_is_reported_and_the_app_stays_up(self):
        def broken(_args, _say):
            raise RuntimeError("boom")

        self.start({"broken": broken})
        lines = []
        self.assertEqual(1, instance.request("broken", on_line=lines.append, where=self.where, key=self.key))
        self.assertIn("boom", lines[0])
        self.assertEqual(0, instance.request("ping", where=self.where, key=self.key))

    def test_the_wrong_key_reaches_nothing(self):
        self.start({})
        self.assertIsNone(instance.request("ping", where=self.where, key=secrets.token_bytes(32)))
        self.assertEqual(0, instance.request("ping", where=self.where, key=self.key))
