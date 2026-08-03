"""
Local HTTP RPC server compatible with F95Checker's real official browser extension
(2026-08-03, per explicit user direction: "we don't want two softwares managing
library updates... that's also why we want to be compatible with the browser
extension"). Protocol confirmed directly against F95Checker's actual source
(`modules/rpc_thread.py`, `common/meta.py`, `modules/utils.py` --
github.com/WillyJL/F95Checker, GPLv3, cloned as a reference, never imported from
directly): the extension is a static, published Chrome/Firefox add-on that always
talks to `http://127.0.0.1:57095` with a small, fixed set of endpoints. Serving the
same endpoint shapes on the same port means the *same, official, unmodified*
extension works against Pythia instead of F95Checker -- no fork of the extension
needed.

Endpoints implemented (real F95Checker request/response shapes):
- `GET /games` -> `[{"id", "icon", "color", "notes"}, ...]` for every tracked f95
  entry (used by the extension to badge/highlight threads already tracked).
- `POST /games/add` -> body is a JSON list of thread URLs (or bare text containing
  them). Thread ids are extracted with the exact same regex F95Checker's own
  `utils.extract_thread_matches()` uses, then folded into datadir via
  `onboarding.record_ownership()` -- the same primitive `sync_owned()`'s bulk import
  uses, just one thread at a time (ported logic-shape from F95Checker's own
  `callbacks.add_games()`, not its literal code, since that function is tightly
  coupled to F95Checker's own async GUI runtime).
- `GET /settings` -> minimal stub (icon glow/tag highlighting are display prefs the
  extension merely reads; Pythia doesn't have an equivalent settings surface yet, so
  this reports safe defaults rather than fabricating support that isn't there).
- `POST /window/show`, `/window/hide`, `/window/refresh_styles` -> no-ops for now
  (Pythia's own window isn't reachable from here yet -- real future wiring once the
  desktop UI exposes a raise/hide hook via backend.py).

Deliberately NOT included: F95Checker's own `/assets/mdi-webfont.ttf` -- purely a
font asset for its extension's icon rendering; a 404 there just means those specific
icons don't render, not a functional break.
"""

import http.server
import json
import re
import socketserver
import threading

RPC_PORT = 57095  # F95Checker's own real, fixed port -- the extension is hardcoded to it
_THREAD_URL_RE = re.compile(r"threads/(?:([^./]*)\.)?(\d+)")

_server: socketserver.TCPServer | None = None
_thread: threading.Thread | None = None


def extract_thread_ids(text: str) -> list[tuple[int, str]]:
    """Same extraction shape as F95Checker's own utils.extract_thread_matches() --
    returns (thread_id, title_slug) pairs. title_slug is the dash-separated URL
    slug (e.g. "some-game-name"), a real but rough name guess -- not a substitute
    for actually fetching the thread's title from f95zone."""
    if not isinstance(text, str):
        return []
    return [(int(m.group(2)), m.group(1) or "") for m in _THREAD_URL_RE.finditer(text)]


def _games_payload() -> list[dict]:
    from pythia import datadir

    out = []
    for game_id, entries in datadir.list_known_games().items():
        entry = entries[0]
        f95_id = next(
            (p.get("id") for p in entry.get("platform_ids", []) if p.get("platform") == "f95"),
            None,
        )
        if f95_id is None:
            continue
        out.append({"id": int(f95_id), "icon": "circle", "color": "#D9A441", "notes": entry.get("notes", "")})
    return out


def _add_threads(urls_text: str) -> int:
    from pythia import onboarding

    added = 0
    for thread_id, title_slug in extract_thread_ids(urls_text):
        name = title_slug.replace("-", " ").strip().title() or None
        detection = {
            "kind": "f95",
            "name": name,
            "version_label": None,
            "platforms": [],
            "external_id": thread_id,
            "extra": {"f95_thread_id": str(thread_id)},
        }
        _entry, created = onboarding.record_ownership(detection)
        if created:
            added += 1
    return added


class _RPCHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args, **_kwargs):
        pass  # quiet by default, matching F95Checker's own non-debug behavior

    def _send_json(self, code: int, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "X-Requested-With, Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_GET(self):
        try:
            if self.path == "/games":
                self._send_json(200, _games_payload())
            elif self.path == "/settings":
                self._send_json(200, {"icon_glow": False, "highlight_tags": False, "tags_highlights": {}})
            else:
                self.send_response(404)
                self.end_headers()
        except Exception:
            self.send_response(500)
            self.end_headers()

    def do_POST(self):
        try:
            if self.path == "/games/add":
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8") if length else ""
                try:
                    payload = json.loads(raw)
                    text = "\n".join(payload) if isinstance(payload, list) else str(payload)
                except json.JSONDecodeError:
                    text = raw
                _add_threads(text)
                self.send_response(200)
                self.end_headers()
            elif self.path in ("/window/show", "/window/hide", "/window/refresh_styles"):
                self.send_response(200)  # no-op for now, see module docstring
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
        except Exception:
            self.send_response(500)
            self.end_headers()


def start():
    """Start the RPC server on a background daemon thread. Safe to call once per
    process; a second call while already running raises OSError (port in use) --
    callers should check is_running() first if that matters."""
    global _server, _thread
    if _server is not None:
        return
    socketserver.TCPServer.allow_reuse_address = True
    _server = socketserver.TCPServer(("127.0.0.1", RPC_PORT), _RPCHandler)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()


def stop():
    global _server, _thread
    if _server is not None:
        _server.shutdown()
        _server.server_close()
    if _thread is not None:
        _thread.join(timeout=5)
    _server = None
    _thread = None


def is_running() -> bool:
    return _server is not None
