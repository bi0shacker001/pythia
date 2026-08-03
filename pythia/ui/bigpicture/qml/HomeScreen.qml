import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

// Home: tracked games as large cards, plus the two entry actions (scan a folder,
// review pending downloads). This is the screen the user lands on and returns to.
Item {
    id: root

    signal downloadsRequested()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 20

        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            Button {
                text: "Scan Folder"
                font.pixelSize: 22
                implicitHeight: 64
                implicitWidth: 220
                onClicked: folderDialog.open()
            }

            Button {
                text: "Downloads"
                font.pixelSize: 22
                implicitHeight: 64
                implicitWidth: 200
                onClicked: root.downloadsRequested()
            }

            Item { Layout.fillWidth: true }

            Label {
                id: statusLabel
                text: ""
                color: "#aaaaaa"
                font.pixelSize: 16
                elide: Text.ElideRight
                Layout.maximumWidth: 400
            }
        }

        GridView {
            id: grid
            Layout.fillWidth: true
            Layout.fillHeight: true
            cellWidth: 320
            cellHeight: 180
            clip: true
            model: backend.games

            delegate: Rectangle {
                width: 300
                height: 160
                radius: 10
                color: "#2a2a2a"
                border.color: "#3d3d3d"
                border.width: 1

                readonly property var entry: modelData.entries[0]

                Column {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 8

                    Label {
                        width: parent.width
                        text: entry.canonical_name
                        font.pixelSize: 22
                        font.bold: true
                        color: "white"
                        wrapMode: Text.WordWrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }

                    Label {
                        text: (entry.versions ? entry.versions.length : 0) + " version(s)"
                        color: "#bbbbbb"
                        font.pixelSize: 16
                    }

                    Label {
                        text: (entry.source && entry.source.kind) ? entry.source.kind : "unknown"
                        color: "#7fb3ff"
                        font.pixelSize: 14
                    }
                }
            }

            Label {
                anchors.centerIn: parent
                visible: grid.count === 0
                text: "No games tracked yet -- tap Scan Folder to get started."
                color: "#777777"
                font.pixelSize: 20
            }
        }
    }

    FolderDialog {
        id: folderDialog
        title: "Choose a folder to scan"
        onAccepted: {
            statusLabel.text = "Scanning..."
            backend.start_scan(selectedFolder.toString())
        }
    }

    Connections {
        target: backend
        function onScanComplete(results) {
            statusLabel.text = "Scan complete: " + results.length + " found"
        }
        function onLogLine(line) {
            statusLabel.text = line
        }
    }
}
