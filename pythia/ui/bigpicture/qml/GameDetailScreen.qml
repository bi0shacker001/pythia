import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Per-game detail -- big-picture had no way to drill into a tracked game at all
// before this (tapping a HomeScreen card did nothing); this closes that gap,
// mirroring desktop's master-detail Versions/Notes/History tabs (see
// pythia/ui/desktop/app.py's _build_detail_panel/_show_detail) with the same
// touch-first large-target language as the rest of big-picture.
Item {
    id: root

    property int gameId: -1
    // entries[0] is this system's own view of the game -- same "primary_entry
    // only, not merged across systems" convention desktop's GameViewModel uses
    // for versions/notes/history.
    property var entry: null
    property var managerCandidates: []

    signal back()

    Theme { id: theme }

    function representativeKind() {
        if (!entry || !entry.versions) return null
        for (var i = 0; i < entry.versions.length; i++) {
            var variants = entry.versions[i].variants || []
            for (var j = 0; j < variants.length; j++) {
                var source = variants[j].source
                if (source && source.kind) return source.kind
            }
        }
        return null
    }

    function refreshManagerCandidates() {
        managerCandidates = (root.gameId >= 0) ? backend.find_manager_for_game(root.gameId) : []
    }

    Component.onCompleted: {
        refreshManagerCandidates()
        notesArea.text = entry ? (entry.notes || "") : ""
    }

    readonly property var eventLabels: ({
        "game_added": "Game added",
        "version_added": "New version detected",
        "ownership_recorded": "Ownership recorded",
        "merged": "Merged with another entry",
        "linked_cross_system": "Linked to another system's record",
    })

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 20

        // ---- head: badge, name, subtitle, Open button --------------------
        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            Rectangle {
                Layout.preferredWidth: 64
                Layout.preferredHeight: 64
                radius: 8
                gradient: Gradient {
                    orientation: Gradient.Diagonal
                    GradientStop { position: 0.0; color: theme.badgeGradient(root.representativeKind())[0] }
                    GradientStop { position: 1.0; color: theme.badgeGradient(root.representativeKind())[1] }
                }
                Label {
                    anchors.centerIn: parent
                    text: entry ? (entry.canonical_name || "?").substring(0, 1).toUpperCase() : "?"
                    color: "#ffffff"
                    font.pixelSize: 28
                    font.family: theme.serifFont
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Label {
                    text: entry ? entry.canonical_name : ""
                    font.pixelSize: 26
                    font.bold: true
                    font.family: theme.serifFont
                    color: theme.text
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Label {
                    text: "game_id " + root.gameId + " · " + (root.representativeKind() || "unknown")
                    font.pixelSize: 14
                    font.family: theme.monoFont
                    color: theme.textFaint
                }
            }

            Button {
                visible: root.managerCandidates && root.managerCandidates.length > 0
                text: root.managerCandidates && root.managerCandidates.length > 0
                    ? "Open in " + root.managerCandidates[0].manager_name : "Open"
                implicitHeight: 52
                implicitWidth: 220
                contentItem: Label {
                    text: parent.text
                    font.pixelSize: 16
                    font.weight: Font.DemiBold
                    color: theme.accentText
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle { color: theme.accent; radius: 8 }
                onClicked: openConfirmDialog.open()
            }
        }

        // ---- tab bar -------------------------------------------------------
        TabBar {
            id: tabBar
            Layout.fillWidth: true
            background: Rectangle { color: "transparent" }

            TabButton {
                text: "Versions"
                contentItem: Label {
                    text: parent.text
                    font.pixelSize: 16
                    color: tabBar.currentIndex === 0 ? theme.accent : theme.textMuted
                    horizontalAlignment: Text.AlignHCenter
                }
                background: Rectangle {
                    color: "transparent"
                    Rectangle {
                        anchors.bottom: parent.bottom
                        width: parent.width
                        height: 3
                        color: tabBar.currentIndex === 0 ? theme.accent : "transparent"
                    }
                }
            }
            TabButton {
                text: "Notes"
                contentItem: Label {
                    text: parent.text
                    font.pixelSize: 16
                    color: tabBar.currentIndex === 1 ? theme.accent : theme.textMuted
                    horizontalAlignment: Text.AlignHCenter
                }
                background: Rectangle {
                    color: "transparent"
                    Rectangle {
                        anchors.bottom: parent.bottom
                        width: parent.width
                        height: 3
                        color: tabBar.currentIndex === 1 ? theme.accent : "transparent"
                    }
                }
            }
            TabButton {
                text: "History"
                contentItem: Label {
                    text: parent.text
                    font.pixelSize: 16
                    color: tabBar.currentIndex === 2 ? theme.accent : theme.textMuted
                    horizontalAlignment: Text.AlignHCenter
                }
                background: Rectangle {
                    color: "transparent"
                    Rectangle {
                        anchors.bottom: parent.bottom
                        width: parent.width
                        height: 3
                        color: tabBar.currentIndex === 2 ? theme.accent : "transparent"
                    }
                }
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: tabBar.currentIndex

            // ---- Versions --------------------------------------------------
            ScrollView {
                clip: true
                ColumnLayout {
                    width: parent.width
                    spacing: 14

                    Label {
                        visible: !entry || !entry.versions || entry.versions.length === 0
                        text: "No versions recorded."
                        color: theme.textFaint
                        font.pixelSize: 16
                    }

                    Repeater {
                        model: entry && entry.versions ? entry.versions : []
                        delegate: ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            property var group: modelData

                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: group.version || "?"
                                    font.pixelSize: 18
                                    font.family: theme.serifFont
                                    color: theme.text
                                }
                                Label {
                                    text: (group.variants ? group.variants.length : 0) + " variant(s)"
                                    font.pixelSize: 12
                                    font.family: theme.monoFont
                                    color: theme.textFaint
                                }
                                Item { Layout.fillWidth: true }
                            }

                            Repeater {
                                model: group.variants || []
                                delegate: Rectangle {
                                    Layout.fillWidth: true
                                    Layout.leftMargin: 20
                                    implicitHeight: variantRow.implicitHeight + 16
                                    radius: 8
                                    color: theme.surface
                                    border.color: theme.border
                                    border.width: 1

                                    RowLayout {
                                        id: variantRow
                                        anchors.fill: parent
                                        anchors.margins: 10
                                        spacing: 10

                                        Rectangle {
                                            radius: 100
                                            color: (modelData.mods && modelData.mods.length > 0) ? theme.accentSoft : "transparent"
                                            border.color: (modelData.mods && modelData.mods.length > 0) ? "transparent" : theme.border
                                            border.width: 1
                                            implicitWidth: modTag.implicitWidth + 18
                                            implicitHeight: modTag.implicitHeight + 8
                                            Label {
                                                id: modTag
                                                anchors.centerIn: parent
                                                text: (modelData.mods && modelData.mods.length > 0)
                                                    ? modelData.mods.map(function(m) { return m.name }).join(", ")
                                                    : "vanilla"
                                                font.pixelSize: 12
                                                font.family: theme.monoFont
                                                color: (modelData.mods && modelData.mods.length > 0) ? theme.accent : theme.textFaint
                                            }
                                        }

                                        Label {
                                            visible: !!modelData.language
                                            text: modelData.language || ""
                                            font.pixelSize: 12
                                            font.family: theme.monoFont
                                            color: theme.textFaint
                                        }

                                        Label {
                                            Layout.fillWidth: true
                                            text: modelData.path || ""
                                            font.pixelSize: 12
                                            font.family: theme.monoFont
                                            color: theme.textMuted
                                            elide: Text.ElideMiddle
                                        }

                                        Label {
                                            text: (modelData.platforms || []).join(", ") || "?"
                                            font.pixelSize: 12
                                            font.bold: true
                                            color: theme.ok
                                        }
                                    }
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 10
                        spacing: 6
                        visible: entry && entry.available_updates && entry.available_updates.length > 0

                        Label {
                            text: "Available, not installed"
                            font.pixelSize: 15
                            font.bold: true
                            color: theme.pending
                        }
                        Repeater {
                            model: entry && entry.available_updates ? entry.available_updates : []
                            delegate: Label {
                                text: (modelData.platform || "?") + ": " + (modelData.version || "?") +
                                      " (via " + (modelData.source || "?") + ")"
                                font.pixelSize: 13
                                font.family: theme.monoFont
                                color: theme.textMuted
                            }
                        }
                    }
                }
            }

            // ---- Notes -------------------------------------------------------
            ColumnLayout {
                spacing: 12
                TextArea {
                    id: notesArea
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    placeholderText: "No notes yet."
                    font.pixelSize: 15
                    color: theme.text
                    background: Rectangle {
                        color: theme.surfaceSunken
                        border.color: theme.border
                        radius: 8
                    }
                }
                Button {
                    text: "Save Notes"
                    implicitHeight: 48
                    implicitWidth: 160
                    contentItem: Label {
                        text: parent.text
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                        color: theme.accentText
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle { color: theme.accent; radius: 8 }
                    onClicked: {
                        backend.save_notes(root.gameId, notesArea.text)
                        saveStatusLabel.text = "Saved."
                        saveStatusTimer.restart()
                    }
                }
                Label {
                    id: saveStatusLabel
                    color: theme.ok
                    font.pixelSize: 13
                }
                Timer {
                    id: saveStatusTimer
                    interval: 2500
                    onTriggered: saveStatusLabel.text = ""
                }
            }

            // ---- History -------------------------------------------------------
            ScrollView {
                clip: true
                ColumnLayout {
                    width: parent.width
                    spacing: 8

                    Label {
                        visible: !entry || !entry.timeline_events || entry.timeline_events.length === 0
                        text: "No history recorded yet for this system's copy of this entry."
                        color: theme.textFaint
                        font.pixelSize: 15
                    }

                    Repeater {
                        // Newest first, same as desktop's history tab.
                        model: {
                            var events = (entry && entry.timeline_events) ? entry.timeline_events.slice() : []
                            events.reverse()
                            return events
                        }
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            spacing: 12
                            Label {
                                text: (modelData.timestamp || "").substring(0, 19).replace("T", " ")
                                font.pixelSize: 12
                                font.family: theme.monoFont
                                color: theme.textFaint
                                Layout.preferredWidth: 160
                            }
                            Label {
                                text: root.eventLabels[modelData.type] || modelData.type || ""
                                font.pixelSize: 13
                                color: theme.text
                            }
                            Label {
                                visible: !!modelData.detail
                                text: modelData.detail || ""
                                font.pixelSize: 12
                                font.family: theme.monoFont
                                color: theme.textMuted
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
            }
        }
    }

    Dialog {
        id: openConfirmDialog
        anchors.centerIn: parent
        modal: true
        title: "Open game"
        standardButtons: Dialog.Yes | Dialog.No
        contentItem: Label {
            text: root.managerCandidates && root.managerCandidates.length > 0
                ? "This will hand off to " + root.managerCandidates[0].manager_name + " to launch “" +
                  (entry ? entry.canonical_name : "") + "”. Proceed?"
                : ""
            wrapMode: Text.WordWrap
            color: theme.text
        }
        onAccepted: {
            if (root.managerCandidates && root.managerCandidates.length > 0) {
                backend.open_in_manager(root.gameId, root.managerCandidates[0].manager_id)
            }
        }
    }
}
