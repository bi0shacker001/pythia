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

from PySide6.QtCore import Signal
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

import threading

from pythia import datadir
from pythia import onboarding
from pythia.identity import app_data_dir, get_setting, get_system_uuid, set_setting
from pythia.plugins import downloader as downloader_plugins
from pythia.plugins import engine as engine_plugins
from pythia.plugins import library as library_plugins
from pythia.plugins import manager as manager_plugins
from pythia.plugins.context import PluginContext
from pythia.plugins.registry import all_plugins, categories_of, get_enabled_plugin_ids, set_enabled_plugin_ids
from pythia.ui import theme

# Two-tier plugin model (2026-08-03, replacing the earlier flat Accounts-only
# section): "Plugins" is whole-plugin install/enable-disable (lifecycle only,
# every category at once); each category below manages capability-level
# enable/config for whichever plugins implement it -- a multi-category plugin
# appears once per capability across these pages, never flattened into one
# blob. Matches the two-tier design settled during the earlier mockup review
# (global Plugins page + per-category pages), never actually built until now.
_SECTIONS = ["System & Sync", "Plugins", "Sources", "Engine Detection", "Managers", "Downloaders", "Appearance"]
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
    f95LoginStatusReady = Signal(bool)
    f95CheckStatusReady = Signal(str)

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.setWindowTitle("Settings")
        self.resize(760, 480)
        self.f95LoginStatusReady.connect(self._on_f95_login_status)
        self.f95CheckStatusReady.connect(self._on_f95_check_status)

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
        self._stack.addWidget(self._build_plugins())
        self._stack.addWidget(self._build_sources())
        self._stack.addWidget(self._build_engine_detection())
        self._stack.addWidget(self._build_managers())
        self._stack.addWidget(self._build_downloaders())
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

    # ---- Plugins -- global whole-plugin lifecycle: every plugin, any category,
    # one enable/disable toggle each (multi-category plugins are on/off as a
    # whole, matching enabled_plugins' own per-id-not-per-category semantics).
    # Per-capability configuration lives on the category pages below, never here.
    def _build_plugins(self) -> QWidget:
        page, outer = self._section(
            "Plugins",
            "Every plugin Pythia has discovered (built-in, or dropped into "
            "app_data_dir()/plugins/), regardless of what it does. Disabling one "
            "here turns it off everywhere at once -- per-capability configuration "
            "lives on the Sources/Engine Detection/Managers/Downloaders pages.",
        )
        plugins = sorted(all_plugins(), key=lambda p: p.display_name)
        self._plugin_checks: dict[str, QCheckBox] = {}
        for plugin in plugins:
            row = QHBoxLayout()
            categories = ", ".join(categories_of(plugin))
            label = QLabel(f"{plugin.display_name}  ·  {categories}")
            row.addWidget(label, stretch=1)
            checkbox = QCheckBox("Enabled")
            checkbox.setChecked(self._is_plugin_enabled(plugin.id))
            checkbox.toggled.connect(lambda checked, pid=plugin.id: self._set_plugin_enabled(pid, checked))
            self._plugin_checks[plugin.id] = checkbox
            row.addWidget(checkbox)
            outer.addLayout(row)
        if not plugins:
            outer.addWidget(_desc("No plugins registered."))
        outer.addStretch()
        return page

    # ---- Engine Detection -- every registered EnginePlugin: enable toggle plus
    # its real capabilities() status where available (never guessed if a plugin
    # can't report one).
    def _build_engine_detection(self) -> QWidget:
        page, outer = self._section(
            "Engine Detection",
            "What identifies an install's engine (Ren'Py, Unity, RPG Maker, ...) and "
            "remediates fixable platform gaps -- e.g. adding missing native Linux "
            "libs. Never launches anything itself.",
        )
        plugins = engine_plugins.all_plugins()
        ctx = PluginContext()
        for plugin in plugins:
            row = QHBoxLayout()
            label = QLabel(plugin.display_name)
            row.addWidget(label, stretch=1)
            try:
                caps = plugin.capabilities(ctx)
                status = "Installed" if caps.get("installed") else "Not installed"
                if caps.get("version"):
                    status += f" ({caps['version']})"
            except NotImplementedError:
                status = ""
            if status:
                status_label = QLabel(status)
                status_label.setStyleSheet(f"font-size: 11.5px; color: {theme.COLORS['text_muted']};")
                row.addWidget(status_label)
            checkbox = QCheckBox("Enabled")
            checkbox.setChecked(self._is_plugin_enabled(plugin.id))
            checkbox.toggled.connect(lambda checked, pid=plugin.id: self._set_plugin_enabled(pid, checked))
            row.addWidget(checkbox)
            outer.addLayout(row)
        if not plugins:
            outer.addWidget(_desc("No engine plugins registered."))
        outer.addStretch()
        return page

    # ---- Managers -- every registered ManagerPlugin: which launcher(s) can
    # hand a game off, and whether each is actually available on this system.
    def _build_managers(self) -> QWidget:
        page, outer = self._section(
            "Managers",
            "Hands a tracked game off to a launcher capable of running it (Steam, "
            "Epic, or a direct-launch executable you've marked yourself). Pythia "
            "never runs a game's own executable itself, except for that explicit "
            "direct-launch exception.",
        )
        plugins = manager_plugins.all_plugins()
        ctx = PluginContext()
        for plugin in plugins:
            row = QHBoxLayout()
            sources = ", ".join(plugin.compatible_sources) or "any (direct-launch only)"
            label = QLabel(f"{plugin.display_name}  ·  {sources}")
            row.addWidget(label, stretch=1)
            available = plugin.is_available(ctx)
            status_label = QLabel("Available" if available else "Not confirmed")
            status_label.setStyleSheet(
                f"font-size: 11.5px; color: {theme.COLORS['ok'] if available else theme.COLORS['text_faint']};"
            )
            row.addWidget(status_label)
            checkbox = QCheckBox("Enabled")
            checkbox.setChecked(self._is_plugin_enabled(plugin.id))
            checkbox.toggled.connect(lambda checked, pid=plugin.id: self._set_plugin_enabled(pid, checked))
            row.addWidget(checkbox)
            outer.addLayout(row)
        if not plugins:
            outer.addWidget(_desc("No manager plugins registered."))
        outer.addStretch()
        return page

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

    # ---- Sources -- every registered LibraryPlugin: enable toggle for all of
    # them, plus (2026-08-03, folded in from the old standalone "Accounts"
    # section) the account-connect form for whichever ones declare
    # credential_fields. Zero plugin-specific UI code for the generic case --
    # see module docstring for why the first, itch.io-hardcoded version was
    # wrong.
    def _build_sources(self) -> QWidget:
        page, outer = self._section(
            "Sources",
            "What recognizes an existing install or account library -- Steam, GOG, "
            "Epic, F95zone, or a plain folder scan. Detection-only where no account "
            "exists; connect below to also pull owned-but-not-installed titles.",
        )
        for plugin in sorted(library_plugins.all_plugins(), key=lambda p: p.display_name):
            row = QHBoxLayout()
            row.addWidget(QLabel(plugin.display_name), stretch=1)
            checkbox = QCheckBox("Enabled")
            checkbox.setChecked(self._is_plugin_enabled(plugin.id))
            checkbox.toggled.connect(lambda checked, pid=plugin.id: self._set_plugin_enabled(pid, checked))
            row.addWidget(checkbox)
            outer.addLayout(row)
        outer.addSpacing(16)

        self._account_fields: dict[str, dict[str, QLineEdit]] = {}
        self._account_status: dict[str, QLabel] = {}

        connectable = [p for p in library_plugins.all_plugins() if p.credential_fields]
        if connectable:
            outer.addWidget(_heading("Connect an account"))
            outer.addWidget(
                _desc(
                    "Pulls your real owned library from the account, including titles "
                    "you haven't installed anywhere. Keys are stored only in this "
                    "system's local database -- they never sync."
                )
            )
            outer.addSpacing(8)
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

        # F95zone's login isn't a typed API key like the plugins above -- it's a
        # real embedded-browser session (see pythia/ui/desktop/f95_login.py's
        # docstring for why a plain form POST doesn't work). One narrow,
        # explicitly-commented exception to the generic credential_fields loop
        # above, since representing it as a text field would be dishonest --
        # F95zone login doesn't actually work that way.
        from pythia.plugins.library import f95 as f95_plugin

        outer.addWidget(_heading_small("F95zone"))
        self._f95_status = _desc("Checking session..." if f95_plugin.get_session_cookies() else "Not logged in.")
        login_btn = QPushButton("Log in via browser...")
        login_btn.setObjectName("primary")
        login_btn.clicked.connect(self._login_f95)
        outer.addWidget(login_btn)
        outer.addWidget(self._f95_status)
        if f95_plugin.get_session_cookies():
            self._refresh_f95_status()

        # Real update-check pass (2026-08-03, pythia/plugins/library/f95_updates.py)
        # -- doesn't need the login above at all (fast_check/full_check hit
        # api.f95checker.dev with cookies=False in the real client); login is
        # only wired to the button above because it's shared UI real estate,
        # not because this check depends on it.
        check_btn = QPushButton("Check for updates now")
        check_btn.clicked.connect(self._check_f95_updates)
        outer.addWidget(check_btn)
        self._f95_check_status = _desc("")
        outer.addWidget(self._f95_check_status)

        outer.addStretch()
        return page

    def _check_f95_updates(self):
        self._f95_check_status.setText("Checking...")
        threading.Thread(target=self._check_f95_updates_worker, daemon=True).start()

    def _check_f95_updates_worker(self):
        from pythia.plugins.library import f95_updates

        result = f95_updates.run_update_check()
        message = f"Checked {result['checked']}, {result['updated']} update(s) found."
        if result["errors"]:
            message += f" ({len(result['errors'])} error(s) -- see logs.)"
        self.f95CheckStatusReady.emit(message)

    def _on_f95_check_status(self, message: str):
        self._f95_check_status.setText(message)
        self.backend.refresh_games()

    def _login_f95(self):
        from pythia.ui.desktop.f95_login import F95LoginDialog

        dialog = F95LoginDialog(self)
        dialog.exec()
        self._f95_status.setText("Checking session...")
        self._refresh_f95_status()

    def _refresh_f95_status(self):
        # is_logged_in() is a real network call (up to a 10s timeout) -- run it
        # off the GUI thread so opening/using Settings never freezes on it.
        threading.Thread(target=self._check_f95_login_worker, daemon=True).start()

    def _check_f95_login_worker(self):
        from pythia.plugins.library import f95 as f95_plugin

        self.f95LoginStatusReady.emit(f95_plugin.is_logged_in())

    def _on_f95_login_status(self, logged_in: bool):
        self._f95_status.setText("Logged in." if logged_in else "Not logged in.")

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

        # Theme: real, persisted (theme.set_palette_name(), same setting store
        # as ui_mode above) -- takes effect on next launch, not live. Live
        # re-styling of every already-built widget is real, scoped-out follow-
        # up work, not attempted here (see theme.py's module docstring).
        self._theme_combo = QComboBox()
        current_theme = theme.get_palette_name()
        for name in theme.available_palettes():
            self._theme_combo.addItem(theme.palette_label(name), name)
        self._theme_combo.setCurrentIndex(theme.available_palettes().index(current_theme))
        self._theme_combo.currentIndexChanged.connect(self._save_theme)
        form.addRow("Theme (applies next launch)", self._theme_combo)

        outer.addLayout(form)
        outer.addStretch()
        return page

    def _save_ui_mode(self, index: int):
        value = self._ui_mode_combo.itemData(index)
        set_setting("ui_mode", value or "")

    def _save_theme(self, index: int):
        theme.set_palette_name(self._theme_combo.itemData(index))
