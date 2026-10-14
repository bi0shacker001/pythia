"""Native global Settings page (2026-08-08, "native global settings and
launch configuration completion"). Every control here is real, persisted,
and reload-reproducible -- no placeholders. Scope, deliberately:

- Launch mode / UI mode: real per-system preferences (Backend.get_launch_mode()/
  set_launch_mode(), get_ui_mode()/set_ui_mode()).
- Default (global-scope) advanced launch/compatibility config: the same
  CascadeConfigForm the per-game Advanced tab uses, at global scope -- this
  IS "default launch/backend behavior," not a separate concept.
- Maintenance: refresh_sources(), start_update_check() (all known games),
  rebuild_cache() -- real busy/complete/error state, no fabricated progress.
- System info: read-only, from get_system_info().
- Plugin configuration: a real navigation link to the Plugins tab (per-
  plugin config forms already live there; not duplicated here).

Deliberately NOT included, and why (see aisync/WORKLOG.md's claim entry for
the fuller research note): "library roots" -- no such persisted concept
exists anywhere in the backend (onboarding is folder-by-folder, nothing
keeps a watched-root list); "logging" -- pythia.cfg (debug/ui/channel) is
read once at process startup by pythia.util.local_config.load_local_config()
with no write-back Slot anywhere. Faking either would violate "never show
fake success."
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QFileDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pythia.ui.native.config_form import ADVANCED_CONFIG_FIELDS, CascadeConfigForm

_LAUNCH_MODES = [("direct", "Direct (Pythia launches the game itself)"), ("manager", "Hand off to a manager")]
_UI_MODES = [("desktop", "Desktop"), ("bigpicture", "Big Picture")]


class SettingsView(QWidget):
    def __init__(self, backend, switch_to_plugins_tab=None, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._switch_to_plugins_tab = switch_to_plugins_tab

        root = QVBoxLayout(self)

        behavior_group = QGroupBox("Startup && launch behavior")
        behavior_layout = QVBoxLayout(behavior_group)

        launch_row = QHBoxLayout()
        launch_row.addWidget(QLabel("Launch mode:"))
        self.launch_mode_combo = QComboBox()
        self.launch_mode_combo.setAccessibleName("Launch mode")
        for value, label in _LAUNCH_MODES:
            self.launch_mode_combo.addItem(label, value)
        self.launch_mode_combo.currentIndexChanged.connect(self._on_launch_mode_changed)
        launch_row.addWidget(self.launch_mode_combo)
        launch_row.addStretch(1)
        behavior_layout.addLayout(launch_row)

        ui_row = QHBoxLayout()
        ui_row.addWidget(QLabel("Preferred UI (takes effect next launch):"))
        self.ui_mode_combo = QComboBox()
        self.ui_mode_combo.setAccessibleName("Preferred UI mode")
        for value, label in _UI_MODES:
            self.ui_mode_combo.addItem(label, value)
        self.ui_mode_combo.currentIndexChanged.connect(self._on_ui_mode_changed)
        ui_row.addWidget(self.ui_mode_combo)
        ui_row.addStretch(1)
        behavior_layout.addLayout(ui_row)
        root.addWidget(behavior_group)

        defaults_group = QGroupBox("Default launch && compatibility settings (applies to every game unless overridden)")
        defaults_layout = QVBoxLayout(defaults_group)
        self.defaults_form = CascadeConfigForm(
            backend, ADVANCED_CONFIG_FIELDS, game_id=None, variant_id=None, edit_scope="global"
        )
        defaults_layout.addWidget(self.defaults_form)
        apply_button = QPushButton("Apply defaults")
        apply_button.clicked.connect(self._on_apply_defaults)
        defaults_layout.addWidget(apply_button)
        root.addWidget(defaults_group)

        maintenance_group = QGroupBox("Maintenance")
        maintenance_layout = QVBoxLayout(maintenance_group)
        self.maintenance_status = QLabel("")
        self.maintenance_status.setWordWrap(True)
        maintenance_layout.addWidget(self.maintenance_status)

        maintenance_row = QHBoxLayout()
        refresh_sources_button = QPushButton("Refresh sources")
        refresh_sources_button.clicked.connect(self._on_refresh_sources)
        maintenance_row.addWidget(refresh_sources_button)

        check_updates_button = QPushButton("Check for game updates")
        check_updates_button.clicked.connect(self._on_check_updates)
        maintenance_row.addWidget(check_updates_button)

        rebuild_cache_button = QPushButton("Rebuild cache")
        rebuild_cache_button.clicked.connect(self._on_rebuild_cache)
        maintenance_row.addWidget(rebuild_cache_button)
        export_diagnostics_button = QPushButton("Export diagnostics…")
        export_diagnostics_button.clicked.connect(self._on_export_diagnostics)
        maintenance_row.addWidget(export_diagnostics_button)
        maintenance_row.addStretch(1)
        maintenance_layout.addLayout(maintenance_row)
        root.addWidget(maintenance_group)

        plugins_group = QGroupBox("Plugins")
        plugins_layout = QVBoxLayout(plugins_group)
        plugins_layout.addWidget(QLabel("Per-plugin enable/disable and configuration live on the Plugins tab."))
        open_plugins_button = QPushButton("Open Plugins...")
        open_plugins_button.clicked.connect(self._on_open_plugins)
        plugins_layout.addWidget(open_plugins_button)
        root.addWidget(plugins_group)

        system_group = QGroupBox("This system")
        system_layout = QVBoxLayout(system_group)
        self.system_info_label = QLabel("")
        self.system_info_label.setWordWrap(True)
        self.system_info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        system_layout.addWidget(self.system_info_label)
        folder_row = QHBoxLayout()
        for label, kind in (("Open app data", "app"), ("Open database", "database"), ("Open sync metadata", "sync")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, key=kind: self._on_open_data_folder(key))
            folder_row.addWidget(button)
        folder_row.addStretch(1)
        system_layout.addLayout(folder_row)
        root.addWidget(system_group)

        root.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        self._load_launch_mode()
        self._load_ui_mode()
        self.defaults_form.refresh()
        self._load_system_info()

    def _load_launch_mode(self) -> None:
        try:
            current = self.backend.get_launch_mode()
        except Exception:
            return
        index = self.launch_mode_combo.findData(current)
        self.launch_mode_combo.blockSignals(True)
        self.launch_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self.launch_mode_combo.blockSignals(False)

    def _load_ui_mode(self) -> None:
        try:
            current = self.backend.get_ui_mode()
        except Exception:
            return
        # `native` was the explicit-hint name before this implemented window
        # became the normal desktop target. Preserve old settings without
        # exposing two choices that now launch the same interface.
        if current in {"native", "web"}:
            current = "desktop"
        index = self.ui_mode_combo.findData(current)
        self.ui_mode_combo.blockSignals(True)
        self.ui_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self.ui_mode_combo.blockSignals(False)

    def _load_system_info(self) -> None:
        try:
            info = self.backend.get_system_info()
        except Exception as e:
            self.system_info_label.setText(f"Couldn't read system info: {e}")
            return
        others = info.get("other_systems") or []
        others_text = ", ".join(f"{o.get('uuid')} ({o.get('count')} game(s))" for o in others) or "none known"
        self.system_info_label.setText(
            f"System UUID: {info.get('system_uuid')}\n"
            f"Sync folder: {info.get('sync_folder')}\n"
            f"App data folder: {info.get('app_data_folder')}\n"
            f"Other known systems: {others_text}"
        )

    def _on_launch_mode_changed(self, _index: int) -> None:
        result = self.backend.set_launch_mode(self.launch_mode_combo.currentData())
        if not result or result.get("status") != "ok":
            QMessageBox.warning(self, "Launch mode", (result or {}).get("message", "The launch mode was not saved."))
            self._load_launch_mode()

    def _on_open_data_folder(self, kind: str) -> None:
        result = self.backend.open_data_folder(kind)
        if result.get("status") != "ok":
            QMessageBox.warning(self, "Open folder", result.get("message", "The folder could not be opened."))

    def _on_export_diagnostics(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export diagnostics", "pythia-diagnostics.json", "JSON (*.json)"
        )
        if not path:
            return
        result = self.backend.export_diagnostics_to_file(path)
        if result.get("status") == "ok":
            QMessageBox.information(self, "Export diagnostics", f"Diagnostics exported to {result.get('path')}.")
        else:
            QMessageBox.warning(self, "Export diagnostics", result.get("message", "Diagnostics export failed."))

    def _on_ui_mode_changed(self, _index: int) -> None:
        result = self.backend.set_ui_mode(self.ui_mode_combo.currentData())
        if not result or result.get("status") != "ok":
            QMessageBox.warning(self, "Interface mode", (result or {}).get("message", "The interface mode was not saved."))
            self._load_ui_mode()

    def _on_apply_defaults(self) -> None:
        result = self.defaults_form.save()
        if result.get("status") == "ok":
            QMessageBox.information(self, "Settings", "Default settings saved.")
        else:
            errors = result.get("errors", {})
            detail = "\n".join(f"{k}: {v}" for k, v in errors.items())
            QMessageBox.warning(self, "Settings", f"Some defaults couldn't be saved:\n{detail}")

    def _on_refresh_sources(self) -> None:
        try:
            result = self.backend.refresh_sources()
        except Exception as e:
            self.maintenance_status.setText(f"Refresh sources failed: {e}")
            return
        if result.get("status") == "ok":
            self.maintenance_status.setText(f"Sources refreshed: {result.get('checked', 0)} checked, {result.get('changed', 0)} changed.")
        elif result.get("status") == "partial":
            self.maintenance_status.setText(
                f"Sources refreshed with errors: {result.get('checked', 0)} checked, "
                f"{result.get('changed', 0)} changed, {result.get('errors', 0)} error(s)."
            )
        else:
            self.maintenance_status.setText(f"Refresh sources failed: {result.get('message', 'unknown error')}")

    def _on_check_updates(self) -> None:
        try:
            game_ids = [row["game_id"] for row in self.backend.games]
        except Exception as e:
            self.maintenance_status.setText(f"Couldn't list games to check: {e}")
            return
        if not game_ids:
            self.maintenance_status.setText("No games known on this system to check.")
            return
        self.maintenance_status.setText(f"Checking {len(game_ids)} game(s) for updates -- see Activity for the real result.")
        self.backend.start_update_check(game_ids)

    def _on_rebuild_cache(self) -> None:
        try:
            result = self.backend.rebuild_cache()
        except Exception as e:
            self.maintenance_status.setText(f"Cache rebuild failed: {e}")
            return
        if result.get("status") == "ok":
            self.maintenance_status.setText("Cache rebuilt.")
        else:
            self.maintenance_status.setText(f"Cache rebuild failed: {result.get('message', 'unknown error')}")

    def _on_open_plugins(self) -> None:
        if self._switch_to_plugins_tab:
            self._switch_to_plugins_tab()
