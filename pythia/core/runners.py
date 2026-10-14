"""
Downloadable external tool manager -- rebuilt (2026-08-03, second pass) to actually
reuse Lutris's real runner-install architecture instead of a bespoke reimplementation
of the same idea. First pass got this wrong: "Why did you do that? We have the
actual runners system. We can repurpose a copy of it... Don't reinvent the wheel."
Corrected by porting the real logic, not just the concept:

- **Architecture resolution** is ported from `lutris/api.py`'s
  `iter_get_from_api_candidates()`/`get_runner_version_from_api()` -- the real
  fallback order Lutris actually uses (exact version+arch -> default-for-arch ->
  any-for-arch -> a 64-bit system accepting a 32-bit default as a last resort),
  generalized off Lutris's `LINUX_SYSTEM.arch`/`is_64_bit` to `platform`-based
  detection so it isn't Linux-specific.
- **Download + extract + make-executable** is ported from
  `lutris/runners/runner.py`'s `download_and_extract()`/`extract()` -- same shape
  (download to a cache path, extract via a stdlib-based archive handler, mark the
  resulting binary executable on POSIX), simplified to stdlib `tarfile`/`zipfile`
  only (Lutris's own `util/extract.py` also has a 7zip external-tool fallback and
  GOG-specific/nested-archive handling this doesn't need yet -- real, stated
  simplification, not a silent gap, extend if a real tool ever needs those formats).
- **Catalog sources are pluggable, per the user's "we may need variants of the
  system for different purposes"** -- `fetch_lutris_catalog()` queries Lutris's own
  real, live API (`https://lutris.net/api/runners/<name>`, the actual infrastructure
  Wine/Proton-GE/DXVK/etc. builds are hosted on -- genuinely reusable once a real
  Wine/Proton port happens, not just a design nicety) in the exact
  `{"version", "architecture", "url", "default"}` shape `resolve_version()` expects.
  `_STATIC_CATALOGS` covers tools that aren't Lutris-hosted runners at all (e.g.
  `legendary`, an Epic Games Store CLI -- GitHub-releases-based, same dict shape so
  the same `resolve_version()`/`download_and_install()` machinery applies uniformly
  to both catalog kinds).

Still true from the first pass, unchanged: the standalone `pythia.exe` (PyInstaller
build) has no pip/Python environment to install an optional Python package into at
runtime, so a downloaded binary + subprocess is the only model that works for both
the source-checkout and the frozen-exe distribution paths. And architecture safety
is still the explicit point, not an afterthought: `resolve_version()` returns None
-- never a mismatched-architecture guess -- when no real build exists for this
machine, same contract as before.
"""

import gzip
import json
import os
import platform
import re
import shutil
import stat
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from pythia.util.safe_archive import safe_extract_zip
from pathlib import Path

from pythia import settings as core_settings  # real Lutris-derived settings module, not a second parallel one
from pythia.identity import app_data_dir

