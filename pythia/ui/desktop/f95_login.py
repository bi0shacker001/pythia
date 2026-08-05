"""
F95zone embedded-browser support (2026-08-03) -- a real, reusable QtWebEngine
session, not a one-off login dialog. F95Checker itself uses its webview for more
than logging in: browsing forum threads, picking a version/build, and resolving
masked download links all happen inside its embedded browser too (confirmed
against the reference clone's modules/webview.py -- `start()` is a general
"open this URL in a real browser context" primitive, login is just its first
caller). Pythia's version follows the same shape: one shared profile/session
(`get_shared_profile()`), a reusable view (`F95WebView`), and dialogs built on
top of it for whichever specific job needs a real browser -- login today,
thread-browsing/download-initiation/version-selection as those get built next.

Ported from F95Checker's real login() (modules/api.py) + webview.py, adapted from
its dedicated PyQt6 webview *subprocess* (needed there to avoid an OpenGL conflict
with F95Checker's own imgui rendering) to an in-process PySide6 QtWebEngineWidgets
view, since Pythia's desktop UI has no such conflict today. Real, stated risk not
hidden: if the QML big-picture mode (also GPU/OpenGL-backed) turns out to conflict
with QtWebEngine in the same process, that needs a real subprocess-based fix
later, same shape as F95Checker's -- not guessed here since it hasn't actually
been observed.

Session persistence: cookies captured here are the same ones f95.py's
is_logged_in()/get_session_cookies()/set_session_cookies() read and write (backed
by pythia.credentials, synced via datadir/ like every other plugin's account
state) -- so logging in once on one system carries over to others, same as
Steam/GOG/Epic's account credentials.
"""

from PySide6.QtCore import QUrl
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

F95_DOMAIN = "f95zone.to"
F95_LOGIN_PAGE = f"https://{F95_DOMAIN}/login/"

_shared_profile = None


def get_shared_profile() -> QWebEngineProfile:
    """One shared, in-memory QWebEngineProfile reused across every F95zone
    dialog this session (login, browsing, future download/version dialogs) --
    not the default global profile, so it never mixes with any other site
    Pythia might ever load in a webview. Seeded from Pythia's own stored
    session cookies on first use (so a returning user isn't asked to log in
    again every launch), and kept in sync going forward: every newly-observed
    f95zone.to cookie is written straight back to storage."""
    global _shared_profile
    if _shared_profile is not None:
        return _shared_profile

    from pythia.plugins.library import f95

    profile = QWebEngineProfile("pythia-f95zone")
    store = profile.cookieStore()

    for name, value in f95.get_session_cookies().items():
        cookie = QNetworkCookie(name.encode("utf-8"), value.encode("utf-8"))
        cookie.setDomain(f".{F95_DOMAIN}")
        cookie.setPath("/")
        store.setCookie(cookie)

    def _on_cookie(cookie):
        domain = cookie.domain().lstrip(".")
        if F95_DOMAIN not in domain:
            return
        name = bytes(cookie.name().data()).decode("utf-8", "replace")
        value = bytes(cookie.value().data()).decode("utf-8", "replace")
        cookies = f95.get_session_cookies()
        cookies[name] = value
        f95.set_session_cookies(cookies)

    store.cookieAdded.connect(_on_cookie)
    _shared_profile = profile
    return profile


class F95WebView(QWebEngineView):
    """A QWebEngineView bound to the shared F95zone profile/session -- the
    reusable piece every F95zone-facing dialog embeds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page = QWebEnginePage(get_shared_profile(), self)
        self.setPage(self._page)


class F95BrowserDialog(QDialog):
    """General-purpose "open this F95zone URL in a real browser" dialog -- the
    base a thread-browsing/version-picker/download-initiation UI builds on.
    Not wired to any specific game-detail action yet; that's real follow-up
    work once it's clear which UI surfaces need it."""

    def __init__(self, url: str, title: str = "F95zone", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 800)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = F95WebView(self)
        layout.addWidget(self._view)
        self._view.load(QUrl(url))


class F95LoginDialog(QDialog):
    """Shows F95zone's real login page in the shared embedded browser.
    `self.session_cookies` holds every f95zone.to cookie captured by the time
    the dialog closes -- populated once `xf_user` shows up (login complete,
    dialog auto-closes; same completion signal F95Checker's own login() waits
    on) or empty if the user closes the window first."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Log in to F95zone")
        self.resize(560, 760)
        self.session_cookies: dict[str, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        hint = QLabel("Log in below -- this window closes automatically once you're signed in.")
        hint.setContentsMargins(10, 8, 10, 6)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._view = F95WebView(self)
        layout.addWidget(self._view)
        get_shared_profile().cookieStore().cookieAdded.connect(self._on_cookie)
        self._view.load(QUrl(F95_LOGIN_PAGE))

    def _on_cookie(self, cookie):
        domain = cookie.domain().lstrip(".")
        if F95_DOMAIN not in domain:
            return
        name = bytes(cookie.name().data()).decode("utf-8", "replace")
        value = bytes(cookie.value().data()).decode("utf-8", "replace")
        self.session_cookies[name] = value
        if name == "xf_user":
            self.accept()
