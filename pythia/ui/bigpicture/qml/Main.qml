import QtQuick
import QtQuick.Controls

// Big-picture shell: a single StackView driving large, touch-first screens. No
// shader effects (blur/drop-shadow/colorize) and no Material style anywhere in this
// UI -- both are well-documented performance costs on embedded/ARM GPUs. Screens
// communicate upward via signals rather than reaching for ids across files, since
// each .qml file is its own scope.
ApplicationWindow {
    id: window
    visible: true
    width: 1280
    height: 720
    title: "Pythia"
    color: "#181818"

    header: ToolBar {
        height: 64
        background: Rectangle { color: "#222222" }
        Label {
            anchors.centerIn: parent
            text: "Pythia"
            font.pixelSize: 26
            color: "white"
        }
    }

    StackView {
        id: stackView
        anchors.fill: parent
        initialItem: homeScreenComponent
    }

    Component {
        id: homeScreenComponent
        HomeScreen {
            onDownloadsRequested: stackView.push(downloadsScreenComponent)
        }
    }

    Component {
        id: decisionScreenComponent
        DecisionScreen {
            onDone: stackView.pop()
        }
    }

    Component {
        id: downloadsScreenComponent
        DownloadsScreen {
            onBack: stackView.pop()
        }
    }

    Connections {
        target: backend
        function onDecisionNeeded(preview) {
            stackView.push(decisionScreenComponent, { preview: preview })
        }
    }
}
