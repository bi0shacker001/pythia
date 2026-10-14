"""Real, deterministic fake-router tests for pythia.core.sync.nat's UPnP
IGD client -- a real local HTTP server (device description + SOAP control)
and a real local UDP responder (SSDP), both on loopback, exercised through
`nat.discover_upnp_gateway()`'s `ssdp_address=` test override so nothing
here depends on real multicast group membership (unreliable in a
sandboxed/CI environment).
"""

import http.server
import socket
import threading
import unittest
from urllib.parse import urlsplit

from pythia.core.sync import nat

_DEVICE_DESCRIPTION = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType>
    <deviceList>
      <device>
        <deviceType>urn:schemas-upnp-org:device:WANDevice:1</deviceType>
        <deviceList>
          <device>
            <deviceType>urn:schemas-upnp-org:device:WANConnectionDevice:1</deviceType>
            <serviceList>
              <service>
                <serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
                <serviceId>urn:upnp-org:serviceId:WANIPConn1</serviceId>
                <controlURL>/ctl/IPConn</controlURL>
                <eventSubURL>/evt/IPConn</eventSubURL>
                <SCPDURL>/IPConn.xml</SCPDURL>
              </service>
            </serviceList>
          </device>
        </deviceList>
      </device>
    </deviceList>
  </device>
</root>"""

_SOAP_SUCCESS_ADD = (
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    '<s:Body><u:AddPortMappingResponse xmlns:u="urn:schemas-upnp-org:service:WANIPConnection:1"/></s:Body></s:Envelope>'
)

_SOAP_SUCCESS_DELETE = (
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    '<s:Body><u:DeletePortMappingResponse xmlns:u="urn:schemas-upnp-org:service:WANIPConnection:1"/></s:Body></s:Envelope>'
)

_SOAP_FAULT_718 = (
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    "<s:Body><s:Fault><faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>"
    '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>718</errorCode>'
    "<errorDescription>ConflictInMappingEntry</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>"
)


def _external_ip_response(address: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body><u:GetExternalIPAddressResponse xmlns:u="urn:schemas-upnp-org:service:WANIPConnection:1">'
        f"<NewExternalIPAddress>{address}</NewExternalIPAddress>"
        "</u:GetExternalIPAddressResponse></s:Body></s:Envelope>"
    )


class _FakeIgdHttpServer:
    """A real HTTP server standing in for a router's UPnP control point.
    `soap_responses` maps action name -> (http_status, body_str); a
    missing action falls back to a generic success/fault based on name."""

    def __init__(self, *, device_xml: str = _DEVICE_DESCRIPTION, soap_responses: dict | None = None):
        self.device_xml = device_xml
        self.soap_responses = soap_responses or {}
        self.requests: list[tuple[str, str, bytes]] = []
        handler = self._make_handler()
        self._server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _make_handler(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = outer.device_xml.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                soap_action = self.headers.get("SOAPACTION", "")
                action = soap_action.rsplit("#", 1)[-1].strip('"')
                outer.requests.append((action, soap_action, body))
                status, response_body = outer.soap_responses.get(
                    action, (200, _SOAP_SUCCESS_ADD if action == "AddPortMapping" else _SOAP_SUCCESS_DELETE)
                )
                response_bytes = response_body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(response_bytes)))
                self.end_headers()
                self.wfile.write(response_bytes)

        return Handler

    @property
    def location(self) -> str:
        return f"http://127.0.0.1:{self.port}/rootDesc.xml"

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


class _FakeSsdpResponder:
    """A real UDP responder standing in for the multicast SSDP rendezvous
    address -- tests point `discover_upnp_gateway(ssdp_address=...)` at
    this instead of the real 239.255.255.250:1900."""

    def __init__(self, location: str | None):
        self._location = location
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def address(self) -> tuple[str, int]:
        return ("127.0.0.1", self.port)

    def _run(self):
        while not self._stop.is_set():
            try:
                _data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if self._location is None:
                continue
            response = (
                "HTTP/1.1 200 OK\r\n"
                "CACHE-CONTROL: max-age=1800\r\n"
                f"LOCATION: {self._location}\r\n"
                "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
                "USN: uuid:test::urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
                "\r\n"
            ).encode("ascii")
            try:
                self._sock.sendto(response, addr)
            except OSError:
                pass

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._sock.close()


class UpnpDiscoveryTests(unittest.TestCase):
    def test_real_discovery_finds_control_url_and_service_type(self):
        http_server = _FakeIgdHttpServer()
        ssdp = _FakeSsdpResponder(http_server.location)
        try:
            gateway = nat.discover_upnp_gateway(timeout=1.0, tries=1, ssdp_address=ssdp.address)
            self.assertIsNotNone(gateway)
            self.assertEqual("urn:schemas-upnp-org:service:WANIPConnection:1", gateway.service_type)
            self.assertTrue(gateway.control_url.endswith("/ctl/IPConn"))
        finally:
            ssdp.close()
            http_server.close()

    def test_no_ssdp_response_returns_none(self):
        ssdp = _FakeSsdpResponder(None)
        try:
            gateway = nat.discover_upnp_gateway(timeout=0.3, tries=1, ssdp_address=ssdp.address)
            self.assertIsNone(gateway)
        finally:
            ssdp.close()

    def test_ssrf_control_url_pointing_off_device_is_rejected(self):
        """A malicious/compromised device description naming a controlURL
        on a DIFFERENT origin must never be trusted -- the real SSRF guard."""
        malicious_xml = _DEVICE_DESCRIPTION.replace("/ctl/IPConn", "http://198.51.100.7/ctl/IPConn")
        http_server = _FakeIgdHttpServer(device_xml=malicious_xml)
        ssdp = _FakeSsdpResponder(http_server.location)
        try:
            gateway = nat.discover_upnp_gateway(timeout=1.0, tries=1, ssdp_address=ssdp.address)
            self.assertIsNone(gateway)
        finally:
            ssdp.close()
            http_server.close()

    def test_doctype_in_device_description_is_rejected(self):
        malicious_xml = '<?xml version="1.0"?><!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>' + _DEVICE_DESCRIPTION
        http_server = _FakeIgdHttpServer(device_xml=malicious_xml)
        ssdp = _FakeSsdpResponder(http_server.location)
        try:
            gateway = nat.discover_upnp_gateway(timeout=1.0, tries=1, ssdp_address=ssdp.address)
            self.assertIsNone(gateway)
        finally:
            ssdp.close()
            http_server.close()

    def test_malformed_xml_is_rejected(self):
        http_server = _FakeIgdHttpServer(device_xml="<not><valid</xml>")
        ssdp = _FakeSsdpResponder(http_server.location)
        try:
            gateway = nat.discover_upnp_gateway(timeout=1.0, tries=1, ssdp_address=ssdp.address)
            self.assertIsNone(gateway)
        finally:
            ssdp.close()
            http_server.close()

    def test_no_matching_service_type_returns_none(self):
        xml_without_service = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
  <deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType>
</device></root>"""
        http_server = _FakeIgdHttpServer(device_xml=xml_without_service)
        ssdp = _FakeSsdpResponder(http_server.location)
        try:
            gateway = nat.discover_upnp_gateway(timeout=1.0, tries=1, ssdp_address=ssdp.address)
            self.assertIsNone(gateway)
        finally:
            ssdp.close()
            http_server.close()


