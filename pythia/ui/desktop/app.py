"""
Desktop UI: master-detail rebuild (Milestone 5, 2026-08-03) replacing Milestone 2's
bare QListWidget+QPlainTextEdit shell. Layout and design tokens translated from the
validated v4 mockup (see pythia/ui/theme.py for the token module) -- a rail, a
search-filtered game list, and a detail pane with Versions/Notes/History tabs, a
native-style menu bar, and a persistent status bar.

Honesty note: the mockup's "11.2 available"/"Update queued" status rows describe a
downloader-sourced available_updates feed with no real implementation behind it yet
(see the plan's Ownership/watchlist section) -- this build only shows status derived
from data Pythia actually has: "Configuration invalid" when no engine plugin
identified the install (kind == "unknown"/None), otherwise blank. The History tab
shows an honest placeholder, not fabricated rows, since no per-game event log exists
yet. The mockup's 3-step density toggle is also not built this pass -- scoped out,
not forgotten.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pythia.ui import theme
from pythia.ui.desktop.reconciliation import ReconciliationDialog
from pythia.ui.desktop.settings import SettingsDialog


def _first_variant(entry: dict):
    for group in entry.get("versions", []):
        variants = group.get("variants", [])
        if variants:
            return variants[0]
    return None


def _representative_kind(entry: dict):
    variant = _first_variant(entry)
    return (variant.get("source") or {}).get("kind") if variant else None


def _badge_label(text: str, kind: str, size: int) -> QLabel:
    start, end = theme.badge_gradient(kind)
    badge = QLabel(text[:1].upper() if text else "?")
    badge.setFixedSize(size, size)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setStyleSheet(
        f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {start}, stop:1 {end});"
        f"border-radius: {size // 4}px; color: rgba(255,255,255,0.92);"
        f"font-family: {theme.SERIF_FONT}; font-size: {size // 2}px;"
    )
    return badge


class GameViewModel:
    """Flattens one backend.games row (game_id + this system's + every synced
    system's entries) into what the list/detail panes render -- rebuilt fresh on
    every refresh rather than cached, since it's cheap and the underlying data can
    change at any time (a scan finishing, a merge, a notes edit)."""

    def __init__(self, game_id: int, entries: list):
        self.game_id = game_id
        self.entries = entries
        self.primary_entry = entries[0]
        self.name = self.primary_entry.get("canonical_name") or "?"
        self.kind = _representative_kind(self.primary_entry)
        self.versions = self.primary_entry.get("versions", [])
        self.status = "critical" if self.kind in (None, "unknown") else "ok"
        self.status_text = "Configuration invalid" if self.status == "critical" else ""
        self.notes = self.primary_entry.get("notes", "")


class MainWindow(QMainWindow):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.setWindowTitle("Pythia")
        self.resize(1180, 720)
        self.setStyleSheet(theme.build_stylesheet())

        self._models = []
        self._visible_models = []
        self._selected_model = None
        self._manager_candidates = []

        self._build_menu_bar()
        self._build_central()
        self._build_status_bar()

        backend.logLine.connect(self._on_log_line)
        backend.gamesChanged.connect(self._refresh_models)
        backend.decisionNeeded.connect(self._on_decision_needed)
        backend.scanComplete.connect(self._on_scan_complete)
        self._refresh_models()

    # ---- chrome -------------------------------------------------------------
    def _build_menu_bar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        scan_action = file_menu.addAction("Scan Folder...")
        scan_action.triggered.connect(self._scan_folder)

        library_menu = menubar.addMenu("Library")
        dup_action = library_menu.addAction("Review Duplicates...")
        dup_action.triggered.connect(self._review_duplicates)

        menubar.addMenu("Downloads")
        menubar.addMenu("View")
        menubar.addMenu("Help")

    def _build_status_bar(self):
        status_bar = self.statusBar()
        self._status_label = QLabel("Idle")
        status_bar.addWidget(self._status_label)
        self._count_label = QLabel("")
        status_bar.addPermanentWidget(self._count_label)

    def _build_central(self):
        splitter = QSplitter()
        self.setCentralWidget(splitter)
        splitter.addWidget(self._build_rail())
        splitter.addWidget(self._build_list_panel())
        splitter.addWidget(self._build_detail_panel())
        splitter.setSizes([200, 300, 680])

    def _build_rail(self):
        rail = QWidget()
        rail.setObjectName("rail")
        rail.setFixedWidth(200)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(10)

        wordmark = QLabel("PYTHIA")
        wordmark.setStyleSheet(f"font-family: {theme.SERIF_FONT}; font-size: 18px; letter-spacing: 1px;")
        layout.addWidget(wordmark)
        layout.addSpacing(10)

        self._nav_list = QListWidget()
        self._nav_list.setObjectName("navList")
        self._nav_list.addItem("Library")
        self._nav_list.addItem("Duplicates")
        self._nav_list.addItem("Settings")
        self._nav_list.setCurrentRow(0)
        self._nav_list.setFixedHeight(112)
        self._nav_list.setFrameShape(QListWidget.Shape.NoFrame)
        self._nav_list.itemClicked.connect(self._on_nav_clicked)
        layout.addWidget(self._nav_list)
        layout.addStretch()

        self._rail_status = QLabel("")
        self._rail_status.setWordWrap(True)
        self._rail_status.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 10.5px; color: {theme.COLORS['text_faint']};")
        layout.addWidget(self._rail_status)
        return rail

    def _build_list_panel(self):
        panel = QWidget()
        panel.setFixedWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        search_wrap = QWidget()
        search_wrap.setObjectName("listSearch")
        search_layout = QVBoxLayout(search_wrap)
        search_layout.setContentsMargins(12, 12, 12, 12)
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Filter...")
        self._search_box.textChanged.connect(self._apply_filter)
        search_layout.addWidget(self._search_box)
        layout.addWidget(search_wrap)

        self._game_list = QListWidget()
        self._game_list.setObjectName("gameList")
        self._game_list.setFrameShape(QListWidget.Shape.NoFrame)
        self._game_list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self._game_list)
        return panel

    def _build_detail_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QWidget()
        head.setObjectName("detailHead")
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(24, 20, 24, 16)
        head_layout.setSpacing(16)

        self._detail_badge = _badge_label("", None, 48)
        head_layout.addWidget(self._detail_badge)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self._detail_title = QLabel("Select a game")
        self._detail_title.setStyleSheet(f"font-family: {theme.SERIF_FONT}; font-size: 21px;")
        title_col.addWidget(self._detail_title)
        self._detail_sub = QLabel("")
        self._detail_sub.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 11px; color: {theme.COLORS['text_faint']};")
        title_col.addWidget(self._detail_sub)
        head_layout.addLayout(title_col)
        head_layout.addStretch()

        self._open_button = QPushButton("Open")
        self._open_button.setObjectName("primary")
        self._open_button.setVisible(False)
        self._open_button.clicked.connect(self._open_in_manager)
        head_layout.addWidget(self._open_button)
        layout.addWidget(head)

        self._tabs = QTabWidget()
        self._versions_list = QListWidget()
        self._versions_list.setFrameShape(QListWidget.Shape.NoFrame)
        self._tabs.addTab(self._versions_list, "Versions")

        notes_wrap = QWidget()
        notes_layout = QVBoxLayout(notes_wrap)
        notes_layout.setContentsMargins(16, 16, 16, 16)
        self._notes_edit = QTextEdit()
        self._notes_edit.setPlaceholderText("No notes yet.")
        notes_layout.addWidget(self._notes_edit)
        save_notes_btn = QPushButton("Save Notes")
        save_notes_btn.setObjectName("primary")
        save_notes_btn.clicked.connect(self._save_notes)
        notes_layout.addWidget(save_notes_btn)
        self._tabs.addTab(notes_wrap, "Notes")

        self._history_list = QListWidget()
        self._history_list.setFrameShape(QListWidget.Shape.NoFrame)
        self._tabs.addTab(self._history_list, "History")

        layout.addWidget(self._tabs)
        return panel

    # ---- data ------------------------------------------------------------------
    def _refresh_models(self):
        games = self.backend.games
        self._models = [GameViewModel(g["game_id"], g["entries"]) for g in games]
        self._models.sort(key=lambda m: m.name.lower())
        self._count_label.setText(f"{len(self._models)} games tracked")
        self._rail_status.setText(f"{len(self._models)} games tracked")
        self._apply_filter()

    def _apply_filter(self):
        query = self._search_box.text().strip().lower()
        self._visible_models = [m for m in self._models if query in m.name.lower()]

        selected_id = self._selected_model.game_id if self._selected_model else None
        self._game_list.blockSignals(True)
        self._game_list.clear()
        select_row = -1
        for i, model in enumerate(self._visible_models):
            item = QListWidgetItem()
            self._game_list.addItem(item)
            row_widget = self._build_list_row(model)
            self._game_list.setItemWidget(item, row_widget)
            # Same instance for both display and sizing (2026-08-03 fix -- building
            # a second, separate throwaway widget just to measure sizeHint() gave a
            # size that didn't reliably match what was actually displayed, clipping
            # the 2-3 stacked labels inside each row).
            row_widget.adjustSize()
            item.setSizeHint(row_widget.sizeHint())
            if model.game_id == selected_id:
                select_row = i
        self._game_list.blockSignals(False)

        if select_row >= 0:
            self._game_list.setCurrentRow(select_row)
        elif self._visible_models:
            self._game_list.setCurrentRow(0)
        else:
            self._show_detail(None)

    def _build_list_row(self, model: GameViewModel) -> QWidget:
        row = QWidget()
        # Explicit minimum height (2026-08-03), on top of the sizeHint fix above --
        # up to 3 stacked labels (name/subtitle/status) need real room, not just
        # whatever a tight sizeHint computation happens to produce.
        row.setMinimumHeight(60)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)
        layout.addWidget(_badge_label(model.name, model.kind, 30))

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        name_label = QLabel(model.name)
        name_label.setStyleSheet("font-weight: 600; font-size: 13.5px;")
        text_col.addWidget(name_label)
        sub_label = QLabel(f"{model.kind or 'unknown'} · {len(model.versions)} version(s)")
        sub_label.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 10.5px; color: {theme.COLORS['text_faint']};")
        text_col.addWidget(sub_label)
        if model.status_text:
            status_label = QLabel(model.status_text)
            status_label.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {theme.status_color(model.status)};")
            text_col.addWidget(status_label)
        layout.addLayout(text_col)
        layout.addStretch()
        return row

    def _on_selection_changed(self, row: int):
        if 0 <= row < len(self._visible_models):
            self._show_detail(self._visible_models[row])
        else:
            self._show_detail(None)

    def _show_detail(self, model):
        self._selected_model = model
        if model is None:
            self._detail_title.setText("Select a game")
            self._detail_sub.setText("")
            self._versions_list.clear()
            self._notes_edit.clear()
            self._history_list.clear()
            self._open_button.setVisible(False)
            return

        self._manager_candidates = self.backend.find_manager_for_game(model.game_id)
        self._open_button.setVisible(bool(self._manager_candidates))
        if self._manager_candidates:
            self._open_button.setText(f"Open in {self._manager_candidates[0]['manager_name']}")

        self._detail_badge.setText(model.name[:1].upper())
        start, end = theme.badge_gradient(model.kind)
        self._detail_badge.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {start}, stop:1 {end});"
            f"border-radius: 12px; color: rgba(255,255,255,0.92);"
            f"font-family: {theme.SERIF_FONT}; font-size: 22px;"
        )
        self._detail_title.setText(model.name)
        self._detail_sub.setText(f"game_id {model.game_id} · {model.kind or 'unknown'}")

        self._versions_list.clear()
        for group in model.versions:
            header_item = QListWidgetItem()
            header_widget = self._build_version_header(group)
            self._versions_list.addItem(header_item)
            self._versions_list.setItemWidget(header_item, header_widget)
            header_widget.adjustSize()
            header_item.setSizeHint(header_widget.sizeHint())

            for variant in group.get("variants", []):
                variant_item = QListWidgetItem()
                variant_widget = self._build_variant_row(variant)
                self._versions_list.addItem(variant_item)
                self._versions_list.setItemWidget(variant_item, variant_widget)
                variant_widget.adjustSize()
                variant_item.setSizeHint(variant_widget.sizeHint())

        self._notes_edit.setPlainText(model.notes)

        self._history_list.clear()
        self._history_list.addItem("No history tracked yet -- Pythia doesn't keep a per-game event log yet.")

    def _build_version_header(self, group: dict) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 8, 8, 4)
        version_label = QLabel(group.get("version") or "?")
        version_label.setStyleSheet(f"font-family: {theme.SERIF_FONT}; font-size: 16px;")
        layout.addWidget(version_label)
        count = len(group.get("variants", []))
        count_label = QLabel(f"{count} variant(s)")
        count_label.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 10.5px; color: {theme.COLORS['text_faint']};")
        layout.addWidget(count_label)
        layout.addStretch()
        return row

    def _build_variant_row(self, variant: dict) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(20, 4, 8, 4)
        layout.setSpacing(10)

        mods = variant.get("mods") or []
        if mods:
            mod_text = ", ".join(m.get("name", "?") for m in mods)
            mod_label = QLabel(mod_text)
            mod_label.setStyleSheet(
                f"font-family: {theme.MONO_FONT}; font-size: 10.5px; color: {theme.COLORS['accent']};"
                f"background: {theme.COLORS['accent_soft']}; border-radius: 100px; padding: 3px 9px;"
            )
        else:
            mod_label = QLabel("vanilla")
            mod_label.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 10.5px; color: {theme.COLORS['text_faint']};")
        layout.addWidget(mod_label)

        language = variant.get("language")
        if language:
            lang_label = QLabel(language)
            lang_label.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 10.5px; color: {theme.COLORS['text_faint']};")
            layout.addWidget(lang_label)

        path_label = QLabel(variant.get("path") or "")
        path_label.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 11px; color: {theme.COLORS['text_muted']};")
        layout.addWidget(path_label, stretch=1)

        platforms = ", ".join(variant.get("platforms", [])) or "?"
        platforms_label = QLabel(platforms)
        platforms_label.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {theme.COLORS['ok']};")
        layout.addWidget(platforms_label)
        return row

    # ---- actions -----------------------------------------------------------
    def _scan_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to scan")
        if path:
            self._status_label.setText(f"Scanning '{path}' ...")
            self.backend.start_scan(path)

    def _review_duplicates(self):
        dialog = ReconciliationDialog(self.backend, self)
        dialog.exec()

    def _open_settings(self):
        dialog = SettingsDialog(self.backend, self)
        dialog.exec()

    def _on_nav_clicked(self, item):
        # "Library" is the persistent main view; "Duplicates"/"Settings" are modal
        # actions -- clicking them opens the relevant dialog and then snaps
        # selection back to Library rather than leaving the rail on a "page" that
        # doesn't actually exist in this pane.
        text = item.text()
        if text == "Duplicates":
            self._review_duplicates()
        elif text == "Settings":
            self._open_settings()
        self._nav_list.setCurrentRow(0)

    def _save_notes(self):
        if self._selected_model is None:
            return
        self.backend.save_notes(self._selected_model.game_id, self._notes_edit.toPlainText())
        self._status_label.setText("Notes saved.")

    def _open_in_manager(self):
        # Pythia never launches a game's own executable -- this hands off to a
        # third-party tool (Steam client, etc.) that runs it, same boundary as
        # cli.py's `open` command. Real side effect (launches an external
        # application), so it's confirmed first, same as the CLI does.
        if self._selected_model is None or not self._manager_candidates:
            return
        candidate = self._manager_candidates[0]
        answer = QMessageBox.question(
            self,
            "Open game",
            f"This will hand off to {candidate['manager_name']} to launch "
            f"“{self._selected_model.name}”. Proceed?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.backend.open_in_manager(self._selected_model.game_id, candidate["manager_id"])
        self._status_label.setText(f"Handing off to {candidate['manager_name']}...")

    def _on_log_line(self, line: str):
        self._status_label.setText(line)

    def _on_scan_complete(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        self._status_label.setText(f"Scan complete: {ok}/{len(results)} onboarded")

    def _on_decision_needed(self, preview):
        # Minimal stand-in: log it and default to "new" rather than blocking silently.
        # A real decision dialog (mirroring big-picture's DecisionScreen.qml) is
        # further Milestone 5 scope.
        self._status_label.setText(
            f"Ambiguous match for '{preview.get('name')}' -- defaulting to new game "
            "(desktop decision dialog not built yet)."
        )
        self.backend.resolveDecision(preview["path"], {"action": "new"})


def run(app, backend) -> int:
    window = MainWindow(backend)
    window.show()
    return app.exec()
