import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Big-picture's Settings screen -- didn't exist at all before (2026-08-03).
// Touch-first left-rail-of-sections layout, backed by real data through backend.py
// (System & Sync, Downloaders, Accounts, Appearance) -- same real/generic-credential
// pattern as desktop's Settings dialog, ported to QML/touch instead of QtWidgets.
Item {
    id: root
    signal back()

    Theme { id: theme }

    Rectangle { anchors.fill: parent; color: theme.ink }

    property var sections: ["System & Sync", "Downloaders", "Accounts", "Appearance"]
    property int currentSection: 0

    property var systemInfo: ({})
    property var downloaderPlugins: []
    property var accountPlugins: []
    property string uiMode: ""

    function reload() {
        root.systemInfo = backend.get_system_info()
        root.downloaderPlugins = backend.get_downloader_plugins()
        root.accountPlugins = backend.get_account_plugins()
        root.uiMode = backend.get_ui_mode()
    }
    Component.onCompleted: reload()

    Connections {
        target: backend
        function onAccountSyncComplete(pluginId, result) { root.reload() }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 64
            Layout.margins: 16
            spacing: 16

            ToolButton {
                text: "←"
                font.pixelSize: 26
                onClicked: root.back()
            }
            Label {
                text: "Settings"
                font.pixelSize: 24
                font.family: theme.serifFont
                color: theme.text
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // Section rail
            ColumnLayout {
                Layout.preferredWidth: 240
                Layout.fillHeight: true
                spacing: 4
                Layout.margins: 8

                Repeater {
                    model: root.sections
                    delegate: Rectangle {
                        Layout.fillWidth: true
                        height: 56
                        radius: 8
                        color: root.currentSection === index ? theme.accentSoft : "transparent"
                        Label {
                            anchors.left: parent.left
                            anchors.leftMargin: 16
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData
                            font.pixelSize: 18
                            color: root.currentSection === index ? theme.accent : theme.textMuted
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: root.currentSection = index
                        }
                    }
                }
            }

            Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: theme.border }

            // Section content
            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: 24
                currentIndex: root.currentSection

                // --- System & Sync ---
                ScrollView {
                    ColumnLayout {
                        width: parent.width
                        spacing: 16

                        Label { text: "This system's identity and where its tracking data lives."; color: theme.textMuted; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }

                        GridLayout {
                            columns: 2
                            columnSpacing: 16
                            rowSpacing: 10
                            Label { text: "System UUID"; color: theme.textFaint; font.pixelSize: 14 }
                            Label { text: root.systemInfo.system_uuid || ""; color: theme.textMuted; font.family: theme.monoFont; font.pixelSize: 13 }
                            Label { text: "Sync folder"; color: theme.textFaint; font.pixelSize: 14 }
                            Label { text: root.systemInfo.sync_folder || ""; color: theme.textMuted; font.family: theme.monoFont; font.pixelSize: 13; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
                        }

                        Label { text: "Other systems seen"; color: theme.text; font.pixelSize: 16; Layout.topMargin: 12 }
                        Repeater {
                            model: root.systemInfo.other_systems || []
                            delegate: Label {
                                text: modelData.uuid.substring(0, 13) + "...  ·  " + modelData.count + " game(s)"
                                color: theme.textMuted
                                font.family: theme.monoFont
                                font.pixelSize: 13
                            }
                        }
                        Label {
                            visible: !root.systemInfo.other_systems || root.systemInfo.other_systems.length === 0
                            text: "No data synced from another system yet."
                            color: theme.textFaint
                            font.pixelSize: 14
                        }
                    }
                }

                // --- Downloaders ---
                ScrollView {
                    ColumnLayout {
                        width: parent.width
                        spacing: 12

                        Label { text: "Pluggable acquisition backends -- Pythia launches each as its own tool and watches for results."; color: theme.textMuted; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }

                        Repeater {
                            model: root.downloaderPlugins
                            delegate: RowLayout {
                                Layout.fillWidth: true
                                Label { text: modelData.display_name; color: theme.text; font.pixelSize: 16; Layout.fillWidth: true }
                                Switch {
                                    checked: modelData.enabled
                                    onToggled: backend.set_plugin_enabled(modelData.id, checked)
                                }
                            }
                        }
                        Label {
                            visible: root.downloaderPlugins.length === 0
                            text: "No downloader plugins registered."
                            color: theme.textFaint
                            font.pixelSize: 14
                        }
                    }
                }

                // --- Accounts ---
                ScrollView {
                    ColumnLayout {
                        width: parent.width
                        spacing: 20

                        Label { text: "Connect an account to pull your real owned library from it."; color: theme.textMuted; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }

                        Repeater {
                            model: root.accountPlugins
                            delegate: ColumnLayout {
                                id: accountDelegate
                                Layout.fillWidth: true
                                spacing: 8

                                Label { text: modelData.display_name; font.family: theme.serifFont; font.pixelSize: 18; color: theme.text }

                                Repeater {
                                    model: accountDelegate.modelData.fields
                                    delegate: RowLayout {
                                        Layout.fillWidth: true
                                        Label { text: modelData.label; color: theme.textFaint; font.pixelSize: 13; Layout.preferredWidth: 180 }
                                        TextField {
                                            id: fieldInput
                                            Layout.fillWidth: true
                                            echoMode: modelData.secret ? TextInput.Password : TextInput.Normal
                                            font.pixelSize: 14
                                            color: theme.text
                                            background: Rectangle { color: theme.surfaceSunken; radius: 6; border.color: theme.border }
                                            onEditingFinished: backend.set_account_credential(accountDelegate.modelData.id, modelData.key, text)
                                        }
                                    }
                                }

                                RowLayout {
                                    spacing: 10
                                    Button {
                                        text: "Sync Now"
                                        font.pixelSize: 14
                                        contentItem: Label { text: parent.text; color: theme.accentText; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                                        background: Rectangle { color: theme.accent; radius: 6 }
                                        onClicked: backend.sync_account(modelData.id)
                                    }
                                    Label {
                                        text: modelData.connected ? "Connected." : "Not connected."
                                        color: modelData.connected ? theme.ok : theme.textFaint
                                        font.pixelSize: 13
                                    }
                                }
                                Rectangle { Layout.fillWidth: true; height: 1; color: theme.border; Layout.topMargin: 8 }
                            }
                        }
                    }
                }

                // --- Appearance ---
                ScrollView {
                    ColumnLayout {
                        width: parent.width
                        spacing: 16

                        Label { text: "Default UI mode"; color: theme.text; font.pixelSize: 16 }
                        RowLayout {
                            spacing: 10
                            Repeater {
                                model: [
                                    { label: "Auto", value: "" },
                                    { label: "Desktop", value: "desktop" },
                                    { label: "Big Picture", value: "bigpicture" },
                                ]
                                delegate: Rectangle {
                                    readonly property bool active: root.uiMode === modelData.value
                                    width: chipLbl.implicitWidth + 28
                                    height: 40
                                    radius: 20
                                    color: active ? theme.accent : "transparent"
                                    border.color: active ? theme.accent : theme.border
                                    border.width: 1
                                    Label {
                                        id: chipLbl
                                        anchors.centerIn: parent
                                        text: modelData.label
                                        color: active ? theme.accentText : theme.textMuted
                                        font.pixelSize: 15
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        onClicked: {
                                            root.uiMode = modelData.value
                                            backend.set_ui_mode(modelData.value)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