class UpnpSoapActionTests(unittest.TestCase):
    def _gateway_for(self, http_server: _FakeIgdHttpServer) -> nat.UpnpGateway:
        return nat.UpnpGateway(
            location=http_server.location,
            control_url=f"http://127.0.0.1:{http_server.port}/ctl/IPConn",
            service_type="urn:schemas-upnp-org:service:WANIPConnection:1",
        )

    def test_add_port_mapping_sends_real_soap_body_in_correct_order(self):
        http_server = _FakeIgdHttpServer()
        try:
            gateway = self._gateway_for(http_server)
            mapping = nat.upnp_add_port_mapping(
                gateway, external_port=51413, internal_port=51413, internal_client="192.168.1.50", ip_protocol="tcp"
            )
            self.assertEqual("upnp", mapping.protocol_name)
            self.assertEqual(51413, mapping.external_port)
            self.assertEqual(1, len(http_server.requests))
            action, soap_action, body = http_server.requests[0]
            self.assertEqual("AddPortMapping", action)
            self.assertEqual('"urn:schemas-upnp-org:service:WANIPConnection:1#AddPortMapping"', soap_action)
            text = body.decode("utf-8")
            # Real, in-order argument check.
            order = ["NewRemoteHost", "NewExternalPort", "NewProtocol", "NewInternalPort", "NewInternalClient", "NewEnabled", "NewPortMappingDescription", "NewLeaseDuration"]
            positions = [text.index(f"<{name}>") for name in order]
            self.assertEqual(sorted(positions), positions)
            self.assertIn("<NewProtocol>TCP</NewProtocol>", text)
            self.assertIn("<NewInternalClient>192.168.1.50</NewInternalClient>", text)
        finally:
            http_server.close()

    def test_delete_port_mapping_real_call(self):
        http_server = _FakeIgdHttpServer()
        try:
            gateway = self._gateway_for(http_server)
            nat.upnp_delete_port_mapping(gateway, external_port=51413, ip_protocol="tcp")
            self.assertEqual(1, len(http_server.requests))
            self.assertEqual("DeletePortMapping", http_server.requests[0][0])
        finally:
            http_server.close()

    def test_get_external_ip_address_success(self):
        http_server = _FakeIgdHttpServer(soap_responses={"GetExternalIPAddress": (200, _external_ip_response("8.8.8.8"))})
        try:
            gateway = self._gateway_for(http_server)
            address = nat.upnp_get_external_ip_address(gateway)
            self.assertEqual("8.8.8.8", address)
        finally:
            http_server.close()

    def test_get_external_ip_address_rejects_private_address(self):
        http_server = _FakeIgdHttpServer(
            soap_responses={"GetExternalIPAddress": (200, _external_ip_response("192.168.1.1"))}
        )
        try:
            gateway = self._gateway_for(http_server)
            with self.assertRaises(nat.NatError):
                nat.upnp_get_external_ip_address(gateway)
        finally:
            http_server.close()

    def test_soap_fault_is_raised_as_real_result_error(self):
        http_server = _FakeIgdHttpServer(soap_responses={"AddPortMapping": (500, _SOAP_FAULT_718)})
        try:
            gateway = self._gateway_for(http_server)
            with self.assertRaises(nat.NatResultError) as ctx:
                nat.upnp_add_port_mapping(
                    gateway, external_port=51413, internal_port=51413, internal_client="192.168.1.50"
                )
            self.assertEqual(718, ctx.exception.code)
        finally:
            http_server.close()

    def test_malformed_soap_response_is_rejected(self):
        http_server = _FakeIgdHttpServer(soap_responses={"AddPortMapping": (200, "<not-xml")})
        try:
            gateway = self._gateway_for(http_server)
            with self.assertRaises(nat.NatError):
                nat.upnp_add_port_mapping(
                    gateway, external_port=51413, internal_port=51413, internal_client="192.168.1.50"
                )
        finally:
            http_server.close()


if __name__ == "__main__":
    unittest.main()
