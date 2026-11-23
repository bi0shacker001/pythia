"""The backend channel's server: authentication, dispatch, the contract's answers, pending results and events,
over a fake backend and the real transport (a Unix socket, or a named pipe on Windows)."""

import struct
import threading
import time
from unittest import TestCase

from pythia.ipc import framing, transport
from pythia.ipc.client import Client
from pythia.ipc.server import Server
from tests.ipc_support import KEY, SCHEMA, FakeBackend, Place


class ServerTest(TestCase):
    def setUp(self):
        self.place = Place()
        self.addCleanup(self.place.cleanup)
        self.backend = FakeBackend()
        self.handlers = self.backend.handlers()
        self.start()

    def start(self, handlers=None):
        self.server = Server(SCHEMA, handlers or self.handlers, KEY, where=self.place.where)
        self.assertTrue(self.server.start())
        self.addCleanup(self.server.stop)

    def client(self) -> Client:
        client = Client(KEY, self.place.where)
        self.addCleanup(client.close)
        return client

    # ---- who may talk ---------------------------------------------------------------
    def test_hello_with_the_key_opens_the_connection(self):
        client = self.client()
        self.assertEqual(client.hello["status"], "ok")
        self.assertEqual(client.hello["protocol"], 1)
        self.assertEqual(client.hello["methods"], len(SCHEMA["methods"]))
        self.assertEqual(client.call("ping")["status"], "ok")

    def test_a_wrong_key_is_refused_and_the_connection_closed(self):
        client = Client(bytes(32), self.place.where)
        self.addCleanup(client.close)
        self.assertEqual(client.hello["status"], "error")
        self.assertEqual(client.hello["code"], "unauthorized")
        self.assertIsNone(client.next_message())

    def test_nothing_is_answered_before_hello(self):
        stream = transport.connect(self.place.where)
        self.addCleanup(stream.close)
        stream.send(framing.encode({"id": 1, "method": "echo", "args": ["x"]}))
        decoder = framing.FrameDecoder()
        answer = None
        while answer is None:
            data = stream.recv()
            self.assertTrue(data, "closed without an answer")
            answer = (decoder.feed(data) or [None])[0]
        self.assertEqual(answer["result"]["code"], "unauthenticated")
        self.assertEqual(self.backend.calls, [])

    def test_a_stream_that_is_not_frames_is_dropped(self):
        stream = transport.connect(self.place.where)
        self.addCleanup(stream.close)
        stream.send(struct.pack(">I", framing.MAX_FRAME + 1))
        deadline = time.monotonic() + 5
        while stream.recv():
            self.assertLess(time.monotonic(), deadline)

    def test_a_second_server_does_not_take_the_channel(self):
        second = Server(SCHEMA, self.handlers, KEY, where=self.place.where)
        self.assertFalse(second.start())
        self.assertEqual(self.client().call("ping")["status"], "ok")

    # ---- dispatch and the contract ----------------------------------------------------
    def test_a_slot_that_answers_is_passed_on_as_it_is(self):
        self.assertEqual(self.client().call("echo", ["hi"]), {"status": "ok", "text": "hi"})
        self.assertEqual(self.backend.calls, [("echo", ("hi",))])

    def test_arguments_may_be_named(self):
        client = self.client()
        self.assertEqual(client.call("two", {"a": "x", "b": True}), {"status": "ok", "a": "x", "b": True})
        self.assertEqual(client.call("two", {"a": "x"}), {"status": "ok", "a": "x", "b": False})
        self.assertEqual(client.call("two", ["x"]), {"status": "ok", "a": "x", "b": False})

    def test_what_a_slot_returns_bare_becomes_an_ok(self):
        client = self.client()
        self.assertEqual(client.call("uuid"), {"status": "ok", "value": "abc-123"})
        self.assertEqual(client.call("nothing"), {"status": "ok"})
        self.assertEqual(client.call("legacy_list"), {"status": "ok", "value": [{"id": "plugin.a"}]})

    def test_unavailable_is_passed_on(self):
        self.assertEqual(self.client().call("not_here"), {"status": "unavailable", "message": "Not built yet."})

    def test_a_slot_that_raises_is_an_error_with_its_reason_and_no_traceback(self):
        answer = self.client().call("broken")
        self.assertEqual(answer["status"], "error")
        self.assertEqual(answer["slot"], "broken")
        self.assertEqual(answer["message"], "it broke")
        self.assertNotIn("Traceback", str(answer))

    def test_an_answer_that_is_not_a_contract_answer_is_an_error(self):
        answer = self.client().call("garbage")
        self.assertEqual((answer["status"], answer["code"]), ("error", "malformed"))

    def test_the_server_stays_up_after_a_failing_slot(self):
        client = self.client()
        client.call("broken")
        self.assertEqual(client.call("uuid")["value"], "abc-123")

    def test_an_unknown_method_is_an_error(self):
        answer = self.client().call("rm_rf")
        self.assertEqual((answer["status"], answer["code"]), ("error", "unknown_method"))
        self.assertEqual(self.backend.calls, [])

    def test_the_wrong_arguments_never_reach_the_slot(self):
        client = self.client()
        for method, args in (
            ("echo", []),
            ("echo", ["a", "b"]),
            ("echo", [1]),
            ("echo", {"nope": "x"}),
            ("echo", "text"),
            ("two", ["x", "not a boolean"]),
            ("slow", [True]),
            ("slow", [1.5]),
            ("uuid", ["extra"]),
        ):
            with self.subTest(method=method, args=args):
                answer = client.call(method, args)
                self.assertEqual((answer["status"], answer["code"]), ("error", "bad_args"), answer)
        self.assertEqual(self.backend.calls, [])

    def test_a_message_that_is_not_a_call_is_an_error_not_a_crash(self):
        client = self.client()
        client.send({"id": 5, "args": []})
        client.send({"method": "ping"})
        client.send({"id": True, "method": "ping"})
        first, second, third = (client.next_message() for _ in range(3))
        self.assertEqual(first["id"], 5)
        self.assertEqual(first["result"]["code"], "bad_request")
        self.assertIsNone(second["id"])
        self.assertEqual(second["result"]["code"], "bad_request")
        self.assertEqual(third["result"]["code"], "bad_request")
        self.assertEqual(client.call("ping")["status"], "ok")

    def test_the_schema_is_served(self):
        answer = self.client().call("schema")
        self.assertEqual(answer["schema"]["methods"], SCHEMA["methods"])

    def test_calls_run_where_the_server_is_told_to_run_them(self):
        seen = []

        def on_main(fn):
            seen.append(threading.current_thread().name)
            return fn()

        server = Server(SCHEMA, self.handlers, KEY, call_on_main=on_main, where=self.place.where + "-2")
        self.assertTrue(server.start())
        self.addCleanup(server.stop)
        with Client(KEY, self.place.where + "-2") as client:
            client.call("uuid")
        self.assertEqual(len(seen), 1)

    # ---- pending answers and events ---------------------------------------------------
    def test_a_pending_answer_is_settled_on_the_same_connection(self):
        client = self.client()
        client.send({"id": 9, "method": "slow", "args": [1]})
        first = client.next_message()
        self.assertEqual(first, {"id": 9, "result": {"status": "pending", "slot": "slow", "request": "slow-1"}})
        self.server.publish("asyncResult", [{"status": "ok", "request": "slow-1", "rows": 3}])
        settled = client.next_message()
        self.assertEqual(
            settled, {"id": 9, "settled": True, "result": {"status": "ok", "request": "slow-1", "rows": 3}}
        )

    def test_call_waits_for_the_settlement(self):
        threading.Timer(
            0.3, lambda: self.server.publish("asyncResult", [{"status": "error", "message": "no", "request": "slow-2"}])
        ).start()
        answer = self.client().call("slow", [2])
        self.assertEqual((answer["status"], answer["request"]), ("error", "slow-2"))

    def test_a_result_that_arrives_before_the_pending_answer_is_not_lost(self):
        def fast(n):
            self.server.publish("asyncResult", [{"status": "ok", "request": f"slow-{n}", "quick": True}])
            return {"status": "pending", "slot": "slow", "request": f"slow-{n}"}

        self.handlers["slow"] = fast
        answer = self.client().call("slow", [3])
        self.assertEqual((answer["status"], answer.get("quick")), ("ok", True))

    def test_a_result_belongs_to_the_connection_that_asked(self):
        first, second = self.client(), self.client()
        first.send({"id": 1, "method": "slow", "args": [4]})
        self.assertEqual(first.next_message()["result"]["status"], "pending")
        self.server.publish("asyncResult", [{"status": "ok", "request": "slow-4"}])
        self.assertEqual(first.next_message()["settled"], True)
        self.assertEqual(second.call("ping")["status"], "ok")
        self.assertEqual(second.events, [])

    def test_a_result_with_nobody_waiting_is_kept_bounded_and_harmless(self):
        for n in range(500):
            self.server.publish("asyncResult", [{"status": "ok", "request": f"other-{n}"}])
        self.server.publish("asyncResult", [])
        self.server.publish("asyncResult", [{"status": "ok"}])
        self.assertEqual(self.client().call("ping")["status"], "ok")

    def test_signals_are_pushed_to_every_connection(self):
        first, second = self.client(), self.client()
        self.server.publish("gamesChanged", [])
        self.server.publish("logLine", ["scanning"])
        for client in (first, second):
            client.call("ping")
            self.assertEqual(
                client.events, [{"event": "gamesChanged", "args": []}, {"event": "logLine", "args": ["scanning"]}]
            )

    def test_a_connection_may_narrow_its_events(self):
        client = self.client()
        self.assertEqual(client.call("subscribe", [["logLine"]])["status"], "ok")
        self.server.publish("gamesChanged", [])
        self.server.publish("logLine", ["x"])
        client.call("ping")
        self.assertEqual(client.events, [{"event": "logLine", "args": ["x"]}])
        self.assertEqual(client.call("subscribe", [["nope"]])["code"], "bad_args")

    def test_an_event_the_schema_does_not_have_is_not_sent(self):
        client = self.client()
        self.server.publish("secretThing", [])
        client.call("ping")
        self.assertEqual(client.events, [])

    def test_a_connection_that_went_does_not_stop_the_others(self):
        gone = self.client()
        gone.close()
        other = self.client()
        for _ in range(5):
            self.server.publish("gamesChanged", [])
        self.assertEqual(other.call("uuid")["value"], "abc-123")

    def test_stopping_closes_the_connections(self):
        client = self.client()
        self.server.stop()
        self.assertIsNone(client.next_message())
