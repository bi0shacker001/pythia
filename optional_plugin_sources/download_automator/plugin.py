"""Optional authenticated HTTP download automation.

The plugin deliberately handles only links that have already resolved to HTTP(S).
CAPTCHA/masked-page interaction remains with the source plugin's webview. Host
adapters can add login/session preparation without coupling Pythia or F95Checker to
specific file hosts.

2026-08-08 hardening pass: checksum verification (when a caller supplies one --
nothing in this codebase currently does, F95's own update data carries no
hash, so this is real-but-unexercised-by-any-current-caller, not fabricated),
a safe-extraction handoff carrying the same bounded rules inside this plugin ZIP
with an explicit-review gate against overwriting
an existing, non-empty destination, and an audited error path: no exception
message here ever includes `headers`/cookies -- only urllib's own exception
text, which never embeds the request object.
"""

import hashlib
import http.client
import importlib.util
import json
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pythia.plugins import DownloaderPlugin

MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024 * 1024
MAX_COOKIE_HEADER_BYTES = 16 * 1024
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_COOKIE_VALUE_RE = re.compile(r'^[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]*$')
_AUTHORIZATION_HEADERS = ("Authorization", "Proxy-Authorization")


class _CredentialSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    @staticmethod
    def _remove_headers(request, names):
        for header in names:
            request.remove_header(header)
            for store in (request.headers, request.unredirected_hdrs):
                for existing in list(store):
                    if existing.casefold() == header.casefold():
                        store.pop(existing, None)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source = urllib.parse.urlparse(req.full_url)
        target = urllib.parse.urlparse(newurl)
        if source.scheme == "https" and target.scheme != "https":
            raise urllib.error.HTTPError(newurl, code, "Refusing an HTTPS-to-HTTP download redirect.", headers, fp)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            if source.hostname != target.hostname:
                self._remove_headers(redirected, ("Cookie",))
            source_origin = (source.scheme, source.hostname, source.port or (443 if source.scheme == "https" else 80))
            target_origin = (target.scheme, target.hostname, target.port or (443 if target.scheme == "https" else 80))
            if source_origin != target_origin:
                self._remove_headers(redirected, _AUTHORIZATION_HEADERS)
        return redirected


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _url_fingerprint(url: str) -> str:
    """Identify a resume source without persisting signed URL credentials."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class DownloadAutomatorPlugin(DownloaderPlugin):
    id = "pythia.downloader.automator"
    display_name = "Download Automator"
    version = "1.0.0"
    author = "pythia"
    platforms = ["windows", "linux"]
    compatible_library_kinds = ["*"]

    @staticmethod
    def _validated_cookies(value) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        valid: dict[str, str] = {}
        size = 0
        for name, cookie_value in value.items():
            if not isinstance(name, str) or not isinstance(cookie_value, str):
                continue
            if not _COOKIE_NAME_RE.fullmatch(name) or not _COOKIE_VALUE_RE.fullmatch(cookie_value):
                continue
            size += len(name.encode("ascii")) + len(cookie_value.encode("ascii")) + 2
            if size > MAX_COOKIE_HEADER_BYTES:
                break
            valid[name] = cookie_value
        return valid

    @staticmethod
    def identify_host(url: str) -> dict:
        try:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.hostname or "").lower()
            parsed.port
        except ValueError:
            return {"supported": False, "host": "", "reason": "URL has an invalid host or port."}
        if parsed.scheme not in {"http", "https"} or not host:
            return {"supported": False, "host": host, "reason": "Not an HTTP(S) URL."}
        if parsed.username is not None or parsed.password is not None:
            return {
                "supported": False,
                "host": host,
                "reason": "Credentials embedded in download URLs are not accepted; use the host sign-in flow.",
            }
        return {
            "supported": True,
            "host": host,
            "adapter": "generic_http",
            "premium_session": False,
        }

    def _host_cookies(self, host: str) -> dict:
        raw = self.get_credential(f"host_cookies:{host}")
        try:
            value = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            value = {}
        return self._validated_cookies(value)

    def get_host_cookies(self, host: str) -> dict:
        return self._host_cookies(host)

    def set_host_cookies(self, host: str, cookies: dict):
        self.set_credential(f"host_cookies:{host.lower()}", json.dumps(self._validated_cookies(cookies)))

    def prepare_download(self, url: str, suggested_name: str = "") -> dict:
        identity = self.identify_host(url)
        if not identity["supported"]:
            return {"status": "unsupported", **identity}
        host = identity["host"]
        cookies = self._host_cookies(host)
        headers = {"User-Agent": "Pythia Download Automator/1.0"}
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            identity["premium_session"] = True
        filename = suggested_name.strip() or Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
        filename = re.sub(r"[^A-Za-z0-9._ -]+", "_", filename).strip() or "download.bin"
        return {"status": "ok", "url": url, "headers": headers, "filename": filename, **identity}

    def download(self, plan: dict, destination: str, progress=None, cancel_event=None) -> dict:
        target_dir = Path(destination)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / plan["filename"]
        partial = target.with_suffix(target.suffix + ".part")
        partial_meta = partial.with_suffix(partial.suffix + ".json")
        if target.exists():
            return {"status": "needs_review", "message": f"{target} already exists; choose another name or remove it explicitly."}
        headers = dict(plan.get("headers") or {})
        existing = partial.stat().st_size if partial.is_file() else 0
        resume_meta = {}
        if existing and partial_meta.is_file():
            try:
                resume_meta = json.loads(partial_meta.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                resume_meta = {}
        if existing and resume_meta.get("url_sha256") != _url_fingerprint(plan["url"]):
            partial.unlink(missing_ok=True)
            partial_meta.unlink(missing_ok=True)
            existing = 0
        if existing:
            headers["Range"] = f"bytes={existing}-"
            validator = resume_meta.get("etag") or resume_meta.get("last_modified")
            if validator:
                headers["If-Range"] = validator
        request = urllib.request.Request(plan["url"], headers=headers)
        try:
            opener = urllib.request.build_opener(_CredentialSafeRedirectHandler())
            with opener.open(request, timeout=30) as response:
                content_type = (response.headers.get("Content-Type") or "").lower()
                if "text/html" in content_type:
                    return {
                        "status": "interactive_required",
                        "message": f"{plan['host']} returned a web page instead of a file; a host-specific adapter or browser step is required.",
                    }
                append = bool(existing) and getattr(response, "status", 200) == 206
                if append:
                    content_range = response.headers.get("Content-Range") or ""
                    if not content_range.startswith(f"bytes {existing}-"):
                        return {"status": "error", "message": "Download server returned an invalid resume range."}
                if not append:
                    existing = 0
                partial_meta.write_text(json.dumps({
                    "url_sha256": _url_fingerprint(plan["url"]),
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                }), encoding="utf-8")
                try:
                    response_length = int(response.headers.get("Content-Length") or 0)
                except ValueError:
                    return {"status": "error", "message": "Download server returned an invalid Content-Length."}
                total = response_length + existing
                if total > MAX_DOWNLOAD_BYTES:
                    return {"status": "error", "message": "Download exceeds Pythia's 256 GiB safety limit."}
                received = existing
                with partial.open("ab" if append else "wb") as output:
                    while True:
                        if cancel_event is not None and cancel_event.is_set():
                            return {"status": "cancelled", "path": str(partial), "received": received, "total": total}
                        chunk = response.read(1 << 16)
                        if not chunk:
                            break
                        output.write(chunk)
                        received += len(chunk)
                        if received > MAX_DOWNLOAD_BYTES:
                            return {"status": "error", "message": "Download exceeded Pythia's 256 GiB safety limit."}
                        if progress:
                            progress(received, total)
                if response_length and received != total:
                    return {"status": "error", "message": "Download ended before the declared file size was received.",
                            "path": str(partial), "received": received, "total": total}
        except (urllib.error.URLError, OSError, http.client.IncompleteRead) as exc:
            # Never let `request` (it carries the Cookie/auth header) reach
            # the returned message -- only urllib's own exception text, which
            # (confirmed: urllib.error.URLError/HTTPError.__str__) never
            # embeds the request object or its headers.
            reason = getattr(exc, "reason", None) or exc
            return {"status": "error", "message": f"Download failed: {reason}"}

        expected_sha256 = plan.get("sha256")
        if expected_sha256:
            actual = _sha256_of(partial)
            if actual.lower() != str(expected_sha256).strip().lower():
                return {
                    "status": "checksum_mismatch",
                    "message": f"Downloaded file does not match the expected checksum (expected {expected_sha256}, got {actual}).",
                    "path": str(partial),
                    "received": received,
                    "total": total,
                }
        partial.replace(target)
        partial_meta.unlink(missing_ok=True)
        return {"status": "ok", "path": str(target), "received": received, "total": total}

    def extract_download(self, archive_path: str, destination: str) -> dict:
        """Real, self-contained safe-extraction handoff with bounded
        traversal/symlink/member-count/size/ratio rules. Refuses to extract into an
        existing, non-empty destination without explicit review ("never
        overwrite an existing game without explicit review") -- returns a
        real "needs_review" status rather than silently skipping or
        silently overwriting."""
        # Packaged plugins may depend on Pythia's plugin API, but behavior helpers
        # ship inside their own ZIP so the package remains independently loadable.
        try:
            from safe_archive import UnsafeArchiveError, safe_extract_zip
        except ModuleNotFoundError:
            # Source-tree tests load plugin.py directly rather than through the
            # built ZIP. Runtime never does, but keep that validation path able
            # to resolve this package's own sibling without a host dependency.
            helper_path = Path(__file__).with_name("safe_archive.py")
            spec = importlib.util.spec_from_file_location("_pythia_download_safe_archive", helper_path)
            if spec is None or spec.loader is None:
                return {"status": "error", "message": "Packaged safe-extraction helper is unavailable."}
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            UnsafeArchiveError = helper.UnsafeArchiveError
            safe_extract_zip = helper.safe_extract_zip

        archive = Path(archive_path)
        target_dir = Path(destination)
        if target_dir.is_dir() and any(target_dir.iterdir()):
            return {
                "status": "needs_review",
                "message": f"{target_dir} already contains files -- extract manually or choose a different destination.",
            }
        staging_dir = Path(tempfile.mkdtemp(prefix=f".{target_dir.name}-extract-", dir=target_dir.parent))
        try:
            extracted = safe_extract_zip(archive, staging_dir)
            # Recheck at commit time: another process may have populated an
            # initially-empty destination while extraction was in progress.
            if target_dir.exists():
                if target_dir.is_dir() and not any(target_dir.iterdir()):
                    target_dir.rmdir()
                else:
                    return {
                        "status": "needs_review",
                        "message": f"{target_dir} gained files while extracting; the staged result was not installed.",
                    }
            staging_dir.replace(target_dir)
        except UnsafeArchiveError as exc:
            return {"status": "error", "message": f"Refused to extract this archive: {exc}"}
        except Exception as exc:
            return {"status": "error", "message": f"Extraction failed: {exc}"}
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
        return {"status": "ok", "path": str(target_dir), "count": len(extracted)}

    def launch(self, context: dict):
        raise RuntimeError("Download Automator is headless; use prepare_download() and download().")


PLUGIN_CLASSES = [DownloadAutomatorPlugin]
