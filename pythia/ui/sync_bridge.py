"""Qt-independent business layer between pythia/ui/backend.py's Slots and
pythia.core.sync's SyncService (2026-08-08, "optional core sync service and
native device/transfer workflow"). Kept out of backend.py itself so this
logic is unit-testable without Qt/PySide6 at all -- the same reasoning
already applied to every other pythia.core.* module in this app.

Owns exactly one SyncService and one SyncRuntime for the process's lifetime.
Constructing a SyncBridge has zero side effects: SyncService itself is inert
until start() actually runs with sync enabled (config.is_enabled()), and
SyncRuntime's background loop thread is not spawned until first use. Calling
ensure_started() is always safe -- it is a real no-op while sync is
disabled, so the native UI can call it every time the Devices & Sync page
opens without violating "opening the page while disabled must not
initialize sync."
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path

from pythia.core.sync import config as sync_config
from pythia.core.sync import identity, network_settings, pairing, service, trust
from pythia.core.sync.runtime import SyncRuntime

_CALL_TIMEOUT = 20.0


class SyncBridge:
    def __init__(
        self,
        *,
        on_status=None,
        on_transfer_offer=None,
        on_metadata_requested=None,
        on_metadata_reconciled=None,
        on_metadata_pull_requested=None,
    ):
        self._runtime = SyncRuntime()
        self._on_status_cb = on_status
        self._on_transfer_offer_cb = on_transfer_offer
        self._on_metadata_requested_cb = on_metadata_requested
        self._on_metadata_reconciled_cb = on_metadata_reconciled
        self._on_metadata_pull_requested_cb = on_metadata_pull_requested
        # Outgoing (sending) transfer futures, keyed by peer system_uuid, so
        # cancel_transfer() can find and cancel a transfer this system
        # itself initiated (2026-08-08, Codex sync review, "Add caller-driven
        # transfer cancellation ... wire it to native UI"). Incoming
        # (receiving) transfers are tracked inside SyncService itself --
        # see SyncService.cancel_transfer().
        self._active_outgoing: dict[str, concurrent.futures.Future] = {}
        self._service = service.SyncService(
            on_status=self._handle_status,
            on_transfer_offer=self._handle_offer,
            on_metadata_requested=self._handle_metadata_requested,
            on_metadata_reconciled=self._handle_metadata_reconciled,
            on_metadata_pull_requested=self._handle_metadata_pull_requested,
        )

    def _handle_status(self, event: dict) -> None:
        if self._on_status_cb:
            self._on_status_cb(event)

    def _handle_offer(self, offer_id: str, offer: dict, peer_system_uuid: str) -> None:
        if self._on_transfer_offer_cb:
            self._on_transfer_offer_cb(offer_id, offer, peer_system_uuid)

    def _handle_metadata_requested(self, peer_system_uuid: str) -> list:
        return self._on_metadata_requested_cb(peer_system_uuid) if self._on_metadata_requested_cb else []

    def _handle_metadata_reconciled(self, peer_system_uuid: str, result: dict) -> None:
        if self._on_metadata_reconciled_cb:
            self._on_metadata_reconciled_cb(peer_system_uuid, result)

    def _handle_metadata_pull_requested(self) -> list:
        return self._on_metadata_pull_requested_cb() if self._on_metadata_pull_requested_cb else []

    # ---- lifecycle --------------------------------------------------------
    def ensure_started(self) -> dict:
        """Safe to call any number of times, including while disabled (a
        real no-op there -- see SyncService.start())."""
        net = network_settings.load()
        return self._runtime.call(
            self._service.start(
                listen_host=net["listen_host"],
                listen_port=net["listen_port"],
                global_discovery_server=net["global_discovery_server"],
                relay_server=net["relay_server"],
            ),
            timeout=_CALL_TIMEOUT,
        )

    def stop(self) -> dict:
        return self._runtime.call(self._service.stop(), timeout=_CALL_TIMEOUT)

    def set_enabled(self, enabled: bool) -> dict:
        sync_config.save({"enabled": bool(enabled)})
        if enabled:
            return self.ensure_started()
        return self.stop()

    @property
    def running(self) -> bool:
        return self._service.running

    # ---- config / settings -------------------------------------------------
    def get_config(self) -> dict:
        return sync_config.load()

    # Config keys whose live effect is entirely handled by SyncService's own
    # targeted set_relay_enabled()/set_global_discovery_enabled() (or need
    # no live action at all -- allow_game_transfer/allow_metadata_sync are
    # gates checked at call time elsewhere, never tied to a background task
    # or socket). Anything else (`listen`, `lan_discovery`) genuinely
    # requires rebinding the listener/LAN socket, so those still fall back
    # to a real, honest full stop()+ensure_started() cycle (2026-08-08,
    # Codex sync review item 5, "if a full restart remains necessary for a
    # specific listener setting, expose that precise result rather than
    # claiming live apply" -- this IS that precise, honest fallback, not a
    # silently-broader restart).
    _LIVE_ONLY_KEYS = frozenset(
        {"relay_discovery", "global_discovery", "nat_traversal", "allow_game_transfer", "allow_metadata_sync"}
    )

    def set_config(self, patch: dict) -> dict:
        """Persists the patch, then applies exactly what actually changed:
        `relay_discovery`/`global_discovery`/`nat_traversal` toggle live via
        SyncService's own targeted methods (no listener/LAN restart);
        anything else that actually changed (`listen`, `lan_discovery`)
        requires a real full stop()+ensure_started() cycle since those
        genuinely need the listener/LAN socket rebound."""
        before = sync_config.load()
        saved = sync_config.save(patch)
        if not self._service.running:
            return saved
        changed_keys = {key for key in patch if before.get(key) != saved.get(key)}
        if changed_keys - self._LIVE_ONLY_KEYS:
            self.stop()
            self.ensure_started()
            return saved
        net = network_settings.load()
        if "relay_discovery" in changed_keys:
            self._runtime.call(
                self._service.set_relay_enabled(saved["relay_discovery"], relay_server=net["relay_server"]),
                timeout=_CALL_TIMEOUT,
            )
        if "global_discovery" in changed_keys:
            self._runtime.call(
                self._service.set_global_discovery_enabled(
                    saved["global_discovery"], global_discovery_server=net["global_discovery_server"]
                ),
                timeout=_CALL_TIMEOUT,
            )
        if "nat_traversal" in changed_keys:
            self._runtime.call(self._service.set_nat_traversal_enabled(saved["nat_traversal"]), timeout=_CALL_TIMEOUT)
        return saved

    def get_network_settings(self) -> dict:
        return network_settings.load()

    def set_network_settings(self, patch: dict) -> dict:
        saved = network_settings.save(patch)
        if self._service.running:
            self.stop()
            self.ensure_started()
        return saved

    def get_status(self) -> dict:
        node = self._service.node
        relay_client = self._service._relay_client
        return {
            "running": self._service.running,
            "config": sync_config.load(),
            "network_settings": network_settings.load(),
            "device_id": node.device_id if node else None,
            "system_uuid": node.system_uuid if node else None,
            "system_name": node.identity_record.get("system_name") if node else None,
            "peers": self._service.known_peers(),
            "pending_offers": self._service.pending_offers(),
            "relay_connected": bool(relay_client is not None and relay_client.connected),
            "lan_discovery": {
                "active": self._service._lan_service is not None,
                "last_error": self._service._lan_last_error,
            },
            "retry_state": self._service.retry_state(),
            "nat": self._service.nat_status(),
            "dual_stack": self._service.dual_stack_status(),
        }

    # ---- identity -----------------------------------------------------------
    def set_system_name(self, name: str) -> dict:
        if self._service.node is None:
            raise RuntimeError("Enable sync before setting a system name.")
        return identity.set_system_name(name)

    # ---- pairing --------------------------------------------------------------
    def create_pairing_invitation(self, *, friendly_name: str | None = None) -> str:
        """Requires sync to already be running -- a pairing invitation
        carries this system's real identity, and identity is only created
        once sync has actually been enabled and started (never merely from
        opening this page)."""
        if self._service.node is None:
            raise RuntimeError("Enable sync before creating a pairing invitation.")
        # Real local + NAT-mapped external candidates (2026-08-08, "internal
        # NAT traversal and direct-route publication" -- a fresh pairing
        # invitation is exactly where a real external endpoint is most
        # useful, letting the paired peer reach us across networks).
        addresses = self._service.own_direct_candidates()
        package = pairing.create_pairing_package(friendly_name=friendly_name, known_addresses=addresses)
        return pairing.encode_pairing_text(package)

    def accept_pairing_invitation(self, text: str) -> dict:
        package = pairing.decode_pairing_text(text)
        return pairing.accept_pairing_package(package)

    def pair_manually(self, *, system_uuid: str, device_id: str, friendly_name: str) -> dict:
        return pairing.pair_by_manual_entry(system_uuid=system_uuid, device_id=device_id, friendly_name=friendly_name)

    # ---- trust ------------------------------------------------------------------
    def list_trusted(self) -> list[dict]:
        return trust.list_trusted()

    def remove_trusted(self, system_uuid: str) -> bool:
        return trust.remove_trusted(system_uuid)

    # ---- transfer ---------------------------------------------------------------
    def offer_and_send_game(self, system_uuid: str, *, root: str | Path, game_id: str, display_name: str) -> dict:
        """Connects to a trusted device and offers a real game transfer.
        Blocks (on the calling thread, expected to be a Backend
        `_run_worker` background thread) until the peer accepts/rejects and,
        if accepted, the whole transfer completes -- unless cancelled first
        via cancel_transfer(), which sends a real TRANSFER_CANCEL and
        interrupts this coroutine at its own next await point (the
        documented `concurrent.futures.Future.cancel()` behavior for a
        `run_coroutine_threadsafe`-backed future)."""
        from pythia.core.sync import protocol, transfer_offer

        net = network_settings.load()

        async def _run():
            connection = await self._service.connect_to_trusted(system_uuid, port=net["listen_port"])
            try:
                return await transfer_offer.offer_and_send_game(
                    connection, root, game_id=game_id, display_name=display_name, stream_id=1, timeout=60
                )
            except asyncio.CancelledError:
                try:
                    await connection.send_json(
                        protocol.MessageType.TRANSFER_CANCEL, {"message": "Cancelled by local user."}, stream_id=1
                    )
                except Exception:
                    pass
                raise
            finally:
                await connection.close()

        future = self._runtime.submit(_run())
        self._active_outgoing[system_uuid] = future
        try:
            return future.result()
        except concurrent.futures.CancelledError:
            return {"status": "cancelled"}
        finally:
            self._active_outgoing.pop(system_uuid, None)

    def cancel_transfer(self, system_uuid: str) -> bool:
        """Cancels whichever real transfer with `system_uuid` is currently
        active on THIS side -- an outgoing send this system initiated (a
        pending future here) or an incoming receive this system accepted
        (tracked inside SyncService itself). Returns False if neither is
        active."""
        future = self._active_outgoing.get(system_uuid)
        if future is not None:
            return future.cancel()
        return self._runtime.call(self._service.cancel_transfer(system_uuid), timeout=10)

    def resolve_offer(
        self, offer_id: str, *, accept: bool, destination: str | None = None, cache: str | None = None, message: str = ""
    ) -> bool:
        return self._service.resolve_offer(offer_id, accept=accept, destination=destination, cache=cache, message=message)

    def pending_offers(self) -> dict:
        return self._service.pending_offers()

    def known_peers(self) -> dict:
        return self._service.known_peers()

    def retry_state(self) -> dict:
        return self._service.retry_state()

    # ---- metadata sync ------------------------------------------------------------
    def sync_metadata_with_device(self, system_uuid: str, local_items: list) -> dict:
        """PUSHES our own local metadata items to a trusted peer -- the
        peer's own passive listener (its SyncService's on_metadata_requested/
        on_metadata_reconciled hooks) reconciles and persists them into its
        own store. See pull_metadata_from_device() for the explicit,
        separately-labeled opposite direction (2026-08-08, Codex sync
        review, "do not call a one-way push 'Sync'")."""
        from pythia.core.sync import metadata_session

        net = network_settings.load()

        async def _run():
            connection = await self._service.connect_to_trusted(system_uuid, port=net["listen_port"])
            try:
                return await metadata_session.send_summary_and_records(connection, local_items, stream_id=1, timeout=60)
            finally:
                await connection.close()

        return self._runtime.call(_run(), timeout=None)

    def pull_metadata_from_device(self, system_uuid: str, local_mirrored_items: list) -> dict:
        """PULLS a trusted peer's own real state into this system's mirror
        of it -- the explicit opposite of sync_metadata_with_device(). The
        peer's own passive listener answers via its own
        on_metadata_pull_requested() hook (its real, own-published items,
        never a mirrored copy of a THIRD system's data). Returns the real
        classification (upserts/tombstones/conflicts) for the caller
        (Backend) to persist -- this method itself never writes to
        datadir."""
        from pythia.core.sync import metadata_session

        net = network_settings.load()

        async def _run():
            connection = await self._service.connect_to_trusted(system_uuid, port=net["listen_port"])
            try:
                return await metadata_session.pull_and_reconcile(
                    connection, local_mirrored_items, peer_system_uuid=system_uuid, stream_id=1, timeout=60
                )
            finally:
                await connection.close()

        return self._runtime.call(_run(), timeout=None)

    # ---- staging (NAS/cloud-synced folder) fallback -------------------------------
    def export_staging_snapshot(self, folder: str | Path, local_items: list, *, repository_id: str = "default") -> dict:
        """Exports OUR OWN real metadata items to a mounted-folder/NAS/
        cloud-synced snapshot (2026-08-08, Codex sync review, "Orchestrate
        staging.py as an optional metadata fallback"). Synchronous --
        staging.py does its own ordinary, local file I/O, no network, no
        runtime needed."""
        from pythia.core.sync import staging

        return staging.export_snapshot(local_items, folder, repository_id=repository_id)

    def import_staging_snapshot(self, folder: str | Path, *, repository_id: str = "default") -> dict:
        """Reads a staged snapshot's real items back -- never trust or
        credentials (staging.py's own data model has no such concept: only
        "record"/"tombstone" metadata.py kinds are ever valid). The caller
        (Backend) is responsible for grouping by real owner and reconciling
        each owner's items against this system's own mirrored state, the
        same as the network push/pull paths -- this method only reads."""
        from pythia.core.sync import staging

        return staging.import_snapshot(folder, repository_id=repository_id)

    # ---- NAT traversal (2026-08-08, "internal NAT traversal and direct-route publication") ----
    def nat_status(self) -> dict:
        return self._service.nat_status()

    def retry_nat_mapping(self) -> dict:
        """No fixed timeout -- a real UPnP SSDP+HTTP round trip can
        legitimately take longer than the usual _CALL_TIMEOUT."""
        return self._runtime.call(self._service.retry_nat_mapping_now(), timeout=None)
