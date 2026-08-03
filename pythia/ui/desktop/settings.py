"""
Settings dialog -- cut down 2026-08-03 after real user feedback ("only two settings
buried in menus, and they're checkboxes... it's a waste of code"). The prior five-
section version (System & Sync / Library & Filters / Downloaders / Accounts /
Appearance) presented three sections that changed nothing when touched -- a stub
description standing in for a control isn't a setting. Kept: System & Sync (real,
load-bearing info -- this is what you point Syncthing at) and Downloaders (real,
already-working enable toggles). Library & Filters is gone outright (nothing in it
was editable). Appearance's UI-mode field is now a real combo box wired to the same
identity.get_setting/set_setting key pythia.ui.mode.resolve_mode() already reads on
every launch, instead of a read-only label describing a setting nothing could change.

Accounts is real AND generic (2026-08-03, second pass -- the first version of this
section hardcoded "itch.io" directly into the UI, which was itself flagged as the
exact kind of non-pluggable code the plugin architecture exists to avoid). It now
iterates every LibraryPlugin declaring `credential_fields` (PluginMeta's generic
secret-storage schema -- see pythia/plugins/__init__.py) and renders one form per
plugin from that declaration, so a third-party plugin with its own credential_fields
gets a real Settings row for free, with zero UI code of its own."""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pythia import datadir
from pythia import onboarding
from pythia.identity import app_data_dir, get_setting, get_system_uuid, set_setting
from pythia.plugins import downloader as downloader_plugins
from pythia.plugins import library as library_plugins
from pythia.plugins.registry import get_enabled_plugin_ids, set_enabled_plugin_ids
from pythia.ui import theme

_SECTIONS = ["System & Sync", "Downloaders", "Accounts", "Appearance"]
_UI_MODES = [("Auto (by screen size)", ""), ("Desktop", "desktop"), ("Big Picture", "bigpicture")]


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"font-family: {theme.SERIF_FONT}; font-size: 19px;")
    return label


def _heading_small(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"font-family: {theme.SERIF_FONT}; font-size: 14.5px; font-weight: 600;")
    return label


def _desc(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"font-size: 13px; color: {theme.COLORS['text_muted']};")
    return label


