import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

// "Launch and observe" surface: point this at a downloader tool (f95checker, a
// browser, ...) and a folder it saves into. Pythia doesn't reimplement that tool's
// UI -- it just launches it as its own window and lists whatever new files show up,
// each with an Import button that hands the path straight to onboarding.
Item {
    id: root
    signal back()

    property var pendingFiles: []

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 20

        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            Button {
                text: "< Back"
                font.pixelSize: 20
                implicitHeight: 56
                onClicked: root.back()
            }

            Label {
                text: "Downloads"
                font.pixelSize: 24
                font.bold: true
                color: "white"
            }

            Item { Layout.fillWidth: true }

            Button {
                text: "Launch Tool..."
                font.pixelSize: 20
                implicitHeight: 56
                onClicked: toolDialog.open()
            }
        }

        Label {
            text: "Watching: " + (watchDirLabel.text || "(no folder chosen yet)")
            id: watchDirLabel
            color: "#aaaaaa"
            font.pixelSize: 14
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
                radius: 10
                color: "#2a2a2a"
                border.color: "#3d3d3d"

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 16

                    Label {
                        text: modelData
                        color: "white"
                        font.pixelSize: 18
                        Layout.fillWidth: true
                        elide: Text.ElideMiddle
                    }

                    Button {
                        text: "Import"
                        font.pixelSize: 18
                        implicitHeight: 48
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
                color: "#777777"
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
            watchDirLabel.text = selectedFolder.toString()
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
