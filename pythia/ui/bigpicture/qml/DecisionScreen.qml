import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The touch-first version of cli.py's interactive `onboard` prompt: large buttons,
// one decision per screen. `preview` is set by whoever pushes this screen (either a
// live ambiguous onboard, or a post-scan review item) and always has the shape
// {path, name, candidates: {certain, suggested}} -- see backend.py. Restyled to the
// shared token set (2026-08-03, big-picture design-token pass) -- previously plain
// greys from Milestone 2.
Item {
    id: root

    property var preview: null
    signal done()

    Theme { id: theme }

    Rectangle { anchors.fill: parent; color: theme.ink }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 32
        spacing: 24

        Label {
            text: "Is this a new game, or one you already track?"
            font.pixelSize: 26
            font.family: theme.serifFont
            color: theme.text
        }

        Label {
            text: root.preview ? ("“" + root.preview.name + "”") : ""
            font.pixelSize: 20
            color: theme.accent
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Label {
            text: root.preview ? root.preview.path : ""
            font.pixelSize: 14
            font.family: theme.monoFont
            color: theme.textFaint
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Label {
            visible: root.preview && root.preview.candidates && root.preview.candidates.suggested && root.preview.candidates.suggested.length > 0
            text: "This might already be one of these:"
            font.pixelSize: 16
            color: theme.textMuted
        }

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            clip: true
            model: root.preview ? root.preview.candidates.suggested : []

            delegate: Rectangle {
                width: ListView.view.width
                height: 88
                radius: 10
                color: theme.surface
                border.color: theme.border

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 16

                    ColumnLayout {
                        Layout.fillWidth: true
                        Label {
                            text: modelData.canonical_name
                            font.pixelSize: 20
                            font.family: theme.serifFont
                            color: theme.text
                        }
                        Label {
                            text: "game_id " + modelData.game_id
                            font.pixelSize: 13
                            font.family: theme.monoFont
                            color: theme.textFaint
                        }
                    }

                    Button {
                        text: "This is the one"
                        font.pixelSize: 18
                        implicitHeight: 56
                        contentItem: Label {
                            text: parent.text
                            color: theme.accentText
                            font.pixelSize: 18
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle { color: theme.accent; radius: 8 }
                        onClicked: {
                            backend.resolveDecision(root.preview.path, {
                                action: "link",
                                game_id: modelData.game_id,
                                import_to_location: false
                            })
                            root.done()
                        }
                    }
                }
            }
        }

        Button {
            text: "This is a new game"
            font.pixelSize: 22
            implicitHeight: 72
            Layout.fillWidth: true
            contentItem: Label {
                text: parent.text
                color: theme.text
                font.pixelSize: 22
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle { color: theme.surfaceRaised; radius: 10; border.color: theme.border; border.width: 1 }
            onClicked: {
                backend.resolveDecision(root.preview.path, { action: "new" })
                root.done()
            }
        }
    }
}
