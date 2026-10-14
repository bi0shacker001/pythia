"""Native Plugins view: real plugin catalog/capability data from
Backend.get_all_plugins(), global + per-capability enablement, and a
schema-generated configuration form (Backend.get_plugin_config()/
save_plugin_config(), driven by each plugin's own config_schema -- see
pythia.plugins.packaging's docstring for where that schema comes from:
type-<category>.py's CONFIG_SCHEMA, never guessed or hard-coded per plugin).
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

PLUGIN_ID_ROLE = Qt.ItemDataRole.UserRole + 1


def _build_field_widget(field: dict, current_value):
    """One widget per config_schema entry. Falls back to a plain text field
    for any type this doesn't specifically know about -- a plugin author's
    schema is still usable, just without a specialized editor, rather than
    the whole config form breaking on an unrecognized "type"."""
    field_type = field.get("type", "text")
    default = field.get("default", "")
    value = current_value if current_value is not None else default

    if field_type == "bool":
        widget = QCheckBox()
        widget.setChecked(bool(value))
        return widget, lambda: widget.isChecked()

    if field_type == "path":
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        line_edit = QLineEdit(str(value or ""))
        browse = QPushButton("Browse...")

        def _pick():
            path = QFileDialog.getExistingDirectory(container, field.get("label", "Select a folder"))
            if path:
                line_edit.setText(path)

        browse.clicked.connect(_pick)
        layout.addWidget(line_edit)
        layout.addWidget(browse)
        return container, lambda: line_edit.text()

    if field_type == "select" and field.get("options"):
        from PySide6.QtWidgets import QComboBox

        widget = QComboBox()
        options = field["options"]
        for option in options:
            option_value = option.get("value", option) if isinstance(option, dict) else option
            option_label = option.get("label", str(option_value)) if isinstance(option, dict) else str(option)
            widget.addItem(option_label, option_value)
        index = widget.findData(value)
        if index >= 0:
            widget.setCurrentIndex(index)
        return widget, lambda: widget.currentData()

    # "text", "number", and anything unrecognized: a plain text field --
    # honest rather than pretending to validate a type we don't model yet.
    widget = QLineEdit("" if value is None else str(value))
    return widget, lambda: widget.text()


class PluginConfigForm(QWidget):
    """Renders `config_schema` (a list of {"key","label","type","default"}
    dicts) against `values` (already-saved config), and hands back the
    current field values on demand -- never claims a save happened; the
    caller (PluginsView) is the one that actually calls
    Backend.save_plugin_config() and reports its real result."""

    def __init__(self, config_schema: list, values: dict, parent=None):
        super().__init__(parent)
        self._getters: dict[str, callable] = {}
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        if not config_schema:
            layout.addRow(QLabel("This plugin has no configurable options."))
            return
        for field in config_schema:
            key = field.get("key")
            if not key:
                continue
            widget, getter = _build_field_widget(field, values.get(key))
            self._getters[key] = getter
            layout.addRow(field.get("label", key), widget)

    def values(self) -> dict:
        return {key: getter() for key, getter in self._getters.items()}


class PluginUpdateDialog(QDialog):
    """Real remote-index check (2026-08-08, native UI depth: "install/
    remove/update ... using existing backend APIs"). A real network/index
    failure (the plugin index repo may simply be private/unreachable -- see
    reference_pythia network notes) is shown as an honest error, not treated
    as "no updates". Only entries the index reports as "new"/"update" get an
    Install/Update button; "current" entries aren't actionable."""

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.setWindowTitle("Check for plugin updates")
        self.resize(640, 420)

        layout = QVBoxLayout(self)
        self.status_label = QLabel("Checking the remote plugin index...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.entries_list = QListWidget()
        self.entries_list.setAccessibleName("Available plugin updates")
        layout.addWidget(self.entries_list)

        install_button = QPushButton("Install / update selected")
        install_button.clicked.connect(self._on_install_selected)
        layout.addWidget(install_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._check()

    def _check(self) -> None:
        try:
            result = self.backend.check_plugin_index()
        except Exception as e:
            self.status_label.setText(f"Couldn't check the plugin index: {e}")
            return
        if result.get("status") != "ok":
            self.status_label.setText(f"Couldn't check the plugin index: {result.get('message', 'unknown error')}")
            return
        actionable = [p for p in result.get("plugins", []) if p.get("relation") in ("new", "update")]
        self.entries_list.clear()
        if not actionable:
            self.status_label.setText("Everything installed is already current.")
            return
        self.status_label.setText(f"{len(actionable)} plugin(s) available to install or update:")
        for p in actionable:
            item = QListWidgetItem(f"{p.get('display_name', p.get('id'))} — {p.get('relation')} (v{p.get('version')})")
            item.setData(Qt.ItemDataRole.UserRole, p.get("id"))
            self.entries_list.addItem(item)

    def _on_install_selected(self) -> None:
        item = self.entries_list.currentItem()
        if item is None:
            return
        plugin_id = item.data(Qt.ItemDataRole.UserRole)
        result = self.backend.install_plugin_from_index(plugin_id)
        if result.get("status") in {"ok", "partial"}:
            self.entries_list.takeItem(self.entries_list.row(item))
            if result.get("status") == "ok":
                QMessageBox.information(self, "Plugins", f"Installed {result.get('display_name', plugin_id)}.")
            else:
                QMessageBox.warning(self, "Plugins", result.get("message", "Installed, but refresh failed."))
        else:
            QMessageBox.warning(self, "Plugins", result.get("message", "Install failed."))


class PluginsView(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._plugins: dict[str, dict] = {}

        list_column = QVBoxLayout()
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        list_column.addWidget(self._list)

        list_actions = QHBoxLayout()
        install_button = QPushButton("Install from file...")
        install_button.clicked.connect(self._on_install_from_file)
        list_actions.addWidget(install_button)
        check_updates_button = QPushButton("Check for updates...")
        check_updates_button.clicked.connect(self._on_check_updates)
        list_actions.addWidget(check_updates_button)
        list_column.addLayout(list_actions)

        list_widget = QWidget()
        list_widget.setLayout(list_column)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(list_widget)

        self._detail_container = QScrollArea()
        self._detail_container.setWidgetResizable(True)
        self._detail_placeholder = QLabel("Select a plugin to see its details.")
        self._detail_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail_container.setWidget(self._detail_placeholder)
        splitter.addWidget(self._detail_container)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        root = QVBoxLayout(self)
        root.addWidget(splitter)

        self.refresh()

    def _on_install_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select a plugin package", "", "Pythia plugin (*.zip)")
        if not path:
            return
        result = self.backend.install_plugin_zip(path)
        if result.get("status") in {"ok", "partial"}:
            if result.get("status") == "ok":
                QMessageBox.information(self, "Plugins", f"Installed {result.get('display_name', result.get('id'))}.")
            else:
                QMessageBox.warning(self, "Plugins", result.get("message", "Installed, but refresh failed."))
            self.refresh()
        else:
            QMessageBox.warning(self, "Plugins", result.get("message", "Install failed."))

    def _on_check_updates(self) -> None:
        dialog = PluginUpdateDialog(self.backend, self)
        dialog.exec()
        self.refresh()

    def refresh(self) -> None:
        try:
            plugins = self.backend.get_all_plugins()
        except Exception as e:  # a broken plugin registry must not blank the whole view
            QMessageBox.warning(self, "Plugins", f"Couldn't load the plugin catalog: {e}")
            plugins = []
        self._plugins = {p["id"]: p for p in plugins}

        current_id = self._current_plugin_id()
        self._list.clear()
        for plugin in plugins:
            label = f"{plugin['display_name']} ({plugin['origin']})"
            if not plugin["enabled"]:
                label += " -- disabled"
            item = QListWidgetItem(label)
            item.setData(PLUGIN_ID_ROLE, plugin["id"])
            self._list.addItem(item)
            if plugin["id"] == current_id:
                self._list.setCurrentItem(item)

    def _current_plugin_id(self) -> str | None:
        item = self._list.currentItem()
        return item.data(PLUGIN_ID_ROLE) if item else None

    def _on_selection_changed(self, current: QListWidgetItem, _previous) -> None:
        if current is None:
            self._detail_container.setWidget(QLabel("Select a plugin to see its details."))
            return
        plugin_id = current.data(PLUGIN_ID_ROLE)
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return
        self._detail_container.setWidget(self._build_detail_widget(plugin))

    def _build_detail_widget(self, plugin: dict) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        header = QLabel(f"<b>{plugin['display_name']}</b> — v{plugin['version']} by {plugin['author']}")
        header.setWordWrap(True)
        layout.addWidget(header)
        layout.addWidget(QLabel(f"Categories: {', '.join(plugin['categories'])}"))
        layout.addWidget(QLabel(f"Origin: {plugin['origin']}"))

        enabled_box = QCheckBox("Enabled")
        enabled_box.setChecked(plugin["enabled"])

        def _on_enabled_toggled(checked: bool) -> None:
            result = self.backend.set_plugin_enabled(plugin["id"], checked)
            if result.get("status") == "ok":
                self.refresh()
                return
            enabled_box.blockSignals(True)
            enabled_box.setChecked(not checked)
            enabled_box.blockSignals(False)
            QMessageBox.warning(self, "Plugins", result.get("message", "The plugin state was not saved."))

        enabled_box.toggled.connect(_on_enabled_toggled)
        layout.addWidget(enabled_box)

        # 2026-08-08, native UI depth: "Protected built-ins must not be
        # removable or shadowable" -- `origin == "Built-in"` is the same
        # signal registry.is_protected() is computed from (the built-in zip
        # set, see pythia.plugins.registry's own docstring), so this stays
        # consistent with the real protection rule rather than a separate
        # guess. A built-in plugin gets an explanatory label instead of a
        # button it would just fail to use.
        if plugin["origin"] == "Built-in":
            protected_label = QLabel("Built-in plugin — cannot be removed.")
            protected_label.setWordWrap(True)
            layout.addWidget(protected_label)
        else:
            remove_button = QPushButton("Remove plugin")

            def _on_remove() -> None:
                confirm = QMessageBox.question(
                    self, "Remove plugin", f"Remove {plugin['display_name']}? This deletes its installed package."
                )
                if confirm != QMessageBox.StandardButton.Yes:
                    return
                result = self.backend.uninstall_plugin_zip(plugin["id"])
                if result.get("status") in {"ok", "partial"}:
                    self.refresh()
                    if result.get("status") == "partial":
                        QMessageBox.warning(self, "Plugins", result.get("message", "Removed, but refresh failed."))
                else:
                    QMessageBox.warning(self, "Plugins", result.get("message", "Couldn't remove this plugin."))

            remove_button.clicked.connect(_on_remove)
            layout.addWidget(remove_button)

        if len(plugin["categories"]) > 1:
            cap_group = QGroupBox("Capabilities")
            cap_layout = QVBoxLayout(cap_group)
            for category in plugin["categories"]:
                box = QCheckBox(category)
                box.setChecked(plugin["capability_enabled"].get(category, True))

                def _make_handler(cat, checkbox):
                    def _handler(checked: bool) -> None:
                        result = self.backend.set_plugin_capability_enabled(plugin["id"], cat, checked)
                        if result.get("status") == "ok":
                            return
                        checkbox.blockSignals(True)
                        checkbox.setChecked(not checked)
                        checkbox.blockSignals(False)
                        QMessageBox.warning(
                            self, "Plugins", result.get("message", "The capability state was not saved.")
                        )

                    return _handler

                box.toggled.connect(_make_handler(category, box))
                cap_layout.addWidget(box)
            layout.addWidget(cap_group)

        if plugin["optional_dependencies"]:
            dep_group = QGroupBox("Optional dependencies")
            dep_layout = QVBoxLayout(dep_group)
            for dep in plugin["optional_dependencies"]:
                state = "installed & enabled" if dep.get("enabled") else ("installed" if dep.get("installed") else "not installed")
                dep_layout.addWidget(QLabel(f"{dep.get('id')}: {state}"))
            layout.addWidget(dep_group)

        if plugin["configurable"]:
            config_group = QGroupBox("Configuration")
            config_layout = QVBoxLayout(config_group)
            try:
                values = self.backend.get_plugin_config(plugin["id"]) or {}
            except Exception:
                values = {}
            form = PluginConfigForm(plugin["config_schema"], values)
            config_layout.addWidget(form)

            save_button = QPushButton("Save configuration")

            def _on_save() -> None:
                try:
                    result = self.backend.save_plugin_config(plugin["id"], json.dumps(form.values()))
                    if result and result.get("status") == "ok":
                        QMessageBox.information(self, "Plugins", f"Saved configuration for {plugin['display_name']}.")
                    else:
                        QMessageBox.warning(self, "Plugins", (result or {}).get("message", "The configuration was not saved."))
                except Exception as e:
                    QMessageBox.warning(self, "Plugins", f"Couldn't save configuration: {e}")

            save_button.clicked.connect(_on_save)
            config_layout.addWidget(save_button)
            layout.addWidget(config_group)

        layout.addStretch(1)
        return container
