"""
Big-picture UI: a QML front end sized for touch/controller use on a small screen
(e.g. a Retroid Pocket 5 running Pocknix). Uses Qt Quick Controls' lightweight
"Basic" style and the QML screens under qml/ avoid shader effects (blur, drop-
shadow, colorize) -- both are well-documented performance costs on embedded/ARM
GPUs. See the Milestone 2 plan's Context section for the research behind this.
"""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

QML_DIR = Path(__file__).parent / "qml"


def run(app, backend) -> int:
    QQuickStyle.setStyle("Basic")

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))

    if not engine.rootObjects():
        return 1

    return app.exec()
