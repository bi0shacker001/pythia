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
