"""The channel over the real Backend: starts the server as the running Pythia does and calls read-only slots.

Needs PySide6 with its offscreen platform. The test thread runs Qt's event loop (calls are made on the Qt thread,
as the HTML UI's are) while a worker thread plays the second front end.
"""

import os
import tempfile
import threading
import time
from unittest import TestCase, mock, skipUnless

from tests.ipc_support import Place

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QGuiApplication

    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@skipUnless(HAVE_QT, "PySide6 is not installed")
class RealBackendTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.TemporaryDirectory(prefix="pythia-ipc-", ignore_cleanup_errors=True)
        cls.env = mock.patch.dict(
            os.environ, {"PYTHIA_DATA_DIR": cls.data.name, "PYTHIA_DATADIR_OVERRIDE": os.path.join(cls.data.name, "dd")}
        )
        cls.env.start()
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from pythia import instance
        from pythia.ipc import qt as ipc
        from pythia.ui.backend import Backend

        cls.key = instance.channel_key()
        cls.place = Place()
        cls.backend = Backend()
        cls.server = ipc.attach(cls.app, cls.backend, where=cls.place.where)

    @classmethod
    def tearDownClass(cls):
        if cls.server is not None:
            cls.server.stop()
        cls.place.cleanup()
        cls.env.stop()
        cls.data.cleanup()

    def over_the_channel(self, work):
        """Runs `work(client)` on a worker thread, with Qt's event loop running here; returns what it returned."""
        from pythia.ipc.client import Client

        box: dict = {}

        def run():
            try:
                with Client(self.key, self.place.where, timeout=20) as client:
                    box["value"] = work(client)
            except BaseException as ex:  # noqa: BLE001 -- shown by the test thread
                box["error"] = ex

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 90
        while thread.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertFalse(thread.is_alive(), "the call did not finish")
        if "error" in box:
            raise box["error"]
        return box["value"]

    def test_the_server_started_with_this_backend(self):
        self.assertIsNotNone(self.server)

    def test_a_read_only_slot_answers_as_the_html_ui_gets_it(self):
        from pythia.identity import get_system_uuid

        answer = self.over_the_channel(lambda c: c.call("get_system_uuid"))
        self.assertEqual(answer, {"status": "ok", "value": get_system_uuid()})

    def test_slots_that_return_answers_are_passed_on(self):
        modes = self.over_the_channel(lambda c: c.call("get_modes"))
        self.assertEqual(modes["status"], "ok")
        self.assertEqual(modes, self.backend.get_modes())
        self.assertEqual(self.over_the_channel(lambda c: c.call("get_ui_mode"))["status"], "ok")
        log = self.over_the_channel(lambda c: c.call("get_ui_log"))
        self.assertIn(log["status"], ("ok", "unavailable"))

    def test_the_schema_the_server_runs_from_is_the_checked_in_one(self):
        from pythia.ipc import schema

        served = self.over_the_channel(lambda c: c.call("schema"))["schema"]
        self.assertEqual(served, schema.load())

    def test_a_slot_that_answers_later_settles_on_the_connection(self):
        def work(client):
            client.send({"id": 100, "method": "get_system_info", "args": []})
            first = client.next_message()
            settled = client.next_message()
            while settled is not None and "event" in settled:
                settled = client.next_message()
            return first, settled

        first, settled = self.over_the_channel(work)
        self.assertEqual(first["result"]["status"], "pending")
        self.assertTrue(settled["settled"])
        self.assertEqual(settled["result"]["request"], first["result"]["request"])
        self.assertIn(settled["result"]["status"], ("ok", "unavailable", "error"))

    def test_a_signal_of_the_backend_is_pushed(self):
        # emitted here, on the Qt thread, while the client is connected
        from pythia.ipc.client import Client

        received: list = []

        def listen():
            with Client(self.key, self.place.where, timeout=20) as client:
                client.call("subscribe", [["modesChanged"]])
                received.append("ready")
                message = client.next_message()
                received.append(message)

        thread = threading.Thread(target=listen, daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while "ready" not in received and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.backend.modesChanged.emit()
        while len(received) < 2 and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertEqual(received[1], {"event": "modesChanged", "args": []})

    def test_the_wrong_arguments_for_a_real_slot_are_an_error(self):
        answer = self.over_the_channel(lambda c: c.call("get_play_state", [1, 2, 3]))
        self.assertEqual((answer["status"], answer["code"]), ("error", "bad_args"))
