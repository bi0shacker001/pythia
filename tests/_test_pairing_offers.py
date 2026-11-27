import threading
from unittest import TestCase

from pythia.pairing_offers import PairingOffers


class PairingOffersTest(TestCase):
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
        self.assertFalse(offers.stop(client, "code"))
        self.assertEqual(stopped, ["client"])
        self.assertTrue(offers.stop(None, "code"))
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