def _mono_value(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 12px; color: {theme.COLORS['text_muted']};")
    return label


class SettingsDialog(QDialog):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.setWindowTitle("Settings")
        self.resize(760, 480)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        nav = QListWidget()
        nav.setObjectName("navList")
        nav.setFixedWidth(190)
        for name in _SECTIONS:
            nav.addItem(QListWidgetItem(name))
        nav.setCurrentRow(0)
        layout.addWidget(nav)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_system_sync())
        self._stack.addWidget(self._build_downloaders())
        self._stack.addWidget(self._build_accounts())
        self._stack.addWidget(self._build_appearance())
        layout.addWidget(self._stack, stretch=1)

        nav.currentRowChanged.connect(self._stack.setCurrentIndex)

    def _section(self, title: str, description: str) -> tuple:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(26, 26, 26, 26)
        outer.setSpacing(4)
        outer.addWidget(_heading(title))
        outer.addWidget(_desc(description))
        outer.addSpacing(12)
        return page, outer

    # ---- System & Sync -- real: system_uuid, other systems seen, sync path
    def _build_system_sync(self) -> QWidget:
        page, outer = self._section(
            "System & Sync",
            "This system's identity and where its tracking data lives. Point Syncthing "
            "(or similar) at the sync folder below -- nothing else needs to be shared.",
        )
        form = QFormLayout()
        form.addRow("System UUID", _mono_value(get_system_uuid()))
        form.addRow("Sync folder", _mono_value(str(datadir.datadir_path())))
        form.addRow("App data folder", _mono_value(str(app_data_dir())))
        outer.addLayout(form)

        outer.addSpacing(16)
        outer.addWidget(QLabel("Other systems seen"))
        other_systems = self._other_systems()
        if other_systems:
            for uuid, count in other_systems.items():
                row = QLabel(f"{uuid[:13]}...  ·  {count} game(s)")
                row.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 11.5px; color: {theme.COLORS['text_muted']};")
                outer.addWidget(row)
        else:
            outer.addWidget(_desc("No data synced from another system yet."))
        outer.addStretch()
        return page

    def _other_systems(self) -> dict:
        this_uuid = get_system_uuid()
        counts: dict = {}
        for entry in datadir.list_all_entries():
            uuid = entry.get("system_uuid")
            if uuid and uuid != this_uuid:
                counts[uuid] = counts.get(uuid, 0) + 1
        return counts

    # ---- Downloaders -- real: every registered DownloaderPlugin, toggled via
    # the enabled_plugins allowlist
    def _build_downloaders(self) -> QWidget:
        page, outer = self._section(
            "Downloaders",
            "Pluggable acquisition backends -- Pythia launches each as its own tool and "
            "watches for results. Launching a game is still never Pythia's job.",
        )
        self._downloader_checks = {}
        for plugin in downloader_plugins.all_plugins():
            row = QHBoxLayout()
            label = QLabel(f"{plugin.display_name}  ({plugin.id})")
            row.addWidget(label, stretch=1)
            checkbox = QCheckBox("Enabled")
            checkbox.setChecked(self._is_plugin_enabled(plugin.id))
            checkbox.toggled.connect(lambda checked, pid=plugin.id: self._set_plugin_enabled(pid, checked))
            self._downloader_checks[plugin.id] = checkbox
            row.addWidget(checkbox)
            outer.addLayout(row)
        if not downloader_plugins.all_plugins():
            outer.addWidget(_desc("No downloader plugins registered."))
        outer.addStretch()
        return page

    def _is_plugin_enabled(self, plugin_id: str) -> bool:
        allowed = get_enabled_plugin_ids()
        return allowed is None or plugin_id in allowed

    def _set_plugin_enabled(self, plugin_id: str, enabled: bool):
        from pythia.plugins.registry import all_plugins

        allowed = get_enabled_plugin_ids()
        current = set(allowed) if allowed is not None else {p.id for p in all_plugins()}
        if enabled:
            current.add(plugin_id)
        else:
            current.discard(plugin_id)
        set_enabled_plugin_ids(list(current))

    # ---- Accounts -- generic: one row per LibraryPlugin that declares
    # credential_fields (PluginMeta's generic secret-storage schema), rendered from
    # that declaration alone. Zero plugin-specific UI code (2026-08-03, second pass
    # -- see module docstring for why the first, itch.io-hardcoded version was wrong).
    def _build_accounts(self) -> QWidget:
        page, outer = self._section(
            "Accounts",
            "Connect an account to pull your real owned library from it, including "
            "titles you haven't installed anywhere. Keys are stored only in this "
            "system's local database -- they never sync.",
        )
        self._account_fields: dict[str, dict[str, QLineEdit]] = {}
        self._account_status: dict[str, QLabel] = {}

        connectable = [p for p in library_plugins.all_plugins() if p.credential_fields]
        for plugin in connectable:
            outer.addWidget(_heading_small(plugin.display_name))

            form = QFormLayout()
            field_edits = {}
            for field in plugin.credential_fields:
                edit = QLineEdit(plugin.get_credential(field["key"]) or "")
                if field.get("secret"):
                    edit.setEchoMode(QLineEdit.EchoMode.Password)
                form.addRow(field["label"], edit)
                field_edits[field["key"]] = edit
            self._account_fields[plugin.id] = field_edits
            outer.addLayout(form)

            button_row = QHBoxLayout()
            save_btn = QPushButton("Save")
            save_btn.clicked.connect(lambda _checked=False, p=plugin: self._save_account(p))
            button_row.addWidget(save_btn)
            sync_btn = QPushButton("Sync Now")
            sync_btn.setObjectName("primary")
            sync_btn.clicked.connect(lambda _checked=False, p=plugin: self._sync_account(p))
            button_row.addWidget(sync_btn)
            button_row.addStretch()
            outer.addLayout(button_row)

            status = _desc("Connected." if plugin.is_connected() else "Not connected.")
            self._account_status[plugin.id] = status
            outer.addWidget(status)
            outer.addSpacing(14)

        if not connectable:
            outer.addWidget(_desc("No account-capable library plugins registered."))
        outer.addStretch()
        return page

    def _save_account(self, plugin):
        for key, edit in self._account_fields[plugin.id].items():
            plugin.set_credential(key, edit.text().strip())
        self._account_status[plugin.id].setText("Connected." if plugin.is_connected() else "Not connected.")

    def _sync_account(self, plugin):
        self._save_account(plugin)
        result = onboarding.sync_owned(plugin)
        if result["status"] != "ok":
            QMessageBox.warning(self, f"{plugin.display_name} sync failed", result["message"])
            self._account_status[plugin.id].setText(result["message"])
            return
        self._account_status[plugin.id].setText(f"Synced: {result['linked']} linked, {result['created']} new.")
        self.backend.refresh_games()
        self.backend.refresh_games()

    # ---- Appearance -- real: default UI mode, actually editable
    def _build_appearance(self) -> QWidget:
        page, outer = self._section(
            "Appearance", "Which UI Pythia launches with when started via `python -m pythia` (no --ui flag)."
        )
        form = QFormLayout()
        self._ui_mode_combo = QComboBox()
        current = get_setting("ui_mode") or ""
        for label, value in _UI_MODES:
            self._ui_mode_combo.addItem(label, value)
        index = max(0, [v for _, v in _UI_MODES].index(current) if current in [v for _, v in _UI_MODES] else 0)
        self._ui_mode_combo.setCurrentIndex(index)
        self._ui_mode_combo.currentIndexChanged.connect(self._save_ui_mode)
        form.addRow("Default UI mode", self._ui_mode_combo)
        outer.addLayout(form)
        outer.addStretch()
        return page

    def _save_ui_mode(self, index: int):
        value = self._ui_mode_combo.itemData(index)
        set_setting("ui_mode", value or "")
