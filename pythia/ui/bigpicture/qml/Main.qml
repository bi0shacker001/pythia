import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Big-picture shell: a single StackView driving large, touch-first screens. No
// shader effects (blur/drop-shadow/colorize) and no Material style anywhere in this
// UI -- both are well-documented performance costs on embedded/ARM GPUs.
//
// Restyled (2026-08-03) to Pythia's own v4 design language (dark ink/amber tokens,
// serif wordmark) mixed with a real pattern pulled from Lutris's actual GUI source
// (lutris/gui/widgets/sidebar.py): Lutris filters its main grid by clicking a
// per-service sidebar row (Steam/GOG/Epic/...). A literal sidebar eats too much
// width on the small, touch-first screens big-picture targets, so that same
// category-filter idea is adapted into a horizontal chip row instead -- same
// underlying UX (quick filter by source), different idiom for a different form
// factor, not a straight port of Lutris's own layout.
ApplicationWindow {
    id: window
    visible: true
    width: 1280
    height: 720
    title: "Pythia"

    Theme { id: theme }

    property string selectedKind: ""  // "" == All

    color: theme.ink

    header: ColumnLayout {
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            height: 72
            color: theme.surface

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 20
                anchors.rightMargin: 20
                spacing: 16

                ToolButton {
                    visible: stackView.depth > 1
                    text: "←"
                    font.pixelSize: 26
                    onClicked: stackView.pop()
                }

                Label {
                    text: "PYTHIA"
                    font.pixelSize: 24
                    font.family: theme.serifFont
                    font.letterSpacing: 2
                    color: theme.text
                }

                Item { width: 12 }

                TextField {
                    id: searchField
                    Layout.preferredWidth: 320
                    placeholderText: "Filter..."
                    font.pixelSize: 18
                    color: theme.text
                    background: Rectangle {
                        color: theme.surfaceSunken
                        border.color: theme.border
                        radius: 8
                    }
                }

                Item { Layout.fillWidth: true }

                Button {
                    text: "Scan"
                    implicitHeight: 44
                    implicitWidth: 96
                    contentItem: Label {
                        text: parent.text
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                        color: theme.accentText
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    // Same treatment as QPushButton#primary in the desktop QSS
                    // (theme.py's build_stylesheet()) -- solid accent fill, not a
                    // generic native button, matching the "previous UI... was
                    // shaping up nicely" reference.
                    background: Rectangle { color: theme.accent; radius: 8 }
                    onClicked: homeScreen && homeScreen.scanRequested()
                }

                Button {
                    text: "⚙"
                    implicitHeight: 44
                    implicitWidth: 44
                    visible: stackView.depth === 1
                    contentItem: Label {
                        text: parent.text
                        font.pixelSize: 20
                        color: theme.text
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        color: theme.surfaceRaised
                        radius: 8
                        border.color: parent.hovered ? theme.accent : theme.border
                        border.width: 1
                    }
                    onClicked: stackView.push(settingsScreenComponent)
                }
            }
        }

        // Source quick-filter chips -- the horizontal-chip adaptation of Lutris's
        // sidebar category rows (see module docstring above).
        Rectangle {
            Layout.fillWidth: true
            height: 52
            color: theme.surfaceRaised
            visible: stackView.depth === 1

            Row {
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                anchors.leftMargin: 20
                spacing: 10

                Repeater {
                    model: [
                        { label: "All", kind: "" },
                        { label: "Steam", kind: "steam" },
                        { label: "GOG", kind: "gog" },
                        { label: "Epic", kind: "egs" },
                        { label: "itch.io", kind: "itch" },
                        { label: "F95zone", kind: "f95" },
                        { label: "Ren'Py", kind: "renpy" },
                    ]
                    delegate: Rectangle {
                        readonly property bool active: window.selectedKind === modelData.kind
                        width: chipLabel.implicitWidth + 24
                        height: 32
                        radius: 16
                        color: active ? theme.accent : "transparent"
                        border.color: active ? theme.accent : theme.border
                        border.width: 1
                        Label {
                            id: chipLabel
                            anchors.centerIn: parent
                            text: modelData.label
                            font.pixelSize: 14
                            color: active ? theme.accentText : theme.textMuted
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: window.selectedKind = modelData.kind
                        }
                    }
                }
            }
        }
    }

    footer: Rectangle {
        height: 36
        color: theme.surface
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            Label {
                id: statusFooterLabel
                text: "Idle"
                color: theme.textMuted
                font.pixelSize: 13
            }
            Item { Layout.fillWidth: true }
            Label {
                text: (backend.games ? backend.games.length : 0) + " games tracked"
                color: theme.textFaint
                font.pixelSize: 13
                font.family: theme.monoFont
            }
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
            id: homeScreenInstance
            searchText: searchField.text
            filterKind: window.selectedKind
            onDownloadsRequested: stackView.push(downloadsScreenComponent)
        }
    }

    property Item homeScreen: stackView.currentItem

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

    Component {
        id: settingsScreenComponent
        SettingsScreen {
            onBack: stackView.pop()
        }
    }

    Connections {
        target: backend
        function onDecisionNeeded(preview) {
            stackView.push(decisionScreenComponent, { preview: preview })
        }
        function onLogLine(line) {
            statusFooterLabel.text = line
        }
        function onScanComplete(results) {
            statusFooterLabel.text = "Scan complete: " + results.length + " found"
        }
    }
}
