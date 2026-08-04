"""
Shared design tokens for the desktop (QtWidgets) UI -- translated from the
validated mockup's real CSS custom properties (pythia_design_direction_v14_signoff.
html, the sign-off pass of the 5-round external design review). Three real
palettes now (2026-08-03), not one: Ink & Amber (dark, default), Parchment
(light), and Slate & Cobalt (dark, blue-leaning) -- the mockup's actual
`html[data-theme="..."]` variants, ported value-for-value, not invented.

Theme switching is a real, persisted setting (`ui_theme` via identity.get_setting/
set_setting, same pattern as `ui_mode`) but takes effect on next launch, not live
-- `COLORS` is resolved once at import time from whichever palette is stored.
Building a live-restyle path (re-applying every already-constructed widget's
inline stylesheet) is real, scoped-out follow-up work, not attempted here.

Big-picture (QML) doesn't consume this yet -- it still uses Milestone 2's plain
colors. Giving it the same token system is real, scoped-out follow-up work, not
done this pass (desktop is what this session's actual testing has exercised).
"""

# Real per-theme token values, ported directly from the mockup's :root/
# html[data-theme=...] blocks -- keys match the mockup's own custom-property
# names (translated to snake_case) so they can be checked against the source
# CSS directly rather than trusted from memory.
_PALETTE_TOKENS = {
    "ink": {
        "page": "#12111a", "chrome": "#181722", "sidebar_surface": "#15141e",
        "surface_0": "#191824", "surface_1": "#201e2d", "surface_2": "#282537", "surface_3": "#302c42",
        "border": "#39364d", "border_strong": "#4a4661",
        "text_primary": "#eeeaf5", "text_secondary": "#aaa5bc", "text_tertiary": "#767188",
        "accent": "#d9a441", "accent_hover": "#ebba63", "accent_soft": "#39301d", "on_accent": "#251b08",
        "positive": "#63bd91", "positive_soft": "#192c24",
        "warning": "#d9a441", "warning_soft": "#342a18",
        "critical": "#df6976", "critical_soft": "#351d25",
        "informational": "#78a9dc", "informational_soft": "#182a3c",
    },
    "parchment": {
        "page": "#ece7da", "chrome": "#f7f4ec", "sidebar_surface": "#e7e1d2",
        "surface_0": "#faf8f2", "surface_1": "#ffffff", "surface_2": "#f3eee2", "surface_3": "#e9e1d0",
        "border": "#d5ccb8", "border_strong": "#bdb19a",
        "text_primary": "#28241f", "text_secondary": "#6b6356", "text_tertiary": "#948a78",
        "accent": "#a8672a", "accent_hover": "#bd7a36", "accent_soft": "#f0dfc2", "on_accent": "#fff9ed",
        "positive": "#2f8f5e", "positive_soft": "#e2f2e7",
        "warning": "#a8672a", "warning_soft": "#f4e5cc",
        "critical": "#b8433f", "critical_soft": "#f7e2df",
        "informational": "#356f9c", "informational_soft": "#e0edf6",
    },
    "cobalt": {
        "page": "#0e141d", "chrome": "#121b26", "sidebar_surface": "#101823",
        "surface_0": "#151f2b", "surface_1": "#1a2634", "surface_2": "#223142", "surface_3": "#293b4e",
        "border": "#314559", "border_strong": "#466078",
        "text_primary": "#e7edf3", "text_secondary": "#9cacbc", "text_tertiary": "#64778a",
        "accent": "#5fa9d8", "accent_hover": "#7dbce2", "accent_soft": "#1b3446", "on_accent": "#07141f",
        "positive": "#63bd91", "positive_soft": "#173128",
        "warning": "#d4a95d", "warning_soft": "#302819",
        "critical": "#df6976", "critical_soft": "#391e27",
        "informational": "#78a9dc", "informational_soft": "#182c40",
    },
}

_PALETTE_LABELS = {"ink": "Ink & Amber", "parchment": "Parchment", "cobalt": "Slate & Cobalt"}
_DEFAULT_PALETTE = "ink"


def available_palettes() -> list[str]:
    return list(_PALETTE_TOKENS)


