"""Explicitly-started UDP LAN discovery lifecycle.

The service uses broadcast only to exchange small signed location announcements.
Game and metadata traffic always use authenticated direct TCP/TLS connections.
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import time
from collections.abc import Callable
from pathlib import Path

from pythia.core.sync import config, discovery

DISCOVERY_PORT = 21027
BROADCAST_ADDRESS = "255.255.255.255"
ANNOUNCE_INTERVAL = 45.0


class LanDiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, *, local_system_uuid: str, base_dir: Path | None, on_peer: Callable[[dict], None]):
        self.local_system_uuid = local_system_uuid
        self.base_dir = base_dir
        self.on_peer = on_peer
        self.transport = None
        self._recent: dict[bytes, float] = {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        now = time.monotonic()
        fingerprint = hashlib.sha256(data).digest()
        self._recent = {key: expiry for key, expiry in self._recent.items() if expiry > now}
        if fingerprint in self._recent:
            return
        try:
            candidate = discovery.verify_announcement(data, base_dir=self.base_dir)
        except discovery.DiscoveryError:
            return
        if candidate["system_uuid"] == self.local_system_uuid:
            return
        self._recent[fingerprint] = now + discovery.MAX_AGE_SECONDS
        candidate["source_address"] = addr[0] if addr else None
        self.on_peer(candidate)

    def error_received(self, _exc):
        # UDP broadcast errors are transient; lifecycle owners can infer liveness
        # from candidates and choose when to restart the service.
        return


class LanDiscoveryService:
    def __init__(self, transport, protocol_instance, announce_task):
        self.transport = transport
        self.protocol = protocol_instance
        self._announce_task = announce_task

    async def close(self):
        self._announce_task.cancel()
        try:
            await self._announce_task
        except asyncio.CancelledError:
            pass
        self.transport.close()


async def start(
    node,
    *,
    direct_port: int,
    addresses: list[str],
    on_peer: Callable[[dict], None],
    base_dir: Path | None = None,
    bind_port: int = DISCOVERY_PORT,
    target: tuple[str, int] = (BROADCAST_ADDRESS, DISCOVERY_PORT),
    interval: float = ANNOUNCE_INTERVAL,
) -> LanDiscoveryService:
    """Start discovery only when both the master and LAN gates are enabled."""
    preferences = config.load(base_dir=base_dir)
    if not preferences["enabled"] or not preferences["lan_discovery"]:
        raise config.SyncConfigError("LAN discovery is disabled")
    if interval <= 0:
        raise ValueError("announcement interval must be positive")
    packet = discovery.create_announcement(node, direct_port, addresses)
    loop = asyncio.get_running_loop()
    protocol_instance = LanDiscoveryProtocol(
        local_system_uuid=node.system_uuid,
        base_dir=base_dir,
        on_peer=on_peer,
    )
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol_instance,
        local_addr=("0.0.0.0", bind_port),
        family=socket.AF_INET,
        allow_broadcast=True,
    )

    async def _announce():
        while True:
            transport.sendto(packet, target)
            await asyncio.sleep(interval)

    task = asyncio.create_task(_announce(), name="pythia-lan-discovery")
    return LanDiscoveryService(transport, protocol_instance, task)