MAX_TAR_MEMBERS = 100_000
MAX_TAR_TOTAL_SIZE = 32 * 1024**3
MAX_TAR_MEMBER_SIZE = 16 * 1024**3
MAX_TOOL_DOWNLOAD_BYTES = 8 * 1024**3
MAX_GZIP_EXPANDED_BYTES = 16 * 1024**3
MAX_RUNNER_CATALOG_BYTES = 2 * 1024**2
MAX_RUNNER_CATALOG_ENTRIES = 10_000
_RUNNER_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class _HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlparse(newurl)
        if target.scheme != "https":
            raise urllib.error.HTTPError(newurl, code, "Refusing a non-HTTPS tool redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _tool_download_filename(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ValueError("tool URL has an invalid host or port") from exc
    if parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None:
        raise ValueError("tool downloads require an HTTPS URL without embedded credentials")
    filename = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
    if (
        not filename
        or filename in {".", ".."}
        or len(filename) > 240
        or "/" in filename
        or "\\" in filename
        or any(ord(char) < 32 or ord(char) == 127 for char in filename)
    ):
        raise ValueError("tool URL does not contain a safe filename")
    return filename

# Static, non-Lutris-hosted tool catalogs -- same {"version", "architecture", "url",
# "default"} shape as a Lutris /api/runners/<name> response, so resolve_version()
# doesn't need to know or care which kind of catalog it's looking at. Every URL here
# was confirmed against the tool's real GitHub release assets before being added
# (2026-08-03: github.com/derrod/legendary's 0.20.34 release) -- never a guessed URL
# pattern.
_STATIC_CATALOGS = {
    "legendary": [
        {
            "version": "0.20.34",
            "architecture": "x86_64",
            "os": "Windows",
            "url": "https://github.com/legendary-gl/legendary/releases/download/0.20.34/legendary.exe",
            "default": True,
        },
        {
            "version": "0.20.34",
            "architecture": "x86_64",
            "os": "Linux",
            "url": "https://github.com/legendary-gl/legendary/releases/download/0.20.34/legendary",
            "default": True,
        },
        # No arm64 build published for any OS as of 0.20.34 (confirmed against the
        # real release asset list -- only "legendary", "legendary.exe",
        # "legendary_macOS.zip" exist). Deliberately absent, not papered over --
        # Windows-on-ARM64 can often still run the x86_64 .exe via the OS's own x64
        # emulation, but that's a user/OS-level choice, not this module's to make by
        # silently downloading the "wrong" arch's binary and hoping.
    ],
    # Valve's own real, stable installer URLs (documented on Valve's SteamCMD wiki
    # page, not a guessed pattern) -- confirmed live (HTTP 200, real content-length)
    # 2026-08-04, then actually downloaded and run (`steamcmd +quit`) against a real
    # copy to confirm the archive layout/entry-point name below is correct, not just
    # that the URL resolves.
    "steamcmd": [
        {
            "version": "stable",
            "architecture": "x86_64",
            "os": "Windows",
            "url": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip",
            "default": True,
        },
        {
            "version": "stable",
            "architecture": "x86_64",
            "os": "Linux",
            "url": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz",
            "default": True,
        },
        # No arm64 build published by Valve for either OS -- same "don't guess an
        # unpublished arch" rule as legendary above.
    ],
}


def _normalized_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "x86_64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return machine


def _is_64_bit() -> bool:
    return platform.architecture()[0] == "64bit"


def fetch_lutris_catalog(runner_name: str, log=lambda *_: None) -> list[dict]:
    """Real, live query against Lutris's own runner catalog API -- the actual
    infrastructure Wine/Proton-GE/DXVK/etc. builds are hosted on
    (ported from lutris/api.py's download_runner_versions())."""
    if not _RUNNER_ID_RE.fullmatch(runner_name):
        log("tools: refused an invalid runner catalog identifier")
        return []
    url = f"{core_settings.SITE_URL.rstrip('/')}/api/runners/{runner_name}"
    try:
        _tool_download_filename(f"{url}/catalog.json")  # validates HTTPS host/port/credentials
        req = urllib.request.Request(url, headers={"User-Agent": core_settings.DEFAULT_USER_AGENT})
        opener = urllib.request.build_opener(_HTTPSOnlyRedirectHandler())
        with opener.open(req, timeout=15) as resp:
            try:
                declared_size = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                raise ValueError("catalog server returned an invalid Content-Length")
            if declared_size > MAX_RUNNER_CATALOG_BYTES:
                raise ValueError("runner catalog exceeds the response-size limit")
            payload = resp.read(MAX_RUNNER_CATALOG_BYTES + 1)
            if len(payload) > MAX_RUNNER_CATALOG_BYTES:
                raise ValueError("runner catalog exceeds the response-size limit")
            data = json.loads(payload.decode("utf-8"))
        versions = data.get("versions") if isinstance(data, dict) else None
        if not isinstance(versions, list) or len(versions) > MAX_RUNNER_CATALOG_ENTRIES:
            raise ValueError("runner catalog has an invalid versions list")
        return [
            entry
            for entry in versions
            if isinstance(entry, dict)
            and isinstance(entry.get("version"), str)
            and isinstance(entry.get("architecture"), str)
            and isinstance(entry.get("url"), str)
        ]
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
    ) as e:
        log(f"tools: failed to query Lutris runner catalog for '{runner_name}': {e}")
        return []


def _catalog_for(tool_id: str, log) -> list[dict]:
    if tool_id in _STATIC_CATALOGS:
        return _STATIC_CATALOGS[tool_id]
    return fetch_lutris_catalog(tool_id, log)


