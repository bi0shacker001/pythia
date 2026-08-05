"""Qt implementations of pythia.delegates' UI injection points -- the real
replacement for gui/dialogs/delegates.py's DialogLaunchUIDelegate/
DialogInstallUIDelegate (both GTK, both deleted with the rest of gui/).
"""

from PySide6.QtWidgets import QFileDialog, QMessageBox

from pythia.delegates import InstallUIDelegate, LaunchUIDelegate


class QtInstallUIDelegate(InstallUIDelegate):
    def __init__(self, parent=None):
        self.parent = parent

    def show_install_yesno_inquiry(self, question: str, title: str) -> bool:
        result = QMessageBox.question(self.parent, title, question)
        return result == QMessageBox.StandardButton.Yes

    def show_install_file_inquiry(self, question: str, title: str, message: str) -> str | None:
        if not self.show_install_yesno_inquiry(question, title):
            return None
        path, _filter = QFileDialog.getOpenFileName(self.parent, message)
        return path or None


class QtLaunchUIDelegate(LaunchUIDelegate):
    def __init__(self, parent=None):
        self.parent = parent
