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

import threading
from pathlib import Path

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
    QSizePolicy,
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


def _pill_label(text: str, kind: str = "neutral") -> QLabel:
    """Rounded status-pill, matching the mockup's `.tag`/`.state-pill` look --
    `kind` picks the color (ok/pending/critical/neutral), same three-way
    status vocabulary used everywhere else (never a fourth ad hoc color)."""
    colors = {
        "ok": (theme.COLORS["ok"], theme.COLORS["ok_soft"]),
        "pending": (theme.COLORS["pending"], theme.COLORS["pending_soft"]),
        "critical": (theme.COLORS["critical"], theme.COLORS["critical_soft"]),
        "neutral": (theme.COLORS["text_muted"], theme.COLORS["surface_raised"]),
    }
    fg, bg = colors.get(kind, colors["neutral"])
    label = QLabel(text)
    label.setStyleSheet(
        f"color: {fg}; background: {bg}; border-radius: 10px; padding: 3px 10px; "
        f"font-size: 10.5px; font-weight: 600;"
    )
    return label


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _elide_label(label: QLabel):
    """Let a label carrying unpredictable-length real text (a game name, a
    filesystem path) shrink below its natural text width instead of forcing
    its whole row -- and every other row in the same list -- wider than the
    panel (the real bug behind the clipped "Set direct..." button and the
    list panel's stray horizontal scrollbar: a QLabel's minimumSizeHint is
    its full text width by default, which a stretch factor alone doesn't
    override)."""
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    # Hard-clips with no ellipsis when squeezed -- a real, honest limitation,
    # not hidden: the full text is still one hover away via the tooltip.
    if label.text():
        label.setToolTip(label.text())