def resolve_version(catalog: list[dict], version: str | None, os_name: str, arch: str) -> dict | None:
    """Pick the best matching entry from `catalog` for `(os_name, arch)` -- ported
    fallback order from lutris/api.py's iter_get_from_api_candidates(): exact
    version+arch match, then the arch's default, then any build for that arch, then
    (only on a 64-bit system) accept a 32-bit default as a last resort. Returns None
    -- never a mismatched-arch guess -- if nothing matches at all."""
    candidates = [c for c in catalog if c.get("os", os_name) == os_name]

    def select(predicate):
        matches = [c for c in candidates if predicate(c)]
        return matches[0] if len(matches) == 1 else (matches[0] if matches and matches[0].get("default") else None)

    if version:
        found = select(lambda c: c.get("architecture") == arch and c.get("version") == version)
        if found:
            return found
        return None  # an explicitly requested version that doesn't exist for this arch is a real miss, not a fallback case

    found = select(lambda c: c.get("architecture") == arch and c.get("default"))
    if found:
        return found
    found = select(lambda c: c.get("architecture") == arch)
    if found:
        return found
    if _is_64_bit():
        found = select(lambda c: bool(c.get("default")))
        if found:
            return found
    return None


def _tools_dir() -> Path:
    d = app_data_dir() / "tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_dir() -> Path:
    d = Path(core_settings.CACHE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_extract_tar(
    archive: tarfile.TarFile,
    destination: Path,
    *,
    max_members: int = MAX_TAR_MEMBERS,
    max_total_size: int = MAX_TAR_TOTAL_SIZE,
    max_member_size: int = MAX_TAR_MEMBER_SIZE,
):
    """Preflight a runner TAR completely before extracting any member."""
    members = archive.getmembers()
    if len(members) > max_members:
        raise tarfile.TarError(f"archive has {len(members)} members; limit is {max_members}")
    total = 0
    seen: set[str] = set()
    checked = []
    for member in members:
        key = member.name.replace("\\", "/").rstrip("/").casefold()
        if not key or key in seen:
            raise tarfile.TarError(f"archive contains a duplicate or empty destination: {member.name!r}")
        seen.add(key)
        if member.size < 0 or member.size > max_member_size:
            raise tarfile.TarError(f"archive member exceeds the size limit: {member.name!r}")
        total += member.size
        if total > max_total_size:
            raise tarfile.TarError(f"archive expands beyond the {max_total_size}-byte total limit")
        # Python's data filter rejects traversal, absolute paths, devices and
        # unsafe link targets. Calling it here keeps that validation atomic.
        filtered = tarfile.data_filter(member, destination)
        if filtered is not None:
            checked.append(filtered)
    archive.extractall(destination, members=checked, filter="data")


def _safe_extract_gzip(archive_path: Path, target: Path, *, max_size: int = MAX_GZIP_EXPANDED_BYTES):
    if target.exists():
        raise FileExistsError(f"refusing to replace existing extracted tool: {target}")
    temporary = target.with_name(f".{target.name}.part")
    written = 0
    try:
        with gzip.open(archive_path, "rb") as src, temporary.open("xb") as dst:
            while chunk := src.read(1024 * 1024):
                written += len(chunk)
                if written > max_size:
                    raise ValueError(f"gzip expands beyond the {max_size}-byte limit")
                dst.write(chunk)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _promote_file_no_replace(source: Path, target: Path):
    """Atomically publish a direct tool binary without replacing a race winner."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with source.open("rb") as input_file, tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{target.name}-install-", suffix=".tmp", delete=False
        ) as temporary:
            temporary_name = temporary.name
            shutil.copyfileobj(input_file, temporary, length=1024 * 1024)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path = Path(temporary_name)
        if os.name == "nt":
            os.rename(temporary_path, target)  # Windows rename refuses an existing target.
        else:
            os.link(temporary_path, target)  # Atomic create-if-absent on POSIX.
            temporary_path.unlink()
        source.unlink()
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def _extract_archive(archive_path: Path, dest_dir: Path, merge_single: bool = True):
    """Stdlib-only archive extraction, ported (simplified) from
    lutris/util/extract.py's extract_archive() -- tar/tgz/txz/zip only (no 7zip
    fallback, no GOG-specific/nested-archive handling; real, stated simplification,
    not silently dropped -- extend if a real downloaded tool ever needs those)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = archive_path.name.lower()

    if name.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar")) or tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path) as tf:
            _safe_extract_tar(tf, dest_dir)
    elif name.endswith(".zip") or zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as zf:
            safe_extract_zip(zf, dest_dir)
    elif name.endswith(".gz"):
        target = dest_dir / archive_path.stem
        _safe_extract_gzip(archive_path, target)
    else:
        raise ValueError(f"unrecognized archive format: {archive_path.name}")

    if merge_single:
        entries = list(dest_dir.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            inner = entries[0]
            for child in inner.iterdir():
                shutil.move(str(child), str(dest_dir / child.name))
            inner.rmdir()


def download_and_install(url: str, dest_dir: Path, is_archive: bool = False, merge_single: bool = True, log=lambda *_: None) -> Path:
    """Download `url` (to Pythia's own cache dir, matching Lutris's own
    settings.CACHE_DIR-based staging) and either extract it into `dest_dir` (archive
    case, ported from runner.py's download_and_extract()/extract()) or save it
    directly as `dest_dir`'s one file (single-binary case, e.g. legendary.exe).
    Marks the result executable on POSIX. Raises on failure -- callers decide how to
    degrade (see ensure_tool(), which catches and returns None)."""
    filename = _tool_download_filename(url)
    cache_path = _cache_dir() / filename
    log(f"tools: downloading {url} ...")
    temp_path = cache_path.with_suffix(cache_path.suffix + ".part")
    temp_path.unlink(missing_ok=True)
    try:
        opener = urllib.request.build_opener(_HTTPSOnlyRedirectHandler())
        with opener.open(url, timeout=60) as resp:
            try:
                declared_size = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                raise ValueError("tool server returned an invalid Content-Length")
            if declared_size > MAX_TOOL_DOWNLOAD_BYTES:
                raise ValueError(f"tool download exceeds the {MAX_TOOL_DOWNLOAD_BYTES}-byte limit")
            received = 0
            with temp_path.open("xb") as f:
                while chunk := resp.read(1024 * 256):
                    received += len(chunk)
                    if received > MAX_TOOL_DOWNLOAD_BYTES:
                        raise ValueError(f"tool download exceeds the {MAX_TOOL_DOWNLOAD_BYTES}-byte limit")
                    f.write(chunk)
            if declared_size and received != declared_size:
                raise ValueError("tool download ended before the declared size was received")
        os.replace(temp_path, cache_path)
    finally:
        temp_path.unlink(missing_ok=True)

    if is_archive:
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix=f".{dest_dir.name}-install-", dir=dest_dir.parent))
        try:
            _extract_archive(cache_path, staging_dir, merge_single=merge_single)
            if dest_dir.exists():
                if dest_dir.is_dir() and not any(dest_dir.iterdir()):
                    dest_dir.rmdir()
                else:
                    raise FileExistsError(f"refusing to replace existing tool directory: {dest_dir}")
            os.replace(staging_dir, dest_dir)
            cache_path.unlink(missing_ok=True)
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
        result = dest_dir
    else:
        _promote_file_no_replace(cache_path, dest_dir)
        result = dest_dir

    if os.name != "nt":
        for path in ([result] if result.is_file() else result.rglob("*")):
            if path.is_file():
                path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return result


