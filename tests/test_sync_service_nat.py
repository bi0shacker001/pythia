"""Real tests for SyncService's NAT-traversal lifecycle wiring --
pythia.core.sync.nat's own protocol clients are already tested against real
fake routers in tests/test_nat_pcp_natpmp.py/test_nat_upnp.py; here the
`nat` module functions are mocked so these tests focus purely on
SyncService's own orchestration: fallback order, renewal scheduling,
deletion on stop/disable, live toggling, explicit retry, and zero-side-
effect defaults.
"""

import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from pythia.core.sync import config as sync_config
from pythia.core.sync import nat, service


def _make_node_and_dir():
    tmp = tempfile.TemporaryDirectory()
    base_dir = Path(tmp.name)
    return tmp, base_dir


class NatZeroSideEffectTests(unittest.IsolatedAsyncioTestCase):
    async def test_nat_disabled_by_default_never_calls_any_nat_function(self):
        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save({"enabled": True, "listen": True, "lan_discovery": False}, base_dir=base_dir)
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            with mock.patch.object(nat, "detect_default_gateway") as detect:
                await svc.start(listen_host="127.0.0.1", listen_port=0)
                await asyncio.sleep(0.2)
                try:
                    self.assertIsNone(svc._nat_task)
                    detect.assert_not_called()
                finally:
                    await svc.stop()
        finally:
            tmp.cleanup()

    async def test_nat_enabled_but_not_listening_creates_no_nat_task(self):
        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save(
                {"enabled": True, "listen": False, "lan_discovery": False, "nat_traversal": True}, base_dir=base_dir
            )
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            await svc.start(listen_host="127.0.0.1", listen_port=0)
            try:
                self.assertIsNone(svc._nat_task)
            finally:
                await svc.stop()
        finally:
            tmp.cleanup()

    async def test_globally_disabled_sync_never_starts_nat_regardless_of_flag(self):
        tmp, base_dir = _make_node_and_dir()
        try:
            sync_config.save({"enabled": False, "nat_traversal": True}, base_dir=base_dir)
            svc = service.SyncService(base_dir=base_dir, system_uuid=str(uuid.uuid4()))
            result = await svc.start(listen_host="127.0.0.1", listen_port=0)
            self.assertEqual({"status": "disabled"}, result)
            self.assertIsNone(svc._nat_task)
        finally:
            tmp.cleanup()


class NatLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp, self.base_dir = _make_node_and_dir()
        self.uuid = str(uuid.uuid4())
        sync_config.save(
            {"enabled": True, "listen": True, "lan_discovery": False, "nat_traversal": True}, base_dir=self.base_dir
        )

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_pcp_success_is_used_first_and_never_falls_back(self):
        fake_mapping = nat.NatMapping(
            protocol_name="pcp", external_address="8.8.8.8", external_port=51413, internal_port=51413,
            ip_protocol="tcp", lifetime_seconds=3600, obtained_at=0.0, nonce=b"x" * 12,
        )
        svc = service.SyncService(base_dir=self.base_dir, system_uuid=self.uuid)
        events = []
        svc._on_status = events.append
        with mock.patch.object(nat, "detect_default_gateway", return_value="192.168.1.1"), mock.patch.object(
            nat, "pcp_request_mapping", return_value=fake_mapping
        ) as pcp, mock.patch.object(nat, "natpmp_get_external_address") as natpmp, mock.patch.object(
            nat, "discover_upnp_gateway"
        ) as upnp:
            await svc.start(listen_host="127.0.0.1", listen_port=0)
            try:
                for _ in range(50):
                    if svc._nat_mapping is not None:
                        break
                    await asyncio.sleep(0.05)
                self.assertIsNotNone(svc._nat_mapping)
                self.assertEqual("pcp", svc._nat_mapping.protocol_name)
                pcp.assert_called_once()
                natpmp.assert_not_called()
                upnp.assert_not_called()
                self.assertTrue(any(e.get("event") == "nat_mapping_acquired" for e in events))
            finally:
                await svc.stop()

    async def test_falls_back_to_natpmp_when_pcp_fails(self):
        natpmp_mapping = nat.NatMapping(
            protocol_name="nat-pmp", external_address=None, external_port=51413, internal_port=51413,
            ip_protocol="tcp", lifetime_seconds=3600, obtained_at=0.0,
        )
        svc = service.SyncService(base_dir=self.base_dir, system_uuid=self.uuid)
        with mock.patch.object(nat, "detect_default_gateway", return_value="192.168.1.1"), mock.patch.object(
            nat, "pcp_request_mapping", side_effect=nat.NatUnsupportedError("no response")
        ), mock.patch.object(
            nat, "natpmp_get_external_address", return_value=("8.8.8.8", 100)
        ), mock.patch.object(
            nat, "natpmp_request_mapping", return_value=natpmp_mapping
        ) as natpmp, mock.patch.object(nat, "discover_upnp_gateway") as upnp:
            await svc.start(listen_host="127.0.0.1", listen_port=0)
            try:
                for _ in range(50):
                    if svc._nat_mapping is not None:
                        break
                    await asyncio.sleep(0.05)
                self.assertIsNotNone(svc._nat_mapping)
                self.assertEqual("nat-pmp", svc._nat_mapping.protocol_name)
                self.assertEqual("8.8.8.8", svc._nat_mapping.external_address)
                natpmp.assert_called_once()
                upnp.assert_not_called()
            finally:
                await svc.stop()

    async def test_falls_back_to_upnp_when_pcp_and_natpmp_both_fail(self):
        upnp_gateway = nat.UpnpGateway(location="http://x", control_url="http://x/ctl", service_type="urn:x")
        svc = service.SyncService(base_dir=self.base_dir, system_uuid=self.uuid)
        with mock.patch.object(nat, "detect_default_gateway", return_value="192.168.1.1"), mock.patch.object(
            nat, "pcp_request_mapping", side_effect=nat.NatUnsupportedError("no response")
        ), mock.patch.object(
            nat, "natpmp_get_external_address", side_effect=nat.NatUnsupportedError("no response")
        ), mock.patch.object(
            nat, "discover_upnp_gateway", return_value=upnp_gateway
        ), mock.patch.object(
            nat, "upnp_add_port_mapping"
        ) as add_mapping, mock.patch.object(
            nat, "upnp_get_external_ip_address", return_value="8.8.8.8"
        ):
            await svc.start(listen_host="127.0.0.1", listen_port=0)
            try:
                for _ in range(50):
                    if svc._nat_mapping is not None:
                        break
                    await asyncio.sleep(0.05)
                self.assertIsNotNone(svc._nat_mapping)
                self.assertEqual("upnp", svc._nat_mapping.protocol_name)
                self.assertEqual("8.8.8.8", svc._nat_mapping.external_address)
                add_mapping.assert_called_once()
            finally:
                await svc.stop()

    async def test_all_protocols_failing_records_real_last_error_and_no_mapping(self):
        svc = service.SyncService(base_dir=self.base_dir, system_uuid=self.uuid)
        events = []
        svc._on_status = events.append
        with mock.patch.object(nat, "detect_default_gateway", return_value=None), mock.patch.object(
            nat, "discover_upnp_gateway", return_value=None
        ):
            await svc.start(listen_host="127.0.0.1", listen_port=0)
            try:
                for _ in range(50):
                    if svc._nat_last_error is not None:
                        break
                    await asyncio.sleep(0.05)
                self.assertIsNone(svc._nat_mapping)
                self.assertIsNotNone(svc._nat_last_error)
                self.assertTrue(any(e.get("event") == "nat_mapping_error" for e in events))
                status = svc.nat_status()
                self.assertFalse(status["active"])
                self.assertIsNotNone(status["last_error"])
            finally:
                await svc.stop()

    async def test_stop_deletes_the_real_active_mapping(self):
        fake_mapping = nat.NatMapping(
            protocol_name="pcp", external_address="8.8.8.8", external_port=51413, internal_port=51413,
            ip_protocol="tcp", lifetime_seconds=3600, obtained_at=0.0, nonce=b"x" * 12,
        )
        svc = service.SyncService(base_dir=self.base_dir, system_uuid=self.uuid)
        delete_calls = []
        with mock.patch.object(nat, "detect_default_gateway", return_value="192.168.1.1"), mock.patch.object(
            nat, "pcp_request_mapping", return_value=fake_mapping
        ), mock.patch.object(nat, "pcp_delete_mapping", side_effect=lambda gw, m, **kw: delete_calls.append((gw, m))):
            await svc.start(listen_host="127.0.0.1", listen_port=0)
            for _ in range(50):
                if svc._nat_mapping is not None:
                    break
                await asyncio.sleep(0.05)
            self.assertIsNotNone(svc._nat_mapping)
            await svc.stop()
        self.assertEqual(1, len(delete_calls))
        self.assertIsNone(svc._nat_mapping)

    async def test_explicit_retry_runs_immediately_without_waiting_for_the_loop_interval(self):
        call_count = 0

        def flaky_pcp(gateway, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise nat.NatUnsupportedError("first attempt fails")
            return nat.NatMapping(
                protocol_name="pcp", external_address="8.8.8.8", external_port=51413, internal_port=51413,
                ip_protocol="tcp", lifetime_seconds=3600, obtained_at=0.0, nonce=b"y" * 12,
            )

        svc = service.SyncService(base_dir=self.base_dir, system_uuid=self.uuid)
        with mock.patch.object(nat, "detect_default_gateway", return_value="192.168.1.1"), mock.patch.object(
            nat, "pcp_request_mapping", side_effect=flaky_pcp
        ), mock.patch.object(nat, "natpmp_get_external_address", side_effect=nat.NatUnsupportedError("x")), mock.patch.object(
            nat, "discover_upnp_gateway", return_value=None
        ):
            await svc.start(listen_host="127.0.0.1", listen_port=0)
            try:
                for _ in range(50):
                    if svc._nat_last_error is not None:
                        break
                    await asyncio.sleep(0.05)
                self.assertIsNone(svc._nat_mapping)  # first attempt failed
                result = await svc.retry_nat_mapping_now()
                self.assertEqual("ok", result["status"])
                self.assertIsNotNone(svc._nat_mapping)
                self.assertEqual(2, call_count)
            finally:
                await svc.stop()

    async def test_live_disable_deletes_mapping_without_touching_listener(self):
        fake_mapping = nat.NatMapping(
            protocol_name="pcp", external_address="8.8.8.8", external_port=51413, internal_port=51413,
            ip_protocol="tcp", lifetime_seconds=3600, obtained_at=0.0, nonce=b"z" * 12,
        )
        svc = service.SyncService(base_dir=self.base_dir, system_uuid=self.uuid)
        with mock.patch.object(nat, "detect_default_gateway", return_value="192.168.1.1"), mock.patch.object(
            nat, "pcp_request_mapping", return_value=fake_mapping
        ), mock.patch.object(nat, "pcp_delete_mapping") as delete_mapping:
            await svc.start(listen_host="127.0.0.1", listen_port=0)
            listener_before = svc._listener
            try:
                for _ in range(50):
                    if svc._nat_mapping is not None:
                        break
                    await asyncio.sleep(0.05)
                result = await svc.set_nat_traversal_enabled(False)
                self.assertEqual({"status": "ok"}, result)
                self.assertIsNone(svc._nat_mapping)
                self.assertIs(listener_before, svc._listener)  # listener untouched
                delete_mapping.assert_called_once()
            finally:
                await svc.stop()

    async def test_live_enable_starts_nat_without_touching_listener(self):
        sync_config.save({"nat_traversal": False}, base_dir=self.base_dir)
        svc = service.SyncService(base_dir=self.base_dir, system_uuid=self.uuid)
        with mock.patch.object(nat, "detect_default_gateway", return_value=None), mock.patch.object(
            nat, "discover_upnp_gateway", return_value=None
        ):
            await svc.start(listen_host="127.0.0.1", listen_port=0)
            listener_before = svc._listener
            try:
                self.assertIsNone(svc._nat_task)
                result = await svc.set_nat_traversal_enabled(True)
                self.assertEqual({"status": "ok"}, result)
                self.assertIsNotNone(svc._nat_task)
                self.assertIs(listener_before, svc._listener)
            finally:
                await svc.stop()

    async def test_cancellation_during_inflight_discovery_leaves_no_dangling_task(self):
        """"cancellation during in-flight discovery" -- stop() must not
        hang or leave the executor/task in a broken state even if it fires
        while the blocking NAT call is still running."""
        started = threading_event = __import__("threading").Event()

        def slow_pcp(gateway, **kwargs):
            started.set()
            import time as _time

            _time.sleep(2)
            raise nat.NatUnsupportedError("irrelevant")

        svc = service.SyncService(base_dir=self.base_dir, system_uuid=self.uuid)
        with mock.patch.object(nat, "detect_default_gateway", return_value="192.168.1.1"), mock.patch.object(
            nat, "pcp_request_mapping", side_effect=slow_pcp
        ):
            await svc.start(listen_host="127.0.0.1", listen_port=0)
            started.wait(timeout=2)
            result = await asyncio.wait_for(svc.stop(), timeout=5)
            self.assertEqual({"status": "ok"}, result)
            self.assertIsNone(svc._nat_task)


if __name__ == "__main__":
    unittest.main()
