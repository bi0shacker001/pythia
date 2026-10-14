"""Real tests for pythia.core.sync.relay -- control-plane-only rendezvous.
A real loopback RelayServer + two RelayClients (mirroring
tests/test_sync_direct_tcp.py's own two-isolated-identities pattern), plus
pure-function tests for the signed candidate-offer authentication.
"""

import asyncio
import base64
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from pythia.core.sync import protocol, relay, trust
from pythia.core.sync.connectivity.direct_tcp import load_local_node


def _make_node(base_dir: Path, name: str):
    return load_local_node(name, base_dir=base_dir, system_uuid=str(uuid.uuid4()))


def _trust_each_other(node_a, node_b) -> None:
    trust.add_trusted(
        system_uuid=node_b.system_uuid,
        device_id=node_b.device_id,
        certificate_fingerprint=node_b.identity_record["certificate_fingerprint"],
        friendly_name="System B",
        base_dir=node_a.base_dir,
    )
    trust.add_trusted(
        system_uuid=node_a.system_uuid,
        device_id=node_a.device_id,
        certificate_fingerprint=node_a.identity_record["certificate_fingerprint"],
        friendly_name="System A",
        base_dir=node_b.base_dir,
    )


class CandidatePayloadSigningTests(unittest.TestCase):
    """Pure signature verification -- no network at all."""

    def setUp(self):
        self._tmp_a = tempfile.TemporaryDirectory()
        self._tmp_b = tempfile.TemporaryDirectory()
        self.node_a = _make_node(Path(self._tmp_a.name), "A")
        self.node_b = _make_node(Path(self._tmp_b.name), "B")
        _trust_each_other(self.node_a, self.node_b)

    def tearDown(self):
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    def test_real_signed_offer_verifies(self):
        payload = relay.sign_candidate_payload(self.node_b, to_system_uuid=self.node_a.system_uuid, candidates=["203.0.113.5:24030"])
        candidates = relay.verify_candidate_payload(payload, expected_from_system_uuid=self.node_b.system_uuid, base_dir=self.node_a.base_dir)
        self.assertEqual(["203.0.113.5:24030"], candidates)

    def test_forged_sender_claim_is_rejected(self):
        """B signs a real payload, but something claims it came from a
        different (unexpected) peer -- must be rejected before any
        candidate is trusted."""
        payload = relay.sign_candidate_payload(self.node_b, to_system_uuid=self.node_a.system_uuid, candidates=["203.0.113.5:24030"])
        with self.assertRaises(relay.RelayAuthError):
            relay.verify_candidate_payload(payload, expected_from_system_uuid="not-node-b-at-all", base_dir=self.node_a.base_dir)

    def test_tampered_candidates_after_signing_is_rejected(self):
        payload = relay.sign_candidate_payload(self.node_b, to_system_uuid=self.node_a.system_uuid, candidates=["203.0.113.5:24030"])
        payload["candidates"] = ["10.0.0.1:9999"]  # tampered post-signature
        with self.assertRaises(relay.RelayAuthError):
            relay.verify_candidate_payload(payload, expected_from_system_uuid=self.node_b.system_uuid, base_dir=self.node_a.base_dir)

    def test_untrusted_sender_is_rejected(self):
        """A well-formed, correctly-signed offer from a device we have never
        paired with must still be rejected -- an untrusted relay can never
        introduce a new device this way."""
        stranger_dir = tempfile.TemporaryDirectory()
        try:
            stranger = _make_node(Path(stranger_dir.name), "Stranger")
            payload = relay.sign_candidate_payload(stranger, to_system_uuid=self.node_a.system_uuid, candidates=["203.0.113.5:24030"])
            with self.assertRaises(relay.RelayAuthError):
                relay.verify_candidate_payload(payload, expected_from_system_uuid=stranger.system_uuid, base_dir=self.node_a.base_dir)
        finally:
            stranger_dir.cleanup()

    def test_stale_offer_is_rejected(self):
        payload = relay.sign_candidate_payload(
            self.node_b, to_system_uuid=self.node_a.system_uuid, candidates=["203.0.113.5:24030"],
            issued_at=int(time.time()) - int(relay.MAX_CANDIDATE_OFFER_AGE) - 10,
        )
        with self.assertRaises(relay.RelayAuthError):
            relay.verify_candidate_payload(payload, expected_from_system_uuid=self.node_b.system_uuid, base_dir=self.node_a.base_dir)

    def test_forged_certificate_device_id_mismatch_is_rejected(self):
        payload = relay.sign_candidate_payload(self.node_b, to_system_uuid=self.node_a.system_uuid, candidates=["203.0.113.5:24030"])
        payload["from_device_id"] = self.node_a.device_id  # claims a different device than the certificate
        with self.assertRaises(relay.RelayAuthError):
            relay.verify_candidate_payload(payload, expected_from_system_uuid=self.node_b.system_uuid, base_dir=self.node_a.base_dir)


class RelayServerJoinTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_join_authenticates_and_routes_between_two_devices(self):
        """End-to-end over real loopback TCP: two RelayClients join a real
        RelayServer, one sends a signed CANDIDATE_OFFER addressed to the
        other, and the receiving side gets the exact real frame, verifiable
        with verify_candidate_payload()."""
        tmp_a = tempfile.TemporaryDirectory()
        tmp_b = tempfile.TemporaryDirectory()
        try:
            node_a = _make_node(Path(tmp_a.name), "A")
            node_b = _make_node(Path(tmp_b.name), "B")
            _trust_each_other(node_a, node_b)

            server = relay.RelayServer()
            await server.start("127.0.0.1", 0)
            port = server._server.sockets[0].getsockname()[1]

            received_offers = []
            client_a = relay.RelayClient(node_a, on_candidate_offer=received_offers.append)
            client_b = relay.RelayClient(node_b)
            try:
                await client_a.connect("127.0.0.1", port)
                await client_b.connect("127.0.0.1", port)
                self.assertTrue(client_a.connected)
                self.assertTrue(client_b.connected)

                await client_b.send_candidate_offer(node_a.system_uuid, ["203.0.113.5:24030"])

                for _ in range(100):
                    if received_offers:
                        break
                    await asyncio.sleep(0.05)
                self.assertEqual(1, len(received_offers))
                candidates = relay.verify_candidate_payload(
                    received_offers[0], expected_from_system_uuid=node_b.system_uuid, base_dir=node_a.base_dir
                )
                self.assertEqual(["203.0.113.5:24030"], candidates)
            finally:
                await client_a.close()
                await client_b.close()
                await server.close()
        finally:
            tmp_a.cleanup()
            tmp_b.cleanup()

    async def test_join_with_forged_device_id_is_rejected(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            node = _make_node(Path(tmp.name), "A")
            server = relay.RelayServer()
            await server.start("127.0.0.1", 0)
            port = server._server.sockets[0].getsockname()[1]
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                decoder = protocol.FrameDecoder(relayed=True)
                challenge = await relay._read_frame(reader, decoder, timeout=5)
                nonce = challenge.json()["nonce"]
                join = relay._build_join_payload(node, nonce)
                join["device_id"] = "AAAAAAB-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA"  # forged, doesn't match cert
                writer.write(protocol.encode_json(protocol.MessageType.HELLO, join, relayed=True))
                await writer.drain()
                # A real rejection closes the connection instead of sending CAPABILITIES.
                with self.assertRaises((ConnectionError, asyncio.TimeoutError)):
                    await relay._read_frame(reader, decoder, timeout=2)
                writer.close()
            finally:
                await server.close()
        finally:
            tmp.cleanup()

    async def test_control_only_relay_rejects_bulk_message_types(self):
        """Real enforcement, not just documentation: attempting to encode a
        MANIFEST frame with relayed=True must fail before it can ever reach
        the wire -- protocol.py's own allowlist, exercised here through this
        module's real usage path."""
        tmp = tempfile.TemporaryDirectory()
        try:
            node = _make_node(Path(tmp.name), "A")
            with self.assertRaises(protocol.ProtocolError):
                protocol.encode_json(protocol.MessageType.MANIFEST, {"files": []}, relayed=True)
        finally:
            tmp.cleanup()

    async def test_server_shutdown_mid_flight_closes_all_routes(self):
        tmp_a = tempfile.TemporaryDirectory()
        tmp_b = tempfile.TemporaryDirectory()
        try:
            node_a = _make_node(Path(tmp_a.name), "A")
            node_b = _make_node(Path(tmp_b.name), "B")
            server = relay.RelayServer()
            await server.start("127.0.0.1", 0)
            port = server._server.sockets[0].getsockname()[1]

            client_a = relay.RelayClient(node_a)
            client_b = relay.RelayClient(node_b)
            await client_a.connect("127.0.0.1", port)
            await client_b.connect("127.0.0.1", port)
            self.assertEqual(2, len(server._routes))

            await server.close()
            self.assertEqual(0, len(server._routes))
            await client_a.close()
            await client_b.close()
        finally:
            tmp_a.cleanup()
            tmp_b.cleanup()

    async def test_offer_to_unknown_target_is_dropped_not_errored(self):
        """No route registered for the target -- a real, honest silent
        drop (the sender gets no error; a relay cannot know whether the
        target is merely offline vs. never-existed, same ambiguity any real
        rendezvous relay has)."""
        tmp_a = tempfile.TemporaryDirectory()
        tmp_b = tempfile.TemporaryDirectory()
        try:
            node_a = _make_node(Path(tmp_a.name), "A")
            node_b = _make_node(Path(tmp_b.name), "B")
            _trust_each_other(node_a, node_b)
            server = relay.RelayServer()
            await server.start("127.0.0.1", 0)
            port = server._server.sockets[0].getsockname()[1]
            client_b = relay.RelayClient(node_b)
            try:
                await client_b.connect("127.0.0.1", port)
                await client_b.send_candidate_offer("no-such-system", ["203.0.113.5:24030"])
                await asyncio.sleep(0.2)  # nothing should crash or hang
                self.assertTrue(client_b.connected)
            finally:
                await client_b.close()
                await server.close()
        finally:
            tmp_a.cleanup()
            tmp_b.cleanup()


if __name__ == "__main__":
    unittest.main()
