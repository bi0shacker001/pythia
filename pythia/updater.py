"""Pythia updates itself on a release lane (docs/SPEC.md, decision 13): Stable, Testing or Unstable, each
published by packaging/release_channel.py with the same file names and a release-info.json listing each
installer's build, size and SHA-256.

- The lane starts as the one this build was installed from (owner, 2026-11-21: "The updater should default to
  whatever stream is installed"). Promotion publishes a build without rebuilding it, so an installer cannot
  carry its lane: the first check asks which lane has this very build, Stable first, then Testing, else
  Unstable, and keeps the answer. The person can change it from the tray.
- Only a release build updates itself (a standalone build CI stamped, pythia._build.CODE above 0). A copy run
  from the repository, or Pythia from a distribution's or the Launchpad package, is updated the way it was
  installed, and the updater says so.
- The installer for this system is downloaded, checked against its SHA-256, and handed over: Windows runs it
  silently and it opens Pythia again (packaging/windows/pythia.iss, /RELAUNCH=1); on macOS the new app replaces
  this one once Pythia has quit, and opens; on Linux the deb or rpm goes in through the system's administrator
  prompt (pkexec), then Pythia starts again. Pythia quits so its files can be replaced.

Network and disk work: never on the Qt thread.
"""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from pythia import _build
from pythia.util.log import logger

REPO = "bi0shacker001/pythia"
LANES = ("stable", "testing", "unstable")
LANE_NAMES = {"stable": "Stable", "testing": "Testing", "unstable": "Unstable"}
SETTING = "update_lane"
BUILD_TAG = re.compile(r"^v[0-9][0-9.]*-dev\.(\d+)$")
TIMEOUT = 30


def installed_build() -> int:
    return _build.CODE


def updatable() -> str | None:
    """None when this Pythia updates itself, else why it does not."""
    if not getattr(sys, "frozen", False) or installed_build() <= 0:
        return "This Pythia was not installed from a Pythia release, so it is updated the way it was installed"
    if asset_kind() is None:
        return "No Pythia release is made for this system"
    return None


def asset_kind() -> str | None:
    """Which installer in release-info.json is this system's."""
    if sys.platform == "win32":
        return "windows-installer"
    if sys.platform == "darwin":
        return "macos-dmg-arm64" if platform.machine() == "arm64" else "macos-dmg-x86_64"
    if sys.platform.startswith("linux"):
        if shutil.which("dpkg") and _quiet(["dpkg", "-s", "pythia"]):
            return "linux-deb"
        if shutil.which("rpm") and _quiet(["rpm", "-q", "pythia"]):
            return "linux-rpm"
    return None


def _quiet(command: list[str]) -> bool:
    try:
        return subprocess.run(command, capture_output=True, timeout=TIMEOUT, check=False).returncode == 0
    except OSError:
        return False


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Pythia-updater", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 (https, fixed host)
        return response.read()


