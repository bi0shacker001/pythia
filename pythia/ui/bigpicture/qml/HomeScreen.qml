import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

// Home: tracked games as large cards, gradient-monogram badges keyed by source
// kind, inline status color (ok/pending/critical), live-filtered by the search
// text Main.qml's top bar owns. Restyled to the shared token set (2026-08-03,
// big-picture design-token pass) -- previously plain greys from Milestone 2.
Item {
    id: root

    property string searchText: ""
    property string filterKind: ""  // "" == All, set from Main.qml's source chips
    signal downloadsRequested()
    function scanRequested() { folderDialog.open() }

    Theme { id: theme }

    function representativeKind(entry) {
        for (var i = 0; i < (entry.versions ? entry.versions.length : 0); i++) {
            var variants = entry.versions[i].variants || []
            for (var j = 0; j < variants.length; j++) {
                var source = variants[j].source
                if (source && source.kind) return source.kind
            }
        }
        return null
    }

    function statusFor(entry) {
        var kind = representativeKind(entry)
        return (kind === null || kind === "unknown") ? "critical" : "ok"
    }

    function statusText(entry) {
        return statusFor(entry) === "critical" ? "Configuration invalid" : ""
    }

    ListModel { id: filteredModel }

    function rebuildFiltered() {
        filteredModel.clear()
        var games = backend.games
        var query = root.searchText.toLowerCase()
        for (var i = 0; i < games.length; i++) {
            var entry = games[i].entries[0]
            var name = entry.canonical_name || ""
            var matchesSearch = query === "" || name.toLowerCase().indexOf(query) !== -1
            var matchesKind = root.filterKind === "" || root.representativeKind(entry) === root.filterKind
            if (matchesSearch && matchesKind) {
                filteredModel.append({ gameId: games[i].game_id, entryIndex: i })
            }
        }
    }

    onSearchTextChanged: rebuildFiltered()
    onFilterKindChanged: rebuildFiltered()
    Component.onCompleted: rebuildFiltered()
    Connections {
        target: backend
        function onGamesChanged() { rebuildFiltered() }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 20

        RowLayout {
            // Scan now lives in Main.qml's persistent header; Downloads stays here
            // since it's a screen-local navigation action, not a global one.
            Layout.fillWidth: true
            spacing: 16

            Button {
                text: "Downloads"
                font.pixelSize: 22
                implicitHeight: 56
                implicitWidth: 200
                onClicked: root.downloadsRequested()
            }

            Item { Layout.fillWidth: true }

            Label {
                id: statusLabel
                text: ""
                color: theme.textMuted
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
            model: filteredModel

            delegate: Rectangle {
                width: 300
                height: 160
                radius: 10
                color: theme.surface
                border.color: theme.border
                border.width: 1

                readonly property var gameEntry: backend.games[entryIndex].entries[0]
                readonly property string kind: root.representativeKind(gameEntry)

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 12

                    Rectangle {
                        Layout.preferredWidth: 44
                        Layout.preferredHeight: 44
                        Layout.alignment: Qt.AlignTop
                        radius: 10
                        gradient: Gradient {
                            orientation: Gradient.Diagonal
                            GradientStop { position: 0.0; color: theme.badgeGradient(kind)[0] }
                            GradientStop { position: 1.0; color: theme.badgeGradient(kind)[1] }
                        }
                        Label {
                            anchors.centerIn: parent
                            text: (gameEntry.canonical_name || "?").substring(0, 1).toUpperCase()
                            color: "#ffffff"
                            font.pixelSize: 20
                            font.family: theme.serifFont
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: 6

                        Label {
                            Layout.fillWidth: true
                            text: gameEntry.canonical_name
                            font.pixelSize: 20
                            font.bold: true
                            font.family: theme.serifFont
                            color: theme.text
                            wrapMode: Text.WordWrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }

                        Label {
                            text: (kind || "unknown") + " · " + (gameEntry.versions ? gameEntry.versions.length : 0) + " version(s)"
                            color: theme.textFaint
                            font.pixelSize: 13
                            font.family: theme.monoFont
                        }

                        Label {
                            visible: root.statusText(gameEntry) !== ""
                            text: root.statusText(gameEntry)
                            color: theme.statusColor(root.statusFor(gameEntry))
                            font.pixelSize: 13
                            font.bold: true
                        }

                        Item { Layout.fillHeight: true }
                    }
                }
            }

            Label {
                anchors.centerIn: parent
                visible: grid.count === 0
                text: root.searchText !== "" ? "No games match “" + root.searchText + "”." : "No games tracked yet -- tap Scan Folder to get started."
                color: theme.textFaint
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
