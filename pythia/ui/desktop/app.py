"""
Desktop UI shell (QtWidgets). Deliberately minimal this milestone -- see the
Milestone 2 plan's Deferred section: the real management-console build-out (bulk
review, filter editing, etc.) is future work. This proves the same backend adapter
big-picture mode uses also works from QtWidgets, and gives desktop users a usable,
if basic, window today rather than nothing.
"""

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pythia.ui.desktop.reconciliation import ReconciliationDialog


class MainWindow(QMainWindow):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.setWindowTitle("Pythia")
        self.resize(900, 600)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(toolbar)

        scan_btn = QPushButton("Scan Folder...")
        scan_btn.clicked.connect(self._scan_folder)
        toolbar_layout.addWidget(scan_btn)

        duplicates_btn = QPushButton("Review Duplicates...")
        duplicates_btn.clicked.connect(self._review_duplicates)
        toolbar_layout.addWidget(duplicates_btn)
        toolbar_layout.addStretch()

        splitter = QSplitter()
        layout.addWidget(splitter)

        self.game_list = QListWidget()
        splitter.addWidget(self.game_list)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        splitter.addWidget(self.log)

        backend.logLine.connect(self.log.appendPlainText)
        backend.gamesChanged.connect(self._refresh_list)
        backend.decisionNeeded.connect(self._on_decision_needed)
        self._refresh_list()

    def _scan_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to scan")
        if path:
            self.log.appendPlainText(f"Scanning '{path}' ...")
            self.backend.start_scan(path)

    def _review_duplicates(self):
        dialog = ReconciliationDialog(self.backend, self)
        dialog.exec()

    def _refresh_list(self):
        self.game_list.clear()
        for game in self.backend.games:
            entry = game["entries"][0]
            versions = entry.get("versions", [])
            label = f"[{game['game_id']}] {entry.get('canonical_name')} ({len(versions)} version(s))"
            self.game_list.addItem(QListWidgetItem(label))

    def _on_decision_needed(self, preview):
        # Minimal stand-in: log it and default to "new" rather than blocking silently.
        # A real decision dialog (mirroring DecisionScreen.qml) is Milestone 3 scope.
        self.log.appendPlainText(
            f"Ambiguous match for '{preview.get('name')}' -- defaulting to new game "
            "(desktop decision dialog not built yet)."
        )
        self.backend.resolveDecision(preview["path"], {"action": "new"})


def run(app, backend) -> int:
    window = MainWindow(backend)
    window.show()
    return app.exec()
