import threading
from unittest import TestCase

from pythia.ipc.client import Client
from pythia.ipc.server import Server
from pythia.pairing_offers import PairingOffers
from tests.ipc_support import KEY, Place


class IpcPairingLeaseTest(TestCase):
    def test_disconnect_does_not_withdraw_another_clients_new_offer(self):
        place = Place()
        self.addCleanup(place.cleanup)
        offers = PairingOffers()
        owners, stopped = [], []
        released = threading.Event()

        def call(name, owner, alive):
            owners.append(owner)
            return offers.begin(owner, "compare", lambda: {"status": "ok"}, lambda: stopped.append(owner), alive)

        def release(owner):
            offers.release(owner)
            released.set()

        schema = {"methods": [{"name": "start_pair_offer", "params": [], "returns": "answer"}], "events": []}
        server = Server(
            schema,
            {"start_pair_offer": lambda: self.fail("lease hook bypassed")},
            KEY,
            where=place.where,
            pairing_call=call,
            pairing_release=release,
        )
        self.assertTrue(server.start())
        self.addCleanup(server.stop)
        old = Client(KEY, place.where)
        self.addCleanup(old.close)
        self.assertEqual(old.call("start_pair_offer")["status"], "ok")
        new = Client(KEY, place.where)
        self.addCleanup(new.close)
        self.assertEqual(new.call("start_pair_offer")["status"], "ok")
        self.assertEqual(stopped, [owners[0]])
        old.close()
        self.assertTrue(released.wait(5))
        self.assertEqual(stopped, [owners[0]])
        released.clear()
        new.close()
        self.assertTrue(released.wait(5))
        self.assertEqual(stopped, owners)
