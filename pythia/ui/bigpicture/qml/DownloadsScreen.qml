import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

// "Launch and observe" surface: point this at a downloader tool (f95checker, a
// browser, ...) and a folder it saves into. Pythia doesn't reimplement that tool's
// UI -- it just launches it as its own window and lists whatever new files show up,
// each with an Import button that hands the path straight to onboarding. Restyled
// to the shared token set (2026-08-03, big-picture design-token pass).
Item {
    id: root
    signal back()

    property var pendingFiles: []

    Theme { id: theme }

    Rectangle { anchors.fill: parent; color: theme.ink }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 20

        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            ToolButton {
                text: "←"
                font.pixelSize: 26
                onClicked: root.back()
            }

            Label {
                text: "Downloads"
                font.pixelSize: 24
                font.family: theme.serifFont
                color: theme.text
            }

            Item { Layout.fillWidth: true }

            Button {
                text: "Launch Tool..."
                font.pixelSize: 18
                implicitHeight: 52
                contentItem: Label {
                    text: parent.text
                    color: theme.accentText
                    font.pixelSize: 18
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle { color: theme.accent; radius: 8 }
                onClicked: toolDialog.open()
            }
        }

        Label {
            id: watchDirLabel
            text: "Watching: (no folder chosen yet)"
            color: theme.textFaint
            font.pixelSize: 13
            font.family: theme.monoFont
        }

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            clip: true
            model: root.pendingFiles

            delegate: Rectangle {
                width: ListView.view.width
                height: 80
                radius: 8
                color: theme.surface
                border.color: theme.border

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 16

                    Label {
                        text: modelData
                        color: theme.text
                        font.pixelSize: 16
                        font.family: theme.monoFont
                        Layout.fillWidth: true
                        elide: Text.ElideMiddle
                    }

                    Button {
                        text: "Import"
                        font.pixelSize: 16
                        implicitHeight: 44
                        contentItem: Label {
                            text: parent.text
                            color: theme.accentText
                            font.pixelSize: 16
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle { color: theme.accent; radius: 8 }
                        onClicked: {
                            backend.start_onboard(modelData)
                            var updated = root.pendingFiles.slice()
                            updated.splice(index, 1)
                            root.pendingFiles = updated
                        }
                    }
                }
            }

            Label {
                anchors.centerIn: parent
                visible: root.pendingFiles.length === 0
                text: "Nothing detected yet."
                color: theme.textFaint
                font.pixelSize: 18
            }
        }
    }

    FileDialog {
        id: toolDialog
        title: "Choose a downloader tool to launch"
        onAccepted: folderDialog.open()
        property string toolPath: ""
        onSelectedFileChanged: toolPath = selectedFile.toString()
    }

    FolderDialog {
        id: folderDialog
        title: "Choose the folder it downloads into"
        onAccepted: {
            watchDirLabel.text = "Watching: " + selectedFolder.toString()
            backend.start_watch(toolDialog.toolPath, selectedFolder.toString())
        }
    }

    Connections {
        target: backend
        function onDownloadDetected(path) {
            root.pendingFiles = root.pendingFiles.concat([path])
        }
    }
}
