"""Real, deterministic fake-router tests for pythia.core.sync.nat's PCP
(RFC 6887) and NAT-PMP (RFC 6886) clients. A real UDP socket on loopback
plays the role of "the router", bound to an ephemeral port (never the real
5351, which may be in use by an actual router-facing service on this
machine) -- exercised via `gateway_port=` overrides every real client
function already supports for exactly this reason.
"""

import socket
import struct
import threading
import time
import unittest

from pythia.core.sync import nat


class _FakeUdpRouter:
    """A real, controllable UDP responder. `handler(data) -> bytes | None`
    decides what to send back (None means "don't respond", simulating an
    unsupported protocol or a dropped packet)."""

    def __init__(self, handler):
        self._handler = handler
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                response = self._handler(data)
            except Exception:
                response = None
            if response is not None:
                try:
                    self._sock.sendto(response, addr)
                except OSError:
                    pass

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._sock.close()


class PcpMappingTests(unittest.TestCase):
    def _real_success_response(self, request: bytes) -> bytes:
        nonce, protocol, _reserved, internal_port, _sugg_ext, _sugg_ip = struct.unpack("!12sB3sHH16s", request[24:60])
        header = struct.pack("!BBBBII12s", 2, 0x81, 0, 0, 3600, 100, b"\x00" * 12)
        map_data = struct.pack(
            "!12sB3sHH16s", nonce, protocol, b"\x00\x00\x00", internal_port, 51413, nat._ipv4_mapped("8.8.8.8")
        )
        return header + map_data

    def test_real_success_response_is_parsed_correctly(self):
        router = _FakeUdpRouter(self._real_success_response)
        try:
            mapping = nat.pcp_request_mapping(
                "127.0.0.1", internal_port=51413, ip_protocol="tcp", gateway_port=router.port, timeout=1, retries=1
            )
            self.assertEqual("pcp", mapping.protocol_name)
            self.assertEqual("8.8.8.8", mapping.external_address)
            self.assertEqual(51413, mapping.external_port)
            self.assertEqual(3600, mapping.lifetime_seconds)
            self.assertIsNotNone(mapping.nonce)
        finally:
            router.close()

    def test_no_response_raises_unsupported(self):
        router = _FakeUdpRouter(lambda data: None)
        try:
            with self.assertRaises(nat.NatUnsupportedError):
                nat.pcp_request_mapping(
                    "127.0.0.1", internal_port=51413, gateway_port=router.port, timeout=0.2, retries=2
                )
        finally:
            router.close()

    def test_explicit_result_error_is_raised_with_real_code(self):
        def handler(request):
            # Real routers echo the full MAP-shaped response (zeroed data)
            # even on error -- the total message size doesn't shrink.
            header = struct.pack("!BBBBII12s", 2, 0x81, 0, 3, 0, 0, b"\x00" * 12)  # MALFORMED_REQUEST
            return header + b"\x00" * 36

        router = _FakeUdpRouter(handler)
        try:
            with self.assertRaises(nat.NatResultError) as ctx:
                nat.pcp_request_mapping("127.0.0.1", internal_port=51413, gateway_port=router.port, timeout=1, retries=1)
            self.assertEqual(3, ctx.exception.code)
        finally:
            router.close()

    def test_forged_response_with_wrong_nonce_is_rejected(self):
        def handler(request):
            _real_nonce, protocol, _r, internal_port, _se, _si = struct.unpack("!12sB3sHH16s", request[24:60])
            header = struct.pack("!BBBBII12s", 2, 0x81, 0, 0, 3600, 100, b"\x00" * 12)
            forged_nonce = b"\xff" * 12  # deliberately wrong
            map_data = struct.pack(
                "!12sB3sHH16s", forged_nonce, protocol, b"\x00\x00\x00", internal_port, 51413, nat._ipv4_mapped("8.8.8.8")
            )
            return header + map_data

        router = _FakeUdpRouter(handler)
        try:
            with self.assertRaises(nat.NatError):
                nat.pcp_request_mapping("127.0.0.1", internal_port=51413, gateway_port=router.port, timeout=1, retries=1)
        finally:
            router.close()

    def test_truncated_response_is_rejected(self):
        router = _FakeUdpRouter(lambda data: b"\x02\x81\x00\x00")  # far too short
        try:
            with self.assertRaises(nat.NatError):
                nat.pcp_request_mapping("127.0.0.1", internal_port=51413, gateway_port=router.port, timeout=1, retries=1)
        finally:
            router.close()

    def test_response_reporting_a_private_external_address_is_rejected(self):
        """Even a "successful" response is hostile input -- a router that
        claims our external address is a private/loopback/documentation
        address must never be trusted as a real publishable endpoint."""

        def handler(request):
            nonce, protocol, _r, internal_port, _se, _si = struct.unpack("!12sB3sHH16s", request[24:60])
            header = struct.pack("!BBBBII12s", 2, 0x81, 0, 0, 3600, 100, b"\x00" * 12)
            map_data = struct.pack(
                "!12sB3sHH16s", nonce, protocol, b"\x00\x00\x00", internal_port, 51413, nat._ipv4_mapped("192.168.1.1")
            )
            return header + map_data

        router = _FakeUdpRouter(handler)
        try:
            with self.assertRaises(nat.NatError):
                nat.pcp_request_mapping("127.0.0.1", internal_port=51413, gateway_port=router.port, timeout=1, retries=1)
        finally:
            router.close()

    def test_delete_reuses_the_real_mapping_nonce(self):
        seen_requests = []

        def handler(request):
            nonce, protocol, _r, internal_port, _se, _si = struct.unpack("!12sB3sHH16s", request[24:60])
            _v, _op, _res, lifetime = struct.unpack("!BBHI", request[:8])
            seen_requests.append((nonce, lifetime))
            header = struct.pack("!BBBBII12s", 2, 0x81, 0, 0, lifetime, 100, b"\x00" * 12)
            map_data = struct.pack(
                "!12sB3sHH16s", nonce, protocol, b"\x00\x00\x00", internal_port, 0 if lifetime == 0 else 51413,
                nat._ipv4_mapped("8.8.8.8"),
            )
            return header + map_data

        router = _FakeUdpRouter(handler)
        try:
            mapping = nat.pcp_request_mapping(
                "127.0.0.1", internal_port=51413, gateway_port=router.port, timeout=1, retries=1
            )
            nat.pcp_delete_mapping("127.0.0.1", mapping, gateway_port=router.port, timeout=1, retries=1)
            self.assertEqual(2, len(seen_requests))
            self.assertEqual(seen_requests[0][0], seen_requests[1][0])  # same real nonce reused
            self.assertEqual(0, seen_requests[1][1])  # delete = lifetime 0
        finally:
            router.close()


