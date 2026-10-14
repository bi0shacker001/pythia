import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pythia.core.sync import config, global_discovery
from pythia.core.sync.connectivity.direct_tcp import load_local_node


class _Response:
    def __init__(self, status, body=b"", headers=None):
        self.status, self._body, self._headers = status, io.BytesIO(body), headers or {}
    def read(self, size=-1): return self._body.read(size)
    def getheader(self, name): return self._headers.get(name)


class _Connection:
    response = _Response(404)
    instances = []
    def __init__(self, host, **kwargs):
        self.host, self.kwargs, self.requests, self.closed = host, kwargs, [], False
        self.__class__.instances.append(self)
    def connect(self): pass
    def request(self, method, path, body=None, headers=None): self.requests.append((method, path, body, headers))
    def getresponse(self): return self.__class__.response
    def close(self): self.closed = True


class GlobalDiscoveryTests(unittest.TestCase):
    def setUp(self):
        _Connection.instances.clear()

    def test_disabled_gate_makes_no_connection(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(global_discovery.GlobalDiscoveryError):
                global_discovery.query_device("https://discovery.example/v2/", "A", base_dir=Path(td), connection_factory=_Connection)
            self.assertFalse(_Connection.instances)

    def test_query_and_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config.save({"enabled": True, "global_discovery": True}, base_dir=base)
            node = load_local_node("Remote", base_dir=base / "remote")
            _Connection.response = _Response(200, b'{"addresses":["tcp://192.0.2.1:22000"]}')
            result = global_discovery.query_device("https://discovery.example/v2/", node.device_id, base_dir=base, connection_factory=_Connection)
            self.assertEqual(result, ["tcp://192.0.2.1:22000"])
            self.assertIn("device=", _Connection.instances[-1].requests[0][1])
            _Connection.response = _Response(404)
            self.assertEqual(global_discovery.query_device("https://discovery.example/v2/", node.device_id, base_dir=base, connection_factory=_Connection), [])

    def test_announcement_uses_client_certificate_and_reannounce_header(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config.save({"enabled": True, "global_discovery": True}, base_dir=base)
            node = load_local_node("Local", base_dir=base)
            _Connection.response = _Response(204, headers={"Reannounce-After": "600"})
            with mock.patch("ssl.SSLContext.load_cert_chain") as load_chain:
                delay = global_discovery.announce("https://discovery.example/v2/", node, ["tcp://:22000"], base_dir=base, connection_factory=_Connection)
            self.assertEqual(delay, 600)
            load_chain.assert_called_once()
            self.assertEqual(_Connection.instances[-1].requests[0][0], "POST")


if __name__ == "__main__":
    unittest.main()
