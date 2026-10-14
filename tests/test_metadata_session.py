"""Real tests for pythia.core.sync.metadata_session -- the wire-level
repository summary/record-request/record-data/tombstone exchange layered over
metadata.py's pure classification functions. Uses the same real loopback
TCP/TLS pattern as tests/test_sync_direct_tcp.py: two isolated identities,
real HELLO-authenticated connections, no mocking of the protocol itself.
"""

import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path

from pythia.core.sync import metadata, metadata_session, trust
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


class MetadataSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp_a = tempfile.TemporaryDirectory()
        self._tmp_b = tempfile.TemporaryDirectory()
        self.node_a = _make_node(Path(self._tmp_a.name), "System A")
        self.node_b = _make_node(Path(self._tmp_b.name), "System B")
        _trust_each_other(self.node_a, self.node_b)
        self.accepted: asyncio.Queue = asyncio.Queue()
        self.server = await direct_tcp.start_listener("127.0.0.1", 0, self.node_a, self._on_connected)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _on_connected(self, conn):
        await self.accepted.put(conn)

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    async def _connect(self):
        client_conn = await direct_tcp.connect(
            "127.0.0.1", self.port, self.node_b, expected_system_uuid=self.node_a.system_uuid
        )
        server_conn = await asyncio.wait_for(self.accepted.get(), timeout=5)
        return client_conn, server_conn

    async def test_peer_only_receives_records_it_actually_owns(self):
        """Node A (offering side) owns records for two different systems (its
        own, and a stray record claiming a THIRD system's ownership -- a real
        bug if it ever leaked). Node B (receiving side) must only ever
        request/receive items whose owner matches Node A's real system_uuid,
        proving the peer-ownership namespace filter in
        receive_summary_and_reconcile() actually works, not just that it
        compiles."""
        own_record = metadata.create_record(
            {"system_uuid": self.node_a.system_uuid, "game_id": "g1", "title": "Game One"}, revision=1
        )
        third_party_record = metadata.create_record(
            {"system_uuid": "some-other-system-entirely", "game_id": "g2", "title": "Not A's"}, revision=1
        )
        local_items_a = [own_record, third_party_record]

        client_conn, server_conn = await self._connect()
        try:
            offer_task = asyncio.create_task(
                metadata_session.send_summary_and_records(server_conn, local_items_a, stream_id=1)
            )
            result = await metadata_session.receive_summary_and_reconcile(
                client_conn, [], peer_system_uuid=self.node_a.system_uuid, stream_id=1
            )
            offer_result = await offer_task

            self.assertEqual(1, offer_result["sent"])  # only the A-owned record, never the third-party one
            self.assertEqual(1, len(result["upserts"]))
            self.assertEqual("g1", result["upserts"][0]["game_id"])
            self.assertEqual(self.node_a.system_uuid, result["upserts"][0]["owner_system_uuid"])
        finally:
            await client_conn.close()
            await server_conn.close()

    async def test_equal_revision_conflict_is_surfaced_not_auto_resolved(self):
        shared_record_a = metadata.create_record(
            {"system_uuid": self.node_a.system_uuid, "game_id": "g1", "title": "Version From A"}, revision=3
        )
        conflicting_local = metadata.create_record(
            {"system_uuid": self.node_a.system_uuid, "game_id": "g1", "title": "Version B Already Has"}, revision=3
        )

        client_conn, server_conn = await self._connect()
        try:
            offer_task = asyncio.create_task(
                metadata_session.send_summary_and_records(server_conn, [shared_record_a], stream_id=1)
            )
            result = await metadata_session.receive_summary_and_reconcile(
                client_conn, [conflicting_local], peer_system_uuid=self.node_a.system_uuid, stream_id=1
            )
            await offer_task

            self.assertEqual(0, len(result["upserts"]))
            self.assertEqual(1, len(result["conflicts"]))
            self.assertEqual("g1", result["conflicts"][0]["incoming"]["game_id"])
        finally:
            await client_conn.close()
            await server_conn.close()

    async def test_tombstone_propagates_as_tombstone(self):
        tombstone = metadata.create_tombstone(self.node_a.system_uuid, "g1", revision=2)

        client_conn, server_conn = await self._connect()
        try:
            offer_task = asyncio.create_task(
                metadata_session.send_summary_and_records(server_conn, [tombstone], stream_id=1)
            )
            result = await metadata_session.receive_summary_and_reconcile(
                client_conn, [], peer_system_uuid=self.node_a.system_uuid, stream_id=1
            )
            await offer_task

            self.assertEqual(1, len(result["tombstones"]))
            self.assertEqual("tombstone", result["tombstones"][0]["kind"])
        finally:
            await client_conn.close()
            await server_conn.close()

    async def test_peer_cannot_publish_a_record_it_does_not_own(self):
        """A record whose owner_system_uuid doesn't match the connection's
        authenticated peer is a real protocol violation -- validate_item()
        must reject it, not silently accept a spoofed entry."""
        forged_record = metadata.create_record(
            {"system_uuid": "not-node-a-at-all", "game_id": "g1", "title": "Forged"}, revision=1
        )
        forged_record["owner_system_uuid"] = "not-node-a-at-all"

        client_conn, server_conn = await self._connect()
        try:
            offer_task = asyncio.create_task(
                metadata_session.send_summary_and_records(server_conn, [forged_record], stream_id=1)
            )
            # receive_summary_and_reconcile only requests keys under the real
            # peer's own namespace prefix, so the forged record (owned by
            # "not-node-a-at-all") is never even requested -- this proves the
            # namespace filter itself is the real defense.
            result = await metadata_session.receive_summary_and_reconcile(
                client_conn, [], peer_system_uuid=self.node_a.system_uuid, stream_id=1
            )
            offer_result = await offer_task

            self.assertEqual(0, offer_result["sent"])
            self.assertEqual(0, len(result["upserts"]))
            self.assertEqual(0, len(result["tombstones"]))
        finally:
            await client_conn.close()
            await server_conn.close()


if __name__ == "__main__":
    unittest.main()