class NatPmpMappingTests(unittest.TestCase):
    def test_external_address_success(self):
        def handler(request):
            return struct.pack("!BBHI4s", 0, 0x80, 0, 100, socket.inet_aton("8.8.8.8"))

        router = _FakeUdpRouter(handler)
        try:
            address, epoch = nat.natpmp_get_external_address("127.0.0.1", gateway_port=router.port, timeout=1, retries=1)
            self.assertEqual("8.8.8.8", address)
            self.assertEqual(100, epoch)
        finally:
            router.close()

    def test_mapping_success_udp_and_tcp_opcodes(self):
        def handler(request):
            _v, opcode, _r, internal_port, suggested_external, lifetime = struct.unpack("!BBHHHI", request)
            return struct.pack("!BBHIHHI", 0, 0x80 + opcode, 0, 50, internal_port, 51413, lifetime)

        router = _FakeUdpRouter(handler)
        try:
            mapping_tcp = nat.natpmp_request_mapping(
                "127.0.0.1", internal_port=51413, ip_protocol="tcp", gateway_port=router.port, timeout=1, retries=1
            )
            self.assertEqual("nat-pmp", mapping_tcp.protocol_name)
            self.assertEqual(51413, mapping_tcp.external_port)
            mapping_udp = nat.natpmp_request_mapping(
                "127.0.0.1", internal_port=51413, ip_protocol="udp", gateway_port=router.port, timeout=1, retries=1
            )
            self.assertEqual(51413, mapping_udp.external_port)
        finally:
            router.close()

    def test_no_response_raises_unsupported(self):
        router = _FakeUdpRouter(lambda data: None)
        try:
            with self.assertRaises(nat.NatUnsupportedError):
                nat.natpmp_request_mapping(
                    "127.0.0.1", internal_port=51413, gateway_port=router.port, timeout=0.2, retries=2
                )
        finally:
            router.close()

    def test_explicit_result_error_is_raised_with_real_code(self):
        def handler(request):
            _v, opcode, _r, internal_port, _se, _lt = struct.unpack("!BBHHHI", request)
            return struct.pack("!BBHIHHI", 0, 0x80 + opcode, 4, 0, internal_port, 0, 0)  # Out of Resources

        router = _FakeUdpRouter(handler)
        try:
            with self.assertRaises(nat.NatResultError) as ctx:
                nat.natpmp_request_mapping("127.0.0.1", internal_port=51413, gateway_port=router.port, timeout=1, retries=1)
            self.assertEqual(4, ctx.exception.code)
        finally:
            router.close()

    def test_malformed_short_response_is_rejected(self):
        router = _FakeUdpRouter(lambda data: b"\x00\x80")
        try:
            with self.assertRaises(nat.NatError):
                nat.natpmp_request_mapping("127.0.0.1", internal_port=51413, gateway_port=router.port, timeout=1, retries=1)
        finally:
            router.close()

    def test_wrong_opcode_response_is_rejected(self):
        def handler(request):
            return struct.pack("!BBHIHHI", 0, 0x82, 0, 0, 51413, 51413, 3600)  # TCP opcode, but request asked for UDP

        router = _FakeUdpRouter(handler)
        try:
            with self.assertRaises(nat.NatError):
                nat.natpmp_request_mapping(
                    "127.0.0.1", internal_port=51413, ip_protocol="udp", gateway_port=router.port, timeout=1, retries=1
                )
        finally:
            router.close()

    def test_delete_uses_zero_lifetime_and_zero_suggested_port(self):
        seen = []

        def handler(request):
            _v, opcode, _r, internal_port, suggested_external, lifetime = struct.unpack("!BBHHHI", request)
            seen.append((suggested_external, lifetime))
            return struct.pack("!BBHIHHI", 0, 0x80 + opcode, 0, 0, internal_port, 0, 0)

        router = _FakeUdpRouter(handler)
        try:
            nat.natpmp_delete_mapping("127.0.0.1", internal_port=51413, gateway_port=router.port, timeout=1, retries=1)
            self.assertEqual([(0, 0)], seen)
        finally:
            router.close()

    def test_restart_detection_real_math(self):
        # Router's own epoch barely advances relative to real elapsed time
        # -> a real restart must be detected.
        self.assertTrue(nat.natpmp_detect_restart(1000, 0.0, 5, 100.0))
        # Router's epoch keeps pace -> no restart.
        self.assertFalse(nat.natpmp_detect_restart(1000, 0.0, 1099, 100.0))


if __name__ == "__main__":
    unittest.main()
