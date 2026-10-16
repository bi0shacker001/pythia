"""The web UI's colour roles and status wording (UX review A1, Droidtop/tracker
#538): accent, caution and critical are three different colours in every
theme, and the top bar never claims success."""

import re
from pathlib import Path
from unittest import TestCase

WEB = Path(__file__).resolve().parent.parent / "pythia" / "ui" / "desktop" / "web"


def theme(css: str, name: str) -> dict:
    block = re.search(r'(?:html\[data-theme="%s"\])\{(.*?)\n\}' % name, css, re.S)
    assert block, name
    return dict(re.findall(r"--([a-z0-9-]+):(#[0-9a-fA-F]{6})", block.group(1)))


class TokensTest(TestCase):
    def test_accent_caution_and_critical_differ_in_every_theme(self):
        css = (WEB / "tokens.css").read_text(encoding="utf-8")
        for name in ("ink", "parchment", "cobalt"):
            roles = theme(css, name)
            colours = {roles["accent"].lower(), roles["warning"].lower(), roles["critical-token"].lower()}
            self.assertEqual(len(colours), 3, name)

    def test_the_top_bar_never_says_library_read(self):
        for page in ("app.js", "index.html"):
            self.assertNotIn("Library read", (WEB / page).read_text(encoding="utf-8"), page)


def luminance(colour: str) -> float:
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# What the page draws as text on what (WCAG AA, 4.5:1), and as a ring or edge on what (3:1).
TEXT_PAIRS = [
    *[("text-primary", g) for g in ("page", "surface-0", "surface-1", "surface-2", "surface-3")],
    *[("text-secondary", g) for g in ("page", "surface-0", "surface-1", "surface-2", "surface-3")],
    *[("text-tertiary", g) for g in ("page", "surface-0", "surface-1", "surface-2")],
    ("on-accent", "accent"),
    ("accent", "page"),
    ("accent", "surface-0"),
    ("accent", "accent-soft"),
    ("positive", "positive-soft"),
    ("warning", "warning-soft"),
    ("critical-token", "critical-soft-token"),
    ("informational", "informational-soft"),
    ("caution-ink", "caution-plate"),
    ("danger-ink", "danger-plate"),
    ("info-ink", "info-plate"),
    ("on-danger-fill", "danger-fill"),
]
RING_PAIRS = [("focus-ring", "page"), ("focus-ring", "surface-0"), ("focus-ring", "surface-2")]


class TokenOnlyTest(TestCase):
    """UX review A10: one stylesheet that reads only tokens.css roles, and a
    contrast check across the three themes."""

    def test_no_colour_value_outside_tokens_css(self):
        colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(")
        for name in ("app.css", "components.css", "index.html"):
            text = (WEB / name).read_text(encoding="utf-8")
            # values only (after ':'); an id like #hhContent is not hex, so it never matches
            values = re.findall(r":\s*([^;{}]*)", re.sub(r"<script.*?</script>", "", text, flags=re.S))
            found = [v for v in values if colour.search(v)]
            self.assertEqual(found, [], name)
        self.assertNotIn("<style>", (WEB / "index.html").read_text(encoding="utf-8"))

    def test_text_and_rings_meet_contrast_in_every_theme(self):
        css = (WEB / "tokens.css").read_text(encoding="utf-8")
        for name in ("ink", "parchment", "cobalt"):
            roles = theme(css, name)
            for fg, bg in TEXT_PAIRS:
                self.assertGreaterEqual(contrast(roles[fg], roles[bg]), 4.5, f"{name}: {fg} on {bg}")
            for fg, bg in RING_PAIRS:
                self.assertGreaterEqual(contrast(roles[fg], roles[bg]), 3.0, f"{name}: {fg} on {bg}")

    def test_the_accent_and_caution_hues_stay_apart(self):
        import colorsys

        def hue(c: str) -> float:
            r, g, b = (int(c[i : i + 2], 16) / 255 for i in (1, 3, 5))
            return colorsys.rgb_to_hls(r, g, b)[0] * 360

        css = (WEB / "tokens.css").read_text(encoding="utf-8")
        for name in ("ink", "parchment", "cobalt"):
            roles = theme(css, name)
            gap = abs(hue(roles["accent"]) - hue(roles["warning"]))
            self.assertGreaterEqual(min(gap, 360 - gap), 15, name)

    def test_tags_read_from_text_roles(self):
        app = (WEB / "app.css").read_text(encoding="utf-8")
        tag = re.search(r"(?:^|[}\s])\.tag\{([^}]*)\}", app, re.M).group(1)
        self.assertIn("color:var(--text-secondary)", tag)
