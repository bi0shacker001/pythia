"""Syncthing Global Discovery v3 client, gated and entirely in-process.

Queries locate trusted device IDs. Announcements authenticate with Pythia's own
device certificate. Discovery carries addresses only; it never carries game data.
"""

from __future__ import annotations

import http.client
import json
import ssl
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pythia.core.sync import config, identity

MAX_RESPONSE = 64 * 1024
MAX_ADDRESSES = 64


class GlobalDiscoveryError(RuntimeError):
    pass


def _prepare_url(server_url: str, **query) -> tuple[str, int, str, str | None]:
    parts = urlsplit(server_url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise GlobalDiscoveryError("global discovery requires an HTTPS server URL without credentials")
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    pinned_id = params.pop("id", None)
    params.update(query)
    path = urlunsplit(("", "", parts.path or "/v2/", urlencode(params), ""))
    return parts.hostname, parts.port or 443, path, pinned_id


def _enabled(base_dir: Path | None) -> None:
    settings = config.load(base_dir=base_dir)
    if not settings["enabled"] or not settings["global_discovery"]:
        raise GlobalDiscoveryError("global discovery is disabled")


def _connect(server_url: str, *, context: ssl.SSLContext, timeout: float, connection_factory, query: dict):
    host, port, path, pinned_id = _prepare_url(server_url, **query)
    connection = connection_factory(host, port=port, context=context, timeout=timeout)
    connection.connect()
    if pinned_id:
        certificate = connection.sock.getpeercert(binary_form=True)
        if not certificate or identity.device_id_from_der(certificate) != pinned_id:
            connection.close()
            raise GlobalDiscoveryError("global discovery server certificate does not match its pinned device ID")
    return connection, path


def _read_json_response(response) -> dict:
    declared = response.getheader("Content-Length")
    if declared:
        try:
            if int(declared) > MAX_RESPONSE:
                raise GlobalDiscoveryError("global discovery response is too large")
        except ValueError as exc:
            raise GlobalDiscoveryError("invalid global discovery Content-Length") from exc
    data = response.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise GlobalDiscoveryError("global discovery response is too large")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GlobalDiscoveryError("global discovery returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise GlobalDiscoveryError("global discovery response must be an object")
    return value


def query_device(
    server_url: str,
    device_id: str,
    *,
    base_dir: Path | None = None,
    timeout: float = 10.0,
    connection_factory=http.client.HTTPSConnection,
) -> list[str]:
    _enabled(base_dir)
    if not identity.validate_device_id(device_id):
        raise GlobalDiscoveryError("cannot query an invalid device ID")
    # CA validation is used unless the URL supplies Syncthing's ?id= pin.
    _, _, _, pinned_id = _prepare_url(server_url)
    context = ssl._create_unverified_context() if pinned_id else ssl.create_default_context()
    connection, path = _connect(
        server_url, context=context, timeout=timeout, connection_factory=connection_factory,
        query={"device": device_id},
    )
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status == 404:
            response.read()
            return []
        if response.status != 200:
            response.read(MAX_RESPONSE)
            raise GlobalDiscoveryError(f"global discovery query failed with HTTP {response.status}")
        value = _read_json_response(response)
    finally:
        connection.close()
    addresses = value.get("addresses")
    if not isinstance(addresses, list) or len(addresses) > MAX_ADDRESSES or any(not isinstance(a, str) for a in addresses):
        raise GlobalDiscoveryError("global discovery returned invalid addresses")
    return addresses


def announce(
    server_url: str,
    node,
    addresses: list[str],
    *,
    base_dir: Path | None = None,
    timeout: float = 10.0,
    connection_factory=http.client.HTTPSConnection,
) -> int | None:
    _enabled(base_dir)
    if not isinstance(addresses, list) or len(addresses) > MAX_ADDRESSES or any(not isinstance(a, str) for a in addresses):
        raise GlobalDiscoveryError("announcement addresses are invalid")
    _, _, _, pinned_id = _prepare_url(server_url)
    context = ssl._create_unverified_context() if pinned_id else ssl.create_default_context()
    context.load_cert_chain(node.identity_record["certificate_path"], node.identity_record["private_key_path"])
    connection, path = _connect(
        server_url, context=context, timeout=timeout, connection_factory=connection_factory, query={},
    )
    body = json.dumps({"addresses": addresses}, separators=(",", ":")).encode("utf-8")
    try:
        connection.request("POST", path, body=body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        response = connection.getresponse()
        response.read(MAX_RESPONSE)
        if response.status != 204:
            raise GlobalDiscoveryError(f"global discovery announcement failed with HTTP {response.status}")
        header = response.getheader("Reannounce-After")
    finally:
        connection.close()
    if header is None:
        return None
    try:
        seconds = int(header)
    except ValueError as exc:
        raise GlobalDiscoveryError("invalid Reannounce-After header") from exc
    return max(seconds, 1)
