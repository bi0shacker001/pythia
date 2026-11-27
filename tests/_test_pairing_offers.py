import threading
from unittest import TestCase

from pythia.pairing_offers import PairingOffers


class PairingOffersTest(TestCase):
    def test_delayed_stop_cannot_withdraw_a_new_offer_on_the_same_connection(self):
        offers = PairingOffers()
        owner = object()
        stopped = []
        offers.begin(owner, "compare", lambda: {}, lambda: stopped.append("old"))
        old_stop = offers.request_stop(owner, "compare")
        new_start = offers.reserve(owner, "compare")
        offers.begin(owner, "compare", lambda: {}, lambda: stopped.append("new"), generation=new_start)
        self.assertFalse(offers.stop(owner, "compare", old_stop))
        self.assertEqual(stopped, ["old"])
        offers.release(owner)
        self.assertEqual(stopped, ["old", "new"])

    def test_stop_before_worker_start_prevents_a_late_listener(self):
        offers = PairingOffers()
        owner = object()
        generation = offers.reserve(owner, "compare")
        offers.request_stop(owner, "compare")
        started = []

        def start():
            started.append(True)
            return {"status": "ok"}

        result = offers.begin(owner, "compare", start, lambda: None, generation=generation)
        self.assertEqual(result["status"], "error")
        self.assertEqual(started, [])

    def test_reordered_old_start_does_not_replace_a_newer_clients_listener(self):
        offers = PairingOffers()
        old, new = object(), object()
        first = offers.reserve(old, "compare")
        second = offers.reserve(new, "compare")
        stopped = []
        offers.begin(new, "compare", lambda: {}, lambda: stopped.append("new"), generation=second)
        result = offers.begin(old, "compare", lambda: {}, lambda: stopped.append("old"), generation=first)
        self.assertEqual(result["status"], "error")
        self.assertEqual(stopped, [])

    def test_disconnect_withdraws_only_its_current_offer(self):
        offers = PairingOffers()
        old, new = object(), object()
        stopped = []
        offers.begin(old, "compare", lambda: {"status": "ok"}, lambda: stopped.append("old"))
        offers.begin(new, "compare", lambda: {"status": "ok"}, lambda: stopped.append("new"))
        offers.release(old)
        self.assertEqual(stopped, ["old"])
        offers.release(new)
        self.assertEqual(stopped, ["old", "new"])

    def test_old_client_cannot_stop_a_new_qt_offer(self):
        offers = PairingOffers()
        client = object()
        stopped = []
        offers.begin(client, "compare", lambda: {}, lambda: stopped.append("client"))
        offers.begin(None, "code", lambda: {}, lambda: stopped.append("qt"))
        offers.release(client)
        self.assertFalse(offers.stop(client, "code", offers.request_stop(client, "code")))
        self.assertEqual(stopped, ["client"])
        self.assertTrue(offers.stop(None, "code", offers.request_stop(None, "code")))
        self.assertEqual(stopped, ["client", "qt"])

    def test_disconnect_during_start_withdraws_the_late_offer(self):
        offers = PairingOffers()
        owner = object()
        entered, complete, disconnected = threading.Event(), threading.Event(), threading.Event()
        stopped, result = [], []

        def start():
            entered.set()
            if not complete.wait(5):
                raise TimeoutError("test start was never released")
            return {"status": "ok"}

        worker = threading.Thread(
            target=lambda: result.append(
                offers.begin(owner, "compare", start, lambda: stopped.append(True), lambda: not disconnected.is_set())
            )
        )
        worker.start()
        self.assertTrue(entered.wait(5))
        disconnected.set()
        complete.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["status"], "error")
        offers.release(owner)
        self.assertEqual(stopped, [True])

    def test_a_closed_client_never_starts_listening(self):
        offers = PairingOffers()
        started = []

        def start():
            started.append(True)
            return {"status": "ok"}

        result = offers.begin(object(), "compare", start, lambda: None, lambda: False)
        self.assertEqual(result["status"], "error")
        self.assertEqual(started, [])

    def test_failed_start_is_withdrawn_and_does_not_own_a_new_offer(self):
        offers = PairingOffers()
        stopped = []

        def broken():
            raise RuntimeError("listener started but setup failed")

        with self.assertRaises(RuntimeError):
            offers.begin(object(), "compare", broken, lambda: stopped.append(True))
        self.assertEqual(stopped, [True])
