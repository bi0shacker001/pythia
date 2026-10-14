import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pythia.core.sync import config, discovery, trust
from pythia.core.sync.connectivity.direct_tcp import load_local_node
from pythia.core.sync.lan import LanDiscoveryProtocol, start


class LanProtocolTests(unittest.TestCase):
    def test_only_trusted_valid_nonself_packets_emit_and_replays_are_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = load_local_node("A", base_dir=root / "a", system_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
            b = load_local_node("B", base_dir=root / "b", system_uuid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
            trust.add_trusted(system_uuid=a.system_uuid, device_id=a.device_id,
                              certificate_fingerprint=a.identity_record["certificate_fingerprint"],
                              friendly_name="A", base_dir=root / "b")
            seen = []
            receiver = LanDiscoveryProtocol(local_system_uuid=b.system_uuid, base_dir=root / "b", on_peer=seen.append)
            packet = discovery.create_announcement(a, 22000, ["tcp://:22000"])
            receiver.datagram_received(packet, ("192.0.2.4", 21027))
            receiver.datagram_received(packet, ("192.0.2.4", 21027))
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0]["source_address"], "192.0.2.4")


class LanLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_gate_opens_no_socket(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            node = mock.Mock(system_uuid="local")
            loop = asyncio.get_running_loop()
            with mock.patch.object(loop, "create_datagram_endpoint", wraps=loop.create_datagram_endpoint) as create:
                with self.assertRaises(config.SyncConfigError):
                    await start(node, direct_port=22000, addresses=["tcp://:22000"], on_peer=lambda _p: None, base_dir=base)
                create.assert_not_called()

    async def test_enabled_gate_sends_immediate_announcement_and_closes(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config.save({"enabled": True, "lan_discovery": True}, base_dir=base)
            node = load_local_node("Local", base_dir=base)
            transport = mock.Mock()
            loop = asyncio.get_running_loop()

            async def fake_endpoint(factory, **_kwargs):
                proto = factory()
                proto.connection_made(transport)
                return transport, proto

            with mock.patch.object(loop, "create_datagram_endpoint", side_effect=fake_endpoint):
                service = await start(node, direct_port=22000, addresses=["tcp://:22000"],
                                      on_peer=lambda _p: None, base_dir=base, interval=60)
                await asyncio.sleep(0)
                transport.sendto.assert_called_once()
                await service.close()
                transport.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
