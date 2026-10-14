"""Internal NAT traversal: PCP (RFC 6887), NAT-PMP (RFC 6886), and UPnP IGD
SOAP control -- pure Python sockets/HTTP/XML, no external tool, daemon, or
subprocess (2026-08-08, "internal NAT traversal and direct-route
publication").

Every request function here is synchronous/blocking with a real, bounded
timeout -- callers run them in a thread executor, the same pattern
global_discovery.py's own blocking HTTP calls already use in this codebase.

Router responses are treated as hostile input throughout: bounded datagram/
HTTP body sizes, bounded timeouts, strict field validation (version/opcode/
result-code/address/port/lifetime), and for UPnP specifically: the SOAP
control URL is only ever trusted if it resolves to the exact same origin
(scheme+host+port) as the device description that named it, and that origin
must be a private/local-network address -- never a public-internet host a
malicious/compromised device description could redirect control traffic to
(an SSRF guard). XML responses are rejected outright if they contain a
DOCTYPE/ENTITY declaration, blocking XXE/billion-laughs vectors without a
third-party XML library.

**Stated limitation, IPv4-only gateway/mapping (2026-08-09, "internal IPv6
direct connectivity and dual-stack route selection")**: `detect_default_gateway()`
below only ever resolves an IPv4 default route (`GetIpForwardTable` on
Windows, `/proc/net/route` on Linux) -- real cross-platform IPv6 default-route
detection (`GetIpForwardTable2`/`AF_INET6` on Windows, `/proc/net/ipv6_route`
on Linux) is real, separate platform-routing work this slice does not add.
PCP itself (RFC 6887) is protocol-family-agnostic on the wire, but with no
real IPv6 gateway address to send a request to, this module cannot honestly
originate a PCP IPv6 mapping -- so it does not attempt one, rather than
guessing a gateway or silently mapping nothing while claiming success. This
is a precise, deliberate limitation, not an oversight: it does not block
native IPv6 direct/LAN connectivity, since a real dual-stack listener
(direct_tcp.start_listener()) and address-family-aware candidate racing work
independently of NAT mapping, and a genuinely public/globally-routable IPv6
host (the common case -- most IPv6 deployments are NAT-free end-to-end) needs
no NAT mapping at all to be reachable. NAT-PMP and UPnP IGD were never
claimed to support IPv6 (NAT-PMP is IPv4-only by protocol design; UPnP IGD's
v1/v2 SOAP actions this module speaks are IPv4-only), and that remains
unchanged."""

from __future__ import annotations

import http.client
import ipaddress
import secrets
import socket
import struct
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

PCP_NATPMP_PORT = 5351
DEFAULT_TIMEOUT = 2.0
DEFAULT_RETRIES = 2
MAX_DATAGRAM = 1100  # generous bound over any real PCP/NAT-PMP packet
MAX_HTTP_BODY = 64 * 1024
MAX_SSDP_RESPONSE = 4096
SSDP_ADDRESS = ("239.255.255.250", 1900)
SSDP_SEARCH_TARGETS = (
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
)
_SERVICE_TYPE_PREFERENCE = (
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
)


class NatError(Exception):
    """Base class for every NAT-mapping failure below."""


class NatUnsupportedError(NatError):
    """The gateway/router does not support this protocol (no response
    after retries, or an explicit unsupported-version/opcode result) -- a
    real, expected outcome callers should treat as "try the next
    protocol", not fatal."""


class NatResultError(NatError):
    """A real, well-formed error response from the router -- carries the
    router's own numeric result code."""

    def __init__(self, message: str, *, code: int):
        self.code = code
        super().__init__(message)


@dataclass
class NatMapping:
    protocol_name: str  # "pcp" | "nat-pmp" | "upnp"
    external_address: str | None
    external_port: int
    internal_port: int
    ip_protocol: str  # "tcp" | "udp"
    lifetime_seconds: int
    obtained_at: float
    # PCP-only: the mapping's identity nonce, required to renew/delete it.
    nonce: bytes | None = None


# ---- gateway detection ---------------------------------------------------------