def release_info(lane: str) -> dict | None:
    """The newest release-info.json on [lane], or None when the lane has no release yet."""
    if lane in ("stable", "testing"):
        try:
            return json.loads(_get(f"https://github.com/{REPO}/releases/download/{lane}/release-info.json"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise
    releases = json.loads(_get(f"https://api.github.com/repos/{REPO}/releases?per_page=50"))
    builds = [(int(m.group(1)), r) for r in releases if (m := BUILD_TAG.match(r.get("tag_name", "")))]
    if not builds:
        return None
    _, newest = max(builds, key=lambda b: b[0])
    asset = next((a for a in newest.get("assets", []) if a.get("name") == "release-info.json"), None)
    return json.loads(_get(asset["browser_download_url"])) if asset else None


def lane() -> str:
    """The lane this Pythia follows: the person's choice, else the one its build was installed from."""
    from pythia import settings  # noqa: PLC0415

    chosen = settings.read_setting(SETTING)
    if chosen in LANES:
        return chosen
    found = installed_lane()
    if found is None:
        return "unstable"
    settings.write_setting(SETTING, found)
    return found


def installed_lane() -> str | None:
    """The lane that has this very build, Stable first; Unstable when neither does; None when it cannot ask."""
    build = installed_build()
    try:
        for candidate in ("stable", "testing"):
            info = release_info(candidate)
            if info and info.get("versionCode") == build:
                return candidate
    except (OSError, ValueError) as error:
        logger.info("Could not tell which lane this build came from: %s", error)
        return None
    return "unstable"


def set_lane(value: str) -> None:
    from pythia import settings  # noqa: PLC0415

    if value not in LANES:
        raise ValueError(f"no lane {value}")
    settings.write_setting(SETTING, value)


def check() -> dict:
    """What the lane offers: ``state`` is ``newer`` (with ``info``), ``current``, ``none`` (the lane has no
    release yet), ``unavailable`` (this Pythia does not update itself) or ``error``, with ``line`` in words."""
    reason = updatable()
    if reason:
        return {"state": "unavailable", "line": reason}
    current = lane()
    try:
        info = release_info(current)
    except (OSError, ValueError) as error:
        return {"state": "error", "line": f"Couldn't check for updates: {error}"}
    if info is None:
        return {"state": "none", "line": f"Nothing is published on {LANE_NAMES[current]} yet"}
    if int(info.get("versionCode", 0)) <= installed_build():
        return {"state": "current", "line": f"Pythia is up to date ({LANE_NAMES[current]})", "info": info}
    return {"state": "newer", "line": f"Pythia {info.get('versionName')} is available", "info": info}


def download(info: dict, progress=None) -> str:
    """Downloads this system's installer from [info] and checks it; returns its path."""
    kind = asset_kind()
    asset = (info.get("assets") or {}).get(kind or "")
    if not asset:
        raise ValueError("This release has no installer for this system")
    tag = "v" + info["versionName"]
    url = f"https://github.com/{REPO}/releases/download/{tag}/{asset['name']}"
    folder = tempfile.mkdtemp(prefix="pythia-update-")
    path = os.path.join(folder, asset["name"])
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "Pythia-updater"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(path, "wb") as out:  # noqa: S310
        total = int(asset.get("size") or response.headers.get("Content-Length") or 0)
        done = 0
        for block in iter(lambda: response.read(1 << 20), b""):
            out.write(block)
            digest.update(block)
            done += len(block)
            if progress and total:
                progress(done / total)
    if digest.hexdigest().upper() != asset["sha256"].upper():
        shutil.rmtree(folder, ignore_errors=True)
        raise ValueError("The download does not match its release; nothing was installed")
    return path


def apply(path: str) -> None:
    """Hands the checked installer over. Pythia quits right after, so its files can be replaced."""
    kind = asset_kind()
    if kind == "windows-installer":
        here = os.path.dirname(sys.executable)
        mode = "/CURRENTUSER" if here.lower().startswith(os.environ.get("LOCALAPPDATA", "~").lower()) else "/ALLUSERS"
        subprocess.Popen(  # noqa: S603 (our own checked installer)
            [path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", mode, "/RELAUNCH=1"],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
        return
    if kind and kind.startswith("macos-dmg"):
        bundle = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", ".."))
        _after_quit(
            "mnt=$(mktemp -d)\n"
            f'hdiutil attach -nobrowse -readonly -mountpoint "$mnt" "{path}" >/dev/null\n'
            f'rm -rf "{bundle}.old" && mv "{bundle}" "{bundle}.old" && ditto "$mnt/Pythia.app" "{bundle}" '
            f'&& rm -rf "{bundle}.old" || mv "{bundle}.old" "{bundle}"\n'
            'hdiutil detach "$mnt" >/dev/null\n'
            f'open "{bundle}"\n'
        )
        return
    if kind in ("linux-deb", "linux-rpm"):
        install = (
            f'pkexec apt-get install -y --allow-downgrades "{path}"'
            if kind == "linux-deb"
            else f'pkexec rpm -U --oldpackage "{path}"'
        )
        _after_quit(f"{install}\npythia &\n")
        return
    raise ValueError("No Pythia release is made for this system")


def _after_quit(script: str) -> None:
    """Runs [script] once this process has ended, detached from it."""
    wrapper = f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done\n{script}"
    subprocess.Popen(["/bin/sh", "-c", wrapper], start_new_session=True, close_fds=True)  # noqa: S603
