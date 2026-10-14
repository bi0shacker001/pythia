import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path

from pythia.core.sync import protocol, trust
from pythia.core.sync.connectivity import direct_tcp


def _make_node(base_dir: Path, name: str) -> direct_tcp.LocalNode:
    return direct_tcp.load_local_node(name, base_dir=base_dir, system_uuid=str(uuid.uuid4()))


def _trust_each_other(node_a: direct_tcp.LocalNode, node_b: direct_tcp.LocalNode) -> None:
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


class DirectTcpLoopbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp_a = tempfile.TemporaryDirectory()
        self._tmp_b = tempfile.TemporaryDirectory()
        self.node_a = _make_node(Path(self._tmp_a.name), "System A")
        self.node_b = _make_node(Path(self._tmp_b.name), "System B")

    async def asyncTearDown(self):
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    async def test_successful_authentication_and_ping_pong(self):
        """Two isolated identities, real loopback TCP/TLS, real HELLO/
        CAPABILITIES/PING/PONG frames exchanged over pythia.core.sync.protocol."""
        _trust_each_other(self.node_a, self.node_b)
        accepted: asyncio.Queue = asyncio.Queue()

        async def on_connected(conn):
            await accepted.put(conn)

        server = await direct_tcp.start_listener("127.0.0.1", 0, self.node_a, on_connected)
        port = server.sockets[0].getsockname()[1]
        try:
            client_conn = await direct_tcp.connect(
                "127.0.0.1", port, self.node_b, expected_system_uuid=self.node_a.system_uuid
            )
            server_conn = await asyncio.wait_for(accepted.get(), timeout=5)

            self.assertEqual(self.node_a.system_uuid, client_conn.peer.system_uuid)
            self.assertEqual(self.node_a.device_id, client_conn.peer.device_id)
            self.assertEqual(self.node_b.system_uuid, server_conn.peer.system_uuid)
            self.assertEqual(self.node_b.device_id, server_conn.peer.device_id)

            async def answer_one_ping():
                frame = await server_conn.recv_frame(timeout=5)
                self.assertEqual(protocol.MessageType.PING, frame.message_type)
                await server_conn.send_json(protocol.MessageType.PONG, {})

            responder = asyncio.create_task(answer_one_ping())
            await client_conn.ping(timeout=5)
            await responder

            await client_conn.close()
            await server_conn.close()
        finally:
            server.close()
            await server.wait_closed()

    async def test_self_connection_rejected(self):
        """Connecting a system to a listener sharing the same system_uuid
        (e.g. a stray loopback/misconfigured address) must be rejected
        explicitly by the dedicated self-connection guard -- not merely
        happen to fail for the unrelated reason of not being trusted, so
        this test pre-trusts "itself" before connecting."""
        trust.add_trusted(
            system_uuid=self.node_a.system_uuid,
            device_id=self.node_a.device_id,
            certificate_fingerprint=self.node_a.identity_record["certificate_fingerprint"],
            friendly_name="Self",
            base_dir=self.node_a.base_dir,
        )
        server = await direct_tcp.start_listener("127.0.0.1", 0, self.node_a, lambda c: asyncio.sleep(0))
        port = server.sockets[0].getsockname()[1]
        try:
            # Reuses system A's own identity files (base_dir) -- guarantees
            # identical system_uuid/device_id, the real "connecting to
            # myself" condition.
            same_as_server = direct_tcp.LocalNode(self.node_a.identity_record, self.node_a.base_dir)
            with self.assertRaises(direct_tcp.SelfConnectionError):
                await direct_tcp.connect("127.0.0.1", port, same_as_server, handshake_timeout=3)
        finally:
            server.close()
            await server.wait_closed()

    async def test_unknown_peer_rejected(self):
        """No pairing/trust exists between A and B -- connecting must fail
        with an explicit UnknownDeviceError, not hang or silently succeed."""
        # Deliberately do NOT call _trust_each_other().
        server = await direct_tcp.start_listener("127.0.0.1", 0, self.node_a, lambda c: asyncio.sleep(0))
        port = server.sockets[0].getsockname()[1]
        try:
            with self.assertRaises(direct_tcp.UnknownDeviceError):
                await direct_tcp.connect("127.0.0.1", port, self.node_b, handshake_timeout=5)
        finally:
            server.close()
            await server.wait_closed()

    async def test_handshake_timeout(self):
        """A listener that accepts the TCP/TLS connection but never speaks
        the Pythia protocol must not hang the connector forever."""

        async def _silent_handler(reader, writer):
            await asyncio.sleep(5)  # outlives the client's short handshake_timeout
            writer.close()

        ssl_ctx = direct_tcp._build_server_ssl_context(self.node_a)
        raw_server = await asyncio.start_server(_silent_handler, "127.0.0.1", 0, ssl=ssl_ctx)
        port = raw_server.sockets[0].getsockname()[1]
        try:
            with self.assertRaises(direct_tcp.HandshakeTimeoutError):
                await direct_tcp.connect("127.0.0.1", port, self.node_b, handshake_timeout=0.3)
        finally:
            raw_server.close()
            await raw_server.wait_closed()

    async def test_clean_shutdown_then_recv_fails(self):
        _trust_each_other(self.node_a, self.node_b)
        accepted: asyncio.Queue = asyncio.Queue()

        server = await direct_tcp.start_listener("127.0.0.1", 0, self.node_a, accepted.put)
        port = server.sockets[0].getsockname()[1]
        try:
            client_conn = await direct_tcp.connect("127.0.0.1", port, self.node_b)
            server_conn = await asyncio.wait_for(accepted.get(), timeout=5)

            await client_conn.close()
            with self.assertRaises(ConnectionError):
                await server_conn.recv_frame(timeout=5)
        finally:
            server.close()
            await server.wait_closed()


