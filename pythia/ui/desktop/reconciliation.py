"""
Reconciliation queue dialog -- the real UI surface for pythia.reconciliation, built
directly in response to a live example (2026-08-03): automatic scanning correctly
refuses to auto-link "suggested" matches, so the same game can fragment into several
game_ids (three BeingADik entries, three Fetish Locator entries). A bare CLI dump of
candidate pairs proved the data layer works but is genuinely hard to judge from --
name-similarity alone put "BONEWORKS"/"Noble Works" at a higher score than several
real matches. This surfaces the same candidates with the context a person actually
needs to decide (engine, path, version count), and never merges anything without an
explicit per-pair click.
"""

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def _describe(summary: dict) -> str:
    engine = summary.get("engine_kind") or "?"
    versions = summary.get("version_count", 0)
    path = summary.get("path") or "(no path)"
    return f"[{summary['game_id']}] {summary.get('name')!r} -- {engine}, {versions} version(s)\n{path}"


class ReconciliationDialog(QDialog):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.setWindowTitle("Review possible duplicate games")
        self.resize(900, 500)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "These games have similar names. Nothing is merged automatically -- "
            "review each pair and choose which one survives, or dismiss it."
        ))

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Score", "Game A", "Game B", "Actions"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        self._candidates = []
        backend.mergeCandidatesReady.connect(self._on_candidates_ready)
        backend.mergeComplete.connect(self._on_merge_complete)
        backend.refresh_merge_candidates()

    def _on_candidates_ready(self, candidates):
        self._candidates = candidates
        self._rebuild_table()

    def _rebuild_table(self):
        self.table.setRowCount(0)
        for candidate in self._candidates:
            self._add_row(candidate)
        self.table.resizeRowsToContents()

    def _add_row(self, candidate: dict):
        a, b = candidate["a"], candidate["b"]
        row = self.table.rowCount()
        self.table.insertRow(row)

        self.table.setItem(row, 0, QTableWidgetItem(f"{candidate['score']:.2f}"))
        self.table.setItem(row, 1, QTableWidgetItem(_describe(a)))
        self.table.setItem(row, 2, QTableWidgetItem(_describe(b)))

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(2, 2, 2, 2)

        keep_a_btn = QPushButton("Keep A, merge B in")
        keep_a_btn.clicked.connect(lambda: self._merge(a["game_id"], b["game_id"]))
        actions_layout.addWidget(keep_a_btn)

        keep_b_btn = QPushButton("Keep B, merge A in")
        keep_b_btn.clicked.connect(lambda: self._merge(b["game_id"], a["game_id"]))
        actions_layout.addWidget(keep_b_btn)

        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.clicked.connect(lambda: self._dismiss(candidate))
        actions_layout.addWidget(dismiss_btn)

        self.table.setCellWidget(row, 3, actions)

    def _merge(self, surviving_id: int, retired_id: int):
        self.backend.merge_games(surviving_id, retired_id)

    def _dismiss(self, candidate: dict):
        self._candidates = [c for c in self._candidates if c is not candidate]
        self._rebuild_table()

    def _on_merge_complete(self, result: dict):
        if result.get("status") != "ok":
            return
        retired_id = result.get("retired_game_id")
        self._candidates = [
            c for c in self._candidates if c["a"]["game_id"] != retired_id and c["b"]["game_id"] != retired_id
        ]
        self._rebuild_table()
