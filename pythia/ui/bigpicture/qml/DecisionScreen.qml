import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The touch-first version of cli.py's interactive `onboard` prompt: large buttons,
// one decision per screen. `preview` is set by whoever pushes this screen (either a
// live ambiguous onboard, or a post-scan review item) and always has the shape
// {path, name, candidates: {certain, suggested}} -- see backend.py.
Item {
    id: root

    property var preview: null
    signal done()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 32
        spacing: 24

        Label {
            text: "Is this a new game, or one you already track?"
            font.pixelSize: 26
            font.bold: true
            color: "white"
        }

        Label {
            text: root.preview ? ("“" + root.preview.name + "”") : ""
            font.pixelSize: 20
            color: "#7fb3ff"
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Label {
            text: root.preview ? root.preview.path : ""
            font.pixelSize: 14
            color: "#888888"
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
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
                color: "#2a2a2a"
                border.color: "#3d3d3d"

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 16

                    ColumnLayout {
                        Layout.fillWidth: true
                        Label {
                            text: modelData.canonical_name
                            font.pixelSize: 20
                            color: "white"
                        }
                        Label {
                            text: "game_id " + modelData.game_id
                            font.pixelSize: 14
                            color: "#999999"
                        }
                    }

                    Button {
                        text: "This is the one"
                        font.pixelSize: 18
                        implicitHeight: 56
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
            onClicked: {
                backend.resolveDecision(root.preview.path, { action: "new" })
                root.done()
            }
        }
    }
}
