"""Native "Devices && Sync" page (2026-08-08, "optional core sync service and
native device/transfer workflow"). All real state, driven entirely through
Backend's sync Slots/Signals (pythia/ui/backend.py's "sync / devices"
section, itself thin delegation to pythia.ui.sync_bridge.SyncBridge):

- Enable/disable is the one real gate: constructing/opening this page calls
  Backend.ensure_sync_started(), which is a genuine no-op while disabled
  (SyncService.start() itself checks config.is_enabled() first) -- so
  "opening the page while disabled must not initialize sync" holds by
  construction, not by this view remembering not to call anything.
- System name, pairing (invitation/import/manual), trusted-device list,
  LAN/global discovery toggles, listener host/port are all real, persisted
  settings -- no placeholder fields.
- Incoming transfer offers arrive via Backend.syncTransferOfferReceived and
  sit in a real, un-decided list until the user explicitly accepts (picking
  a real destination folder) or rejects -- never auto-resolved.
- Per-game "Send to device" reuses the same real destination-review pattern
  on the sending side's confirmation, and blocks in a background worker
  (Backend._run_worker) rather than the GUI thread.
"""

from __future__ import annotations

import json
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

OFFER_ID_ROLE = Qt.ItemDataRole.UserRole + 1
SYSTEM_UUID_ROLE = Qt.ItemDataRole.UserRole + 2


