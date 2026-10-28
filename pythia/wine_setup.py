"""Setting up Windows games on Linux (Droidtop/tracker#629): a Wine build for
Pythia's own use, fetched when the person asks ("Set up Windows games"), so
Play works without installing anything from the system's package manager.

The build is Kron4ek's Wine-Builds (github.com/Kron4ek/Wine-Builds), plain
upstream Wine and Wine Staging built to run on any distribution from a
folder, the same builds other launchers offer. It is unpacked into Pythia's
runners folder (``settings.WINE_DIR``), where Lutris's own code lists Pythia's
Wine builds too; nothing outside that folder is written. Play uses the newest
build there, else the system's ``wine``.

Not on Windows, where games run natively, and not on macOS, which these
builds do not cover.
"""

import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from pythia import settings
from pythia.util.log import logger

RELEASES = "https://api.github.com/repos/Kron4ek/Wine-Builds/releases/latest"
# The asset to fetch, in order of preference: Wine Staging with its gaming
# patches, then Staging, then plain Wine; all 64-bit with 32-bit support.
FLAVOURS = ("-staging-tkg-amd64.tar.xz", "-staging-amd64.tar.xz", "-amd64.tar.xz")
TIMEOUT = 30


class SetupError(Exception):
    """Why Windows games could not be set up, in the person's words."""


def supported() -> bool:
    """Whether Pythia sets up Wine here: Linux on x86-64 only."""
    return sys.platform.startswith("linux") and os.uname().machine in ("x86_64", "amd64")


def builds() -> list[dict]:
    """The Wine builds Pythia has set up, newest first: ``name`` and ``wine``."""
    root = Path(settings.WINE_DIR)
    if not root.is_dir():
        return []
    found: list[tuple[float, str, str]] = []
    for child in root.iterdir():
        wine = child / "bin" / "wine"
        if wine.is_file() and os.access(wine, os.X_OK):
            found.append((child.stat().st_mtime, child.name, str(wine)))
    found.sort(reverse=True)
    return [{"name": name, "wine": wine} for _, name, wine in found]


def latest() -> dict:
    """The build to fetch: ``name`` (its folder), ``url``, ``size`` (bytes)
    and ``version``. Raises SetupError when GitHub cannot be asked."""
    request = urllib.request.Request(RELEASES, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            release = json.load(response)
    except (OSError, ValueError) as ex:
        raise SetupError(f"Could not ask GitHub for the latest Wine build: {ex}") from ex
    return pick(release)


def pick(release: dict) -> dict:
    """The asset of a release (GitHub's JSON) Pythia sets up."""
    assets = release.get("assets") or []
    for flavour in FLAVOURS:
        for asset in assets:
            name = asset.get("name") or ""
            if name.endswith(flavour) and "wow64" not in name:
                return {
                    "name": name[: -len(".tar.xz")],
                    "url": asset["browser_download_url"],
                    "size": int(asset.get("size") or 0),
                    "version": release.get("tag_name") or "",
                }
    raise SetupError("The latest Wine-Builds release has no build Pythia can use.")


def _safe_members(archive: tarfile.TarFile, into: Path):
    """The archive's members, refusing any that would land outside ``into``
    or that are devices; links may only point inside."""
    root = into.resolve()
    for member in archive.getmembers():
        target = (into / member.name).resolve()
        if target != root and root not in target.parents:
            raise SetupError(f"The Wine build holds a path outside its folder: {member.name}")
        if member.isdev() or member.isfifo():
            raise SetupError(f"The Wine build holds a device file: {member.name}")
        if member.issym() or member.islnk():
            link = (target.parent / member.linkname).resolve() if member.issym() else (into / member.linkname).resolve()
            if link != root and root not in link.parents:
                raise SetupError(f"The Wine build holds a link outside its folder: {member.name}")
        yield member


def install(progress: Callable[[int, int], None] | None = None, build: dict | None = None) -> dict:
    """Fetches and unpacks the latest build (or ``build``) into the runners
    folder; ``progress`` gets (done, total) bytes. Returns the build as
    builds() lists it. Raises SetupError."""
    if not supported():
        raise SetupError("Pythia sets up Wine on Linux (x86-64) only; on Windows games run as they are.")
    build = build or latest()
    root = Path(settings.WINE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    final = root / build["name"]
    if (final / "bin" / "wine").is_file():
        return {"name": final.name, "wine": str(final / "bin" / "wine")}
    free = shutil.disk_usage(root).free
    if build["size"] and free < build["size"] * 4:
        raise SetupError(f"Not enough space in {root}: Wine needs about {build['size'] * 4 // 2**20} MB unpacked.")
    with tempfile.TemporaryDirectory(dir=root, prefix=".setup-") as work:
        part = Path(work) / "wine.tar.xz"
        logger.info("Fetching %s", build["url"])
        try:
            with urllib.request.urlopen(build["url"], timeout=TIMEOUT) as response, part.open("wb") as out:
                total = int(response.headers.get("Content-Length") or build["size"] or 0)
                done = 0
                while chunk := response.read(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        except OSError as ex:
            raise SetupError(f"The Wine build did not download: {ex}") from ex
        if build["size"] and part.stat().st_size != build["size"]:
            raise SetupError("The Wine build arrived cut short; try again.")
        unpacked = Path(work) / "unpacked"
        unpacked.mkdir()
        try:
            with tarfile.open(part, "r:xz") as archive:
                archive.extractall(unpacked, members=list(_safe_members(archive, unpacked)))  # noqa: S202 (members checked)
        except (tarfile.TarError, OSError) as ex:
            raise SetupError(f"The Wine build could not be unpacked: {ex}") from ex
        tops = [p for p in unpacked.iterdir() if p.is_dir()]
        top = tops[0] if len(tops) == 1 else unpacked
        if not (top / "bin" / "wine").is_file():
            raise SetupError("The Wine build has no bin/wine.")
        top.rename(final)
    logger.info("Wine set up in %s", final)
    return {"name": final.name, "wine": str(final / "bin" / "wine")}
