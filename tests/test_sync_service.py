"""Real tests for pythia.core.sync.service.SyncService -- two isolated
identities, real loopback TCP/TLS (same pattern tests/test_sync_direct_tcp.py
established), real LAN broadcast disabled by default here to keep tests fast
and deterministic (LAN itself is already covered by tests/test_sync_lan.py).
"""

import asyncio
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from pythia.core.sync import config as sync_config
from pythia.core.sync import metadata, service, trust
from pythia.core.sync.connectivity import direct_tcp


def _make_node_and_dir():
    tmp = tempfile.TemporaryDirectory()
    base_dir = Path(tmp.name)
    return tmp, base_dir


def _trust_each_other(node_a, node_b, base_dir_a, base_dir_b):
    trust.add_trusted(
        system_uuid=node_b.system_uuid,
        device_id=node_b.device_id,
        certificate_fingerprint=node_b.identity_record["certificate_fingerprint"],
        friendly_name="System B",
        base_dir=base_dir_a,
    )
    trust.add_trusted(
        system_uuid=node_a.system_uuid,
        device_id=node_a.device_id,
        certificate_fingerprint=node_a.identity_record["certificate_fingerprint"],
        friendly_name="System A",
        base_dir=base_dir_b,
    )


class DisabledIsRealNoOpTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_start_creates_no_identity_files_no_sockets(self):
        """The core requirement: opening/starting while disabled must have
        ZERO side effects -- no identity/cert files, no sockets. (Writing
        the sync.json config file itself, via config.save() below, is a
        real, separate, already-covered concern -- config.py's own tests
        own that; this test only asserts identity was never touched.)"""
        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save({"enabled": False}, base_dir=base_dir)
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            result = await svc.start()
            self.assertEqual({"status": "disabled"}, result)
            self.assertFalse(svc.running)
            self.assertIsNone(svc.node)
            self.assertFalse((base_dir / "network" / "identity.json").exists())
            self.assertFalse((base_dir / "network" / "cert.pem").exists())
            self.assertFalse((base_dir / "network" / "key.pem").exists())
        finally:
            tmp.cleanup()

    async def test_stop_when_never_started_is_a_real_no_op(self):
        tmp, base_dir = _make_node_and_dir()
        try:
            svc = service.SyncService(base_dir=base_dir)
            result = await svc.stop()
            self.assertEqual({"status": "not_running"}, result)
        finally:
            tmp.cleanup()


class EnabledLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_start_creates_identity_and_real_listener_then_stop_closes_it(self):
        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=base_dir)
            events = []
            svc = service.SyncService(base_dir=base_dir, on_status=events.append)
            result = await svc.start(listen_host="127.0.0.1", listen_port=0)
            self.assertEqual("ok", result["status"])
            self.assertTrue(svc.running)
            self.assertIsNotNone(svc.node)
            self.assertTrue((base_dir / "network" / "identity.json").is_file())  # real identity file
            self.assertTrue(any(e.get("event") == "started" for e in events))

            # A real double-start is reported honestly, not silently ignored.
            second = await svc.start()
            self.assertEqual({"status": "already_running"}, second)

            stop_result = await svc.stop()
            self.assertEqual({"status": "ok"}, stop_result)
            self.assertFalse(svc.running)
            self.assertTrue(any(e.get("event") == "stopped" for e in events))
        finally:
            tmp.cleanup()

    async def test_listen_disabled_means_no_listener_started(self):
        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save({"enabled": True, "listen": False, "lan_discovery": False}, base_dir=base_dir)
            svc = service.SyncService(base_dir=base_dir)
            result = await svc.start()
            self.assertEqual("ok", result["status"])
            self.assertIsNone(svc._listener)
            await svc.stop()
        finally:
            tmp.cleanup()


class ConnectAndOfferAcceptRejectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp_a, self.base_dir_a = _make_node_and_dir()
        self._tmp_b, self.base_dir_b = _make_node_and_dir()
        self.uuid_a = str(uuid.uuid4())
        self.uuid_b = str(uuid.uuid4())
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_a)
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_b)

    async def asyncTearDown(self):
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    async def test_connect_to_untrusted_device_fails_honestly(self):
        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        try:
            with self.assertRaises(direct_tcp.UnknownDeviceError):
                await svc_a.connect_to_trusted(str(uuid.uuid4()), port=1)
        finally:
            await svc_a.stop()

    async def test_connect_with_no_known_address_fails_honestly(self):
        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        try:
            # Trust it, but never record an address for it.
            trust.add_trusted(
                system_uuid="some-other-system",
                device_id="AAAAAAB-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA",
                certificate_fingerprint="0" * 64,
                friendly_name="No address yet",
                base_dir=self.base_dir_a,
            )
            with self.assertRaises(direct_tcp.DirectConnectError):
                await svc_a.connect_to_trusted("some-other-system", port=1)
        finally:
            await svc_a.stop()

    async def test_offer_reject_by_real_user_decision(self):
        offers_seen = []
        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)  # receiver
        svc_a._on_transfer_offer = lambda offer_id, offer, peer_uuid: offers_seen.append((offer_id, offer, peer_uuid))
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)  # sender
        await svc_b.start(listen_host="127.0.0.1", listen_port=0)
        try:
            _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)

            port = svc_a._listener.sockets[0].getsockname()[1]

            from pythia.core.sync import transfer_offer

            async def sender():
                conn = await direct_tcp.connect("127.0.0.1", port, svc_b.node, expected_system_uuid=svc_a.node.system_uuid)
                result = await transfer_offer.offer_and_send_game(
                    conn, "/nonexistent", game_id="g1", display_name="Test Game", stream_id=1, timeout=10
                )
                await conn.close()
                return result

            async def decide_once_offer_seen():
                for _ in range(100):
                    if offers_seen:
                        offer_id, offer, peer_uuid = offers_seen[0]
                        self.assertEqual("g1", offer["game_id"])
                        self.assertEqual(svc_b.node.system_uuid, peer_uuid)
                        svc_a.resolve_offer(offer_id, accept=False, message="Not right now.")
                        return
                    await asyncio.sleep(0.05)
                self.fail("offer was never observed")

            sender_result, _ = await asyncio.gather(sender(), decide_once_offer_seen())
            self.assertEqual("rejected", sender_result["status"])
            self.assertEqual("Not right now.", sender_result["message"])
        finally:
            await svc_a.stop()
            await svc_b.stop()

    async def test_offer_accept_and_real_file_transfer(self):
        with tempfile.TemporaryDirectory() as source_root, tempfile.TemporaryDirectory() as dest_root, tempfile.TemporaryDirectory() as cache_root:
            source = Path(source_root) / "game"
            source.mkdir()
            (source / "game.exe").write_bytes(b"pretend-binary-data" * 1000)

            events = []
            svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a, on_status=events.append)  # receiver
            destination = Path(dest_root) / "received"

            def on_offer(offer_id, offer, peer_uuid):
                svc_a.resolve_offer(offer_id, accept=True, destination=str(destination), cache=cache_root)

            svc_a._on_transfer_offer = on_offer
            await svc_a.start(listen_host="127.0.0.1", listen_port=0)
            svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)  # sender
            await svc_b.start(listen_host="127.0.0.1", listen_port=0)
            try:
                _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)
                port = svc_a._listener.sockets[0].getsockname()[1]

                from pythia.core.sync import transfer_offer

                conn = await direct_tcp.connect("127.0.0.1", port, svc_b.node, expected_system_uuid=svc_a.node.system_uuid)
                result = await transfer_offer.offer_and_send_game(
                    conn, source, game_id="g1", display_name="Test Game", stream_id=1, timeout=10
                )
                await conn.close()
                self.assertEqual("ok", result["status"])

                # Give the receiver's background _on_connected coroutine a
                # moment to finish materializing the file after conn.close().
                for _ in range(100):
                    if (destination / "game.exe").is_file():
                        break
                    await asyncio.sleep(0.05)
                self.assertEqual((source / "game.exe").read_bytes(), (destination / "game.exe").read_bytes())
            finally:
                await svc_a.stop()
                await svc_b.stop()


class ConnectToTrustedCandidateRacingTests(unittest.IsolatedAsyncioTestCase):
    """connect_to_trusted() upgraded from a sequential address loop to
    real bounded-concurrent candidate racing (2026-08-08, "relay-assisted
    rendezvous and direct-path candidate orchestration") -- same public
    signature, real behavior change underneath."""

    async def asyncSetUp(self):
        self._tmp_a, self.base_dir_a = _make_node_and_dir()
        self._tmp_b, self.base_dir_b = _make_node_and_dir()
        self.uuid_a = str(uuid.uuid4())
        self.uuid_b = str(uuid.uuid4())
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_a)
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_b)

    async def asyncTearDown(self):
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    async def test_succeeds_via_the_real_address_when_a_dead_candidate_is_also_present(self):
        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)
        await svc_b.start(listen_host="127.0.0.1", listen_port=0)
        try:
            _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)
            real_port = svc_b._listener.sockets[0].getsockname()[1]
            # A dead candidate (nothing listening on port 1) plus the real one.
            trust.update_known_addresses(
                svc_b.node.system_uuid, [f"127.0.0.1:{real_port}", "127.0.0.1:1"], base_dir=self.base_dir_a
            )

            connection = await svc_a.connect_to_trusted(svc_b.node.system_uuid, port=real_port)
            try:
                self.assertEqual(svc_b.node.system_uuid, connection.peer.system_uuid)
            finally:
                await connection.close()
            peers = svc_a.known_peers()
            self.assertEqual("direct_outgoing", peers[svc_b.node.system_uuid]["route"])
        finally:
            await svc_a.stop()
            await svc_b.stop()

    async def test_all_candidates_unreachable_records_every_real_error(self):
        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        try:
            trust.add_trusted(
                system_uuid="dead-peer",
                device_id="AAAAAAB-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA",
                certificate_fingerprint="0" * 64,
                friendly_name="Dead",
                known_addresses=["127.0.0.1:1", "127.0.0.1:2"],
                base_dir=self.base_dir_a,
            )
            with self.assertRaises(direct_tcp.DirectConnectError):
                await svc_a.connect_to_trusted("dead-peer", port=1)
            peers = svc_a.known_peers()
            self.assertEqual("unreachable", peers["dead-peer"]["route"])
            self.assertIn("127.0.0.1:1", peers["dead-peer"]["error"])
            self.assertIn("127.0.0.1:2", peers["dead-peer"]["error"])
        finally:
            await svc_a.stop()

    async def test_bare_host_address_falls_back_to_the_given_port(self):
        """Older LAN-discovered records (before the port-combining fix)
        may still contain a bare host with no port -- connect_to_trusted()
        must keep working for those via the given `port` fallback."""
        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)
        await svc_b.start(listen_host="127.0.0.1", listen_port=0)
        try:
            _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)
            real_port = svc_b._listener.sockets[0].getsockname()[1]
            trust.update_known_addresses(svc_b.node.system_uuid, ["127.0.0.1"], base_dir=self.base_dir_a)

            connection = await svc_a.connect_to_trusted(svc_b.node.system_uuid, port=real_port)
            await connection.close()
        finally:
            await svc_a.stop()
            await svc_b.stop()


class LanPeerPortCombiningTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_lan_peer_combines_bare_addresses_with_the_announced_port(self):
        """Real bug fix regression test: discovery.parse_announcement()'s
        own `port` field used to be silently discarded -- only bare hosts
        were ever persisted to the trust store."""
        tmp, base_dir = _make_node_and_dir()
        try:
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            trust.add_trusted(
                system_uuid="peer-1",
                device_id="AAAAAAB-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA",
                certificate_fingerprint="0" * 64,
                friendly_name="Peer",
                base_dir=base_dir,
            )
            svc._on_lan_peer({"system_uuid": "peer-1", "addresses": ["192.168.1.5", "192.168.1.6"], "port": 24030})
            record = trust.get_trusted("peer-1", base_dir=base_dir)
            self.assertEqual(["192.168.1.5:24030", "192.168.1.6:24030"], record["known_addresses"])
        finally:
            tmp.cleanup()

    async def test_on_lan_peer_falls_back_to_bare_addresses_without_a_real_port(self):
        tmp, base_dir = _make_node_and_dir()
        try:
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            trust.add_trusted(
                system_uuid="peer-1",
                device_id="AAAAAAB-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA",
                certificate_fingerprint="0" * 64,
                friendly_name="Peer",
                base_dir=base_dir,
            )
            svc._on_lan_peer({"system_uuid": "peer-1", "addresses": ["192.168.1.5"], "port": None})
            record = trust.get_trusted("peer-1", base_dir=base_dir)
            self.assertEqual(["192.168.1.5"], record["known_addresses"])
        finally:
            tmp.cleanup()


class RelayDisabledZeroSideEffectTests(unittest.IsolatedAsyncioTestCase):
    async def test_relay_discovery_disabled_creates_no_relay_client_even_with_server_configured(self):
        """A configured relay_server must never be used unless
        relay_discovery is actually enabled -- disabled must mean
        disabled, not "enabled if a URL happens to be set."""
        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save(
                {"enabled": True, "listen": True, "lan_discovery": False, "relay_discovery": False}, base_dir=base_dir
            )
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            await svc.start(listen_host="127.0.0.1", listen_port=0, relay_server="127.0.0.1:1")
            try:
                self.assertIsNone(svc._relay_client)
            finally:
                await svc.stop()
        finally:
            tmp.cleanup()

    async def test_relay_discovery_enabled_with_no_server_url_creates_no_relay_client(self):
        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save(
                {"enabled": True, "listen": True, "lan_discovery": False, "relay_discovery": True}, base_dir=base_dir
            )
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            await svc.start(listen_host="127.0.0.1", listen_port=0, relay_server="")
            try:
                self.assertIsNone(svc._relay_client)
            finally:
                await svc.stop()
        finally:
            tmp.cleanup()

    async def test_globally_disabled_sync_creates_no_relay_client_regardless_of_settings(self):
        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save({"enabled": False}, base_dir=base_dir)
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            result = await svc.start(listen_host="127.0.0.1", listen_port=0, relay_server="127.0.0.1:1")
            self.assertEqual({"status": "disabled"}, result)
            self.assertIsNone(svc._relay_client)
        finally:
            tmp.cleanup()


class RelayAssistedConnectTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end: two SyncServices with NO known direct address at all,
    both connected to a real RelayServer -- connect_to_trusted() must
    still succeed by asking the peer for its real current candidates via
    relay rendezvous first, matching "coordinate simultaneous direct
    connection attempts ... if no direct route succeeds, report that
    honestly" for the case where one route (relay-assisted discovery, THEN
    direct) does succeed."""

    async def asyncSetUp(self):
        self._tmp_a, self.base_dir_a = _make_node_and_dir()
        self._tmp_b, self.base_dir_b = _make_node_and_dir()
        self.uuid_a = str(uuid.uuid4())
        self.uuid_b = str(uuid.uuid4())
        sync_config.save(
            {"enabled": True, "listen": True, "lan_discovery": False, "relay_discovery": True}, base_dir=self.base_dir_a
        )
        sync_config.save(
            {"enabled": True, "listen": True, "lan_discovery": False, "relay_discovery": True}, base_dir=self.base_dir_b
        )
        from pythia.core.sync import relay

        self.relay_server = relay.RelayServer()
        await self.relay_server.start("127.0.0.1", 0)
        self.relay_port = self.relay_server._server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        await self.relay_server.close()
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    async def test_connect_succeeds_via_relay_assisted_candidate_discovery(self):
        from unittest import mock

        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)
        # The test listener only binds 127.0.0.1 (real machine-network
        # isolation, same as every other test in this suite) -- real NIC
        # addresses from _local_ipv4_addresses() would be refused here even
        # though this exact flow works over a real LAN. Patched to loopback
        # for this whole test so it proves the real relay-assisted exchange
        # + direct connect logic without depending on this sandbox's own
        # network topology.
        with mock.patch.object(service, "_local_ipv4_addresses", return_value=["127.0.0.1"]):
            await svc_a.start(listen_host="127.0.0.1", listen_port=0, relay_server=f"127.0.0.1:{self.relay_port}")
            await svc_b.start(listen_host="127.0.0.1", listen_port=0, relay_server=f"127.0.0.1:{self.relay_port}")
            try:
                for _ in range(50):
                    if svc_a._relay_client is not None and svc_a._relay_client.connected and svc_b._relay_client is not None and svc_b._relay_client.connected:
                        break
                    await asyncio.sleep(0.05)
                self.assertIsNotNone(svc_a._relay_client)
                self.assertIsNotNone(svc_b._relay_client)

                _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)
                # Deliberately NO known_addresses recorded anywhere -- the
                # only path to a real connection here is relay-assisted
                # discovery.
                self.assertEqual(
                    [], trust.get_trusted(svc_b.node.system_uuid, base_dir=self.base_dir_a).get("known_addresses") or []
                )

                connection = await svc_a.connect_to_trusted(svc_b.node.system_uuid, port=1)
                try:
                    self.assertEqual(svc_b.node.system_uuid, connection.peer.system_uuid)
                finally:
                    await connection.close()

                record = trust.get_trusted(svc_b.node.system_uuid, base_dir=self.base_dir_a)
                self.assertTrue(record["known_addresses"])  # learned and persisted for next time
            finally:
                await svc_a.stop()
                await svc_b.stop()

    async def test_connect_fails_honestly_when_relay_peer_never_answers(self):
        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        await svc_a.start(listen_host="127.0.0.1", listen_port=0, relay_server=f"127.0.0.1:{self.relay_port}")
        try:
            for _ in range(50):
                if svc_a._relay_client is not None and svc_a._relay_client.connected:
                    break
                await asyncio.sleep(0.05)
            trust.add_trusted(
                system_uuid="offline-peer",
                device_id="AAAAAAB-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA",
                certificate_fingerprint="0" * 64,
                friendly_name="Offline",
                base_dir=self.base_dir_a,
            )
            with self.assertRaises(direct_tcp.DirectConnectError):
                await svc_a.connect_to_trusted("offline-peer", port=1)
        finally:
            await svc_a.stop()

    async def test_relay_offer_from_untrusted_device_is_rejected(self):
        """A device we've never paired with sends a candidate offer via the
        relay -- must be rejected, never silently trusted just because the
        relay forwarded it."""
        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)  # never paired with A
        events = []
        svc_a._on_status = events.append
        await svc_a.start(listen_host="127.0.0.1", listen_port=0, relay_server=f"127.0.0.1:{self.relay_port}")
        await svc_b.start(listen_host="127.0.0.1", listen_port=0, relay_server=f"127.0.0.1:{self.relay_port}")
        try:
            for _ in range(50):
                if svc_a._relay_client is not None and svc_a._relay_client.connected and svc_b._relay_client is not None and svc_b._relay_client.connected:
                    break
                await asyncio.sleep(0.05)

            await svc_b._relay_client.send_candidate_offer(svc_a.node.system_uuid, ["127.0.0.1:1"])

            for _ in range(50):
                if any(e.get("event") == "relay_offer_rejected" for e in events):
                    break
                await asyncio.sleep(0.05)
            self.assertTrue(any(e.get("event") == "relay_offer_rejected" for e in events))
            self.assertIsNone(trust.get_trusted(svc_b.node.system_uuid, base_dir=self.base_dir_a))
        finally:
            await svc_a.stop()
            await svc_b.stop()


class MetadataPushTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp_a, self.base_dir_a = _make_node_and_dir()
        self._tmp_b, self.base_dir_b = _make_node_and_dir()
        self.uuid_a = str(uuid.uuid4())
        self.uuid_b = str(uuid.uuid4())
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_a)
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_b)

    async def asyncTearDown(self):
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    async def test_pushed_metadata_is_reconciled_and_handed_to_caller(self):
        """B pushes its own record to A. A's SyncService (the passive
        listener) reconciles it against A's real on_metadata_requested()
        items and hands the classified result to on_metadata_reconciled() --
        proving the whole receiving-role wire path works end to end, not
        just each half in isolation."""
        from pythia.core.sync import metadata_session

        reconciled = []
        requested_for = []
        svc_a = service.SyncService(
            base_dir=self.base_dir_a,
            system_uuid=self.uuid_a,
            on_metadata_requested=lambda peer_uuid: requested_for.append(peer_uuid) or [],
            on_metadata_reconciled=lambda peer_uuid, result: reconciled.append((peer_uuid, result)),
        )
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)
        await svc_b.start(listen_host="127.0.0.1", listen_port=0)
        try:
            _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)
            port = svc_a._listener.sockets[0].getsockname()[1]

            record = metadata.create_record(
                {"system_uuid": svc_b.node.system_uuid, "game_id": "g1", "title": "Pushed Game"}, revision=1
            )
            conn = await direct_tcp.connect("127.0.0.1", port, svc_b.node, expected_system_uuid=svc_a.node.system_uuid)
            result = await metadata_session.send_summary_and_records(conn, [record], stream_id=1, timeout=10)
            await conn.close()
            self.assertEqual(1, result["sent"])

            for _ in range(100):
                if reconciled:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(1, len(reconciled))
            peer_uuid, reconcile_result = reconciled[0]
            self.assertEqual(svc_b.node.system_uuid, peer_uuid)
            self.assertEqual(1, len(reconcile_result["upserts"]))
            self.assertEqual("g1", reconcile_result["upserts"][0]["game_id"])
            # The real fix under test: on_metadata_requested() must be called
            # WITH the real authenticated peer's system_uuid, not blindly --
            # otherwise the caller has no way to return that peer's own
            # previously-mirrored state (see backend.py's
            # _sync_mirrored_items_for_peer()).
            self.assertEqual([svc_b.node.system_uuid], requested_for)
        finally:
            await svc_a.stop()
            await svc_b.stop()

    async def test_stale_push_is_rejected_when_a_newer_mirror_is_already_known(self):
        """The exact defect Codex's review found: if on_metadata_requested()
        returns the receiver's real prior mirror of this peer's record (a
        newer revision than what's being pushed now), the older push must be
        ignored, not blindly upserted over the newer mirror."""
        from pythia.core.sync import metadata_session

        newer_mirror = metadata.create_record(
            {"system_uuid": self.uuid_b, "game_id": "g1", "title": "Newer, already mirrored"}, revision=5
        )
        reconciled = []
        svc_a = service.SyncService(
            base_dir=self.base_dir_a,
            system_uuid=self.uuid_a,
            on_metadata_requested=lambda peer_uuid: [newer_mirror] if peer_uuid == self.uuid_b else [],
            on_metadata_reconciled=lambda peer_uuid, result: reconciled.append((peer_uuid, result)),
        )
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)
        await svc_b.start(listen_host="127.0.0.1", listen_port=0)
        try:
            _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)
            port = svc_a._listener.sockets[0].getsockname()[1]

            stale_push = metadata.create_record(
                {"system_uuid": self.uuid_b, "game_id": "g1", "title": "Stale, older push"}, revision=2
            )
            conn = await direct_tcp.connect("127.0.0.1", port, svc_b.node, expected_system_uuid=svc_a.node.system_uuid)
            result = await metadata_session.send_summary_and_records(conn, [stale_push], stream_id=1, timeout=10)
            await conn.close()
            self.assertEqual(0, result["sent"])  # never even requested -- summary comparison rejected it up front

            for _ in range(100):
                if reconciled:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(1, len(reconciled))
            _, reconcile_result = reconciled[0]
            self.assertEqual(0, len(reconcile_result["upserts"]))
        finally:
            await svc_a.stop()
            await svc_b.stop()

    async def test_push_with_no_requested_hook_wired_is_a_real_no_op(self):
        """A never wired on_metadata_requested() -- the push must be
        honestly refused (connection closed), not silently treated as a
        success."""
        from pythia.core.sync import metadata_session

        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)
        await svc_b.start(listen_host="127.0.0.1", listen_port=0)
        try:
            _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)
            port = svc_a._listener.sockets[0].getsockname()[1]

            record = metadata.create_record(
                {"system_uuid": svc_b.node.system_uuid, "game_id": "g1", "title": "Pushed Game"}, revision=1
            )
            conn = await direct_tcp.connect("127.0.0.1", port, svc_b.node, expected_system_uuid=svc_a.node.system_uuid)
            with self.assertRaises((ConnectionError, Exception)):
                await metadata_session.send_summary_and_records(conn, [record], stream_id=1, timeout=5)
            await conn.close()
        finally:
            await svc_a.stop()
            await svc_b.stop()


class MetadataPullTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp_a, self.base_dir_a = _make_node_and_dir()
        self._tmp_b, self.base_dir_b = _make_node_and_dir()
        self.uuid_a = str(uuid.uuid4())
        self.uuid_b = str(uuid.uuid4())
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_a)
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_b)

    async def asyncTearDown(self):
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    async def test_pull_fetches_the_peers_own_published_state(self):
        """A pulls from B: B's passive listener answers via
        on_metadata_pull_requested() with its own real items, and A's
        connecting side reconciles them for real -- the symmetric
        counterpart to the push path, using the SAME underlying wire
        exchange (send_summary_and_records/receive_summary_and_reconcile),
        just with an explicit METADATA_PULL_REQUEST kicking it off so
        neither side deadlocks waiting to receive first."""
        from pythia.core.sync import metadata_session

        own_record_b = metadata.create_record(
            {"system_uuid": self.uuid_b, "game_id": "g1", "title": "B's Own Game"}, revision=1
        )
        svc_b = service.SyncService(
            base_dir=self.base_dir_b, system_uuid=self.uuid_b, on_metadata_pull_requested=lambda: [own_record_b]
        )
        await svc_b.start(listen_host="127.0.0.1", listen_port=0)
        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        try:
            _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)
            port = svc_b._listener.sockets[0].getsockname()[1]

            conn = await direct_tcp.connect("127.0.0.1", port, svc_a.node, expected_system_uuid=svc_b.node.system_uuid)
            result = await metadata_session.pull_and_reconcile(
                conn, [], peer_system_uuid=svc_b.node.system_uuid, stream_id=1, timeout=10
            )
            await conn.close()

            self.assertEqual(1, len(result["upserts"]))
            self.assertEqual("g1", result["upserts"][0]["game_id"])
            self.assertEqual(self.uuid_b, result["upserts"][0]["owner_system_uuid"])
        finally:
            await svc_a.stop()
            await svc_b.stop()

    async def test_pull_with_no_hook_wired_is_a_real_no_op(self):
        from pythia.core.sync import metadata_session

        svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)
        await svc_b.start(listen_host="127.0.0.1", listen_port=0)
        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        try:
            _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)
            port = svc_b._listener.sockets[0].getsockname()[1]
            conn = await direct_tcp.connect("127.0.0.1", port, svc_a.node, expected_system_uuid=svc_b.node.system_uuid)
            with self.assertRaises(Exception):
                await metadata_session.pull_and_reconcile(
                    conn, [], peer_system_uuid=svc_b.node.system_uuid, stream_id=1, timeout=5
                )
            await conn.close()
        finally:
            await svc_a.stop()
            await svc_b.stop()


class TransferCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp_a, self.base_dir_a = _make_node_and_dir()
        self._tmp_b, self.base_dir_b = _make_node_and_dir()
        self.uuid_a = str(uuid.uuid4())
        self.uuid_b = str(uuid.uuid4())
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_a)
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_b)

    async def asyncTearDown(self):
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    async def test_cancel_transfer_interrupts_receive_and_leaves_no_destination_file(self):
        """A real, larger transfer is accepted, then cancelled mid-flight
        from the receiving side's own cancel_transfer() -- the destination
        must never contain a materialized (necessarily-incomplete) file,
        since materialize() only ever runs after every block is verified."""
        with tempfile.TemporaryDirectory() as source_root, tempfile.TemporaryDirectory() as dest_root, tempfile.TemporaryDirectory() as cache_root:
            source = Path(source_root) / "game"
            source.mkdir()
            (source / "game.exe").write_bytes(b"x" * (1024 * 1024))  # large enough to not finish in one batch

            events = []
            svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a, on_status=events.append)
            destination = Path(dest_root) / "received"

            def on_offer(offer_id, offer, peer_uuid):
                svc_a.resolve_offer(offer_id, accept=True, destination=str(destination), cache=cache_root)

            svc_a._on_transfer_offer = on_offer
            await svc_a.start(listen_host="127.0.0.1", listen_port=0)
            svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)
            await svc_b.start(listen_host="127.0.0.1", listen_port=0)
            try:
                _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)
                port = svc_a._listener.sockets[0].getsockname()[1]

                from pythia.core.sync import transfer_offer

                async def sender():
                    conn = await direct_tcp.connect(
                        "127.0.0.1", port, svc_b.node, expected_system_uuid=svc_a.node.system_uuid
                    )
                    try:
                        return await transfer_offer.offer_and_send_game(
                            conn, source, game_id="g1", display_name="Test Game", stream_id=1, timeout=10
                        )
                    finally:
                        await conn.close()

                async def cancel_once_active():
                    for _ in range(100):
                        if svc_a._active_transfers:
                            break
                        await asyncio.sleep(0.01)
                    self.assertTrue(svc_a._active_transfers, "transfer never became active to cancel")
                    cancelled = await svc_a.cancel_transfer(svc_b.node.system_uuid)
                    self.assertTrue(cancelled)

                try:
                    await asyncio.wait_for(asyncio.gather(sender(), cancel_once_active()), timeout=10)
                except Exception:
                    pass  # the sender is EXPECTED to fail (TRANSFER_CANCEL) -- only asserting real effects below

                for _ in range(50):
                    if any(e.get("event") == "transfer_cancelled" for e in events):
                        break
                    await asyncio.sleep(0.05)
                self.assertTrue(any(e.get("event") == "transfer_cancelled" for e in events))
                self.assertFalse(destination.exists())
                self.assertFalse(svc_a._active_transfers)
            finally:
                await svc_a.stop()
                await svc_b.stop()

    async def test_cancel_transfer_for_unknown_peer_returns_false(self):
        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        try:
            self.assertFalse(await svc_a.cancel_transfer("no-such-peer"))
        finally:
            await svc_a.stop()


class ShutdownWithPendingConnectionTests(unittest.IsolatedAsyncioTestCase):
    """A real hang found while building the relay-rendezvous milestone's
    own "shutdown during in-flight work" test: Python 3.13 changed
    `asyncio.base_events.Server.wait_closed()` to block until every
    still-running per-connection handler task actually returns. A
    connection stuck waiting on an unresolved transfer-offer decision was
    never tracked anywhere stop() looked, so `await listener.wait_closed()`
    would hang forever. Fixed via SyncService._connection_tasks tracking
    every _on_connected() invocation, cancelled before the listener is ever
    closed."""

    async def asyncSetUp(self):
        self._tmp_a, self.base_dir_a = _make_node_and_dir()
        self._tmp_b, self.base_dir_b = _make_node_and_dir()
        self.uuid_a = str(uuid.uuid4())
        self.uuid_b = str(uuid.uuid4())
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_a)
        sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=self.base_dir_b)

    async def asyncTearDown(self):
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    async def test_stop_returns_promptly_with_an_unresolved_pending_offer(self):
        from pythia.core.sync import transfer_offer

        svc_a = service.SyncService(base_dir=self.base_dir_a, system_uuid=self.uuid_a)  # never resolves the offer
        await svc_a.start(listen_host="127.0.0.1", listen_port=0)
        svc_b = service.SyncService(base_dir=self.base_dir_b, system_uuid=self.uuid_b)
        await svc_b.start(listen_host="127.0.0.1", listen_port=0)
        try:
            _trust_each_other(svc_a.node, svc_b.node, self.base_dir_a, self.base_dir_b)
            port = svc_a._listener.sockets[0].getsockname()[1]

            conn = await direct_tcp.connect("127.0.0.1", port, svc_b.node, expected_system_uuid=svc_a.node.system_uuid)
            send_task = asyncio.ensure_future(
                transfer_offer.offer_and_send_game(conn, "/nonexistent", game_id="g1", display_name="Test", stream_id=1, timeout=125)
            )
            try:
                for _ in range(100):
                    if svc_a._connection_tasks:
                        break
                    await asyncio.sleep(0.02)
                self.assertTrue(svc_a._connection_tasks, "connection never registered as pending")

                # The real regression: this must NOT hang.
                result = await asyncio.wait_for(svc_a.stop(), timeout=5)
                self.assertEqual({"status": "ok"}, result)
                self.assertEqual(set(), svc_a._connection_tasks)
            finally:
                send_task.cancel()
                try:
                    await send_task
                except (asyncio.CancelledError, Exception):
                    pass
                await conn.close()
        finally:
            await svc_b.stop()


class RetryBackoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_loop_schedules_backoff_for_unreachable_trusted_peer(self):
        """A trusted device with a known but unreachable address should show
        up in retry_state() with a growing attempt count and honest
        last-error -- never fake reachability. Speeds up the module's real
        constants for this test only, restored in the finally block."""
        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=base_dir)
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            await svc.start(listen_host="127.0.0.1", listen_port=0)
            try:
                trust.add_trusted(
                    system_uuid="unreachable-peer",
                    device_id="AAAAAAB-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA",
                    certificate_fingerprint="0" * 64,
                    friendly_name="Unreachable",
                    known_addresses=["127.0.0.1"],
                    base_dir=base_dir,
                )
                original_interval, original_base = service.RETRY_CHECK_INTERVAL, service.RETRY_BASE_SECONDS
                service.RETRY_CHECK_INTERVAL = 0.05
                service.RETRY_BASE_SECONDS = 0.01
                try:
                    for _ in range(100):
                        state = svc.retry_state()
                        if state.get("unreachable-peer", {}).get("attempts", 0) >= 1:
                            break
                        await asyncio.sleep(0.05)
                    state = svc.retry_state()
                    self.assertIn("unreachable-peer", state)
                    self.assertGreaterEqual(state["unreachable-peer"]["attempts"], 1)
                    self.assertIn("last_error", state["unreachable-peer"])
                finally:
                    service.RETRY_CHECK_INTERVAL = original_interval
                    service.RETRY_BASE_SECONDS = original_base
            finally:
                await svc.stop()
            # stop() must really cancel the retry loop -- no further state changes after this point.
            self.assertIsNone(svc._retry_task)
        finally:
            tmp.cleanup()


class GlobalDiscoveryInvocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_announces_and_queries_trusted_peers_via_configured_server(self):
        """Real invocation, not just "the module exists": start() with
        global_discovery enabled and a real server_url actually calls
        global_discovery.announce()/query_device() with the configured URL,
        and a real candidate address returned by query_device() updates both
        trust.update_known_addresses() (persisted) and known_peers()
        (in-memory status)."""
        from unittest import mock

        from pythia.core.sync import global_discovery

        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save(
                {"enabled": True, "listen": True, "lan_discovery": False, "global_discovery": True}, base_dir=base_dir
            )
            trust.add_trusted(
                system_uuid="remote-peer",
                device_id="AAAAAAB-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA",
                certificate_fingerprint="0" * 64,
                friendly_name="Remote",
                base_dir=base_dir,
            )
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))

            announce_calls = []
            query_calls = []

            def fake_announce(server_url, node, addresses, **kwargs):
                announce_calls.append((server_url, tuple(addresses)))
                return None

            def fake_query(server_url, device_id, **kwargs):
                query_calls.append((server_url, device_id))
                return ["203.0.113.5:24030"]

            with mock.patch.object(global_discovery, "announce", side_effect=fake_announce), mock.patch.object(
                global_discovery, "query_device", side_effect=fake_query
            ):
                await svc.start(
                    listen_host="127.0.0.1", listen_port=0, global_discovery_server="https://discover.example/v2/"
                )
                try:
                    for _ in range(100):
                        if announce_calls and query_calls:
                            break
                        await asyncio.sleep(0.05)
                finally:
                    await svc.stop()

            self.assertTrue(announce_calls)
            self.assertEqual("https://discover.example/v2/", announce_calls[0][0])
            self.assertTrue(query_calls)
            self.assertEqual(
                ("https://discover.example/v2/", "AAAAAAB-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA"),
                query_calls[0],
            )
            record = trust.get_trusted("remote-peer", base_dir=base_dir)
            self.assertEqual(["203.0.113.5:24030"], record["known_addresses"])
        finally:
            tmp.cleanup()

    async def test_stop_really_cancels_the_global_discovery_loop(self):
        from unittest import mock

        from pythia.core.sync import global_discovery

        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save(
                {"enabled": True, "listen": True, "lan_discovery": False, "global_discovery": True}, base_dir=base_dir
            )
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            with mock.patch.object(global_discovery, "announce", return_value=None), mock.patch.object(
                global_discovery, "query_device", return_value=[]
            ):
                await svc.start(
                    listen_host="127.0.0.1", listen_port=0, global_discovery_server="https://discover.example/v2/"
                )
                self.assertIsNotNone(svc._global_discovery_task)
                await svc.stop()
            self.assertIsNone(svc._global_discovery_task)
        finally:
            tmp.cleanup()

    async def test_stop_waits_for_inflight_discovery_request(self):
        from unittest import mock

        from pythia.core.sync import global_discovery

        tmp, base_dir = _make_node_and_dir()
        entered = threading.Event()
        release = threading.Event()
        try:
            sync_config.save(
                {"enabled": True, "listen": True, "lan_discovery": False, "global_discovery": True}, base_dir=base_dir
            )
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))

            def blocking_announce(*args, **kwargs):
                entered.set()
                release.wait(2)

            with mock.patch.object(global_discovery, "announce", side_effect=blocking_announce):
                await svc.start(
                    listen_host="127.0.0.1", listen_port=0,
                    global_discovery_server="https://discover.example/v2/",
                )
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                threading.Timer(0.15, release.set).start()
                started = time.monotonic()
                await svc.stop()
                self.assertGreaterEqual(time.monotonic() - started, 0.10)
                self.assertIsNone(svc._global_discovery_executor)
        finally:
            release.set()
            tmp.cleanup()

    async def test_disabled_global_discovery_never_starts_the_loop(self):
        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save(
                {"enabled": True, "listen": True, "lan_discovery": False, "global_discovery": False}, base_dir=base_dir
            )
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            await svc.start(
                listen_host="127.0.0.1", listen_port=0, global_discovery_server="https://discover.example/v2/"
            )
            self.assertIsNone(svc._global_discovery_task)
            await svc.stop()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
