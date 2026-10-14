"""Interactive F95zone masked-link resolver.

F95Checker resolves `/masked/.../` links by loading the authenticated page in
QtWebEngine and waiting for its real `a.host_link` anchor. This implements the
same browser-dependent boundary: it never guesses or attempts to bypass the
page, CAPTCHA, or login flow.
"""

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QPushButton, QVBoxLayout


class F95LoginDialog(QDialog):
    sessionSaved = Signal(object)

    def __init__(self, existing_cookies=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in to F95zone")
        self.resize(900, 720)
        self._cookies = dict(existing_cookies or {})
        self._view = QWebEngineView(self)
        self._profile = QWebEngineProfile(self)
        self._view.setPage(QWebEnginePage(self._profile, self._view))
        store = self._profile.cookieStore()
        store.cookieAdded.connect(self._cookie_added)
        origin = QUrl("https://f95zone.to/")
        for name, value in self._cookies.items():
            cookie = QNetworkCookie(str(name).encode(), str(value).encode())
            cookie.setDomain(".f95zone.to")
            cookie.setPath("/")
            cookie.setSecure(True)
            store.setCookie(cookie, origin)
        save = QPushButton("Save session", self)
        save.clicked.connect(self._save)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        layout.addWidget(save)
        self._view.load(QUrl("https://f95zone.to/login/"))

    def _cookie_added(self, cookie):
        domain = cookie.domain().lstrip(".").lower()
        if domain == "f95zone.to" or domain.endswith(".f95zone.to"):
            self._cookies[bytes(cookie.name()).decode("utf-8")] = bytes(cookie.value()).decode("utf-8")

    def _save(self):
        if not self._cookies:
            return
        self.sessionSaved.emit(dict(self._cookies))
        self.accept()


class DownloadHostLoginDialog(QDialog):
    sessionSaved = Signal(str, object)

    def __init__(self, url: str, existing_cookies=None, parent=None):
        super().__init__(parent)
        target = QUrl(url)
        if not target.isValid() or target.scheme() not in {"http", "https"} or not target.host():
            raise ValueError("A valid HTTP(S) download-host URL is required.")
        self._host = target.host().lower()
        self._cookies = dict(existing_cookies or {})
        self.setWindowTitle(f"Sign in to {self._host}")
        self.resize(900, 720)
        self._view = QWebEngineView(self)
        self._profile = QWebEngineProfile(self)
        self._view.setPage(QWebEnginePage(self._profile, self._view))
        store = self._profile.cookieStore()
        store.cookieAdded.connect(self._cookie_added)
        origin = QUrl(f"{target.scheme()}://{self._host}/")
        for name, value in self._cookies.items():
            cookie = QNetworkCookie(str(name).encode(), str(value).encode())
            cookie.setDomain(self._host)
            cookie.setPath("/")
            cookie.setSecure(target.scheme() == "https")
            store.setCookie(cookie, origin)
        save = QPushButton("Save host session", self)
        save.clicked.connect(self._save)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        layout.addWidget(save)
        self._view.load(target)

    def _cookie_added(self, cookie):
        domain = cookie.domain().lstrip(".").lower()
        if domain == self._host or self._host.endswith("." + domain) or domain.endswith("." + self._host):
            self._cookies[bytes(cookie.name()).decode()] = bytes(cookie.value()).decode()

    def _save(self):
        if self._cookies:
            self.sessionSaved.emit(self._host, dict(self._cookies))
            self.accept()


class F95MaskResolver(QDialog):
    resolved = Signal(str)
    failed = Signal(str)

    def __init__(self, masked_url: str, cookies: dict, parent=None, timeout_ms: int = 120_000):
        super().__init__(parent)
        url = QUrl(masked_url)
        if url.scheme() != "https" or url.host().lower() not in {"f95zone.to", "www.f95zone.to"}:
            raise ValueError("Only HTTPS links on f95zone.to can be resolved.")
        if not url.path().startswith("/masked/"):
            raise ValueError("This is not an F95zone masked link.")

        self.setWindowTitle("Resolve F95zone download link")
        self.resize(720, 640)
        self._finished = False
        self._view = QWebEngineView(self)
        self._profile = QWebEngineProfile(self)
        self._view.setPage(QWebEnginePage(self._profile, self._view))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        store = self._profile.cookieStore()
        origin = QUrl("https://f95zone.to/")
        for name, value in cookies.items():
            cookie = QNetworkCookie(str(name).encode("utf-8"), str(value).encode("utf-8"))
            cookie.setDomain(".f95zone.to")
            cookie.setPath("/")
            cookie.setSecure(True)
            store.setCookie(cookie, origin)

        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._check_link)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self._fail("Timed out waiting for the masked link to resolve."))
        self._view.loadFinished.connect(self._on_load_finished)
        self._view.load(url)
        self._timeout.start(timeout_ms)

    def _on_load_finished(self, ok: bool):
        if not ok:
            self._fail("F95zone could not load the masked-link page.")
            return
        self._poll.start()
        self._check_link()

    def _check_link(self):
        self._view.page().runJavaScript(
            "(() => { const a=document.querySelector('a.host_link'); return a ? a.href : ''; })()",
            self._handle_link,
        )

    def _handle_link(self, link):
        if self._finished or not link:
            return
        url = QUrl(str(link))
        if not url.isValid() or url.scheme() not in {"http", "https"}:
            return
        self._finished = True
        self._poll.stop()
        self._timeout.stop()
        self.resolved.emit(url.toString())
        self.accept()

    def _fail(self, message: str):
        if self._finished:
            return
        self._finished = True
        self._poll.stop()
        self.failed.emit(message)
        self.reject()

    def reject(self):
        if not self._finished:
            self._finished = True
            self._poll.stop()
            self._timeout.stop()
            self.failed.emit("Masked-link resolution was cancelled.")
        super().reject()
