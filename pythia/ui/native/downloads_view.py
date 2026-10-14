"""Native Downloads view (2026-08-08, "download automator end-to-end
hardening and native workflow"). Real state only:

- Whether the optional Download Automator plugin is installed/enabled,
  from the real `Backend.list_optional_plugins()` -- F95 library/update/
  mask-resolution keep working without it; only automated downloads need it,
  and that's exactly what this label says.
- Real, per-download rows driven by `Backend.automatedDownloadProgress`,
  keyed by the real `download_id` `start_automated_download()` mints.
  Byte progress is shown when the server reported a real Content-Length;
  "size unknown" otherwise -- never a fabricated percentage.
- Cancel wired to the real `cancel_automated_download(download_id)`.
- "Add to library..." only enables once a real final path (downloaded file
  or, if auto_extract succeeded, the extracted folder) exists, and calls the
  existing real `start_onboard()` -- never claims the game was imported
  itself, only that onboarding was started.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

DOWNLOAD_ID_ROLE = Qt.ItemDataRole.UserRole + 1
AUTOMATOR_PLUGIN_ID = "pythia.downloader.automator"


class DownloadsView(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._rows: dict[str, dict] = {}

        root = QVBoxLayout(self)

        f95_group = QGroupBox("F95zone account (optional)")
        f95_layout = QHBoxLayout(f95_group)
        f95_layout.addWidget(QLabel("Used for authenticated update checks and masked-link resolution."))
        f95_sign_in_button = QPushButton("Sign in to F95zone...")
        f95_sign_in_button.clicked.connect(self._on_sign_in_f95)
        f95_layout.addWidget(f95_sign_in_button)
        f95_clear_button = QPushButton("Clear F95zone session")
        f95_clear_button.clicked.connect(self._on_clear_f95_session)
        f95_layout.addWidget(f95_clear_button)
        f95_import_button = QPushButton("Import F95Checker database...")
        f95_import_button.clicked.connect(self._on_import_f95)
        f95_layout.addWidget(f95_import_button)
        root.addWidget(f95_group)

        if hasattr(backend, "f95LoginComplete"):
            backend.f95LoginComplete.connect(self._on_f95_login_complete)
        if hasattr(backend, "accountSyncComplete"):
            backend.accountSyncComplete.connect(self._on_account_sync_complete)
        if hasattr(backend, "downloadHostLoginComplete"):
            backend.downloadHostLoginComplete.connect(self._on_download_host_login_complete)

        automator_group = QGroupBox("Download Automator (optional)")
        automator_layout = QVBoxLayout(automator_group)
        self.automator_status_label = QLabel("")
        self.automator_status_label.setWordWrap(True)
        automator_layout.addWidget(self.automator_status_label)

        automator_row = QHBoxLayout()
        self.install_automator_button = QPushButton("Install Download Automator")
        self.install_automator_button.clicked.connect(self._on_install_automator)
        automator_row.addWidget(self.install_automator_button)
        self.sign_in_host_button = QPushButton("Sign in to a download host...")
        self.sign_in_host_button.setAccessibleName("Sign in to a download host")
        self.sign_in_host_button.clicked.connect(self._on_sign_in_host)
        automator_row.addWidget(self.sign_in_host_button)
        self.clear_host_session_button = QPushButton("Clear a host session...")
        self.clear_host_session_button.setAccessibleName("Clear a saved download host session")
        self.clear_host_session_button.clicked.connect(self._on_clear_host_session)
        automator_row.addWidget(self.clear_host_session_button)
        automator_row.addStretch(1)
        automator_layout.addLayout(automator_row)
        root.addWidget(automator_group)

        self.downloads_list = QListWidget()
        self.downloads_list.setAccessibleName("Downloads")
        self.downloads_list.currentItemChanged.connect(self._on_selection_changed)
        root.addWidget(self.downloads_list)

        action_row = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel selected")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._on_cancel_selected)
        action_row.addWidget(self.cancel_button)
        self.add_to_library_button = QPushButton("Add to library...")
        self.add_to_library_button.setEnabled(False)
        self.add_to_library_button.clicked.connect(self._on_add_to_library)
        action_row.addWidget(self.add_to_library_button)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self.refresh_automator_status()

    # ---- optional-dependency state ------------------------------------------
    def refresh_automator_status(self) -> None:
        try:
            optional = self.backend.list_optional_plugins()
        except Exception as e:
            self.automator_status_label.setText(f"Couldn't check optional plugins: {e}")
            self.install_automator_button.setEnabled(False)
            self.sign_in_host_button.setEnabled(False)
            self.clear_host_session_button.setEnabled(False)
            return
        entry = next((p for p in optional if p.get("id") == AUTOMATOR_PLUGIN_ID), None)
        if entry is None:
            self.automator_status_label.setText(
                "Download Automator package not found in this build. F95 library/update/mask-resolution "
                "still work without it; only automated downloads need it."
            )
            self.install_automator_button.setEnabled(False)
            self.sign_in_host_button.setEnabled(False)
            self.clear_host_session_button.setEnabled(False)
            return
        if entry.get("enabled"):
            self.automator_status_label.setText(f"Download Automator v{entry.get('version')} is installed and enabled.")
            self.install_automator_button.setEnabled(False)
            self.sign_in_host_button.setEnabled(True)
            self.clear_host_session_button.setEnabled(True)
        elif entry.get("installed"):
            self.automator_status_label.setText(
                "Download Automator is installed but disabled -- enable it on the Plugins tab to use it."
            )
            self.install_automator_button.setEnabled(False)
            self.sign_in_host_button.setEnabled(False)
            self.clear_host_session_button.setEnabled(False)
        else:
            self.automator_status_label.setText(
                "Download Automator is not installed. F95 library/update/mask-resolution keep working "
                "without it; only automated downloads need it."
            )
            self.install_automator_button.setEnabled(True)
            self.sign_in_host_button.setEnabled(False)
            self.clear_host_session_button.setEnabled(False)

    def _on_install_automator(self) -> None:
        result = self.backend.install_optional_plugin(AUTOMATOR_PLUGIN_ID)
        if result.get("status") in {"ok", "partial"}:
            if result.get("status") == "ok":
                QMessageBox.information(self, "Download Automator", "Installed. Enable it on the Plugins tab if it isn't already.")
            else:
                QMessageBox.warning(self, "Download Automator", result.get("message", "Installed, but refresh failed."))
        else:
            QMessageBox.warning(self, "Download Automator", result.get("message", "Install failed."))
        self.refresh_automator_status()

    def _on_sign_in_host(self) -> None:
        url, ok = QInputDialog.getText(self, "Sign in to a download host", "Host URL:")
        if not ok or not url.strip():
            return
        result = self.backend.start_download_host_login(url.strip())
        if result.get("status") != "pending":
            QMessageBox.warning(self, "Sign in", result.get("message", "Couldn't start sign-in."))

    def _on_clear_host_session(self) -> None:
        url, ok = QInputDialog.getText(self, "Clear a download host session", "Host URL:")
        if not ok or not url.strip():
            return
        result = self.backend.clear_download_host_session(url.strip())
        if result.get("status") == "ok":
            QMessageBox.information(self, "Host session", result.get("message", "Saved session cleared."))
        else:
            QMessageBox.warning(self, "Host session", result.get("message", "Couldn't clear the saved session."))

    def _on_download_host_login_complete(self, result) -> None:
        if result.get("status") == "ok":
            QMessageBox.information(self, "Host session", result.get("message", "Saved the host session."))
        else:
            QMessageBox.warning(self, "Host session", result.get("message", "Couldn't save the host session."))

    def _on_sign_in_f95(self) -> None:
        result = self.backend.start_f95_login()
        if result.get("status") != "pending":
            QMessageBox.warning(self, "F95zone sign in", result.get("message", "Couldn't start sign-in."))

    def _on_f95_login_complete(self, result) -> None:
        if result.get("status") == "ok":
            QMessageBox.information(self, "F95zone session", f"Saved {result.get('cookies', 0)} session cookie(s).")
        else:
            QMessageBox.warning(self, "F95zone session", result.get("message", "Couldn't save the session."))

    def _on_clear_f95_session(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Clear F95zone session",
            "Clear the saved F95zone session on this system?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        result = self.backend.clear_f95_session()
        if result.get("status") == "ok":
            QMessageBox.information(self, "F95zone session", result.get("message", "Session cleared."))
        else:
            QMessageBox.warning(self, "F95zone session", result.get("message", "Couldn't clear the session."))

    def _on_import_f95(self) -> None:
        path = self.backend.pick_f95_database_file()
        if not path:
            return
        self.backend.import_f95(path)

    def _on_account_sync_complete(self, plugin_id: str, result) -> None:
        if plugin_id != "pythia.library.f95":
            return
        if result.get("status") == "ok":
            QMessageBox.information(self, "F95Checker import", result.get("message", "F95Checker database imported."))
        elif result.get("status") == "partial":
            QMessageBox.warning(self, "F95Checker import", result.get("message", "Import partially completed."))
        else:
            QMessageBox.warning(self, "F95Checker import", result.get("message", "Import failed."))

    # ---- starting + tracking downloads ---------------------------------------
    def start_download(self, url: str, suggested_name: str = "") -> dict:
        """The one real entry point other views (e.g. an update row's
        Download button) call -- download-tracking state lives in exactly
        one place."""
        result = self.backend.start_automated_download(url, suggested_name)
        if result.get("status") == "pending" and result.get("download_id"):
            self._add_row(result["download_id"], result.get("filename") or suggested_name or url, result.get("host", ""))
        return result

    def _add_row(self, download_id: str, filename: str, host: str) -> None:
        item = QListWidgetItem(f"{filename} ({host}) -- starting...")
        item.setData(DOWNLOAD_ID_ROLE, download_id)
        self.downloads_list.addItem(item)
        self._rows[download_id] = {"item": item, "filename": filename, "extract_path": None, "final_path": None}

    def on_progress(self, event: dict) -> None:
        """Connect to Backend.automatedDownloadProgress."""
        download_id = event.get("download_id")
        row = self._rows.get(download_id) if download_id else None
        if row is None:
            self._add_row(download_id or "", event.get("filename", "?"), "")
            row = self._rows.get(download_id)
            if row is None:
                return
        item = row["item"]
        status = event.get("status")

        if event.get("phase") == "extract":
            if status == "ok":
                row["extract_path"] = event.get("path")
                item.setText(f"{row['filename']} -- extracted to {event.get('path')}")
            elif status == "needs_review":
                item.setText(f"{row['filename']} -- extraction needs review: {event.get('message', '')}")
            else:
                item.setText(f"{row['filename']} -- extraction {status}: {event.get('message', '')}")
            self._update_action_buttons()
            return

        if status == "running":
            received = event.get("received", 0)
            total = event.get("total", 0)
            if total:
                item.setText(f"{row['filename']} -- {received}/{total} bytes")
            else:
                item.setText(f"{row['filename']} -- {received} bytes (size unknown)")
        elif status == "ok":
            row["final_path"] = event.get("path")
            item.setText(f"{row['filename']} -- complete ({event.get('path')})")
        elif status == "cancelled":
            item.setText(f"{row['filename']} -- cancelled")
        elif status == "checksum_mismatch":
            item.setText(f"{row['filename']} -- checksum mismatch: {event.get('message', '')}")
        elif status == "interactive_required":
            item.setText(f"{row['filename']} -- needs a manual sign-in/browser step: {event.get('message', '')}")
        elif status == "unavailable":
            item.setText(f"{row['filename']} -- {event.get('message', 'Download Automator is unavailable.')}")
        else:
            item.setText(f"{row['filename']} -- {status}: {event.get('message', '')}")
        self._update_action_buttons()

    def _row_for_item(self, item) -> dict | None:
        if item is None:
            return None
        download_id = item.data(DOWNLOAD_ID_ROLE)
        return self._rows.get(download_id)

    def _update_action_buttons(self) -> None:
        row = self._row_for_item(self.downloads_list.currentItem())
        self.cancel_button.setEnabled(row is not None)
        self.add_to_library_button.setEnabled(bool(row and (row.get("final_path") or row.get("extract_path"))))

    def _on_selection_changed(self, _current, _previous) -> None:
        self._update_action_buttons()

    def _on_cancel_selected(self) -> None:
        item = self.downloads_list.currentItem()
        if item is None:
            return
        download_id = item.data(DOWNLOAD_ID_ROLE)
        result = self.backend.cancel_automated_download(download_id)
        if result.get("status") != "ok":
            QMessageBox.information(
                self, "Cancel download", result.get("message", "Couldn't cancel (it may have already finished).")
            )

    def _on_add_to_library(self) -> None:
        row = self._row_for_item(self.downloads_list.currentItem())
        if row is None:
            return
        path = row.get("extract_path")
        if not path and row.get("final_path"):
            path = str(Path(row["final_path"]).parent)
        if not path:
            return
        self.backend.start_onboard(path)
        QMessageBox.information(self, "Add to library", f"Onboarding started for {path} -- see Activity for the result.")