def palette_label(name: str) -> str:
    return _PALETTE_LABELS.get(name, name)


def get_palette_name() -> str:
    from pythia.identity import get_setting

    name = get_setting("ui_theme")
    return name if name in _PALETTE_TOKENS else _DEFAULT_PALETTE


def set_palette_name(name: str):
    from pythia.identity import set_setting

    if name in _PALETTE_TOKENS:
        set_setting("ui_theme", name)


def _flatten(tokens: dict) -> dict:
    """Map the mockup's real token names onto the flat key vocabulary the rest
    of the desktop UI already codes against -- so switching *which* palette
    backs COLORS never requires touching a call site, only this mapping."""
    return {
        "ink": tokens["page"],
        "chrome": tokens["chrome"],
        "sidebar_surface": tokens["sidebar_surface"],
        "surface": tokens["surface_1"],
        "surface_raised": tokens["surface_2"],
        "surface_sunken": tokens["surface_0"],
        "surface_deep": tokens["surface_3"],
        "border": tokens["border"],
        "border_strong": tokens["border_strong"],
        "text": tokens["text_primary"],
        "text_muted": tokens["text_secondary"],
        "text_faint": tokens["text_tertiary"],
        "accent": tokens["accent"],
        "accent_hover": tokens["accent_hover"],
        "accent_soft": tokens["accent_soft"],
        "accent_text": tokens["on_accent"],
        "ok": tokens["positive"],
        "ok_soft": tokens["positive_soft"],
        "pending": tokens["warning"],
        "pending_soft": tokens["warning_soft"],
        "critical": tokens["critical"],
        "critical_soft": tokens["critical_soft"],
        "info": tokens["informational"],
        "info_soft": tokens["informational_soft"],
    }


COLORS = _flatten(_PALETTE_TOKENS[get_palette_name()])

# Font stacks with real fallbacks -- Qt's QSS parser accepts comma-separated
# font-family lists, same as CSS. Iowan Old Style/Palatino are mac-only in
# practice; Georgia is the closest serif actually present on stock Windows.
# Ported from the mockup's --serif/--sans/--mono (Inter substituted with the
# same Segoe UI fallback chain Windows actually has, since Inter isn't a
# system font here).
SERIF_FONT = '"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif'
SANS_FONT = '"Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
MONO_FONT = '"Cascadia Code", "SF Mono", Consolas, "Roboto Mono", monospace'

# Badge background gradients, keyed by engine/library kind -- falls back to the
# "unknown" gradient for every kind not listed (most engines added this session
# don't have a bespoke color yet; adding one per engine is cosmetic follow-up, not
# a functional gap).
_BADGE_GRADIENTS = {
    "steam": ("#1b2838", "#2a4a63"),
    "renpy": ("#5a3a1e", "#b3792a"),
    "gog": ("#3d2b1f", "#8a5a2a"),
    "egs": ("#1a1a2e", "#3a3a5e"),
    "itch": ("#4a1a1a", "#8a2a2a"),
    "f95": ("#2a1a3a", "#5a3a7a"),
}
_BADGE_FALLBACK = ("#2e2c42", "#46425f")


def badge_gradient(kind: str | None) -> tuple:
    return _BADGE_GRADIENTS.get(kind or "", _BADGE_FALLBACK)


# Display labels for a plugin `kind` string -- purely cosmetic (the sidebar/detail
# views need *something* nicer than a raw "egs"/"f95" token), never load-bearing
# logic, so a kind with no entry here just falls back to a title-cased version of
# the raw string rather than needing a hardcoded row added per new plugin.
_KIND_LABELS = {
    "steam": "Steam",
    "gog": "GOG",
    "egs": "Epic Games",
    "itch": "itch.io",
    "f95": "F95zone",
    "renpy": "Ren'Py",
}


def kind_label(kind: str | None) -> str:
    if not kind or kind == "unknown":
        return "Unidentified"
    return _KIND_LABELS.get(kind, kind.replace("_", " ").title())


