"""
Shared design tokens for the desktop (QtWidgets) UI -- translated directly from the
validated v4 mockup's CSS custom properties (dark ink/amber palette, serif headings,
mono for data/paths/IDs, three-way status color that's never conflated). See the
Milestone 5 plan for the mockup this was validated against.

Big-picture (QML) doesn't consume this yet -- it still uses Milestone 2's plain
colors. Giving it the same token system is real, scoped-out follow-up work, not
done this pass (desktop is what this session's actual testing has exercised).
"""

COLORS = {
    "ink": "#14131f",
    "surface": "#201e30",
    "surface_raised": "#262439",
    "surface_sunken": "#1a1828",
    "border": "#34324a",
    "text": "#e9e7f2",
    "text_muted": "#938fac",
    "text_faint": "#625f7a",
    "accent": "#d9a441",
    "accent_soft": "#3a331f",
    "accent_text": "#2a1f08",
    "ok": "#5fb88a",
    "ok_soft": "#1c2b24",
    "pending": "#7b8fd4",
    "pending_soft": "#212440",
    "critical": "#e0616e",
    "critical_soft": "#341f24",
}

# Font stacks with real fallbacks -- Qt's QSS parser accepts comma-separated
# font-family lists, same as CSS. Iowan Old Style/Palatino are mac-only in
# practice; Georgia is the closest serif actually present on stock Windows.
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
}
_BADGE_FALLBACK = ("#2e2c42", "#46425f")


def badge_gradient(kind: str | None) -> tuple:
    return _BADGE_GRADIENTS.get(kind or "", _BADGE_FALLBACK)


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
    #rail, #detailHead, #listSearch {{
        background: {c['surface']};
    }}
    #rail {{
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
        color: {c['text_muted']};
    }}
    #navList::item:selected {{
        background: {c['accent_soft']};
        color: {c['accent']};
    }}
    """