def detect_default_gateway() -> str | None:
    """Best-effort real default gateway IPv4 address -- returns None if it
    can't be determined (never a fabricated guess; a genuine limitation in
    some environments -- containers, unusual routing setups, or platforms
    other than Windows/Linux). No subprocess is ever spawned."""
    try:
        if sys.platform.startswith("win"):
            return _detect_default_gateway_windows()
        if sys.platform.startswith("linux"):
            return _detect_default_gateway_linux()
    except Exception:
        return None
    return None


def _detect_default_gateway_windows() -> str | None:
    import ctypes

    class _MibIpForwardRow(ctypes.Structure):
        _fields_ = [
            ("dwForwardDest", ctypes.c_ulong),
            ("dwForwardMask", ctypes.c_ulong),
            ("dwForwardPolicy", ctypes.c_ulong),
            ("dwForwardNextHop", ctypes.c_ulong),
            ("dwForwardIfIndex", ctypes.c_ulong),
            ("dwForwardType", ctypes.c_ulong),
            ("dwForwardProto", ctypes.c_ulong),
            ("dwForwardAge", ctypes.c_ulong),
            ("dwForwardNextHopAS", ctypes.c_ulong),
            ("dwForwardMetric1", ctypes.c_ulong),
            ("dwForwardMetric2", ctypes.c_ulong),
            ("dwForwardMetric3", ctypes.c_ulong),
            ("dwForwardMetric4", ctypes.c_ulong),
            ("dwForwardMetric5", ctypes.c_ulong),
        ]

    destination = struct.unpack("<I", socket.inet_aton("8.8.8.8"))[0]
    row = _MibIpForwardRow()
    result = ctypes.windll.iphlpapi.GetBestRoute(destination, 0, ctypes.byref(row))
    if result != 0:
        return None
    return socket.inet_ntoa(struct.pack("<I", row.dwForwardNextHop))


def _detect_default_gateway_linux() -> str | None:
    try:
        with open("/proc/net/route", "r", encoding="ascii") as handle:
            lines = handle.readlines()
    except OSError:
        return None
    for line in lines[1:]:
        fields = line.strip().split()
        if len(fields) < 3:
            continue
        _iface, destination, gateway = fields[0], fields[1], fields[2]
        if destination != "00000000":
            continue
        try:
            gateway_int = int(gateway, 16)
        except ValueError:
            continue
        return socket.inet_ntoa(struct.pack("<I", gateway_int))
    return None


