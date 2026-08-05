import QtQuick

// Same design tokens as pythia/ui/theme.py's COLORS dict, ported to QML so
// big-picture mode reads from the same source of truth as the desktop QSS
// stylesheet instead of the placeholder greys Milestone 2 shipped with
// (2026-08-03 -- big-picture design-token pass). Not a pragma-Singleton (this
// app loads QML by direct file path, not as a registered module, so singleton
// registration would need a qmldir this project doesn't have) -- instead each
// screen instantiates one `Theme { id: theme }` at its own top level, same
// pattern QML file-based apps without a build step commonly use.
QtObject {
    readonly property color ink: "#14131f"
    readonly property color surface: "#201e30"
    readonly property color surfaceRaised: "#262439"
    readonly property color surfaceSunken: "#1a1828"
    readonly property color border: "#34324a"
    readonly property color text: "#e9e7f2"
    readonly property color textMuted: "#938fac"
    readonly property color textFaint: "#625f7a"
    readonly property color accent: "#d9a441"
    readonly property color accentSoft: "#3a331f"
    readonly property color accentText: "#2a1f08"
    readonly property color ok: "#5fb88a"
    readonly property color okSoft: "#1c2b24"
    readonly property color pending: "#7b8fd4"
    readonly property color pendingSoft: "#212440"
    readonly property color critical: "#e0616e"
    readonly property color criticalSoft: "#341f24"

    readonly property string serifFont: "Palatino Linotype"
    readonly property string sansFont: "Segoe UI"
    readonly property string monoFont: "Consolas"

    function statusColor(status) {
        if (status === "ok") return ok
        if (status === "pending") return pending
        if (status === "critical") return critical
        return textMuted
    }

    function badgeGradient(kind) {
        switch (kind) {
        case "steam": return ["#1b2838", "#2a4a63"]
        case "renpy": return ["#5a3a1e", "#b3792a"]
        case "gog": return ["#3d2b1f", "#8a5a2a"]
        case "egs": return ["#1a1a2e", "#3a3a5e"]
        case "itch": return ["#4a1a1a", "#8a2a2a"]
        case "f95": return ["#2a1a3a", "#5a3a7a"]
        default: return ["#2a2840", "#413d5e"]
        }
    }
}