class GameViewModel:
    """Flattens one backend.games row (game_id + this system's + every synced
    system's entries) into what the list/detail panes render -- rebuilt fresh on
    every refresh rather than cached, since it's cheap and the underlying data can
    change at any time (a scan finishing, a merge, a notes edit)."""

    def __init__(self, game_id: str, entries: list):
        self.game_id = game_id
        self.entries = entries
        self.primary_entry = entries[0]
        self.name = self.primary_entry.get("canonical_name") or "?"
        self.kind = _representative_kind(self.primary_entry)
        self.versions = self.primary_entry.get("versions", [])
        self.status = "critical" if self.kind in (None, "unknown") else "ok"
        self.status_text = "Configuration invalid" if self.status == "critical" else ""
        self.notes = self.primary_entry.get("notes", "")
        self.timeline_events = self.primary_entry.get("timeline_events", [])
        self.f95_thread_id = next(
            (p.get("id") for p in self.primary_entry.get("platform_ids", []) if p.get("platform") == "f95"),
            None,
        )
        self.available_updates = self.primary_entry.get("available_updates", [])


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
        self._selected_kind = None  # None == "All Games", else a source `kind` string

        self._build_menu_bar()
        self._build_central()
        self._build_status_bar()

        backend.logLine.connect(self._on_log_line)
        backend.gamesChanged.connect(self._refresh_models)
        backend.decisionNeeded.connect(self._on_decision_needed)
        backend.scanComplete.connect(self._on_scan_complete)
        backend.accountSyncComplete.connect(self._on_account_sync_complete)
        self._refresh_models()

    # ---- chrome -------------------------------------------------------------
    def _build_menu_bar(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")
        scan_action = file_menu.addAction("Scan Folder...")
        scan_action.triggered.connect(self._scan_folder)
        file_menu.addSeparator()
        settings_action = file_menu.addAction("Settings...")
        settings_action.triggered.connect(self._open_settings)
        file_menu.addSeparator()
        exit_action = file_menu.addAction("Exit")
        exit_action.triggered.connect(self.close)

        library_menu = menubar.addMenu("Library")
        dup_action = library_menu.addAction("Review Duplicates...")
        dup_action.triggered.connect(self._review_duplicates)
        check_updates_action = library_menu.addAction("Check F95zone Updates")
        check_updates_action.triggered.connect(self._check_f95_updates_menu)

        # Downloads: real, previously CLI-only F95Checker db.sqlite3 interop
        # (`pythia f95-import`/`f95-export`) -- never reachable from the
        # desktop UI at all until now.
        downloads_menu = menubar.addMenu("Downloads")
        import_action = downloads_menu.addAction("Import from F95Checker...")
        import_action.triggered.connect(self._import_f95)
        export_action = downloads_menu.addAction("Export to F95Checker...")
        export_action.triggered.connect(self._export_f95)

        view_menu = menubar.addMenu("View")
        self._view_list_action = view_menu.addAction("List")
        self._view_list_action.setCheckable(True)
        self._view_list_action.setChecked(True)
        self._view_list_action.triggered.connect(lambda: self._set_presentation("list"))
        self._view_grid_action = view_menu.addAction("Grid")
        self._view_grid_action.setCheckable(True)
        self._view_grid_action.triggered.connect(lambda: self._set_presentation("grid"))

        help_menu = menubar.addMenu("Help")
        about_action = help_menu.addAction("About Pythia")
        about_action.triggered.connect(self._show_about)

    def _build_status_bar(self):
        status_bar = self.statusBar()
        self._status_label = QLabel("Idle")
        status_bar.addWidget(self._status_label)
        self._count_label = QLabel("")
        status_bar.addPermanentWidget(self._count_label)

    def _build_central(self):
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._build_topbar())

        splitter = QSplitter()
        central_layout.addWidget(splitter, stretch=1)
        splitter.addWidget(self._build_rail())
        splitter.addWidget(self._build_list_panel())
        splitter.addWidget(self._build_detail_panel())
        splitter.setSizes([200, 300, 680])
        self.setCentralWidget(central)

    def _build_topbar(self):
        # Real topbar (2026-08-03), matching the validated mockup's chrome:
        # brand + a prominent global search + an honest sync/status indicator.
        # Deliberately does NOT include the mockup's notification-bell/add-menu
        # icons or its live theme-popover -- neither has a real feature behind
        # it yet (no notification system; theme changes apply on next launch,
        # not live, so a topbar quick-switcher would visually lie about that).
        topbar = QWidget()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(58)
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(16)

        brand_col = QVBoxLayout()
        brand_col.setSpacing(0)
        brand_name = QLabel("PYTHIA")
        brand_name.setStyleSheet(f"font-family: {theme.SERIF_FONT}; font-size: 19px; letter-spacing: 1.5px;")
        brand_col.addWidget(brand_name)
        brand_sub = QLabel("GAME LIBRARY MANAGER")
        brand_sub.setStyleSheet(
            f"font-family: {theme.MONO_FONT}; font-size: 8.5px; letter-spacing: 1.5px; "
            f"color: {theme.COLORS['text_faint']};"
        )
        brand_col.addWidget(brand_sub)
        brand_wrap = QWidget()
        brand_wrap.setLayout(brand_col)
        brand_wrap.setMinimumWidth(190)
        layout.addWidget(brand_wrap)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Filter your library...")
        self._search_box.setMinimumHeight(36)
        self._search_box.setMaximumWidth(560)
        self._search_box.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search_box, stretch=1)

        layout.addStretch()

        self._sync_dot = QLabel("●")
        self._sync_dot.setStyleSheet(f"color: {theme.COLORS['ok']}; font-size: 10px;")
        layout.addWidget(self._sync_dot)
        self._sync_label = QLabel("")
        self._sync_label.setStyleSheet(f"color: {theme.COLORS['text_muted']}; font-size: 12px;")
        layout.addWidget(self._sync_label)

        topbar.setStyleSheet(
            f"#topbar {{ background: {theme.COLORS['surface']}; border-bottom: 1px solid {theme.COLORS['border']}; }}"
        )
        return topbar

    def _refresh_sync_indicator(self):
        critical = sum(1 for m in self._models if m.status == "critical")
        if not self._models:
            self._sync_dot.setStyleSheet(f"color: {theme.COLORS['text_faint']}; font-size: 10px;")
            self._sync_label.setText("No games tracked")
        elif critical:
            self._sync_dot.setStyleSheet(f"color: {theme.COLORS['critical']}; font-size: 10px;")
            self._sync_label.setText(f"{critical} need attention")
        else:
            self._sync_dot.setStyleSheet(f"color: {theme.COLORS['ok']}; font-size: 10px;")
            self._sync_label.setText(f"{len(self._models)} games tracked")

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"font-family: {theme.MONO_FONT}; font-size: 10px; font-weight: 600; "
            f"letter-spacing: 1.5px; color: {theme.COLORS['text_faint']};"
        )
        return label

    # Small, purely decorative glyphs mirroring the mockup's per-row `.nav-icon`
    # column -- not fabricated data, just iconography for a real kind string
    # that's already shown as text next to it. Falls back to a plain dot for
    # any kind without a specific glyph.
    _KIND_ICONS = {
        None: "▦",  # "All Games" / Library
        "steam": "◆", "gog": "◇", "egs": "◈", "itch": "◉", "f95": "◐",
        "renpy": "▶", "unity": "▲", "unreal": "△", "godot": "◑",
        "kirikiri": "◒", "buriko": "◓", "catsystem2": "◔",
        "rpgmaker_mv": "■", "rpgmaker_mz": "□", "rpgmaker_vxace": "▫",
        "flash": "▷", "html": "◁", "unknown": "○",
        "__reconcile__": "⇄", "__settings__": "⚙",
    }

    def _build_rail(self):
        # Sectioned sidebar (2026-08-03): "Library" (the whole tracked collection,
        # always one row -- multi-root folder management is real future scope, not
        # this pass) is kept separate from "Sources" (one row per distinct plugin
        # `kind` actually present in the data, sorted by count), matching the
        # Library-vs-Sources distinction settled during mockup review: Library is
        # the tracked collection itself, Sources are the storefront/account
        # plugins that contributed entries to it -- never conflated into one flat
        # nav list. No wordmark here anymore -- the topbar carries the brand now,
        # so repeating it in the rail was pure duplication.
        rail = QWidget()
        rail.setObjectName("rail")
        rail.setFixedWidth(212)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(6)

        layout.addWidget(self._section_label("LIBRARY"))
        self._library_list = QListWidget()
        self._library_list.setObjectName("navList")
        self._library_list.setFrameShape(QListWidget.Shape.NoFrame)
        self._library_list.itemClicked.connect(lambda _item: self._select_kind(None))
        layout.addWidget(self._library_list)

        layout.addSpacing(14)
        layout.addWidget(self._section_label("SOURCES"))
        self._source_list = QListWidget()
        self._source_list.setObjectName("navList")
        self._source_list.setFrameShape(QListWidget.Shape.NoFrame)
        self._source_list.itemClicked.connect(self._on_source_clicked)
        layout.addWidget(self._source_list, stretch=1)

        # Real nav-rows (2026-08-03 fix), matching the mockup's actual sidebar
        # -- it lists Reconciliation/Settings as full-width nav-row buttons
        # (icon + label) right alongside Library, not tucked into a small
        # icon-only corner control. An earlier pass's tiny "⚙" gear button
        # was genuinely hard to find, which is exactly the bug this replaces.
        layout.addSpacing(14)
        layout.addWidget(self._section_label("SYSTEM"))
        system_list = QListWidget()
        system_list.setObjectName("navList")
        system_list.setFrameShape(QListWidget.Shape.NoFrame)
        self._add_sidebar_row(system_list, "Reconciliation", None, "__reconcile__")
        self._add_sidebar_row(system_list, "Settings", None, "__settings__")
        system_list.itemClicked.connect(self._on_system_nav_clicked)
        layout.addWidget(system_list)
        return rail

    def _on_system_nav_clicked(self, item: QListWidgetItem):
        target = item.data(Qt.ItemDataRole.UserRole)
        if target == "__reconcile__":
            self._review_duplicates()
        elif target == "__settings__":
            self._open_settings()

    def _build_sidebar_row(self, label: str, count: int, kind) -> tuple:
        item = QListWidgetItem()
        row = QWidget()
        row.setMinimumHeight(36)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 6, 8, 6)
        row_layout.setSpacing(9)
        icon_label = QLabel(self._KIND_ICONS.get(kind, "○"))
        icon_label.setFixedWidth(16)
        icon_label.setStyleSheet(f"color: {theme.COLORS['text_faint']}; font-size: 11px;")
        row_layout.addWidget(icon_label)
        text_label = QLabel(label)
        text_label.setStyleSheet("font-size: 12.5px;")
        row_layout.addWidget(text_label)
        row_layout.addStretch()
        if count is not None:
            count_label = QLabel(str(count))
            count_label.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 11px; color: {theme.COLORS['text_faint']};")
            row_layout.addWidget(count_label)
        return item, row

    def _add_sidebar_row(self, list_widget: QListWidget, label: str, count: int, kind):
        item, row = self._build_sidebar_row(label, count, kind)
        item.setData(Qt.ItemDataRole.UserRole, kind)
        list_widget.addItem(item)
        list_widget.setItemWidget(item, row)
        row.adjustSize()
        item.setSizeHint(row.sizeHint())
        return item

    def _refresh_sidebar(self):
        self._library_list.blockSignals(True)
        self._library_list.clear()
        self._add_sidebar_row(self._library_list, "All Games", len(self._models), None)
        self._library_list.setCurrentRow(0 if self._selected_kind is None else -1)
        self._library_list.blockSignals(False)

        counts: dict = {}
        for model in self._models:
            counts[model.kind] = counts.get(model.kind, 0) + 1

        self._source_list.blockSignals(True)
        self._source_list.clear()
        select_row = -1
        for i, (kind, count) in enumerate(sorted(counts.items(), key=lambda t: (-t[1], theme.kind_label(t[0])))):
            self._add_sidebar_row(self._source_list, theme.kind_label(kind), count, kind)
            if kind == self._selected_kind:
                select_row = i
        self._source_list.setCurrentRow(select_row)
        self._source_list.blockSignals(False)

    def _on_source_clicked(self, item: QListWidgetItem):
        self._select_kind(item.data(Qt.ItemDataRole.UserRole))

    def _select_kind(self, kind):
        self._selected_kind = kind
        self._refresh_sidebar()
        self._apply_filter()

    def _build_list_panel(self):
        panel = QWidget()
        panel.setMinimumWidth(260)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # List/Grid presentation toggle (2026-08-03), matching the mockup's
        # `.seg` control -- same underlying QListWidget for both, just
        # switching flow/wrapping and which row-builder populates it.
        toolbar = QWidget()
        toolbar.setObjectName("listSearch")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.addStretch()
        self._list_view_btn = QPushButton("List")
        self._list_view_btn.setObjectName("segButton")
        self._list_view_btn.setCheckable(True)
        self._list_view_btn.setChecked(True)
        self._list_view_btn.clicked.connect(lambda: self._set_presentation("list"))
        toolbar_layout.addWidget(self._list_view_btn)
        self._grid_view_btn = QPushButton("Grid")
        self._grid_view_btn.setObjectName("segButton")
        self._grid_view_btn.setCheckable(True)
        self._grid_view_btn.clicked.connect(lambda: self._set_presentation("grid"))
        toolbar_layout.addWidget(self._grid_view_btn)
        layout.addWidget(toolbar)

        self._presentation = "list"
        self._game_list = QListWidget()
        self._game_list.setObjectName("gameList")
        self._game_list.setFrameShape(QListWidget.Shape.NoFrame)
        self._game_list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self._game_list)

        # First-run / empty-library state (2026-08-03) -- previously there was no
        # onboarding path at all: a fresh app-data dir (no local.db yet) just
        # showed a silent, unexplained blank list with nothing telling the user
        # what to do next. Shown only when the whole tracked library is empty,
        # never for a search/source filter that merely narrowed it to zero.
        self._empty_state = QWidget()
        self._empty_state.setVisible(False)
        empty_layout = QVBoxLayout(self._empty_state)
        empty_layout.setContentsMargins(24, 40, 24, 24)
        empty_layout.setSpacing(10)
        empty_layout.addStretch()
        empty_title = QLabel("No games tracked yet")
        empty_title.setStyleSheet(f"font-family: {theme.SERIF_FONT}; font-size: 17px;")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_body = QLabel("Scan a folder to find games Pythia can track.")
        empty_body.setWordWrap(True)
        empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_body.setStyleSheet(f"color: {theme.COLORS['text_muted']}; font-size: 12px;")
        empty_layout.addWidget(empty_body)
        empty_scan_btn = QPushButton("Scan Folder...")
        empty_scan_btn.setObjectName("primary")
        empty_scan_btn.clicked.connect(self._scan_folder)
        empty_layout.addWidget(empty_scan_btn)
        empty_layout.addStretch()
        layout.addWidget(self._empty_state)
        return panel

    def _build_detail_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Real "hero" treatment (2026-08-03), matching the mockup's
        # `.detail-hero`: a larger cover-style badge, title, a real tag row
        # (kind/status/update-available -- only ever the three-way status
        # vocabulary already used elsewhere, never a new ad hoc color), and
        # the action buttons row underneath rather than crammed beside the
        # title.
        head = QWidget()
        head.setObjectName("detailHead")
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(24, 22, 24, 20)
        head_layout.setSpacing(20)

        self._detail_badge = _badge_label("", None, 96)
        head_layout.addWidget(self._detail_badge, 0, Qt.AlignmentFlag.AlignTop)

        content_col = QVBoxLayout()
        content_col.setSpacing(6)

        self._detail_title = QLabel("Select a game")
        self._detail_title.setStyleSheet(f"font-family: {theme.SERIF_FONT}; font-size: 27px;")
        content_col.addWidget(self._detail_title)

        self._detail_sub = QLabel("")
        self._detail_sub.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 11px; color: {theme.COLORS['text_faint']};")
        content_col.addWidget(self._detail_sub)

        self._detail_tags_row = QHBoxLayout()
        self._detail_tags_row.setSpacing(6)
        self._detail_tags_row.addStretch()
        content_col.addLayout(self._detail_tags_row)

        content_col.addSpacing(6)
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        self._f95_browse_button = QPushButton("Browse on F95zone")
        self._f95_browse_button.setVisible(False)
        self._f95_browse_button.clicked.connect(self._browse_f95_thread)
        actions_row.addWidget(self._f95_browse_button)

        self._open_button = QPushButton("Open")
        self._open_button.setObjectName("primary")
        self._open_button.setVisible(False)
        self._open_button.clicked.connect(self._open_in_manager)
        actions_row.addWidget(self._open_button)
        actions_row.addStretch()
        content_col.addLayout(actions_row)

        head_layout.addLayout(content_col, stretch=1)
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
        self._refresh_sync_indicator()
        self._refresh_sidebar()
        self._apply_filter()

    def _apply_filter(self):
        self._empty_state.setVisible(not self._models)
        self._game_list.setVisible(bool(self._models))

        query = self._search_box.text().strip().lower()
        self._visible_models = [
            m
            for m in self._models
            if query in m.name.lower() and (self._selected_kind is None or m.kind == self._selected_kind)
        ]

        selected_id = self._selected_model.game_id if self._selected_model else None
        self._game_list.blockSignals(True)
        self._game_list.clear()
        select_row = -1
        build_row = self._build_grid_card if self._presentation == "grid" else self._build_list_row
        for i, model in enumerate(self._visible_models):
            item = QListWidgetItem()
            self._game_list.addItem(item)
            row_widget = build_row(model)
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
        _elide_label(name_label)
        text_col.addWidget(name_label)
        sub_label = QLabel(f"{model.kind or 'unknown'} · {len(model.versions)} version(s)")
        sub_label.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 10.5px; color: {theme.COLORS['text_faint']};")
        _elide_label(sub_label)
        text_col.addWidget(sub_label)
        if model.status_text:
            status_label = QLabel(model.status_text)
            status_label.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {theme.status_color(model.status)};")
            text_col.addWidget(status_label)
        layout.addLayout(text_col, stretch=1)
        return row

    def _build_grid_card(self, model: GameViewModel) -> QWidget:
        card = QWidget()
        card.setFixedSize(126, 178)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        art = _badge_label(model.name, model.kind, 100)
        layout.addWidget(art, 0, Qt.AlignmentFlag.AlignHCenter)

        name_label = QLabel(model.name)
        name_label.setWordWrap(True)
        name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name_label.setStyleSheet("font-weight: 600; font-size: 11.5px;")
        name_label.setMaximumHeight(32)
        layout.addWidget(name_label)

        sub_label = QLabel(model.status_text if model.status_text else theme.kind_label(model.kind))
        sub_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        sub_label.setStyleSheet(
            f"font-family: {theme.MONO_FONT}; font-size: 9.5px; "
            f"color: {theme.status_color(model.status) if model.status_text else theme.COLORS['text_faint']};"
        )
        layout.addWidget(sub_label)
        return card

    def _set_presentation(self, mode: str):
        self._presentation = mode
        self._list_view_btn.setChecked(mode == "list")
        self._grid_view_btn.setChecked(mode == "grid")
        self._view_list_action.setChecked(mode == "list")
        self._view_grid_action.setChecked(mode == "grid")
        if mode == "grid":
            self._game_list.setFlow(QListWidget.Flow.LeftToRight)
            self._game_list.setWrapping(True)
            self._game_list.setResizeMode(QListWidget.ResizeMode.Adjust)
            self._game_list.setSpacing(6)
        else:
            self._game_list.setFlow(QListWidget.Flow.TopToBottom)
            self._game_list.setWrapping(False)
            self._game_list.setSpacing(0)
        self._apply_filter()

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
            self._f95_browse_button.setVisible(False)
            return

        self._f95_browse_button.setVisible(model.f95_thread_id is not None)

        self._manager_candidates = self.backend.find_manager_for_game(model.game_id)
        self._open_button.setVisible(bool(self._manager_candidates))
        if self._manager_candidates:
            candidate = self._manager_candidates[0]
            if candidate["manager_id"] == "pythia.manager.direct":
                self._open_button.setText("Run Game")
            else:
                self._open_button.setText(f"Open in {candidate['manager_name']}")

        self._detail_badge.setText(model.name[:1].upper())
        start, end = theme.badge_gradient(model.kind)
        self._detail_badge.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {start}, stop:1 {end});"
            f"border-radius: 24px; color: rgba(255,255,255,0.92);"
            f"font-family: {theme.SERIF_FONT}; font-size: 48px;"
        )
        self._detail_title.setText(model.name)
        self._detail_sub.setText(f"game_id {model.game_id} · {model.kind or 'unknown'}")

        _clear_layout(self._detail_tags_row)
        self._detail_tags_row.addWidget(_pill_label(theme.kind_label(model.kind), "neutral"))
        if model.status == "critical":
            self._detail_tags_row.addWidget(_pill_label(model.status_text or "Needs attention", "critical"))
        else:
            self._detail_tags_row.addWidget(_pill_label("Configured", "ok"))
        if model.available_updates:
            self._detail_tags_row.addWidget(_pill_label("Update available", "pending"))
        self._detail_tags_row.addStretch()

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
                variant_widget = self._build_variant_row(variant, model.game_id)
                self._versions_list.addItem(variant_item)
                self._versions_list.setItemWidget(variant_item, variant_widget)
                variant_widget.adjustSize()
                variant_item.setSizeHint(variant_widget.sizeHint())

        self._notes_edit.setPlainText(model.notes)

        self._history_list.clear()
        if not model.timeline_events:
            self._history_list.addItem("No history recorded yet for this system's copy of this entry.")
        else:
            # Newest first -- real events (game_added/version_added/ownership_recorded/
            # merged, see datadir.add_timeline_event()), not a placeholder. Only this
            # system's own entry's events are shown, same "primary_entry only, not
            # merged across systems" convention model.versions/model.notes already use.
            for event in reversed(model.timeline_events):
                item = QListWidgetItem()
                row = self._build_history_row(event)
                self._history_list.addItem(item)
                self._history_list.setItemWidget(item, row)
                row.adjustSize()
                item.setSizeHint(row.sizeHint())

    _EVENT_LABELS = {
        "game_added": "Game added",
        "version_added": "New version detected",
        "ownership_recorded": "Ownership recorded",
        "merged": "Merged with another entry",
        "linked_cross_system": "Linked to another system's record",
        "f95_update_detected": "F95zone update detected",
    }

    def _build_history_row(self, event: dict) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        timestamp = (event.get("timestamp") or "")[:19].replace("T", " ")
        time_label = QLabel(timestamp)
        time_label.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 10.5px; color: {theme.COLORS['text_faint']};")
        time_label.setFixedWidth(140)
        layout.addWidget(time_label)

        event_type = event.get("type", "")
        text_label = QLabel(self._EVENT_LABELS.get(event_type, event_type))
        text_label.setStyleSheet(f"font-size: 12.5px; color: {theme.COLORS['text']};")
        layout.addWidget(text_label)

        detail = event.get("detail") or ""
        if detail:
            detail_label = QLabel(detail)
            detail_label.setStyleSheet(f"font-family: {theme.MONO_FONT}; font-size: 11px; color: {theme.COLORS['text_muted']};")
            layout.addWidget(detail_label)
        layout.addStretch()
        return row

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

    def _build_variant_row(self, variant: dict, game_id: str) -> QWidget:
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
        _elide_label(path_label)
        layout.addWidget(path_label, stretch=1)

        platforms = ", ".join(variant.get("platforms", [])) or "?"
        platforms_label = QLabel(platforms)
        platforms_label.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {theme.COLORS['ok']};")
        layout.addWidget(platforms_label)

        # Direct-launch toggle -- the one deliberate exception to never launching
        # a game's own executable (see pythia/plugins/__init__.py and
        # pythia.plugins.manager.direct). Doesn't affect source/version/update
        # tracking, only how it launches. Set here, per variant, since the
        # executable path is inherently per-install.
        if variant.get("direct") and variant.get("executable"):
            direct_btn = QPushButton(f"▶ {Path(variant['executable']).name}")
            direct_btn.setToolTip("Runs this executable directly when opened -- click to change.")
            clear_btn = QPushButton("✕")
            clear_btn.setFixedWidth(28)
            clear_btn.setToolTip("Stop treating this variant as direct-launch.")
            clear_btn.clicked.connect(lambda: self._clear_direct(game_id, variant))
            layout.addWidget(clear_btn)
        else:
            direct_btn = QPushButton("Set direct...")
            direct_btn.setToolTip(
                "For an executable that needs no runner/compatibility layer -- "
                "just runs it directly when opened. Doesn't affect update/patch tracking."
            )
        direct_btn.clicked.connect(lambda: self._pick_direct_executable(game_id, variant))
        layout.addWidget(direct_btn)
        return row

    def _pick_direct_executable(self, game_id: str, variant: dict):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose the executable to run directly",
            variant.get("path") or "",
            "Executable (*.exe);;All files (*)",
        )
        if not path:
            return
        self.backend.set_variant_direct(game_id, variant.get("path") or "", path)
        self._status_label.setText("Marked direct -- this executable will run directly when opened.")

    def _clear_direct(self, game_id: str, variant: dict):
        self.backend.set_variant_direct(game_id, variant.get("path") or "", "")
        self._status_label.setText("Cleared direct-launch executable.")

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

    def _save_notes(self):
        if self._selected_model is None:
            return
        self.backend.save_notes(self._selected_model.game_id, self._notes_edit.toPlainText())
        self._status_label.setText("Notes saved.")

    def _browse_f95_thread(self):
        if self._selected_model is None or self._selected_model.f95_thread_id is None:
            return
        from pythia.ui.desktop.f95_login import F95BrowserDialog

        url = f"https://f95zone.to/threads/{self._selected_model.f95_thread_id}/"
        dialog = F95BrowserDialog(url, title=self._selected_model.name, parent=self)
        dialog.exec()

    def _open_in_manager(self):
        # Pythia never launches a game's own executable -- this hands off to a
        # third-party tool (Steam client, etc.) that runs it, same boundary as
        # cli.py's `open` command. No confirmation dialog: the button itself
        # already says exactly what it does ("Open in Steam" / "Run Game"),
        # clicking it is the confirmation.
        if self._selected_model is None or not self._manager_candidates:
            return
        candidate = self._manager_candidates[0]
        self.backend.open_in_manager(self._selected_model.game_id, candidate["manager_id"])
        self._status_label.setText(f"Handing off to {candidate['manager_name']}...")

    def _on_log_line(self, line: str):
        self._status_label.setText(line)

    def _on_scan_complete(self, results):
        ok = sum(1 for r in results if r.get("status") == "ok")
        self._status_label.setText(f"Scan complete: {ok}/{len(results)} onboarded")

    def _on_account_sync_complete(self, plugin_id: str, result: dict):
        if result.get("status") != "ok":
            self._status_label.setText(f"{plugin_id}: {result.get('message', 'sync failed')}")
            return
        self._status_label.setText(f"{plugin_id}: {result['linked']} linked, {result['created']} new.")

    def _import_f95(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose F95Checker's db.sqlite3", "", "SQLite database (*.sqlite3);;All files (*)"
        )
        if not path:
            return
        self._status_label.setText("Importing from F95Checker...")
        self.backend.import_f95(path)

    def _export_f95(self):
        from pythia.plugins.library.f95 import export_to_sqlite

        path, _ = QFileDialog.getSaveFileName(
            self, "Write F95Checker-compatible db.sqlite3", "db.sqlite3", "SQLite database (*.sqlite3)"
        )
        if not path:
            return
        count = export_to_sqlite(path)
        self._status_label.setText(f"Wrote {count} game(s) to '{path}'.")

    def _check_f95_updates_menu(self):
        self._status_label.setText("Checking F95zone for updates...")
        threading.Thread(target=self._check_f95_updates_menu_worker, daemon=True).start()

    def _check_f95_updates_menu_worker(self):
        from pythia.plugins.library import f95_updates

        result = f95_updates.run_update_check()
        message = f"F95zone check: {result['checked']} checked, {result['updated']} update(s) found."
        if result["errors"]:
            message += f" ({len(result['errors'])} error(s).)"
        self.backend.logLine.emit(message)
        self.backend.refresh_games()

    def _show_about(self):
        QMessageBox.about(
            self,
            "About Pythia",
            "Pythia -- a multi-system game library manager.\n\n"
            "Tracks versions/variants across your own machines, pulls ownership/"
            "metadata from storefront accounts, and hands off to a real launcher "
            "(Steam, Epic, etc.) to actually run games -- Pythia itself never "
            "launches a game's own executable, except for variants you've "
            "explicitly marked direct.\n\n"
            "github.com/bi0shacker001/pythia",
        )

    def _on_decision_needed(self, preview):
        # Minimal stand-in: log it and default to "new" rather than blocking silently.
        # A real decision dialog (mirroring big-picture's DecisionScreen.qml) is
        # further Milestone 5 scope.
        self._status_label.setText(
            f"Ambiguous match for '{preview.get('name')}' -- defaulting to new game "
            "(desktop decision dialog not built yet)."
        )
        self.backend.resolveDecision(preview["path"], {"action": "new"})

    # ---- the app-singleton interface real Lutris-derived core code calls
    # (see pythia.util.app_singleton and every services/*.py call site that
    # does get_default_application().show_installer_window(...)) -- this UI
    # was designed around onboarding.py's folder-based tracking, which has no
    # concept of a Lutris-style install script, so these are new: real, but
    # minimal, since the install wizard itself isn't built out yet. ----
    def show_installer_window(self, installers, service=None, appid=None, installation_kind=None) -> None:
        if not installers:
            self._status_label.setText("No installers available.")
            return
        try:
            from pythia.installer.interpreter import ScriptInterpreter  # noqa: PLC0415
            from pythia.delegates import InstallUIDelegate  # noqa: PLC0415
        except ImportError as ex:
            self._status_label.setText(
                f"Can't run installers yet: {ex}. The install wizard needs a Qt port of "
                "ScriptInterpreter (currently subclasses GObject.Object)."
            )
            return
        delegate = InstallUIDelegate()
        ScriptInterpreter(installers[0], delegate).launch_install(delegate)

    def show_pythia_installer_window(self, game_slug: str) -> None:
        from pythia.installer import get_installers  # noqa: PLC0415

        self.show_installer_window(get_installers(game_slug=game_slug))


def run(app, backend) -> int:
    # Applied at the QApplication level (2026-08-03 fix), not just on
    # MainWindow -- a QDialog is its own top-level window even when
    # constructed with a parent, so Qt never cascaded MainWindow's stylesheet
    # to Settings/Duplicates/etc; they were rendering with zero theming at
    # all (confirmed: dlg.styleSheet() was empty). This is the actual root
    # cause of dialogs looking like "the old UI" -- there was no styling on
    # them whatsoever, not a mismatched one.
    app.setStyleSheet(theme.build_stylesheet())
    window = MainWindow(backend)
    # Real Lutris-derived core code (services/*.py) reaches this window via
    # pythia.util.app_singleton.get_default().show_installer_window(...) --
    # see MainWindow.show_installer_window/show_pythia_installer_window above.
    from pythia.util import app_singleton  # noqa: PLC0415

    app_singleton.set_default(window)
    window.show()
    return app.exec()