def _local_ipv4_for_gateway(gateway: str) -> str:
    """The real local IPv4 address the OS would use to reach `gateway` --
    connecting a UDP socket never sends a packet, it only makes the kernel
    resolve the outbound route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((gateway, PCP_NATPMP_PORT))
        return probe.getsockname()[0]
    finally:
        probe.close()


# ---- PCP (RFC 6887) ---------------------------------------------------------------

_PCP_VERSION = 2
_PCP_OPCODE_MAP = 1
_PCP_PROTOCOL_NUMBERS = {"tcp": 6, "udp": 17}
_PCP_RESULT_NAMES = {
    0: "SUCCESS",
    1: "UNSUPP_VERSION",
    2: "NOT_AUTHORIZED",
    3: "MALFORMED_REQUEST",
    4: "UNSUPP_OPCODE",
    5: "UNSUPP_OPTION",
    6: "MALFORMED_OPTION",
    7: "NETWORK_FAILURE",
    8: "NO_RESOURCES",
    9: "UNSUPP_PROTOCOL",
    10: "USER_EX_QUOTA",
    11: "CANNOT_PROVIDE_EXTERNAL",
    12: "ADDRESS_MISMATCH",
    13: "EXCESSIVE_REMOTE_PEERS",
}


def _ipv4_mapped(address: str) -> bytes:
    return socket.inet_pton(socket.AF_INET6, f"::ffff:{address}")


def _ipv4_from_mapped(mapped: bytes) -> str:
    text = socket.inet_ntop(socket.AF_INET6, mapped)
    if text.startswith("::ffff:"):
        return text[len("::ffff:") :]
    return text


def _udp_exchange(gateway: str, port: int, payload: bytes, *, timeout: float, retries: int) -> bytes:
    """Real, bounded UDP request/response with linear retry -- returns the
    first real datagram received, or raises NatUnsupportedError if nothing
    real ever arrives (a real, expected "this router doesn't speak this
    protocol" outcome, not a bug)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        last_error: Exception | None = None
        for _ in range(max(1, retries)):
            try:
                sock.sendto(payload, (gateway, port))
                data, _addr = sock.recvfrom(MAX_DATAGRAM)
                return data
            except socket.timeout as exc:
                last_error = exc
                continue
            except OSError as exc:
                last_error = exc
                continue
        raise NatUnsupportedError(f"no response from {gateway}:{port} after {retries} attempt(s)") from last_error
    finally:
        sock.close()


def pcp_request_mapping(
    gateway: str,
    *,
    internal_port: int,
    ip_protocol: str = "tcp",
    suggested_external_port: int = 0,
    lifetime: int = 7200,
    nonce: bytes | None = None,
    client_ip: str | None = None,
    gateway_port: int = PCP_NATPMP_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> NatMapping:
    """A real PCP MAP request -- pass `nonce` (the same 12 bytes) to renew
    or `lifetime=0` with the original nonce to delete an existing mapping.
    Raises NatUnsupportedError if the gateway never answers, or
    NatResultError with the real router-reported result code on any
    explicit failure. Never fabricates an external port/address.
    `gateway_port` defaults to the real PCP/NAT-PMP port; test-only override
    lets tests point at a fake router on an ephemeral port instead of the
    real 5351 (which may be in use by an actual router-facing service on
    this machine)."""
    if ip_protocol not in _PCP_PROTOCOL_NUMBERS:
        raise ValueError(f"unsupported ip_protocol: {ip_protocol!r}")
    if not (0 <= internal_port <= 65535) or not (0 <= suggested_external_port <= 65535):
        raise ValueError("port values must be 0-65535")
    nonce = nonce or secrets.token_bytes(12)
    if len(nonce) != 12:
        raise ValueError("PCP mapping nonce must be exactly 12 bytes")
    client_ip = client_ip or _local_ipv4_for_gateway(gateway)

    header = struct.pack("!BBHI16s", _PCP_VERSION, _PCP_OPCODE_MAP, 0, max(0, lifetime), _ipv4_mapped(client_ip))
    map_data = struct.pack(
        "!12sB3sHH16s",
        nonce,
        _PCP_PROTOCOL_NUMBERS[ip_protocol],
        b"\x00\x00\x00",
        internal_port,
        suggested_external_port,
        _ipv4_mapped("0.0.0.0"),
    )
    response = _udp_exchange(gateway, gateway_port, header + map_data, timeout=timeout, retries=retries)
    return _parse_pcp_map_response(response, expected_nonce=nonce, ip_protocol=ip_protocol, internal_port=internal_port)


def _parse_pcp_map_response(data: bytes, *, expected_nonce: bytes, ip_protocol: str, internal_port: int) -> NatMapping:
    if len(data) < 60:
        raise NatError(f"PCP response is too short ({len(data)} bytes, expected 60)")
    version, opcode_byte, _reserved, result_code, response_lifetime, _epoch, _reserved12 = struct.unpack(
        "!BBBBII12s", data[:24]
    )
    if version != _PCP_VERSION:
        raise NatUnsupportedError(f"unsupported PCP response version {version}")
    if not (opcode_byte & 0x80):
        raise NatError("PCP datagram is not a response (R bit not set)")
    if (opcode_byte & 0x7F) != _PCP_OPCODE_MAP:
        raise NatError(f"expected a MAP response, got opcode {opcode_byte & 0x7F}")
    if result_code != 0:
        raise NatResultError(f"PCP MAP failed: {_PCP_RESULT_NAMES.get(result_code, result_code)}", code=result_code)

    nonce, protocol_number, _reserved3, resp_internal_port, external_port, external_ip_raw = struct.unpack(
        "!12sB3sHH16s", data[24:60]
    )
    if nonce != expected_nonce:
        raise NatError("PCP MAP response nonce does not match the request -- possible spoofed/stale response")
    if protocol_number != _PCP_PROTOCOL_NUMBERS[ip_protocol]:
        raise NatError("PCP MAP response protocol does not match the request")
    if resp_internal_port != internal_port:
        raise NatError("PCP MAP response internal port does not match the request")
    external_address = _ipv4_from_mapped(external_ip_raw)
    _validate_public_address(external_address)
    return NatMapping(
        protocol_name="pcp",
        external_address=external_address,
        external_port=external_port,
        internal_port=internal_port,
        ip_protocol=ip_protocol,
        lifetime_seconds=response_lifetime,
        obtained_at=time.time(),
        nonce=nonce,
    )


def pcp_delete_mapping(
    gateway: str,
    mapping: NatMapping,
    *,
    client_ip: str | None = None,
    gateway_port: int = PCP_NATPMP_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> None:
    """Real PCP deletion: lifetime=0 with the mapping's own real nonce.
    Best-effort -- callers should not treat a delete failure as fatal (the
    mapping will simply expire on its own)."""
    if mapping.nonce is None:
        raise ValueError("mapping has no PCP nonce -- was it really obtained via pcp_request_mapping()?")
    pcp_request_mapping(
        gateway,
        internal_port=mapping.internal_port,
        ip_protocol=mapping.ip_protocol,
        suggested_external_port=0,
        lifetime=0,
        nonce=mapping.nonce,
        client_ip=client_ip,
        gateway_port=gateway_port,
        timeout=timeout,
        retries=retries,
    )


# ---- NAT-PMP (RFC 6886) -----------------------------------------------------------

_NATPMP_VERSION = 0
_NATPMP_OP_EXTERNAL_ADDRESS = 0
_NATPMP_OP_MAP = {"udp": 1, "tcp": 2}
_NATPMP_RESULT_NAMES = {
    0: "Success",
    1: "Unsupported Version",
    2: "Not Authorized/Refused",
    3: "Network Failure",
    4: "Out of Resources",
    5: "Unsupported Opcode",
}


def natpmp_get_external_address(
    gateway: str, *, gateway_port: int = PCP_NATPMP_PORT, timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES
) -> tuple[str, int]:
    """Returns (external_ipv4_address, seconds_since_start_of_epoch)."""
    request = struct.pack("!BB", _NATPMP_VERSION, _NATPMP_OP_EXTERNAL_ADDRESS)
    response = _udp_exchange(gateway, gateway_port, request, timeout=timeout, retries=retries)
    if len(response) < 12:
        raise NatError(f"NAT-PMP external-address response is too short ({len(response)} bytes, expected 12)")
    version, opcode, result_code, epoch, address_raw = struct.unpack("!BBHI4s", response[:12])
    if version != _NATPMP_VERSION:
        raise NatUnsupportedError(f"unsupported NAT-PMP response version {version}")
    if opcode != 0x80:
        raise NatError(f"expected a NAT-PMP external-address response, got opcode {opcode}")
    if result_code != 0:
        raise NatResultError(
            f"NAT-PMP external-address request failed: {_NATPMP_RESULT_NAMES.get(result_code, result_code)}",
            code=result_code,
        )
    address = socket.inet_ntoa(address_raw)
    _validate_public_address(address)
    return address, epoch


def natpmp_request_mapping(
    gateway: str,
    *,
    internal_port: int,
    ip_protocol: str = "tcp",
    suggested_external_port: int = 0,
    lifetime: int = 7200,
    gateway_port: int = PCP_NATPMP_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> NatMapping:
    """A real NAT-PMP port-mapping request. The response never carries an
    external IP address (NAT-PMP's own limitation) -- pair this with
    natpmp_get_external_address() for the full picture, same as any real
    NAT-PMP client must."""
    if ip_protocol not in _NATPMP_OP_MAP:
        raise ValueError(f"unsupported ip_protocol: {ip_protocol!r}")
    if not (0 <= internal_port <= 65535) or not (0 <= suggested_external_port <= 65535):
        raise ValueError("port values must be 0-65535")
    opcode = _NATPMP_OP_MAP[ip_protocol]
    request = struct.pack("!BBHHHI", _NATPMP_VERSION, opcode, 0, internal_port, suggested_external_port, max(0, lifetime))
    response = _udp_exchange(gateway, gateway_port, request, timeout=timeout, retries=retries)
    return _parse_natpmp_map_response(response, expected_opcode=opcode, ip_protocol=ip_protocol, internal_port=internal_port)


def _parse_natpmp_map_response(data: bytes, *, expected_opcode: int, ip_protocol: str, internal_port: int) -> NatMapping:
    if len(data) < 16:
        raise NatError(f"NAT-PMP mapping response is too short ({len(data)} bytes, expected 16)")
    version, opcode, result_code, epoch, resp_internal_port, external_port, resp_lifetime = struct.unpack(
        "!BBHIHHI", data[:16]
    )
    if version != _NATPMP_VERSION:
        raise NatUnsupportedError(f"unsupported NAT-PMP response version {version}")
    if opcode != 0x80 + expected_opcode:
        raise NatError(f"expected NAT-PMP mapping response opcode {0x80 + expected_opcode}, got {opcode}")
    if result_code != 0:
        raise NatResultError(
            f"NAT-PMP mapping request failed: {_NATPMP_RESULT_NAMES.get(result_code, result_code)}", code=result_code
        )
    if resp_internal_port != internal_port:
        raise NatError("NAT-PMP mapping response internal port does not match the request")
    return NatMapping(
        protocol_name="nat-pmp",
        external_address=None,
        external_port=external_port,
        internal_port=internal_port,
        ip_protocol=ip_protocol,
        lifetime_seconds=resp_lifetime,
        obtained_at=time.time(),
    )


def natpmp_delete_mapping(
    gateway: str,
    *,
    internal_port: int,
    ip_protocol: str = "tcp",
    gateway_port: int = PCP_NATPMP_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> None:
    """Real deletion: lifetime=0, suggested external port=0, per RFC 6886.
    Best-effort -- the mapping will simply expire on its own if this fails."""
    natpmp_request_mapping(
        gateway,
        internal_port=internal_port,
        ip_protocol=ip_protocol,
        suggested_external_port=0,
        lifetime=0,
        gateway_port=gateway_port,
        timeout=timeout,
        retries=retries,
    )


def natpmp_detect_restart(previous_epoch: int, previous_local_time: float, new_epoch: int, new_local_time: float) -> bool:
    """Real router-restart detection per RFC 6886 -- if the router's own
    SSSOE counter fails to advance roughly in step with our own clock, its
    mapping table (and therefore every mapping we thought we had) was
    reset. The 7/8 factor tolerates the router's clock running up to
    ~12.5% slow; the 2-second term absorbs rounding/jitter."""
    elapsed_local = max(0.0, new_local_time - previous_local_time)
    expected_minimum = previous_epoch + elapsed_local * 7 / 8 - 2
    return new_epoch < expected_minimum


# ---- UPnP IGD ------------------------------------------------------------------------


def _validate_public_address(address: str) -> None:
    """Router-reported external addresses are hostile input too -- never
    accept a wildcard/loopback/unspecified/documentation/multicast/private
    address as a real, publishable external endpoint (2026-08-08, "Do not
    publish wildcard, loopback, unspecified, documentation, or invalid
    addresses")."""
    try:
        ip = ipaddress.IPv4Address(address)
    except ValueError as exc:
        raise NatError(f"router reported an invalid external address: {address!r}") from exc
    if (
        ip.is_loopback
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_link_local
        or ip.is_private
        or address == "255.255.255.255"
    ):
        raise NatError(f"router reported a non-publishable external address: {address}")


class UpnpError(NatError):
    pass


@dataclass
class UpnpGateway:
    location: str
    control_url: str
    service_type: str


def _ssdp_discover_locations(
    *, timeout: float, tries: int, search_targets=SSDP_SEARCH_TARGETS, ssdp_address: tuple[str, int] = SSDP_ADDRESS
) -> list[str]:
    """Real, bounded SSDP M-SEARCH discovery -- returns every distinct
    real LOCATION URL seen, deduplicated. Never raises on a router that
    doesn't respond; an empty list is the honest "nothing found" result.
    `ssdp_address` defaults to the real multicast rendezvous address; test-
    only override lets tests point at a fake local UDP responder on
    loopback instead of relying on real multicast group membership
    (unreliable in a sandboxed/CI environment)."""
    locations: list[str] = []
    seen: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        except OSError:
            pass
        sock.settimeout(timeout)
        deadline = time.time() + timeout
        for search_target in search_targets:
            message = (
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: {ssdp_address[0]}:{ssdp_address[1]}\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                f"ST: {search_target}\r\n"
                "\r\n"
            ).encode("ascii")
            for _ in range(max(1, tries)):
                try:
                    sock.sendto(message, ssdp_address)
                except OSError:
                    return locations
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, _addr = sock.recvfrom(MAX_SSDP_RESPONSE)
            except socket.timeout:
                break
            except OSError:
                break
            location = _parse_ssdp_location(data)
            if location and location not in seen:
                seen.add(location)
                locations.append(location)
    finally:
        sock.close()
    return locations


def _parse_ssdp_location(data: bytes) -> str | None:
    try:
        text = data.decode("ascii", errors="ignore")
    except Exception:
        return None
    for line in text.split("\r\n"):
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        if name.strip().lower() == "location":
            location = value.strip()
            parts = urlsplit(location)
            if parts.scheme in ("http", "https") and parts.hostname:
                return location
    return None


def _bounded_http_get(url: str, *, timeout: float) -> bytes:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UpnpError(f"refusing to fetch a non-HTTP(S) URL: {url!r}")
    connection_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = connection_cls(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), timeout=timeout)
    try:
        path = parts.path or "/"
        if parts.query:
            path += f"?{parts.query}"
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read(MAX_HTTP_BODY + 1)
        if len(body) > MAX_HTTP_BODY:
            raise UpnpError("device description response exceeds the size limit")
        if response.status != 200:
            raise UpnpError(f"device description request failed with HTTP {response.status}")
        return body
    finally:
        connection.close()


def _reject_dangerous_xml(body: bytes) -> None:
    """A real, simple XXE/billion-laughs guard without a third-party XML
    library: any DOCTYPE or ENTITY declaration is refused outright before
    parsing -- a legitimate UPnP device description never needs either."""
    lowered = body[:4096].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise UpnpError("device description contains a DOCTYPE/ENTITY declaration -- refusing to parse")


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_control_url(root: ET.Element) -> tuple[str, str] | None:
    """Recursively walks every <service> element regardless of nesting or
    namespace declaration quirks (some real devices misdeclare their own
    namespace) -- returns (control_url, service_type) for the first
    service matching the preferred WANIPConnection/WANPPPConnection types."""
    candidates: dict[str, str] = {}
    for element in root.iter():
        if _strip_namespace(element.tag) != "service":
            continue
        service_type = None
        control_url = None
        for child in element:
            name = _strip_namespace(child.tag)
            if name == "serviceType":
                service_type = (child.text or "").strip()
            elif name == "controlURL":
                control_url = (child.text or "").strip()
        if service_type and control_url:
            candidates[service_type] = control_url
    for preferred in _SERVICE_TYPE_PREFERENCE:
        if preferred in candidates:
            return candidates[preferred], preferred
    return None


def discover_upnp_gateway(
    *, timeout: float = 2.5, tries: int = 2, ssdp_address: tuple[str, int] = SSDP_ADDRESS
) -> UpnpGateway | None:
    """Real SSDP discovery + device-description fetch + control-URL
    extraction. Returns None (never raises) if no usable IGD is found --
    a real, honest "not available in this environment" outcome."""
    locations = _ssdp_discover_locations(timeout=timeout, tries=tries, ssdp_address=ssdp_address)
    for location in locations:
        try:
            body = _bounded_http_get(location, timeout=timeout)
            _reject_dangerous_xml(body)
            root = ET.fromstring(body)
        except (UpnpError, ET.ParseError, OSError):
            continue
        found = _find_control_url(root)
        if found is None:
            continue
        control_url, service_type = found
        resolved = urljoin(location, control_url)
        if not _same_local_origin(location, resolved):
            continue
        return UpnpGateway(location=location, control_url=resolved, service_type=service_type)
    return None


def _same_local_origin(location: str, control_url: str) -> bool:
    """SSRF guard: the real control URL must resolve to the exact same
    scheme+host+port as the device description that named it (never
    trust a description that redirects control traffic elsewhere), and
    that host must be a real private/local-network address -- never a
    public-internet host."""
    loc = urlsplit(location)
    ctl = urlsplit(control_url)
    if (loc.scheme, loc.hostname, loc.port or (443 if loc.scheme == "https" else 80)) != (
        ctl.scheme,
        ctl.hostname,
        ctl.port or (443 if ctl.scheme == "https" else 80),
    ):
        return False
    try:
        ip = ipaddress.IPv4Address(loc.hostname)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


_SOAP_ENVELOPE = (
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    "<s:Body>{body}</s:Body></s:Envelope>"
)


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _soap_call(gateway: UpnpGateway, action: str, args: list[tuple[str, str]], *, timeout: float) -> ET.Element:
    arg_xml = "".join(f"<{name}>{_xml_escape(str(value))}</{name}>" for name, value in args)
    action_xml = f'<u:{action} xmlns:u="{gateway.service_type}">{arg_xml}</u:{action}>'
    body = _SOAP_ENVELOPE.format(body=action_xml).encode("utf-8")

    parts = urlsplit(gateway.control_url)
    connection_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = connection_cls(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), timeout=timeout)
    try:
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "Content-Length": str(len(body)),
            "SOAPACTION": f'"{gateway.service_type}#{action}"',
            "Connection": "Close",
        }
        path = parts.path or "/"
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read(MAX_HTTP_BODY + 1)
        if len(response_body) > MAX_HTTP_BODY:
            raise UpnpError("SOAP response exceeds the size limit")
        _reject_dangerous_xml(response_body)
        if response.status not in (200, 500):
            raise UpnpError(f"SOAP request failed with HTTP {response.status}")
        try:
            root = ET.fromstring(response_body)
        except ET.ParseError as exc:
            raise UpnpError(f"SOAP response is not valid XML: {exc}") from exc
        if response.status == 500:
            code, description = _extract_soap_fault(root)
            raise NatResultError(f"UPnP {action} failed: {description} (error {code})", code=code or -1)
        return root
    finally:
        connection.close()


def _extract_soap_fault(root: ET.Element) -> tuple[int | None, str]:
    code = None
    description = "unknown UPnP error"
    for element in root.iter():
        name = _strip_namespace(element.tag)
        if name == "errorCode" and element.text:
            try:
                code = int(element.text.strip())
            except ValueError:
                pass
        elif name == "errorDescription" and element.text:
            description = element.text.strip()
    return code, description


def upnp_get_external_ip_address(gateway: UpnpGateway, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    root = _soap_call(gateway, "GetExternalIPAddress", [], timeout=timeout)
    for element in root.iter():
        if _strip_namespace(element.tag) == "NewExternalIPAddress" and element.text:
            address = element.text.strip()
            _validate_public_address(address)
            return address
    raise UpnpError("GetExternalIPAddress response did not include NewExternalIPAddress")


def upnp_add_port_mapping(
    gateway: UpnpGateway,
    *,
    external_port: int,
    internal_port: int,
    internal_client: str,
    ip_protocol: str = "tcp",
    lease_duration: int = 0,
    description: str = "Pythia",
    timeout: float = DEFAULT_TIMEOUT,
) -> NatMapping:
    if ip_protocol not in ("tcp", "udp"):
        raise ValueError(f"unsupported ip_protocol: {ip_protocol!r}")
    args = [
        ("NewRemoteHost", ""),
        ("NewExternalPort", str(external_port)),
        ("NewProtocol", ip_protocol.upper()),
        ("NewInternalPort", str(internal_port)),
        ("NewInternalClient", internal_client),
        ("NewEnabled", "1"),
        ("NewPortMappingDescription", description),
        ("NewLeaseDuration", str(lease_duration)),
    ]
    _soap_call(gateway, "AddPortMapping", args, timeout=timeout)
    return NatMapping(
        protocol_name="upnp",
        external_address=None,
        external_port=external_port,
        internal_port=internal_port,
        ip_protocol=ip_protocol,
        lifetime_seconds=lease_duration,
        obtained_at=time.time(),
    )


def upnp_delete_port_mapping(
    gateway: UpnpGateway, *, external_port: int, ip_protocol: str = "tcp", timeout: float = DEFAULT_TIMEOUT
) -> None:
    args = [("NewRemoteHost", ""), ("NewExternalPort", str(external_port)), ("NewProtocol", ip_protocol.upper())]
    _soap_call(gateway, "DeletePortMapping", args, timeout=timeout)