class SyncView(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._pending_offer_rows: dict[str, QListWidgetItem] = {}
        # peer_system_uuid -> destination folder chosen when accepting that
        # peer's offer, so a later "transfer_received" status event (which
        # only carries system_uuid + result, not the destination) can trigger
        # real onboarding of the newly materialized game -- see
        # _on_status_event() below.
        self._pending_transfer_destinations: dict[str, str] = {}

        root = QVBoxLayout(self)

        enable_group = QGroupBox("Device sync")
        enable_layout = QVBoxLayout(enable_group)
        self.enabled_checkbox = QCheckBox("Enable device sync")
        self.enabled_checkbox.setAccessibleName("Enable device sync")
        self.enabled_checkbox.toggled.connect(self._on_enabled_toggled)
        enable_layout.addWidget(self.enabled_checkbox)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        enable_layout.addWidget(self.status_label)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("System name:"))
        self.system_name_edit = QLineEdit()
        self.system_name_edit.setAccessibleName("System name")
        name_row.addWidget(self.system_name_edit)
        save_name_button = QPushButton("Save name")
        save_name_button.clicked.connect(self._on_save_system_name)
        name_row.addWidget(save_name_button)
        enable_layout.addLayout(name_row)
        root.addWidget(enable_group)

        network_group = QGroupBox("Network settings")
        network_layout = QVBoxLayout(network_group)
        self.lan_checkbox = QCheckBox("LAN discovery")
        self.lan_checkbox.setAccessibleName("LAN discovery")
        network_layout.addWidget(self.lan_checkbox)
        self.global_checkbox = QCheckBox("Global discovery")
        self.global_checkbox.setAccessibleName("Global discovery")
        network_layout.addWidget(self.global_checkbox)

        global_server_row = QHBoxLayout()
        global_server_row.addWidget(QLabel("Global discovery server URL:"))
        self.global_server_edit = QLineEdit()
        self.global_server_edit.setAccessibleName("Global discovery server URL")
        self.global_server_edit.setPlaceholderText("https://...")
        global_server_row.addWidget(self.global_server_edit)
        network_layout.addLayout(global_server_row)

        listen_row = QHBoxLayout()
        listen_row.addWidget(QLabel("Listen host:"))
        self.listen_host_edit = QLineEdit()
        self.listen_host_edit.setAccessibleName("Listen host")
        listen_row.addWidget(self.listen_host_edit)
        listen_row.addWidget(QLabel("Port:"))
        self.listen_port_spin = QSpinBox()
        self.listen_port_spin.setAccessibleName("Listen port")
        self.listen_port_spin.setRange(1, 65535)
        listen_row.addWidget(self.listen_port_spin)
        network_layout.addLayout(listen_row)

        apply_network_button = QPushButton("Apply network settings")
        apply_network_button.clicked.connect(self._on_apply_network_settings)
        network_layout.addWidget(apply_network_button)
        root.addWidget(network_group)

        nat_group = QGroupBox("Internal NAT traversal (optional)")
        nat_layout = QVBoxLayout(nat_group)
        self.nat_checkbox = QCheckBox("Attempt automatic port mapping (PCP / NAT-PMP / UPnP)")
        self.nat_checkbox.setAccessibleName("NAT traversal")
        nat_layout.addWidget(self.nat_checkbox)
        self.nat_status_label = QLabel("")
        self.nat_status_label.setWordWrap(True)
        self.nat_status_label.setAccessibleName("NAT traversal status")
        nat_layout.addWidget(self.nat_status_label)
        nat_retry_button = QPushButton("Retry now")
        nat_retry_button.setAccessibleName("Retry NAT mapping")
        nat_retry_button.clicked.connect(self._on_retry_nat_mapping)
        nat_layout.addWidget(nat_retry_button)
        root.addWidget(nat_group)

        relay_group = QGroupBox("Relay-assisted rendezvous (optional)")
        relay_layout = QVBoxLayout(relay_group)
        self.relay_checkbox = QCheckBox("Relay rendezvous")
        self.relay_checkbox.setAccessibleName("Relay rendezvous")
        relay_layout.addWidget(self.relay_checkbox)
        relay_server_row = QHBoxLayout()
        relay_server_row.addWidget(QLabel("Relay server (host:port):"))
        self.relay_server_edit = QLineEdit()
        self.relay_server_edit.setAccessibleName("Relay server address")
        self.relay_server_edit.setPlaceholderText("relay.example:24040")
        relay_server_row.addWidget(self.relay_server_edit)
        relay_layout.addLayout(relay_server_row)
        self.relay_status_label = QLabel("")
        self.relay_status_label.setWordWrap(True)
        relay_layout.addWidget(self.relay_status_label)
        root.addWidget(relay_group)

        route_group = QGroupBox("Route diagnostics")
        route_layout = QVBoxLayout(route_group)
        self.dual_stack_label = QLabel("")
        self.dual_stack_label.setWordWrap(True)
        self.dual_stack_label.setAccessibleName("Dual-stack status")
        route_layout.addWidget(self.dual_stack_label)
        self.route_list = QListWidget()
        self.route_list.setAccessibleName("Route diagnostics")
        route_layout.addWidget(self.route_list)
        root.addWidget(route_group)

        pairing_group = QGroupBox("Pairing")
        pairing_layout = QVBoxLayout(pairing_group)

        invite_row = QHBoxLayout()
        create_invite_button = QPushButton("Create pairing invitation")
        create_invite_button.clicked.connect(self._on_create_invitation)
        invite_row.addWidget(create_invite_button)
        self.invitation_edit = QLineEdit()
        self.invitation_edit.setAccessibleName("Pairing invitation text")
        self.invitation_edit.setReadOnly(True)
        invite_row.addWidget(self.invitation_edit)
        pairing_layout.addLayout(invite_row)

        accept_row = QHBoxLayout()
        self.accept_invitation_edit = QLineEdit()
        self.accept_invitation_edit.setAccessibleName("Paste pairing invitation")
        self.accept_invitation_edit.setPlaceholderText("Paste an invitation here...")
        accept_row.addWidget(self.accept_invitation_edit)
        accept_button = QPushButton("Accept invitation")
        accept_button.clicked.connect(self._on_accept_invitation)
        accept_row.addWidget(accept_button)
        pairing_layout.addLayout(accept_row)

        manual_button = QPushButton("Pair by device ID...")
        manual_button.clicked.connect(self._on_pair_manually)
        pairing_layout.addWidget(manual_button)
        root.addWidget(pairing_group)

        trusted_group = QGroupBox("Trusted devices")
        trusted_layout = QVBoxLayout(trusted_group)
        self.trusted_list = QListWidget()
        self.trusted_list.setAccessibleName("Trusted devices")
        trusted_layout.addWidget(self.trusted_list)
        trusted_row = QHBoxLayout()
        remove_trusted_button = QPushButton("Remove selected device")
        remove_trusted_button.clicked.connect(self._on_remove_trusted)
        trusted_row.addWidget(remove_trusted_button)
        # Precisely labeled, separate actions (2026-08-08, Codex sync review:
        # "do not call a one-way push 'Sync'") -- Push sends OUR state to the
        # selected device; Pull fetches THEIRS into our own mirror.
        push_metadata_button = QPushButton("Push metadata to selected device")
        push_metadata_button.clicked.connect(self._on_push_metadata)
        trusted_row.addWidget(push_metadata_button)
        pull_metadata_button = QPushButton("Pull metadata from selected device")
        pull_metadata_button.clicked.connect(self._on_pull_metadata)
        trusted_row.addWidget(pull_metadata_button)
        cancel_transfer_button = QPushButton("Cancel active transfer")
        cancel_transfer_button.clicked.connect(self._on_cancel_transfer)
        trusted_row.addWidget(cancel_transfer_button)
        trusted_row.addStretch(1)
        trusted_layout.addLayout(trusted_row)
        root.addWidget(trusted_group)

        staging_group = QGroupBox("Mounted-folder / NAS / cloud staging (optional fallback)")
        staging_layout = QVBoxLayout(staging_group)
        staging_row = QHBoxLayout()
        staging_row.addWidget(QLabel("Staging folder:"))
        self.staging_folder_edit = QLineEdit()
        self.staging_folder_edit.setAccessibleName("Staging folder")
        staging_row.addWidget(self.staging_folder_edit)
        browse_staging_button = QPushButton("Browse...")
        browse_staging_button.clicked.connect(self._on_browse_staging_folder)
        staging_row.addWidget(browse_staging_button)
        staging_layout.addLayout(staging_row)
        staging_actions_row = QHBoxLayout()
        export_staging_button = QPushButton("Export our state to folder")
        export_staging_button.clicked.connect(self._on_export_staging)
        staging_actions_row.addWidget(export_staging_button)
        import_staging_button = QPushButton("Import from folder")
        import_staging_button.clicked.connect(self._on_import_staging)
        staging_actions_row.addWidget(import_staging_button)
        staging_actions_row.addStretch(1)
        staging_layout.addLayout(staging_actions_row)
        root.addWidget(staging_group)

        offers_group = QGroupBox("Incoming transfer offers")
        offers_layout = QVBoxLayout(offers_group)
        self.offers_list = QListWidget()
        self.offers_list.setAccessibleName("Incoming transfer offers")
        offers_layout.addWidget(self.offers_list)
        offers_row = QHBoxLayout()
        accept_offer_button = QPushButton("Accept...")
        accept_offer_button.clicked.connect(self._on_accept_offer)
        offers_row.addWidget(accept_offer_button)
        reject_offer_button = QPushButton("Reject")
        reject_offer_button.clicked.connect(self._on_reject_offer)
        offers_row.addWidget(reject_offer_button)
        offers_row.addStretch(1)
        offers_layout.addLayout(offers_row)
        root.addWidget(offers_group)

        root.addStretch(1)

        backend.syncStatusChanged.connect(self._on_status_event)
        backend.syncTransferOfferReceived.connect(self._on_offer_received)
        backend.syncTransferComplete.connect(self._on_transfer_complete)
        backend.syncMetadataSynced.connect(self._on_metadata_synced)
        backend.syncStagingComplete.connect(self._on_staging_complete)

        self.refresh()

    # ---- loading real state -------------------------------------------------
    def refresh(self) -> None:
        try:
            self.backend.ensure_sync_started()
        except Exception:
            pass
        self._reload_status()

    def _reload_status(self) -> None:
        try:
            status = self.backend.get_sync_status()
        except Exception as e:
            self.status_label.setText(f"Couldn't read sync status: {e}")
            return
        config = status.get("config", {})
        net = status.get("network_settings", {})

        for checkbox in (self.enabled_checkbox, self.lan_checkbox, self.global_checkbox, self.relay_checkbox, self.nat_checkbox):
            checkbox.blockSignals(True)
        self.enabled_checkbox.setChecked(bool(config.get("enabled")))
        self.lan_checkbox.setChecked(bool(config.get("lan_discovery")))
        self.global_checkbox.setChecked(bool(config.get("global_discovery")))
        self.relay_checkbox.setChecked(bool(config.get("relay_discovery")))
        self.nat_checkbox.setChecked(bool(config.get("nat_traversal")))
        for checkbox in (self.enabled_checkbox, self.lan_checkbox, self.global_checkbox, self.relay_checkbox, self.nat_checkbox):
            checkbox.blockSignals(False)

        if not self.listen_host_edit.hasFocus():
            self.listen_host_edit.setText(net.get("listen_host", ""))
        if not self.listen_port_spin.hasFocus():
            self.listen_port_spin.setValue(int(net.get("listen_port", 24030)))
        if not self.global_server_edit.hasFocus():
            self.global_server_edit.setText(net.get("global_discovery_server", ""))
        if not self.relay_server_edit.hasFocus():
            self.relay_server_edit.setText(net.get("relay_server", ""))
        if not self.system_name_edit.hasFocus() and status.get("system_name"):
            self.system_name_edit.setText(status.get("system_name") or "")

        if status.get("running"):
            self.status_label.setText(
                f"Running. Device ID: {status.get('device_id')}\nSystem UUID: {status.get('system_uuid')}"
            )
        else:
            self.status_label.setText("Sync is disabled.")

        self.relay_status_label.setText("Relay: connected" if status.get("relay_connected") else "Relay: not connected")
        self._reload_nat_status(status.get("nat") or {})
        self._reload_dual_stack_status(status.get("dual_stack") or {})

        self._reload_trusted()
        self._reload_route_diagnostics(status)

    def _reload_dual_stack_status(self, dual_stack: dict) -> None:
        """Real, honest IPv6/dual-stack diagnostic (2026-08-09, "internal
        IPv6 direct connectivity and dual-stack route selection") -- never
        a decorative "IPv6 ready" indicator, only what the listener
        actually bound and what candidates we actually have to offer."""
        families = dual_stack.get("listening_families") or []
        if not dual_stack.get("ipv6_supported"):
            text = "IPv6: not available on this system."
        elif "IPv6" in families:
            text = "IPv6: listening."
        elif families:
            text = "IPv6: available on this system, but not currently listening (IPv4-only bind)."
        else:
            text = "IPv6: available on this system."
        own_ipv6 = dual_stack.get("own_ipv6_candidates") or []
        if own_ipv6:
            text += f" Own IPv6 candidate(s): {', '.join(own_ipv6)}"
        self.dual_stack_label.setText(text)

    def _reload_nat_status(self, nat_status: dict) -> None:
        if nat_status.get("active"):
            self.nat_status_label.setText(
                f"Mapped via {nat_status.get('protocol')}: {nat_status.get('external_address')}:{nat_status.get('external_port')} "
                f"(lease {nat_status.get('lifetime_seconds')}s)"
            )
        elif nat_status.get("last_error"):
            self.nat_status_label.setText(f"No active mapping. Last error: {nat_status.get('last_error')}")
        else:
            self.nat_status_label.setText("No active mapping.")

    def _reload_route_diagnostics(self, status: dict) -> None:
        """Real, honest per-peer route/last-seen/error -- never guessed
        reachability (2026-08-08, "Wire optional-by-default settings and
        native Devices UI status/control for relay rendezvous and route
        diagnostics")."""
        self.route_list.clear()
        peers = status.get("peers") or {}
        retry_state = status.get("retry_state") or {}
        for system_uuid, info in peers.items():
            route = info.get("route", "unknown")
            error = info.get("error")
            text = f"{system_uuid}: {route}"
            if error:
                text += f" -- {error}"
            retry = retry_state.get(system_uuid)
            if retry:
                text += f" (retry attempt {retry.get('attempts', 0)}, next in {max(0, int(retry.get('next_attempt', 0) - time.time()))}s)"
            self.route_list.addItem(QListWidgetItem(text))

    def _reload_trusted(self) -> None:
        self.trusted_list.clear()
        try:
            trusted = self.backend.list_sync_trusted_devices()
        except Exception:
            trusted = []
        for record in trusted or []:
            item = QListWidgetItem(f"{record.get('friendly_name', '?')} ({record.get('system_uuid', '?')})")
            item.setData(SYSTEM_UUID_ROLE, record.get("system_uuid"))
            self.trusted_list.addItem(item)

    # ---- enable / settings ---------------------------------------------------
    def _on_enabled_toggled(self, checked: bool) -> None:
        result = self.backend.set_sync_enabled(checked)
        if isinstance(result, dict) and result.get("status") == "error":
            QMessageBox.warning(self, "Device sync", f"Couldn't change sync state: {result.get('message')}")
        self._reload_status()

    def _on_save_system_name(self) -> None:
        name = self.system_name_edit.text().strip()
        if not name:
            return
        result = self.backend.set_sync_system_name(name)
        if isinstance(result, dict) and result.get("status") == "error":
            QMessageBox.warning(self, "Device sync", f"Couldn't set system name: {result.get('message')}")

    def _on_apply_network_settings(self) -> None:
        patch = {
            "listen_host": self.listen_host_edit.text().strip() or "0.0.0.0",
            "listen_port": self.listen_port_spin.value(),
            "global_discovery_server": self.global_server_edit.text().strip(),
            "relay_server": self.relay_server_edit.text().strip(),
        }
        result = self.backend.set_sync_network_settings(json.dumps(patch))
        if isinstance(result, dict) and result.get("status") == "error":
            QMessageBox.warning(self, "Device sync", f"Couldn't save network settings: {result.get('message')}")
        config_patch = json.dumps(
            {
                "lan_discovery": self.lan_checkbox.isChecked(),
                "global_discovery": self.global_checkbox.isChecked(),
                "relay_discovery": self.relay_checkbox.isChecked(),
                "nat_traversal": self.nat_checkbox.isChecked(),
            }
        )
        self.backend.set_sync_config(config_patch)
        self._reload_status()

    def _on_retry_nat_mapping(self) -> None:
        self.backend.retry_sync_nat_mapping()

    # ---- pairing --------------------------------------------------------------
    def _on_create_invitation(self) -> None:
        text = self.backend.create_sync_pairing_invitation(self.system_name_edit.text().strip())
        if not text:
            QMessageBox.warning(self, "Device sync", "Enable sync before creating a pairing invitation.")
            return
        self.invitation_edit.setText(text)
        self.invitation_edit.selectAll()

    def _on_accept_invitation(self) -> None:
        text = self.accept_invitation_edit.text().strip()
        if not text:
            return
        result = self.backend.accept_sync_pairing_invitation(text)
        if isinstance(result, dict) and result.get("status") == "error":
            QMessageBox.warning(self, "Device sync", f"Couldn't accept invitation: {result.get('message')}")
            return
        self.accept_invitation_edit.clear()
        self._reload_trusted()

    def _on_pair_manually(self) -> None:
        system_uuid, ok = QInputDialog.getText(self, "Pair by device ID", "Peer system UUID:")
        if not ok or not system_uuid.strip():
            return
        device_id, ok = QInputDialog.getText(self, "Pair by device ID", "Peer device ID:")
        if not ok or not device_id.strip():
            return
        friendly_name, ok = QInputDialog.getText(self, "Pair by device ID", "Friendly name:")
        if not ok:
            friendly_name = system_uuid
        result = self.backend.pair_sync_device_manually(system_uuid.strip(), device_id.strip(), friendly_name.strip() or system_uuid.strip())
        if isinstance(result, dict) and result.get("status") == "error":
            QMessageBox.warning(self, "Device sync", f"Couldn't pair device: {result.get('message')}")
            return
        self._reload_trusted()

    def _on_remove_trusted(self) -> None:
        item = self.trusted_list.currentItem()
        if item is None:
            return
        system_uuid = item.data(SYSTEM_UUID_ROLE)
        self.backend.remove_sync_trusted_device(system_uuid)
        self._reload_trusted()

    def _on_push_metadata(self) -> None:
        item = self.trusted_list.currentItem()
        if item is None:
            return
        self.backend.push_sync_metadata_to_device(item.data(SYSTEM_UUID_ROLE))

    def _on_pull_metadata(self) -> None:
        item = self.trusted_list.currentItem()
        if item is None:
            return
        self.backend.pull_sync_metadata_from_device(item.data(SYSTEM_UUID_ROLE))

    def _on_cancel_transfer(self) -> None:
        item = self.trusted_list.currentItem()
        if item is None:
            return
        cancelled = self.backend.cancel_sync_transfer(item.data(SYSTEM_UUID_ROLE))
        if not cancelled:
            QMessageBox.information(self, "Device sync", "No active transfer with this device to cancel.")

    def _on_browse_staging_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a staging folder")
        if folder:
            self.staging_folder_edit.setText(folder)

    def _on_export_staging(self) -> None:
        folder = self.staging_folder_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, "Device sync", "Choose a staging folder first.")
            return
        self.backend.export_sync_staging_snapshot(folder)

    def _on_import_staging(self) -> None:
        folder = self.staging_folder_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, "Device sync", "Choose a staging folder first.")
            return
        self.backend.import_sync_staging_snapshot(folder)

    def _on_staging_complete(self, event) -> None:
        if not isinstance(event, dict):
            return
        result = event.get("result") or {}
        direction = event.get("direction")
        if result.get("status") != "ok":
            QMessageBox.warning(self, "Device sync", f"Staging {direction} failed: {result.get('message', 'unknown error')}")
            return
        if direction == "export":
            QMessageBox.information(self, "Device sync", f"Exported {result.get('items', 0)} item(s) to the staging folder.")
        else:
            conflicts = len(result.get("conflicts") or [])
            message = f"Imported and applied {result.get('applied', 0)} item(s)."
            if conflicts:
                message += f" {conflicts} conflict(s) need review (not auto-applied)."
            QMessageBox.information(self, "Device sync", message)

    # ---- transfer offers -------------------------------------------------------
    def _on_offer_received(self, event) -> None:
        if not isinstance(event, dict):
            return
        offer = event.get("offer") or {}
        text = f"{offer.get('display_name', offer.get('game_id', '?'))} from {event.get('peer_system_uuid')}"
        item = QListWidgetItem(text)
        item.setData(OFFER_ID_ROLE, event.get("offer_id"))
        item.setData(SYSTEM_UUID_ROLE, event.get("peer_system_uuid"))
        self.offers_list.addItem(item)
        self._pending_offer_rows[event.get("offer_id")] = item

    def _on_accept_offer(self) -> None:
        item = self.offers_list.currentItem()
        if item is None:
            return
        offer_id = item.data(OFFER_ID_ROLE)
        destination = QFileDialog.getExistingDirectory(self, "Choose a destination folder for the received game")
        if not destination:
            return
        cache = QFileDialog.getExistingDirectory(self, "Choose a cache folder for resumable transfers")
        peer_system_uuid = item.data(SYSTEM_UUID_ROLE)
        if peer_system_uuid:
            self._pending_transfer_destinations[peer_system_uuid] = destination
        self.backend.resolve_sync_transfer_offer(offer_id, True, destination, cache, "")
        self._remove_offer_row(offer_id)

    def _on_reject_offer(self) -> None:
        item = self.offers_list.currentItem()
        if item is None:
            return
        offer_id = item.data(OFFER_ID_ROLE)
        self.backend.resolve_sync_transfer_offer(offer_id, False, "", "", "Declined.")
        self._remove_offer_row(offer_id)

    def _remove_offer_row(self, offer_id: str) -> None:
        item = self._pending_offer_rows.pop(offer_id, None)
        if item is not None:
            self.offers_list.takeItem(self.offers_list.row(item))

    def _on_transfer_complete(self, event) -> None:
        if not isinstance(event, dict):
            return
        result = event.get("result") or {}
        if result.get("status") == "ok":
            QMessageBox.information(self, "Device sync", f"Transfer of {event.get('game_id')} completed.")
        elif result.get("status") == "rejected":
            QMessageBox.information(self, "Device sync", f"Transfer of {event.get('game_id')} was declined.")
        else:
            QMessageBox.warning(self, "Device sync", f"Transfer failed: {result.get('message', 'unknown error')}")

    # ---- status/metadata events -------------------------------------------------
    def _on_status_event(self, event) -> None:
        if isinstance(event, dict) and event.get("event") == "transfer_received":
            self._on_transfer_received(event)
        self._reload_status()

    def _on_transfer_received(self, event: dict) -> None:
        """Real onboarding after successful, already-atomically-materialized
        receipt (transfer_session.materialize() already ran before this
        status event was ever emitted -- see SyncService._on_connected()) --
        never claims completion before that, only reacts to it."""
        peer_system_uuid = event.get("system_uuid")
        destination = self._pending_transfer_destinations.pop(peer_system_uuid, None)
        if not destination:
            return
        self.backend.start_onboard(destination)

    def _on_metadata_synced(self, event) -> None:
        if not isinstance(event, dict):
            return
        result = event.get("result") or event.get("pushed") or {}
        self.status_label.setText(self.status_label.text() + f"\nMetadata sync with {event.get('peer_system_uuid')}: {result}")