def status_color(status: str) -> str:
    return {"ok": COLORS["ok"], "pending": COLORS["pending"], "critical": COLORS["critical"]}.get(
        status, COLORS["text_muted"]
    )


def build_stylesheet() -> str:
    c = COLORS
    return f"""
    QWidget {{
        background: {c['ink']};
        color: {c['text']};
        font-family: {SANS_FONT};
        font-size: 13px;
    }}
    QMainWindow, QDialog {{
        background: {c['ink']};
    }}
    #detailHead, #listSearch {{
        background: {c['surface']};
    }}
    #rail {{
        background: {c['sidebar_surface']};
        border-right: 1px solid {c['border']};
    }}
    #gameList {{
        background: {c['surface']};
        border-right: 1px solid {c['border']};
        border: none;
        outline: none;
    }}
    #gameList::item {{
        border-bottom: 1px solid {c['border']};
        padding: 10px 12px;
    }}
    #gameList::item:selected {{
        background: {c['accent_soft']};
        color: {c['accent']};
    }}
    QListWidget, QTableWidget {{
        background: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: 8px;
    }}
    QTableWidget::item {{
        padding: 6px;
    }}
    QHeaderView::section {{
        background: {c['surface_sunken']};
        color: {c['text_muted']};
        border: none;
        border-bottom: 1px solid {c['border']};
        padding: 6px;
        font-family: {MONO_FONT};
        font-size: 11px;
        text-transform: uppercase;
    }}
    QLineEdit {{
        background: {c['surface_sunken']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 8px 12px;
        color: {c['text']};
    }}
    QLineEdit:focus {{
        border-color: {c['accent']};
    }}
    QPlainTextEdit, QTextEdit {{
        background: {c['surface_raised']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 10px;
        color: {c['text_muted']};
    }}
    QPushButton {{
        background: {c['surface_raised']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 8px 14px;
        color: {c['text']};
        font-weight: 600;
    }}
    QPushButton:hover {{
        border-color: {c['accent']};
    }}
    QPushButton#primary {{
        background: {c['accent']};
        border-color: {c['accent']};
        color: {c['accent_text']};
    }}
    QPushButton#ghostAction {{
        background: transparent;
        border: none;
        color: {c['text_muted']};
        font-weight: 500;
    }}
    QPushButton#ghostAction:hover {{
        background: {c['surface_raised']};
        color: {c['text']};
    }}
    QPushButton#segButton {{
        padding: 5px 14px;
        font-size: 11.5px;
        font-weight: 600;
        color: {c['text_muted']};
        background: transparent;
    }}
    QPushButton#segButton:checked {{
        background: {c['accent_soft']};
        border-color: {c['accent']};
        color: {c['accent']};
    }}
    QTabWidget::pane {{
        border: none;
        border-top: 1px solid {c['border']};
    }}
    QTabBar::tab {{
        background: transparent;
        color: {c['text_muted']};
        padding: 9px 14px;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{
        color: {c['accent']};
        border-bottom: 2px solid {c['accent']};
        font-weight: 600;
    }}
    QMenuBar {{
        background: {c['surface_sunken']};
        color: {c['text_muted']};
        border-bottom: 1px solid {c['border']};
    }}
    QMenuBar::item:selected {{
        background: {c['surface_raised']};
        color: {c['text']};
    }}
    QMenu {{
        background: {c['surface_raised']};
        border: 1px solid {c['border']};
        color: {c['text']};
    }}
    QMenu::item:selected {{
        background: {c['accent_soft']};
        color: {c['accent']};
    }}
    QStatusBar {{
        background: {c['surface_sunken']};
        border-top: 1px solid {c['border']};
        color: {c['text_faint']};
        font-family: {MONO_FONT};
        font-size: 11px;
    }}
    QSplitter::handle {{
        background: {c['border']};
    }}
    #navList {{
        border: none;
        background: transparent;
    }}
    #navList::item {{
        border-radius: 8px;
        padding: 8px 10px;
        border-left: 3px solid transparent;
        color: {c['text_muted']};
    }}
    #navList::item:selected {{
        background: {c['accent_soft']};
        border-left: 3px solid {c['accent']};
        color: {c['accent']};
    }}
    """