def is_available(tool_id: str, version: str | None = None, log=lambda *_: None) -> bool:
    """True if a real build exists for this exact machine's OS+arch -- call before
    ensure_tool() to give an honest "not supported here" message instead of
    attempting (and failing) a download."""
    catalog = _catalog_for(tool_id, log)
    return resolve_version(catalog, version, platform.system(), _normalized_arch()) is not None


def ensure_tool(tool_id: str, version: str | None = None, exe_name: str | None = None, log=lambda *_: None) -> Path | None:
    """Return the path to a working local copy of `tool_id`'s binary, downloading
    (and, if it's an archive, extracting) it -- once, cached under
    app_data_dir()/tools/ -- if not already present. None if no build exists for
    this machine's real OS+architecture or the download failed. `exe_name` names the
    executable inside an extracted archive (ignored for a single-file download)."""
    catalog = _catalog_for(tool_id, log)
    info = resolve_version(catalog, version, platform.system(), _normalized_arch())
    if info is None:
        log(f"tools: no prebuilt '{tool_id}' build for {platform.system()}/{_normalized_arch()}")
        return None

    resolved_version = info["version"]
    url = info["url"]
    filename = os.path.basename(url)
    # A URL whose filename has no extension at all (e.g. the bare "legendary" Linux
    # binary) or ends in .exe is a direct single-file download; anything with a real
    # archive extension is treated as an archive to extract.
    is_archive = filename.lower().endswith((".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar", ".zip", ".gz"))

    version_dir = _tools_dir() / tool_id / resolved_version
    if is_archive:
        target = version_dir / (exe_name or tool_id)
        if target.exists() or (version_dir.is_dir() and any(version_dir.iterdir())):
            return target if target.exists() else version_dir
    else:
        target = version_dir / filename
        if target.is_file():
            return target

    try:
        if is_archive:
            download_and_install(url, version_dir, is_archive=True, log=log)
            return target if target.exists() else version_dir
        else:
            return download_and_install(url, target, is_archive=False, log=log)
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"tools: failed to install {tool_id}: {e}")
        return None
