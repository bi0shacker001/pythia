"""Real tests for IPv6 dual-stack listener/connect behavior in
pythia.core.sync.connectivity.direct_tcp (2026-08-09, "internal IPv6 direct
connectivity and dual-stack route selection"). Uses real loopback/link-local
sockets on this machine -- no internet access, no router state -- and skips
individual cases honestly (never fakes a pass) when this environment
genuinely can't support them (no real IPv6, no real link-local interface).
"""

import asyncio
import socket
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from pythia.core.sync import trust
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


def _real_link_local_with_scope() -> tuple[str, str] | None:
    """A real link-local IPv6 address this machine actually has, with its
    real numeric scope id -- or None if this environment genuinely has
    none (never fabricated)."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6)
    except OSError:
        return None
    for _fam, _kind, _proto, _canon, sockaddr in infos:
        ip, _port, _flow, scope_id = sockaddr
        if ip.startswith("fe80") and scope_id:
            return ip, str(scope_id)
    return None


class Ipv6SupportProbeTests(unittest.TestCase):
    def test_ipv6_supported_returns_a_real_bool(self):
        result = direct_tcp.ipv6_supported()
        self.assertIsInstance(result, bool)
        # Cached -- calling twice must not re-probe or flip-flop.
        self.assertEqual(result, direct_tcp.ipv6_supported())


class ListenerFamiliesTests(unittest.IsolatedAsyncioTestCase):
    async def test_listener_families_reports_ipv4_only_for_explicit_ipv4_host(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            node = _make_node(Path(tmp.name), "A")

            async def on_connected(_conn):
                pass

            server = await direct_tcp.start_listener("127.0.0.1", 0, node, on_connected)
            try:
                self.assertEqual(["IPv4"], direct_tcp.listener_families(server))
            finally:
                server.close()
                await server.wait_closed()
        finally:
            tmp.cleanup()

    @unittest.skipUnless(direct_tcp.ipv6_supported(), "this environment has no real usable IPv6")
    async def test_listener_families_reports_ipv6_only_for_explicit_ipv6_host(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            node = _make_node(Path(tmp.name), "A")

            async def on_connected(_conn):
                pass

            server = await direct_tcp.start_listener("::1", 0, node, on_connected)
            try:
                self.assertEqual(["IPv6"], direct_tcp.listener_families(server))
            finally:
                server.close()
                await server.wait_closed()
        finally:
            tmp.cleanup()

    @unittest.skipUnless(direct_tcp.ipv6_supported(), "this environment has no real usable IPv6")
    async def test_wildcard_host_binds_both_families_when_ipv6_is_real(self):
        """The `0.0.0.0` sentinel is the "listen everywhere" default (see
        service.DEFAULT_LISTEN_HOST) -- on a system with real IPv6, this
        must produce a genuine dual-stack bind, not stay IPv4-only."""
        tmp = tempfile.TemporaryDirectory()
        try:
            node = _make_node(Path(tmp.name), "A")

            async def on_connected(_conn):
                pass

            server = await direct_tcp.start_listener("0.0.0.0", 0, node, on_connected)
            try:
                families = direct_tcp.listener_families(server)
                self.assertIn("IPv4", families)
                self.assertIn("IPv6", families)
            finally:
                server.close()
                await server.wait_closed()
        finally:
            tmp.cleanup()

    async def test_wildcard_host_stays_ipv4_only_when_ipv6_unavailable(self):
        """Real IPv4-only-system fallback: with ipv6_supported() forced
        False, the dual-stack sentinel must never even attempt an IPv6
        bind -- confirms the listener degrades honestly rather than
        raising or silently going dark."""
        tmp = tempfile.TemporaryDirectory()
        try:
            node = _make_node(Path(tmp.name), "A")

            async def on_connected(_conn):
                pass

            with mock.patch.object(direct_tcp, "ipv6_supported", return_value=False):
                server = await direct_tcp.start_listener("0.0.0.0", 0, node, on_connected)
            try:
                self.assertEqual(["IPv4"], direct_tcp.listener_families(server))
            finally:
                server.close()
                await server.wait_closed()
        finally:
            tmp.cleanup()

    async def test_listener_shutdown_leaves_no_bound_sockets(self):
        """After close()+wait_closed(), every socket the dual-stack
        listener opened (both families) is really gone -- a fresh listener
        can immediately rebind the exact same port."""
        tmp = tempfile.TemporaryDirectory()
        try:
            node = _make_node(Path(tmp.name), "A")

            async def on_connected(_conn):
                pass

            server = await direct_tcp.start_listener("0.0.0.0", 0, node, on_connected)
            port = server.sockets[0].getsockname()[1]
            server.close()
            await server.wait_closed()

            # Rebind the exact same port -- only possible if nothing was
            # left listening.
            server2 = await direct_tcp.start_listener("0.0.0.0", port, node, on_connected)
            try:
                self.assertTrue(server2.sockets)
            finally:
                server2.close()
                await server2.wait_closed()
        finally:
            tmp.cleanup()


class Ipv6LoopbackConnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp_a = tempfile.TemporaryDirectory()
        self._tmp_b = tempfile.TemporaryDirectory()
        self.node_a = _make_node(Path(self._tmp_a.name), "System A")
        self.node_b = _make_node(Path(self._tmp_b.name), "System B")

    async def asyncTearDown(self):
        self._tmp_a.cleanup()
        self._tmp_b.cleanup()

    @unittest.skipUnless(direct_tcp.ipv6_supported(), "this environment has no real usable IPv6")
    async def test_real_authenticated_handshake_over_ipv6_loopback(self):
        _trust_each_other(self.node_a, self.node_b)
        accepted: asyncio.Queue = asyncio.Queue()

        async def on_connected(conn):
            await accepted.put(conn)

        server = await direct_tcp.start_listener("::1", 0, self.node_a, on_connected)
        port = server.sockets[0].getsockname()[1]
        try:
            client_conn = await direct_tcp.connect(
                "::1", port, self.node_b, expected_system_uuid=self.node_a.system_uuid
            )
            server_conn = await asyncio.wait_for(accepted.get(), timeout=5)
            self.assertEqual(self.node_a.system_uuid, client_conn.peer.system_uuid)
            self.assertEqual(self.node_b.system_uuid, server_conn.peer.system_uuid)
            await client_conn.close()
            await server_conn.close()
        finally:
            server.close()
            await server.wait_closed()

    async def test_real_scoped_link_local_listen_and_connect(self):
        """Uses a real link-local address and real numeric scope id this
        machine actually has -- skipped honestly if this environment has
        none (a container/VM with no such interface)."""
        found = _real_link_local_with_scope()
        if found is None:
            self.skipTest("this environment has no real link-local IPv6 interface")
        ip, scope_id = found
        _trust_each_other(self.node_a, self.node_b)
        accepted: asyncio.Queue = asyncio.Queue()

        async def on_connected(conn):
            await accepted.put(conn)

        host = f"{ip}%{scope_id}"
        server = await direct_tcp.start_listener(host, 0, self.node_a, on_connected)
        port = server.sockets[0].getsockname()[1]
        try:
            client_conn = await direct_tcp.connect(
                host, port, self.node_b, expected_system_uuid=self.node_a.system_uuid
            )
            server_conn = await asyncio.wait_for(accepted.get(), timeout=5)
            self.assertEqual(self.node_a.system_uuid, client_conn.peer.system_uuid)
            self.assertEqual(self.node_b.system_uuid, server_conn.peer.system_uuid)
            await client_conn.close()
            await server_conn.close()
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
