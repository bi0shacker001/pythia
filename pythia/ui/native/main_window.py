"""Native QMainWindow shell -- Library (list + inspector), Plugins, and
Activity Log as tabs (see aisync/CLAUDE-UI-RESEARCH.md for why tabs rather
than a persistent nav rail at this stage: not enough top-level screens yet
to justify one). Every action here calls a real pythia.ui.backend.Backend
method and shows its real result -- no static sample data, no decorative
controls that imply behavior nothing backs (same "no false success" rule
the web UI redesign established).
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pythia.ui.native.config_form import ADVANCED_CONFIG_FIELDS, CascadeConfigForm
from pythia.ui.native.downloads_view import DownloadsView
from pythia.ui.native.models import GAME_ID_ROLE, SUMMARY_ROLE, GameFilterProxyModel, GameListModel
from pythia.ui.native.plugins_view import PluginsView
from pythia.ui.native.settings_view import SettingsView
from pythia.ui.native.sync_view import SyncView

_SETTINGS_ORG = "Pythia"
_SETTINGS_APP = "PythiaNativeUI"


def _first_download_url(update: dict) -> str | None:
    """Best-effort real extraction of a download URL from an update
    observation's "downloads" field (pythia.plugin_sources.library.f95's own
    real shape: a sequence of (platform, [(host_label, url), ...]) pairs).
    Returns the first URL found, or None if the shape is empty/unexpected --
    never fabricates one."""
    downloads = update.get("downloads")
    if not downloads:
        return None
    try:
        for _platform, hosts in downloads:
            for _label, url in hosts:
                if url:
                    return url
    except (TypeError, ValueError):
        return None
    return None

_READY_LABELS = {
    "ready": "Ready to launch",
    "not_configured": "Not configured",
    "folder_missing": "Install folder missing",
    "executable_missing": "Executable missing",
    "needs_executable_confirmation": "Needs executable confirmation",
    "backend_unavailable": "Compatibility backend unavailable",
    "invalid_command_prefix": "Invalid launch command prefix",
    "instance_unavailable": "Not available on this system",
}


class LibraryInspector(QWidget):
    """The selected game's detail panel -- docked beside the list, not a
    modal (Lutris gui/widgets/game_bar.py's shape: header + primary action +
    fact row + expandable sections, per aisync/CLAUDE-UI-RESEARCH.md).

    2026-08-08, Codex correctness review -- every real Slot return shape used
    below was re-verified against pythia/ui/backend.py before this rewrite,
    not re-guessed:
    - find_manager_for_game() returns a LIST of candidates, not a status dict.
    - get_engine_status()["platforms"] is a DICT keyed by platform name (each
      value has real supported/native/requires/fixable fields -- see
      pythia.plugins.__init__.EnginePlugin.platform_status()'s own contract),
      not a list.
    - rescan_game() returns {"status": "ok"|"partial"|"error", "processed",
      "errors": <int count>} -- there is no "message" key.
    - get_launch_readiness()/launch_game_direct() take an optional
      variant_id; omitting it falls back to "first variant with a path" --
      real, but never what a user who explicitly picked another installed
      instance actually wants."""

    def __init__(self, backend, parent=None, start_download_callback=None):
        super().__init__(parent)
        self.backend = backend
        self._start_download_callback = start_download_callback
        self._game_id: str | None = None
        self._variant_id: str | None = None
        self._has_local_variant: bool = False

        root = QVBoxLayout(self)

        self.title_label = QLabel("Select a game")
        self.title_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.title_label.setAccessibleName("Selected game name")
        root.addWidget(self.title_label)

        self.facts_label = QLabel("")
        self.facts_label.setWordWrap(True)
        root.addWidget(self.facts_label)

        instance_row = QHBoxLayout()
        instance_row.addWidget(QLabel("Instance:"))
        self.variant_combo = QComboBox()
        self.variant_combo.setAccessibleName("Installed instance")
        self.variant_combo.currentIndexChanged.connect(self._on_variant_changed)
        instance_row.addWidget(self.variant_combo, 1)
        root.addLayout(instance_row)

        action_row = QHBoxLayout()
        self.launch_button = QPushButton("Launch")
        self.launch_button.setEnabled(False)
        self.launch_button.clicked.connect(self._on_launch)
        action_row.addWidget(self.launch_button)

        self.rescan_button = QPushButton("Rescan")
        self.rescan_button.setEnabled(False)
        self.rescan_button.clicked.connect(self._on_rescan)
        action_row.addWidget(self.rescan_button)

        self.rename_button = QPushButton("Rename...")
        self.rename_button.setEnabled(False)
        self.rename_button.clicked.connect(self._on_rename)
        action_row.addWidget(self.rename_button)

        self.manager_button = QPushButton("Open in manager")
        self.manager_button.setEnabled(False)
        self.manager_button.clicked.connect(self._on_open_in_manager)
        action_row.addWidget(self.manager_button)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self.detail_tabs = QTabWidget()
        root.addWidget(self.detail_tabs, 1)

        # ---- Engine & Compatibility tab ----
        engine_tab = QWidget()
        engine_layout = QVBoxLayout(engine_tab)
        self.engine_summary_label = QLabel("")
        self.engine_summary_label.setWordWrap(True)
        engine_layout.addWidget(self.engine_summary_label)

        self.platforms_container = QWidget()
        self.platforms_layout = QVBoxLayout(self.platforms_container)
        self.platforms_layout.setContentsMargins(0, 0, 0, 0)
        engine_layout.addWidget(self.platforms_container)

        override_row = QHBoxLayout()
        override_row.addWidget(QLabel("Correct engine:"))
        self.engine_combo = QComboBox()
        self.engine_combo.setAccessibleName("Manual engine correction")
        override_row.addWidget(self.engine_combo)
        self.engine_apply_button = QPushButton("Apply")
        self.engine_apply_button.clicked.connect(self._on_apply_engine_override)
        override_row.addWidget(self.engine_apply_button)
        override_row.addStretch(1)
        engine_layout.addLayout(override_row)
        engine_layout.addStretch(1)
        self.detail_tabs.addTab(engine_tab, "Engine && Compatibility")

        # ---- Versions & Variants tab (2026-08-08, native UI depth: real
        # cross-system detail from models.summarize_game()'s "detail_rows" --
        # every system's own recorded copy, "local" only for the owning
        # system's own launchable variants) ----
        versions_tab = QWidget()
        versions_layout = QVBoxLayout(versions_tab)
        self.versions_tree = QTreeWidget()
        self.versions_tree.setAccessibleName("Versions and variants")
        self.versions_tree.setHeaderLabels(["System", "Version", "Variant ID", "Source", "Language", "Mods", "Path"])
        self.versions_tree.setRootIsDecorated(False)
        self.versions_tree.setUniformRowHeights(True)
        versions_layout.addWidget(self.versions_tree)
        self.detail_tabs.addTab(versions_tab, "Versions && Variants")

        # ---- Systems & Updates tab ----
        systems_tab = QWidget()
        systems_layout = QVBoxLayout(systems_tab)
        systems_layout.addWidget(QLabel("Systems tracking this game:"))
        self.systems_label = QLabel("")
        self.systems_label.setWordWrap(True)
        systems_layout.addWidget(self.systems_label)
        systems_layout.addWidget(QLabel("Available updates:"))
        self.updates_list = QListWidget()
        self.updates_list.setAccessibleName("Available updates")
        systems_layout.addWidget(self.updates_list)
        self.dismiss_updates_button = QPushButton("Dismiss updates")
        self.dismiss_updates_button.setEnabled(False)
        self.dismiss_updates_button.clicked.connect(self._on_dismiss_updates)
        systems_layout.addWidget(self.dismiss_updates_button)
        self.detail_tabs.addTab(systems_tab, "Systems && Updates")

        # ---- Timeline tab (real events from datadir.add_timeline_event(),
        # own system only -- see models.summarize_game()'s own docstring) ----
        timeline_tab = QWidget()
        timeline_layout = QVBoxLayout(timeline_tab)
        self.timeline_list = QListWidget()
        self.timeline_list.setAccessibleName("Timeline")
        timeline_layout.addWidget(self.timeline_list)
        self.detail_tabs.addTab(timeline_tab, "Timeline")

        # ---- Notes tab ----
        notes_tab = QWidget()
        notes_layout = QVBoxLayout(notes_tab)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setAccessibleName("Game notes")
        notes_layout.addWidget(self.notes_edit)
        self.notes_save_button = QPushButton("Save notes")
        self.notes_save_button.clicked.connect(self._on_save_notes)
        notes_layout.addWidget(self.notes_save_button)
        self.detail_tabs.addTab(notes_tab, "Notes")

        # ---- Advanced tab (2026-08-08, native UI depth: real per-game/
        # instance config-cascade editor, sharing CascadeConfigForm with the
        # global Settings page rather than duplicating cascade logic) ----
        advanced_tab = QWidget()
        advanced_layout = QVBoxLayout(advanced_tab)

        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Editing scope:"))
        self.advanced_scope_combo = QComboBox()
        self.advanced_scope_combo.setAccessibleName("Advanced config editing scope")
        self.advanced_scope_combo.addItem("Game (applies to every instance)", "game")
        self.advanced_scope_combo.addItem("Instance (this install only)", "instance")
        self.advanced_scope_combo.currentIndexChanged.connect(self._on_advanced_scope_changed)
        scope_row.addWidget(self.advanced_scope_combo)
        scope_row.addStretch(1)
        advanced_layout.addLayout(scope_row)

        self.advanced_form_container = QWidget()
        self.advanced_form_layout = QVBoxLayout(self.advanced_form_container)
        self.advanced_form_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.addWidget(self.advanced_form_container)
        self.advanced_form: CascadeConfigForm | None = None

        self.advanced_apply_button = QPushButton("Apply advanced settings")
        self.advanced_apply_button.setEnabled(False)
        self.advanced_apply_button.clicked.connect(self._on_apply_advanced)
        advanced_layout.addWidget(self.advanced_apply_button)

        plan_group = QGroupBox("Effective launch plan")
        plan_layout = QVBoxLayout(plan_group)
        self.plan_label = QLabel("")
        self.plan_label.setWordWrap(True)
        plan_layout.addWidget(self.plan_label)
        advanced_layout.addWidget(plan_group)
        advanced_layout.addStretch(1)
        self.detail_tabs.addTab(advanced_tab, "Advanced")

        self.set_summary(None)

    def set_summary(self, summary: dict | None) -> None:
        self._summary = summary
        self._game_id = summary["game_id"] if summary else None
        editable = bool(summary and summary.get("editable"))

        self._populate_variant_combo(summary.get("variants") if summary else [])
        self._refresh_versions_tree(summary.get("detail_rows") if summary else [])
        self._refresh_systems_and_updates(summary)
        self._refresh_timeline(summary.get("timeline_events") if summary else [])

        if summary is None:
            self.title_label.setText("Select a game")
            self.facts_label.setText("")
            self.engine_summary_label.setText("")
            self._clear_platform_rows()
            self.notes_edit.setPlainText("")
            for widget in (
                self.launch_button,
                self.rescan_button,
                self.rename_button,
                self.manager_button,
                self.engine_apply_button,
                self.notes_save_button,
            ):
                widget.setEnabled(False)
            self._rebuild_advanced_form()
            return

        self.title_label.setText(summary["canonical_name"])
        platform_names = ", ".join(sorted({p.get("platform", "?") for p in summary["platform_ids"]})) or "none recorded"
        facts = (
            f"Systems: {len(summary['systems'])} · "
            f"Versions: {summary['version_count']} · "
            f"Variants: {summary['variant_count']} · "
            f"Platforms: {platform_names}"
        )
        if summary["has_updates"]:
            facts += " · Updates available"
        if not editable:
            facts += " · (tracked by another system only -- not editable here)"
        if not self._has_local_variant:
            facts += " · Not installed on this system"
        self.facts_label.setText(facts)
        self.notes_edit.setPlainText(summary.get("notes", ""))

        # 2026-08-08, Codex review: Launch must never be enabled for a game
        # with no local, selectable variant -- get_launch_readiness(game_id,
        # None)/launch_game_direct(game_id, None) fall back to Backend's
        # legacy "first variant with a path across EVERY known system," which
        # can pick a genuinely foreign, non-existent-on-this-machine path.
        self.launch_button.setEnabled(self._has_local_variant)
        self.rescan_button.setEnabled(editable)
        self.rename_button.setEnabled(editable)
        self.manager_button.setEnabled(True)
        self.notes_save_button.setEnabled(editable)
        self.engine_apply_button.setEnabled(editable)

        self._refresh_launch_readiness()
        self._refresh_engine_status()
        self._rebuild_advanced_form()

    def _populate_variant_combo(self, variants: list | None) -> None:
        """Real installed instances for THIS game on THIS system (models.
        summarize_game() already scopes this to the owning entry's own
        variants). Selecting one sets `_variant_id`, which every
        readiness/launch call below passes explicitly.

        2026-08-08, Codex review: an empty `variants` list -- a remote-only
        game with no local install -- must set `_has_local_variant = False`
        and the caller (set_summary) must then disable Launch entirely,
        rather than calling get_launch_readiness()/launch_game_direct() with
        variant_id=None, which falls back to Backend's legacy "first variant
        with a path across every known system" and can hand back a path
        that simply doesn't exist on this machine."""
        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()
        variants = variants or []
        self._has_local_variant = bool(variants)
        if not variants:
            self.variant_combo.addItem("(not installed on this system)", None)
            self.variant_combo.setEnabled(False)
            self._variant_id = None
        else:
            self.variant_combo.setEnabled(True)
            for v in variants:
                label = f"{v.get('version') or 'unversioned'} — {v.get('path', '')}"
                self.variant_combo.addItem(label, v.get("variant_id"))
            self.variant_combo.setCurrentIndex(0)
            self._variant_id = variants[0].get("variant_id")
        self.variant_combo.blockSignals(False)

    def _on_variant_changed(self, _index: int) -> None:
        self._variant_id = self.variant_combo.currentData()
        self._refresh_launch_readiness()
        self._rebuild_advanced_form()

    def _on_advanced_scope_changed(self, _index: int) -> None:
        self._rebuild_advanced_form()

    def _clear_advanced_form(self) -> None:
        while self.advanced_form_layout.count():
            item = self.advanced_form_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.advanced_form = None

    def _rebuild_advanced_form(self) -> None:
        """Rebuilt (not just refreshed) on every game/variant/scope change --
        CascadeConfigForm is bound to a fixed (game_id, variant_id, scope) at
        construction, matching how _refresh_engine_status() already rebuilds
        its per-platform rows rather than trying to mutate a form in place."""
        self._clear_advanced_form()
        if not self._game_id or not self._summary or not self._summary.get("editable"):
            self.advanced_form_layout.addWidget(
                QLabel("Select an editable (own-system) game to configure advanced settings.")
            )
            self.advanced_apply_button.setEnabled(False)
            self._refresh_effective_plan()
            return

        scope = self.advanced_scope_combo.currentData() or "game"
        if scope == "instance" and not self._variant_id:
            self.advanced_form_layout.addWidget(
                QLabel("Select an installed instance to edit instance-scope settings.")
            )
            self.advanced_apply_button.setEnabled(False)
        else:
            variant_id_for_scope = self._variant_id if scope == "instance" else None
            self.advanced_form = CascadeConfigForm(
                self.backend,
                ADVANCED_CONFIG_FIELDS,
                game_id=self._game_id,
                variant_id=variant_id_for_scope,
                edit_scope=scope,
            )
            self.advanced_form_layout.addWidget(self.advanced_form)
            self.advanced_apply_button.setEnabled(True)
        self._refresh_effective_plan()

    def _on_apply_advanced(self) -> None:
        if self.advanced_form is None:
            return
        result = self.advanced_form.save()
        if result.get("status") == "ok":
            QMessageBox.information(self, "Advanced settings", "Saved.")
        else:
            detail = "\n".join(f"{k}: {v}" for k, v in result.get("errors", {}).items())
            QMessageBox.warning(self, "Advanced settings", f"Some values couldn't be saved:\n{detail}")
        self._refresh_effective_plan()

    def _refresh_effective_plan(self) -> None:
        """Preview from the SAME backend path launch uses
        (get_effective_launch_plan -> pythia.launch.resolve_launch_plan()) --
        never a separate computation, so this can't disagree with what
        Launch actually does."""
        if not self._game_id or not self._has_local_variant or not self._variant_id:
            self.plan_label.setText("No local, selected instance to preview a launch plan for.")
            return
        try:
            plan = self.backend.get_effective_launch_plan(self._game_id, self._variant_id)
        except Exception as e:
            self.plan_label.setText(f"Couldn't compute the effective launch plan: {e}")
            return
        label = _READY_LABELS.get(plan.get("state"), plan.get("state", "Unknown"))
        self.plan_label.setText(
            f"State: {label}\n"
            f"Executable: {plan.get('executable') or '(none)'}\n"
            f"Arguments: {plan.get('args') or []}\n"
            f"Working directory: {plan.get('working_dir') or '(default)'}\n"
            f"Backend: {plan.get('backend', 'native')}\n"
            f"Environment: {plan.get('environment') or {}}\n"
            f"Wrappers: {plan.get('wrappers') or []}\n"
            f"Monitoring: {plan.get('monitoring') or {}}"
        )

    def _clear_platform_rows(self) -> None:
        while self.platforms_layout.count():
            item = self.platforms_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _refresh_launch_readiness(self) -> None:
        if not self._game_id:
            return
        if not self._has_local_variant:
            self.launch_button.setText("Launch — Not installed on this system")
            self.launch_button.setToolTip("This game has no installed variant on this system.")
            self.launch_button.setEnabled(False)
            return
        try:
            readiness = self.backend.get_launch_readiness(self._game_id, self._variant_id)
        except Exception as e:
            self.launch_button.setText(f"Launch (error: {e})")
            self.launch_button.setEnabled(False)
            return
        label = _READY_LABELS.get(readiness.get("state"), readiness.get("state", "Unknown"))
        self.launch_button.setText(f"Launch — {label}")
        self.launch_button.setToolTip(readiness.get("reason") or "")

    def _refresh_engine_status(self) -> None:
        self.engine_combo.clear()
        self.engine_combo.addItem("(automatic detection)", "")
        self.engine_combo.setEnabled(True)
        self.engine_combo.setToolTip("")
        try:
            for option in self.backend.get_engine_options():
                self.engine_combo.addItem(option["name"], option["kind"])
        except Exception as exc:
            self.engine_combo.setEnabled(False)
            self.engine_combo.setToolTip(f"Couldn't load engine choices: {exc}")
        current = (self._summary or {}).get("engine_override", "")
        index = self.engine_combo.findData(current)
        if index >= 0:
            self.engine_combo.setCurrentIndex(index)

        self._clear_platform_rows()
        if not self._game_id:
            return
        try:
            status = self.backend.get_engine_status(self._game_id)
        except Exception as e:
            self.engine_summary_label.setText(f"Couldn't read engine status: {e}")
            return
        if status.get("status") != "ok":
            self.engine_summary_label.setText(status.get("message", "No engine information available."))
            return
        manual = " (manually set)" if status.get("manual") else ""
        self.engine_summary_label.setText(f"Engine: {status.get('plugin_name', 'unknown')}{manual}")

        platforms = status.get("platforms") or {}
        if not isinstance(platforms, dict) or not platforms:
            self.platforms_layout.addWidget(QLabel("No per-platform status reported."))
            return
        for platform_name, info in platforms.items():
            info = info or {}
            row = QHBoxLayout()
            if info.get("native") or info.get("supported"):
                state = "supported (native)" if info.get("native") else "supported"
            else:
                state = "not supported"
            requires = info.get("requires") or []
            text = f"{platform_name}: {state}"
            if requires:
                text += f" -- requires: {', '.join(str(r) for r in requires)}"
            row.addWidget(QLabel(text))
            if info.get("fixable"):
                enable_button = QPushButton("Enable")
                enable_button.clicked.connect(lambda _checked=False, p=platform_name: self._on_enable_platform(p))
                row.addWidget(enable_button)
            row.addStretch(1)
            row_widget = QWidget()
            row_widget.setLayout(row)
            self.platforms_layout.addWidget(row_widget)

    def _on_enable_platform(self, platform: str) -> None:
        if not self._game_id:
            return
        self.backend.enable_engine_platform(self._game_id, platform)

    def _refresh_versions_tree(self, detail_rows: list | None) -> None:
        """Real cross-system detail (models.summarize_game()'s
        "detail_rows") -- every system's own recorded copy of this game.
        `local` rows are this system's own, launchable variants; foreign
        rows are shown for visibility only and are never wired to any
        launch/rescan control (those only ever read `_variant_id`, which is
        populated exclusively from the own-system `variants` list)."""
        self.versions_tree.clear()
        for row in detail_rows or []:
            system_label = row.get("system_uuid", "") + (" (this system)" if row.get("local") else "")
            item = QTreeWidgetItem(
                [
                    system_label,
                    str(row.get("version", "")),
                    str(row.get("variant_id", "")),
                    str(row.get("source_kind", "")),
                    str(row.get("language", "")),
                    ", ".join(str(m) for m in row.get("mods") or []),
                    str(row.get("path", "")),
                ]
            )
            self.versions_tree.addTopLevelItem(item)

    def _refresh_systems_and_updates(self, summary: dict | None) -> None:
        summary = summary or {}
        own_uuid = self.backend.get_system_uuid() if self._game_id else None
        systems = summary.get("systems") or []
        self.systems_label.setText(
            "\n".join(f"{s}{' (this system)' if s == own_uuid else ''}" for s in systems) or "None recorded."
        )

        self.updates_list.clear()
        updates = summary.get("updates") or []
        for u in updates:
            version = u.get("version", "?")
            reported_by = u.get("_reported_by", "")
            changelog = (u.get("changelog") or "").strip()
            text = f"v{version} (reported by {reported_by})"
            if changelog:
                text += f" — {changelog.splitlines()[0][:120]}"
            item = QListWidgetItem()
            self.updates_list.addItem(item)

            # 2026-08-08, download-automator hardening: a real Download
            # button when this update actually carries a download URL --
            # ties F95's real update data to the real automated-download
            # path instead of leaving updates purely informational. Only
            # shown when the caller (MainWindow) actually wired a download
            # starter in; this inspector never talks to Backend's download
            # Slots directly, keeping that logic in one place (DownloadsView).
            download_url = _first_download_url(u)
            if download_url and self._start_download_callback:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(4, 2, 4, 2)
                row_layout.addWidget(QLabel(text), 1)
                download_button = QPushButton("Download")
                download_button.clicked.connect(
                    lambda _checked=False, u=download_url, name=summary.get("canonical_name", ""): self._on_download_update(u, name)
                )
                row_layout.addWidget(download_button)
                item.setSizeHint(row.sizeHint())
                self.updates_list.setItemWidget(item, row)
            else:
                item.setText(text)
        self.dismiss_updates_button.setEnabled(bool(updates) and bool(summary.get("editable")))

    def _on_download_update(self, url: str, suggested_name: str) -> None:
        if not self._start_download_callback:
            return
        result = self._start_download_callback(url, suggested_name)
        if result.get("status") not in ("pending",):
            QMessageBox.warning(self, "Download", result.get("message", "Couldn't start this download."))

    def _on_dismiss_updates(self) -> None:
        """2026-08-08, Codex review: a successful dismissal used to leave the
        stale update rows visible until some *other* refresh happened to
        touch this game -- Backend.dismiss_updates() does call
        refresh_games() internally, which in the real, fully-wired app
        cascades back here via gamesChanged -> LibraryView.refresh() ->
        set_summary() before this method even returns (same-thread Qt
        signals run synchronously) -- but that made this view's own visible
        result depend entirely on an indirect signal chain elsewhere,
        fragile if this widget is ever reused outside that exact wiring.
        Updating the display directly here, from the real confirmed-
        successful result, is self-contained and correct regardless."""
        if not self._game_id:
            return
        result = self.backend.dismiss_updates(self._game_id)
        if result.get("status") != "ok":
            QMessageBox.warning(self, "Updates", result.get("message", "Couldn't dismiss updates."))
            return
        self.updates_list.clear()
        self.dismiss_updates_button.setEnabled(False)
        if self._summary is not None:
            self._summary["updates"] = []
            self._summary["has_updates"] = False

    def _refresh_timeline(self, events: list | None) -> None:
        """Own-system events only (see models.summarize_game()'s docstring
        on why: add_timeline_event() only ever appends to the caller's own
        entry). Real timestamps/types/details -- never fabricated."""
        self.timeline_list.clear()
        for event in events or []:
            timestamp = event.get("timestamp", "")
            event_type = event.get("type", "")
            detail = event.get("detail", "")
            text = f"{timestamp} — {event_type}"
            if detail:
                text += f": {detail}"
            self.timeline_list.addItem(QListWidgetItem(text))
        if not events:
            self.timeline_list.addItem(QListWidgetItem("No recorded history on this system."))

    def _on_launch(self) -> None:
        if not self._game_id or not self._has_local_variant:
            return
        try:
            result = self.backend.launch_game_direct(self._game_id, self._variant_id)
        except Exception as e:
            QMessageBox.warning(self, "Launch", f"Launch failed: {e}")
            return
        if not result.get("success"):
            QMessageBox.warning(self, "Launch", result.get("message", "Launch failed."))
        self._refresh_launch_readiness()

    def _on_rescan(self) -> None:
        """rescan_game() returns {"status": "ok"|"partial"|"error",
        "processed", "errors": <int count>} -- no "message" field exists.
        A "partial" or "error" status (errors > 0) must never be presented
        as an unqualified success."""
        if not self._game_id:
            return
        try:
            result = self.backend.rescan_game(self._game_id)
        except Exception as e:
            QMessageBox.warning(self, "Rescan", f"Rescan failed: {e}")
            return
        status = result.get("status")
        processed = result.get("processed", 0)
        errors = result.get("errors", 0)
        if status == "ok":
            QMessageBox.information(self, "Rescan", f"Rescan complete: {processed} item(s) processed.")
        elif status == "partial":
            QMessageBox.warning(self, "Rescan", f"Rescan finished with {errors} error(s) out of {processed} item(s).")
        else:
            QMessageBox.warning(self, "Rescan", f"Rescan failed ({errors} error(s)).")
        self._refresh_launch_readiness()

    def _on_rename(self) -> None:
        if not self._game_id or not self._summary:
            return
        new_name, ok = QInputDialog.getText(self, "Rename game", "New name:", text=self._summary["canonical_name"])
        if not ok or not new_name.strip():
            return
        result = self.backend.rename_game(self._game_id, new_name.strip())
        if result.get("status") != "ok":
            QMessageBox.warning(self, "Rename", result.get("message", "Rename failed."))

    def _on_open_in_manager(self) -> None:
        """find_manager_for_game() returns a LIST of
        {"manager_id","manager_name","kind"} candidates -- zero, one, or
        several. open_in_manager() is a fire-and-forget worker that only
        ever reports its real result via Backend.logLine (see
        _open_in_manager_worker()'s own body) -- so this never shows a
        success message itself; it only confirms the hand-off was
        *attempted* and lets the Activity Log/status bar show what actually
        happened once the worker logs it."""
        if not self._game_id:
            return
        try:
            candidates = self.backend.find_manager_for_game(self._game_id)
        except Exception as e:
            QMessageBox.warning(self, "Open in manager", f"Couldn't look up a manager: {e}")
            return
        if not candidates:
            QMessageBox.information(self, "Open in manager", "No manager is available for this game.")
            return
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            labels = [f"{c.get('manager_name', c.get('manager_id'))} ({c.get('kind', '?')})" for c in candidates]
            choice, ok = QInputDialog.getItem(self, "Open in manager", "Choose a manager:", labels, 0, False)
            if not ok:
                return
            chosen = candidates[labels.index(choice)]
        confirm = QMessageBox.question(
            self,
            "Open in manager",
            f"Open this game in {chosen.get('manager_name', chosen.get('manager_id'))}?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.backend.open_in_manager(self._game_id, chosen["manager_id"])

    def _on_apply_engine_override(self) -> None:
        if not self._game_id:
            return
        kind = self.engine_combo.currentData() or ""
        result = self.backend.set_engine_override(self._game_id, kind)
        if result.get("status") != "ok":
            QMessageBox.warning(self, "Engine", result.get("message", "Couldn't update engine identification."))
        else:
            self._refresh_engine_status()

    def _on_save_notes(self) -> None:
        if not self._game_id:
            return
        result = self.backend.save_notes(self._game_id, self.notes_edit.toPlainText())
        if result.get("status") != "ok":
            QMessageBox.warning(self, "Notes", result.get("message", "Couldn't save notes."))


class LibraryView(QWidget):
    def __init__(self, backend, parent=None, start_download_callback=None):
        super().__init__(parent)
        self.backend = backend
        self.model = GameListModel(own_system_uuid=backend.get_system_uuid())
        self.proxy = GameFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.sort(0)
        self._current_game_id: str | None = None

        root = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by name or platform...")
        self.search_box.setAccessibleName("Search library")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self.proxy.set_search_text)
        search_row.addWidget(self.search_box, 1)

        self.saved_search_combo = QComboBox()
        self.saved_search_combo.setAccessibleName("Saved searches")
        self.saved_search_combo.activated.connect(self._on_apply_saved_search)
        search_row.addWidget(self.saved_search_combo)

        save_search_button = QPushButton("Save search...")
        save_search_button.clicked.connect(self._on_save_search)
        search_row.addWidget(save_search_button)

        remove_search_button = QPushButton("Remove search")
        remove_search_button.clicked.connect(self._on_remove_saved_search)
        search_row.addWidget(remove_search_button)
        root.addLayout(search_row)

        bulk_row = QHBoxLayout()
        self.bulk_rescan_button = QPushButton("Rescan selected")
        self.bulk_rescan_button.setEnabled(False)
        self.bulk_rescan_button.clicked.connect(self._on_bulk_rescan)
        bulk_row.addWidget(self.bulk_rescan_button)

        self.bulk_export_button = QPushButton("Export selected")
        self.bulk_export_button.setEnabled(False)
        self.bulk_export_button.clicked.connect(self._on_bulk_export)
        bulk_row.addWidget(self.bulk_export_button)
        bulk_row.addStretch(1)
        root.addLayout(bulk_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list_view = QListView()
        self.list_view.setAccessibleName("Game library list")
        self.list_view.setModel(self.proxy)
        self.list_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_view.selectionModel().currentChanged.connect(self._on_selection_changed)
        self.list_view.selectionModel().selectionChanged.connect(self._on_selection_set_changed)
        splitter.addWidget(self.list_view)

        self.inspector = LibraryInspector(backend, start_download_callback=start_download_callback)
        splitter.addWidget(self.inspector)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter)

        self.refresh_saved_searches()
        self.refresh()

    def refresh(self) -> None:
        """Keep the current selection stable across a refresh where
        possible; if the previously-selected game genuinely no longer
        exists (removed, merged away, ...), clear the inspector honestly
        instead of silently keeping stale data on screen (2026-08-08,
        native UI depth: "keep selection stable ... or clear the inspector
        honestly when the selected game no longer exists")."""
        self.model.refresh(self.backend.games)

        if self._current_game_id:
            source_index = self.model.index_for_game_id(self._current_game_id)
            if source_index.isValid():
                proxy_index = self.proxy.mapFromSource(source_index)
                self.list_view.setCurrentIndex(proxy_index)
                self.inspector.set_summary(self.model.summary_for_game_id(self._current_game_id))
            else:
                self._current_game_id = None
                self.inspector.set_summary(None)

    def selected_game_ids(self) -> list[str]:
        ids = []
        for index in self.list_view.selectionModel().selectedIndexes():
            gid = index.data(GAME_ID_ROLE)
            if gid and gid not in ids:
                ids.append(gid)
        return ids

    def _on_selection_changed(self, current, _previous) -> None:
        if not current.isValid():
            self._current_game_id = None
            self.inspector.set_summary(None)
            return
        self._current_game_id = current.data(GAME_ID_ROLE)
        self.inspector.set_summary(current.data(SUMMARY_ROLE))

    def _on_selection_set_changed(self, _selected, _deselected) -> None:
        count = len(self.selected_game_ids())
        self.bulk_rescan_button.setEnabled(count > 0)
        self.bulk_export_button.setEnabled(count > 0)

    # ---- bulk actions -- only "rescan"/"export" are real per
    # Backend.bulk_action()'s own contract; "reconcile" isn't exposed here
    # since it returns merge *candidates* to review, not a completed action,
    # and this first pass doesn't yet have a review UI for it. --------------
    def _on_bulk_rescan(self) -> None:
        ids = self.selected_game_ids()
        if not ids:
            return
        confirm = QMessageBox.question(self, "Rescan selected", f"Rescan {len(ids)} selected game(s)?")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        result = self.backend.bulk_action(ids, "rescan")
        if result.get("status") in {"ok", "partial"}:
            errors = result.get("errors", 0)
            processed = result.get("processed", 0)
            if result.get("status") == "partial" or errors:
                QMessageBox.warning(self, "Rescan selected", f"Rescanned {processed} game(s), {errors} with errors.")
            else:
                QMessageBox.information(self, "Rescan selected", f"Rescanned {processed} game(s).")
        else:
            QMessageBox.warning(self, "Rescan selected", result.get("message", "Bulk rescan failed."))
        self.refresh()

    def _on_bulk_export(self) -> None:
        """2026-08-08, Codex review: this used to say "Exported N entrie(s)"
        after only *retrieving* the data, never writing it anywhere -- a
        real false-success claim. Now: a real native save-file dialog, then
        Backend.export_entries_to_file() (shared with the desktop shell)
        does the actual atomic write; cancellation and write failures are
        both reported honestly, distinctly from success."""
        ids = self.selected_game_ids()
        if not ids:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export selected games", "pythia-export.json", "JSON (*.json)")
        if not path:
            return  # cancelled -- not an error, no message needed
        result = self.backend.export_entries_to_file(ids, path)
        if result.get("status") != "ok":
            QMessageBox.warning(self, "Export selected", result.get("message", "Export failed."))
            return
        QMessageBox.information(self, "Export selected", f"Exported {result.get('count', 0)} entrie(s) to {result.get('path')}.")

    # ---- saved searches -- get_saved_searches()/save_search()/
    # remove_saved_search() (2026-08-08, native UI depth) ---------------------
    def refresh_saved_searches(self) -> None:
        self.saved_search_combo.clear()
        self.saved_search_combo.addItem("(saved searches)", None)
        try:
            searches = self.backend.get_saved_searches()
        except Exception:
            searches = []
        for s in searches:
            self.saved_search_combo.addItem(s.get("name", ""), s.get("search", ""))

    def _on_apply_saved_search(self, index: int) -> None:
        search = self.saved_search_combo.itemData(index)
        if search is not None:
            self.search_box.setText(search)

    def _on_save_search(self) -> None:
        name, ok = QInputDialog.getText(self, "Save search", "Name for this search:")
        if not ok or not name.strip():
            return
        # save_search() upserts by name (removes any existing entry with the
        # same name first) -- real update-on-duplicate-name behavior already
        # lives in Backend, not reimplemented here.
        result = self.backend.save_search(name.strip(), self.search_box.text())
        if not result or result.get("status") != "ok":
            QMessageBox.warning(self, "Save search", (result or {}).get("message", "The search was not saved."))
            return
        self.refresh_saved_searches()

    def _on_remove_saved_search(self) -> None:
        name = self.saved_search_combo.currentText()
        if not name or self.saved_search_combo.currentData() is None:
            return
        result = self.backend.remove_saved_search(name)
        if not result or result.get("status") != "ok":
            QMessageBox.warning(self, "Remove saved search", (result or {}).get("message", "The saved search was not removed."))
            return
        self.refresh_saved_searches()


class ReconcileDialog(QDialog):
    """Real reconcile-review workflow (2026-08-08, Codex review requirement:
    "call the real reconcile candidate API, display enough real fields to
    make a decision, require explicit selection and confirmation, then
    invoke the existing merge backend. Never describe a candidate as merged
    until merge returns success"). Uses reconciliation.find_merge_candidates()/
    merge() via Backend.refresh_merge_candidates()/merge_games() directly --
    NOT Backend.bulk_action(..., "reconcile"), whose candidate filter keys
    `left`/`right` while find_merge_candidates() actually returns `a`/`b`
    (a real, separate bug observed and reported in aisync/WORKLOG.md, not
    fixed here since this dialog doesn't go through that path at all).

    A candidate row shows "Merging..." (an honest in-progress state, never a
    claimed success) the instant Merge is confirmed, and is only removed
    (on success) or marked "Failed: ..." (on failure) once the real
    `mergeComplete` signal reports a result whose ids match what THIS dialog
    asked for -- a merge some other part of the app triggered is ignored."""

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.setWindowTitle("Find duplicate games")
        self.resize(720, 480)
        self._pending: dict[tuple, QListWidgetItem] = {}

        layout = QVBoxLayout(self)
        self.status_label = QLabel("Looking for duplicate games on this system...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.candidates_list = QListWidget()
        self.candidates_list.setAccessibleName("Duplicate game candidates")
        self.candidates_list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.candidates_list)

        action_row = QHBoxLayout()
        self.keep_a_button = QPushButton("Keep A, merge B into A")
        self.keep_a_button.setEnabled(False)
        self.keep_a_button.clicked.connect(lambda: self._on_merge_clicked("a"))
        action_row.addWidget(self.keep_a_button)
        self.keep_b_button = QPushButton("Keep B, merge A into B")
        self.keep_b_button.setEnabled(False)
        self.keep_b_button.clicked.connect(lambda: self._on_merge_clicked("b"))
        action_row.addWidget(self.keep_b_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.backend.mergeCandidatesReady.connect(self._on_candidates_ready)
        self.backend.mergeCandidatesFailed.connect(self._on_candidates_failed)
        self.backend.mergeComplete.connect(self._on_merge_complete)
        self.backend.refresh_merge_candidates()

    def closeEvent(self, event) -> None:
        try:
            self.backend.mergeCandidatesReady.disconnect(self._on_candidates_ready)
            self.backend.mergeCandidatesFailed.disconnect(self._on_candidates_failed)
            self.backend.mergeComplete.disconnect(self._on_merge_complete)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)

    def _describe_pair(self, c: dict) -> str:
        a, b = c.get("a") or {}, c.get("b") or {}

        def _fmt(entry):
            return f"{entry.get('name')} [{entry.get('engine_kind') or '?'}, {entry.get('version_count', 0)} version(s)] — {entry.get('path') or 'no path'}"

        return f"[{c.get('score', 0):.2f}] A: {_fmt(a)}  vs  B: {_fmt(b)}"

    def _on_candidates_ready(self, candidates) -> None:
        self.candidates_list.clear()
        if not candidates:
            self.status_label.setText("No likely duplicates found on this system.")
            return
        self.status_label.setText(f"{len(candidates)} possible duplicate pair(s) — review before merging:")
        for c in candidates:
            item = QListWidgetItem(self._describe_pair(c))
            item.setData(Qt.ItemDataRole.UserRole, c)
            self.candidates_list.addItem(item)

    def _on_candidates_failed(self, message: str) -> None:
        self.candidates_list.clear()
        self.status_label.setText(message or "Could not find duplicate games.")

    def _on_selection_changed(self, current, _previous) -> None:
        enabled = current is not None
        self.keep_a_button.setEnabled(enabled)
        self.keep_b_button.setEnabled(enabled)

    def _on_merge_clicked(self, keep: str) -> None:
        item = self.candidates_list.currentItem()
        if item is None:
            return
        c = item.data(Qt.ItemDataRole.UserRole)
        a, b = c.get("a") or {}, c.get("b") or {}
        survivor, retired = (a, b) if keep == "a" else (b, a)
        confirm = QMessageBox.question(
            self,
            "Confirm merge",
            f"Merge '{retired.get('name')}' into '{survivor.get('name')}'? "
            "The retired game's record will be deleted after its data is folded in.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        surviving_id, retired_id = survivor.get("game_id"), retired.get("game_id")
        self._pending[(surviving_id, retired_id)] = item
        item.setText(f"Merging: {retired.get('name')} -> {survivor.get('name')} ...")
        self.keep_a_button.setEnabled(False)
        self.keep_b_button.setEnabled(False)
        self.backend.merge_games(surviving_id, retired_id)

    def _on_merge_complete(self, result) -> None:
        if not isinstance(result, dict):
            return
        key = (result.get("game_id"), result.get("retired_game_id"))
        item = self._pending.pop(key, None)
        if item is None:
            return  # a merge this dialog didn't initiate
        if result.get("status") == "ok":
            self.candidates_list.takeItem(self.candidates_list.row(item))
        else:
            item.setText(f"Failed: {result.get('message', 'unknown error')}")


class MainWindow(QMainWindow):
    installerUnavailableRequested = Signal(str, str)

    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.setWindowTitle("Pythia")
        self.setAccessibleName("Pythia main window")
        self.installerUnavailableRequested.connect(self._show_installer_unavailable)

        self._settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.tabs = QTabWidget()
        # Constructed before LibraryView so its real start_download() method
        # can be handed to LibraryInspector's update rows -- one real
        # download-tracking surface, not a second one duplicated per view.
        self.downloads_view = DownloadsView(backend)
        self.library_view = LibraryView(backend, start_download_callback=self.downloads_view.start_download)
        self.tabs.addTab(self.library_view, "Library")
        self.tabs.addTab(self.downloads_view, "Downloads")
        self.plugins_view = PluginsView(backend)
        self.tabs.addTab(self.plugins_view, "Plugins")
        self.settings_view = SettingsView(backend, switch_to_plugins_tab=lambda: self.tabs.setCurrentWidget(self.plugins_view))
        self.tabs.addTab(self.settings_view, "Settings")
        self.sync_view = SyncView(backend)
        self.tabs.addTab(self.sync_view, "Devices && Sync")

        # 2026-08-08, native UI depth: upgraded from a plain log textbox to a
        # structured jobs/events list -- each real backend signal appends one
        # tagged, timestamped entry (the timestamp is genuinely "when this UI
        # received the event", not fabricated progress). No percentages are
        # ever synthesized -- the backend doesn't report any for these
        # operations, so none are shown.
        activity_page = QWidget()
        activity_layout = QVBoxLayout(activity_page)
        process_group = QGroupBox("Launched game processes")
        process_layout = QVBoxLayout(process_group)
        self.process_tree = QTreeWidget()
        self.process_tree.setHeaderLabels(["PID", "State", "Exit", "Started", "Command"])
        self.process_tree.setRootIsDecorated(False)
        self.process_tree.setAccessibleName("Pythia-launched game processes")
        self.process_tree.currentItemChanged.connect(
            lambda current, _previous: self.stop_process_button.setEnabled(
                current is not None and current.text(1) == "Running"
            )
        )
        process_layout.addWidget(self.process_tree)
        process_actions = QHBoxLayout()
        self.stop_process_button = QPushButton("Stop selected")
        self.stop_process_button.clicked.connect(self._stop_selected_process)
        self.clear_process_button = QPushButton("Clear finished")
        self.clear_process_button.clicked.connect(self._clear_finished_processes)
        self.refresh_process_button = QPushButton("Refresh")
        self.refresh_process_button.clicked.connect(self._refresh_processes)
        process_actions.addWidget(self.stop_process_button)
        process_actions.addWidget(self.clear_process_button)
        process_actions.addStretch(1)
        process_actions.addWidget(self.refresh_process_button)
        process_layout.addLayout(process_actions)
        activity_layout.addWidget(process_group)
        activity_layout.addWidget(QLabel("Application events"))
        self.activity_list = QListWidget()
        self.activity_list.setAccessibleName("Activity")
        try:
            for line in backend.get_log_contents().splitlines():
                if line.strip():
                    self.activity_list.addItem(QListWidgetItem(line))
        except Exception:
            pass
        activity_layout.addWidget(self.activity_list, 1)
        self.tabs.addTab(activity_page, "Activity")

        self._process_timer = QTimer(self)
        self._process_timer.setInterval(2000)
        self._process_timer.timeout.connect(self._refresh_processes)
        self._process_timer.start()
        self._refresh_processes()

        self.setCentralWidget(self.tabs)

        self._build_menu_and_toolbar()
        self._connect_backend_signals()
        self._restore_geometry()

    def _refresh_processes(self) -> None:
        rows = self.backend.get_monitored_launches(True)
        selected = self.process_tree.currentItem()
        selected_pid = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        self.process_tree.clear()
        running = False
        for row in rows or []:
            pid = int(row.get("pid", 0))
            is_running = bool(row.get("running"))
            running = running or is_running
            started = row.get("started_at")
            started_text = datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S") if started else ""
            command = " ".join(str(part) for part in (row.get("command") or []))
            item = QTreeWidgetItem([
                str(pid), "Running" if is_running else "Finished",
                "" if is_running else str(row.get("exit_code", "")), started_text, command,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, pid)
            self.process_tree.addTopLevelItem(item)
            if pid == selected_pid:
                self.process_tree.setCurrentItem(item)
        self.stop_process_button.setEnabled(running and self.process_tree.currentItem() is not None)
        self.clear_process_button.setEnabled(any(not row.get("running") for row in (rows or [])))

    def _stop_selected_process(self) -> None:
        item = self.process_tree.currentItem()
        if item is None:
            return
        result = self.backend.stop_monitored_launch(int(item.data(0, Qt.ItemDataRole.UserRole)))
        if result.get("status") not in ("ok", "not_running"):
            QMessageBox.warning(self, "Stop process", result.get("message", "Could not stop the selected process."))
        self._refresh_processes()

    def _clear_finished_processes(self) -> None:
        result = self.backend.clear_finished_launches()
        self.status_bar.showMessage(result.get("message", "Finished launch records cleared."), 5000)
        self._refresh_processes()

    # ---- shell chrome -------------------------------------------------------
    def _build_menu_and_toolbar(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        add_folder_action = QAction("&Add Folder...", self)
        add_folder_action.setShortcut(QKeySequence("Ctrl+O"))
        add_folder_action.triggered.connect(self._on_add_folder)
        file_menu.addAction(add_folder_action)

        rescan_action = QAction("&Rescan Library", self)
        rescan_action.triggered.connect(self._on_rescan_library)
        file_menu.addAction(rescan_action)

        duplicates_action = QAction("Find &Duplicate Games...", self)
        duplicates_action.triggered.connect(self._on_find_duplicates)
        file_menu.addAction(duplicates_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&View")
        refresh_action = QAction("&Refresh", self)
        refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_action.triggered.connect(self.library_view.refresh)
        view_menu.addAction(refresh_action)

        find_action = QAction("&Find", self)
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(lambda: self.library_view.search_box.setFocus())
        view_menu.addAction(find_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About Pythia", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.addAction(add_folder_action)
        toolbar.addAction(rescan_action)
        toolbar.addAction(refresh_action)
        self.addToolBar(toolbar)

    def _connect_backend_signals(self) -> None:
        self.backend.gamesChanged.connect(self.library_view.refresh)
        self.backend.workerError.connect(self._on_worker_error)
        self.backend.logLine.connect(self._on_log_line)
        self.backend.onboardComplete.connect(self._on_onboard_complete)
        self.backend.scanComplete.connect(self._on_scan_complete)
        self.backend.decisionNeeded.connect(self._on_decision_needed)
        self.backend.engineEnableComplete.connect(self._on_engine_enable_complete)
        self.backend.mergeComplete.connect(self._on_merge_complete)
        self.backend.updateCheckComplete.connect(self._on_update_check_complete)
        self.backend.automatedDownloadProgress.connect(self._on_automated_download_progress)

    def _restore_geometry(self) -> None:
        geometry = self._settings.value("main_window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1100, 700)
        state = self._settings.value("main_window/state")
        if state:
            self.restoreState(state)

    def closeEvent(self, event) -> None:
        self._settings.setValue("main_window/geometry", self.saveGeometry())
        self._settings.setValue("main_window/state", self.saveState())
        super().closeEvent(event)

    # ---- backend signal handlers -- each real signal appends one
    # structured, timestamped entry to the Activity view (see its own
    # construction comment: no fabricated progress, ever). ---------------------
    def _append_activity(self, text: str) -> None:
        import datetime

        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.activity_list.addItem(QListWidgetItem(f"[{timestamp}] {text}"))

    def _on_worker_error(self, message: str) -> None:
        self.status_bar.showMessage(f"Error: {message}", 8000)
        self._append_activity(f"[error] {message}")

    def _on_log_line(self, line: str) -> None:
        self._append_activity(line)

    def _on_onboard_complete(self, result: dict) -> None:
        status = result.get("status")
        if status == "ok":
            self.status_bar.showMessage(f"Onboarded: game {result.get('game_id')}", 5000)
            self._append_activity(f"[onboard] Onboarded game {result.get('game_id')}.")
        elif status == "cancelled":
            self.status_bar.showMessage("Onboarding cancelled.", 5000)
            self._append_activity("[onboard] Cancelled by the user.")
        else:
            self.status_bar.showMessage(f"Onboarding failed: {result.get('message', '')}", 8000)
            self._append_activity(f"[onboard] Failed: {result.get('message', '')}")

    def _on_scan_complete(self, results) -> None:
        ok_count = sum(1 for r in results if r.get("status") == "ok")
        self.status_bar.showMessage(f"Scan complete: {ok_count}/{len(results)} identified", 5000)
        self._append_activity(f"[scan] {ok_count}/{len(results)} item(s) identified.")

    def _on_engine_enable_complete(self, result: dict) -> None:
        message = result.get("message", "")
        if result.get("status") == "ok":
            self.status_bar.showMessage(f"Engine enablement: {message}", 6000)
            self._append_activity(f"[engine] {message}")
        else:
            self.status_bar.showMessage(f"Engine enablement failed: {message}", 8000)
            self._append_activity(f"[engine] Failed: {message}")
        self.library_view.refresh()

    def _on_merge_complete(self, result) -> None:
        """reconciliation.merge() returns {"status": "ok", "game_id",
        "retired_game_id"} on success (no "message" key there) or
        {"status": "error", "message"} on failure -- handled separately
        rather than assuming a "message" key always exists."""
        if isinstance(result, dict) and result.get("status") == "ok":
            self._append_activity(f"[merge] Merged {result.get('retired_game_id')} into {result.get('game_id')}.")
        elif isinstance(result, dict):
            self._append_activity(f"[merge] Failed: {result.get('message', 'unknown error')}")
        else:
            self._append_activity("[merge] Complete.")
        self.library_view.refresh()

    def _on_update_check_complete(self, result) -> None:
        if isinstance(result, dict):
            checked = result.get("checked", 0)
            updated = result.get("updated", 0)
            self.status_bar.showMessage(f"Update check: {updated}/{checked} game(s) have an update.", 6000)
            self._append_activity(f"[updates] Checked {checked}, {updated} with an update available.")
        else:
            self._append_activity("[updates] Update check complete.")
        self.library_view.refresh()

    def _on_automated_download_progress(self, event: dict) -> None:
        self.downloads_view.on_progress(event)
        if event.get("status") not in ("running",):  # avoid spamming Activity every chunk
            phase = event.get("phase")
            label = f"[download{'/extract' if phase == 'extract' else ''}]"
            self._append_activity(f"{label} {event.get('filename', '?')}: {event.get('status')} {event.get('message', '')}".rstrip())

    def _on_decision_needed(self, preview: dict) -> None:
        """A real onboarding-ambiguity prompt -- see pythia.onboarding's
        find_candidates()/onboard() docstrings for what "certain"/"suggested"
        and the decision dict's "action"/"game_id" keys mean.

        2026-08-08, Codex correctness review: two real bugs fixed here.
        (1) Cancel used to silently resolve as {"action": "new"} -- passing
        decision=None to resolveDecision() is NOT a safe "do nothing" either,
        since onboarding.onboard()'s own "action is None" branch means
        "auto-resolve automatically" (certain match -> link, else mint new),
        not "stay unresolved". A real {"action": "cancel"} was added to
        onboarding.onboard() (see its own docstring) specifically so Cancel
        here can mean "nothing was onboarded", including unblocking a live
        onboard's worker thread, which is genuinely waiting on this
        decision and must not hang forever. (2) `certain` was previously
        dropped entirely -- scan-triggered decisionNeeded (unlike the live
        onboard path) can carry both a `certain` match and `suggested`
        alternatives at once (pythia.onboarding.find_candidates()'s "certain"
        is not always None for that trigger); it's now offered as the
        default, clearly-labeled first choice."""
        candidates = preview.get("candidates", {})
        certain = candidates.get("certain")
        suggested = candidates.get("suggested") or []

        # option_targets is index-aligned with options: None marks "Add as a
        # new game", anything else is the candidate entry to link to. Simpler
        # and less error-prone than deriving positions arithmetically from
        # separately-built lists (an earlier version of this got that math
        # wrong -- caught before commit by the targeted decision tests below).
        options: list[str] = []
        option_targets: list[dict | None] = []
        if certain is not None:
            options.append(f"Confirmed match: {certain.get('canonical_name', certain.get('game_id'))}")
            option_targets.append(certain)
        options.append("Add as a new game")
        option_targets.append(None)
        for c in suggested:
            options.append(f"Link to existing: {c.get('canonical_name', c.get('game_id'))}")
            option_targets.append(c)

        choice, ok = QInputDialog.getItem(
            self,
            "Confirm this install",
            f"'{preview.get('name', preview.get('path'))}' -- how should this be tracked?",
            options,
            0,
            False,
        )
        if not ok:
            decision = {"action": "cancel"}
        else:
            target = option_targets[options.index(choice)]
            decision = {"action": "new"} if target is None else {"action": "link", "game_id": target.get("game_id")}
        self.backend.resolveDecision(preview.get("path", ""), decision)

    # ---- toolbar/menu actions -------------------------------------------------
    def _on_add_folder(self) -> None:
        path = self.backend.pick_folder()
        if not path:
            return
        self.backend.start_onboard(path)
        self.status_bar.showMessage(f"Onboarding '{path}'...", 3000)

    def _on_rescan_library(self) -> None:
        path = self.backend.pick_folder()
        if not path:
            return
        self.backend.start_scan(path)
        self.status_bar.showMessage(f"Scanning '{path}'...", 3000)

    def _on_find_duplicates(self) -> None:
        dialog = ReconcileDialog(self.backend, self)
        dialog.exec()
        self.library_view.refresh()

    def _on_about(self) -> None:
        from pythia import settings as pythia_settings

        QMessageBox.about(self, "About Pythia", f"Pythia {pythia_settings.VERSION}\nNative desktop interface.")

    # ---- app-singleton installer interface ---------------------------------
    def _show_installer_unavailable(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def show_installer_window(self, installers, service=None, appid=None, installation_kind=None) -> None:
        count = len(installers or [])
        kind = getattr(installation_kind, "value", None) or str(installation_kind or "installation")
        service_name = getattr(service, "name", None) or "the selected service"
        self.installerUnavailableRequested.emit(
            "Installer unavailable",
            f"Pythia found {count} {kind} option(s) from {service_name}, but the legacy installer engine does not yet have a Qt workflow. Nothing was installed.",
        )

    def show_pythia_installer_window(self, game_slug: str) -> None:
        self.installerUnavailableRequested.emit(
            "Installer unavailable",
            f"The installer workflow for '{game_slug}' is not available in the Qt interface yet. Nothing was installed.",
        )