class PinnedIdentityVerificationTests(unittest.TestCase):
    """Focused, non-async tests of the actual trust-verification logic
    (_extract_and_verify_identity) -- avoids depending on real-socket
    timing to prove the pinned-certificate-mismatch case specifically."""

    def setUp(self):
        self._tmp_a = tempfile.TemporaryDirectory()
        self._tmp_b = tempfile.TemporaryDirectory()
        self.node_a = _make_node(Path(self._tmp_a.name), "System A")
        self.node_b = _make_node(Path(self._tmp_b.name), "System B")

    def tearDown(self):
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    def _hello_for(self, node: direct_tcp.LocalNode) -> dict:
        return direct_tcp._hello_payload(node)

    def test_unknown_device_rejected(self):
        hello = self._hello_for(self.node_b)
        with self.assertRaises(direct_tcp.UnknownDeviceError):
            direct_tcp._extract_and_verify_identity(hello, base_dir=self.node_a.base_dir)

    def test_pinned_certificate_mismatch_rejected(self):
        # A trusts B, but under a device ID that doesn't match B's real
        # current certificate -- simulates B having regenerated its identity
        # without a fresh pairing exchange.
        trust.add_trusted(
            system_uuid=self.node_b.system_uuid,
            device_id="AAAAAAB-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA",
            certificate_fingerprint="0" * 64,
            friendly_name="Stale System B",
            base_dir=self.node_a.base_dir,
        )
        hello = self._hello_for(self.node_b)
        with self.assertRaises(direct_tcp.CertificateMismatchError):
            direct_tcp._extract_and_verify_identity(hello, base_dir=self.node_a.base_dir)

    def test_matching_pin_accepted(self):
        _trust_each_other(self.node_a, self.node_b)
        hello = self._hello_for(self.node_b)
        peer, cert_der = direct_tcp._extract_and_verify_identity(hello, base_dir=self.node_a.base_dir)
        self.assertEqual(self.node_b.system_uuid, peer.system_uuid)
        self.assertEqual(self.node_b.device_id, peer.device_id)


class FrameReaderFragmentationTests(unittest.IsolatedAsyncioTestCase):
    """Proves _FrameReader reassembles a frame delivered across many small
    reads, and correctly queues a second frame that arrived bundled with the
    first in one underlying read() call."""

    class _ChunkedReader:
        def __init__(self, chunks: list[bytes]):
            self._chunks = list(chunks)

        async def read(self, _n: int) -> bytes:
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

    async def test_frame_reassembled_across_many_small_chunks(self):
        encoded = protocol.encode_json(protocol.MessageType.PING, {"hello": "world"})
        # Split into 1-byte chunks to force many partial reads.
        chunks = [encoded[i : i + 1] for i in range(len(encoded))]
        reader = self._ChunkedReader(chunks)
        frame_reader = direct_tcp._FrameReader(reader, protocol.FrameDecoder())

        frame = await frame_reader.read_frame(timeout=5)
        self.assertEqual(protocol.MessageType.PING, frame.message_type)
        self.assertEqual({"hello": "world"}, frame.json())

    async def test_two_frames_in_one_chunk_are_both_delivered(self):
        first = protocol.encode_json(protocol.MessageType.HELLO, {"n": 1})
        second = protocol.encode_json(protocol.MessageType.PING, {"n": 2})
        reader = self._ChunkedReader([first + second])
        frame_reader = direct_tcp._FrameReader(reader, protocol.FrameDecoder())

        frame1 = await frame_reader.read_frame(timeout=5)
        frame2 = await frame_reader.read_frame(timeout=5)
        self.assertEqual(protocol.MessageType.HELLO, frame1.message_type)
        self.assertEqual(protocol.MessageType.PING, frame2.message_type)
        self.assertEqual({"n": 1}, frame1.json())
        self.assertEqual({"n": 2}, frame2.json())


class HandshakeTranscriptTests(unittest.TestCase):
    def test_proof_is_bound_to_both_peer_identities_and_protocol_domain(self):
        base = dict(
            nonce="00" * 32,
            listener_system_uuid="listener-system",
            listener_device_id="listener-device",
            connector_system_uuid="connector-system",
            connector_device_id="connector-device",
        )
        proof = direct_tcp._proof_message(**base)
        self.assertIn(b"pythia-direct-connect-proof-v1", proof)
        for field in (
            "listener_system_uuid",
            "listener_device_id",
            "connector_system_uuid",
            "connector_device_id",
            "nonce",
        ):
            changed = dict(base)
            changed[field] = changed[field] + "-changed"
            self.assertNotEqual(proof, direct_tcp._proof_message(**changed), field)

    def test_capabilities_shape_and_version_are_validated(self):
        valid = protocol.Frame(
            protocol.MessageType.CAPABILITIES,
            b'{"protocol_version":1,"features":["direct-transfer"]}',
        )
        self.assertEqual(["direct-transfer"], direct_tcp._validate_capabilities(valid)["features"])
        invalid_payloads = (
            b"[]",
            b'{"protocol_version":99,"features":[]}',
            b'{"protocol_version":1,"features":"all"}',
            b'{"protocol_version":1,"features":[1]}',
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(direct_tcp.DirectConnectError):
                direct_tcp._validate_capabilities(protocol.Frame(protocol.MessageType.CAPABILITIES, payload))


if __name__ == "__main__":
    unittest.main()
