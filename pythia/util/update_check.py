"""App version-check across real release channels (2026-08-07: "we wanna
build an autoupdate system" -- "we also need channels. Latest, Nightly,
Unstable, and Stable builds" -- "Keep the systems simple."). Deliberately
just a check, not an auto-applier -- see the module-level note further down
for why applying differs too much per distribution channel to do safely here
yet.

Four channels, each with its own manifest at docs/app/<channel>.json,
published by CI (see .github/workflows/build.yml):
  - stable:   real tagged releases (v1.2.3) -- compared by real semver.
  - unstable: pre-release tags (v1.2.3-rc.1) -- also real semver (a
              prerelease version still orders correctly against a lower one).
  - nightly:  built once a day from the tip of the dev branch.
  - latest:   built on every single commit to the dev branch.
Nightly/latest builds don't have a meaningful version number to compare (the
same pyproject version stays fixed between real releases) -- those two
compare the manifest's `commit` field against this build's own
pythia.build_info.BUILD_COMMIT (stamped by CI right before packaging)
instead: different real commit = an update exists on that channel.

Every channel's builds are permanently archived as their own uniquely-tagged
GitHub Release (2026-08-07: "We also must never delete old builds. We keep
the whole history.") -- the manifest here only ever points at the NEWEST one
per channel; older builds stay reachable from the repo's Releases page, not
deleted when a newer one on the same channel is published.

DEFAULT_MANIFEST_BASE isn't fetchable yet -- this repo is private until 1.0
(same as pythia.plugins.remote_index). Code is correct now, needs nothing
further once it's public.
"""

import json
import urllib.error
import urllib.request

from pythia.build_info import BUILD_COMMIT
from pythia.settings import VERSION

DEFAULT_MANIFEST_BASE = "https://bi0shacker001.github.io/pythia/app"

_COMMIT_COMPARED_CHANNELS = ("latest", "nightly")


def _version_tuple(v: str) -> tuple:
    """Same simple dotted-version comparison as
    pythia.plugins.packaging._version_tuple() -- duplicated rather than
    imported to keep this a plain util module with no plugin-system
    dependency; both are tiny and meant to stay identical in behavior, not
    literally shared code. A trailing "-rc.1"-style suffix (real for the
    unstable channel) is stripped before parsing -- good enough for "is
    this newer," not a full semver-precedence implementation."""
    parts = []
    for seg in v.split("-", 1)[0].split("."):
        digits = "".join(c for c in seg if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_for_update(channel: str | None = None, manifest_base: str = DEFAULT_MANIFEST_BASE, timeout: int = 10) -> dict | None:
    """{"channel", "version", "download_url", "release_notes_url"} if a
    newer build is published on `channel` than the one currently running,
    else None. Returns None (never raises) on any real network/format
    failure -- a failed check must never look like "you're already up to
    date" to a caller that doesn't check for that distinction; use
    check_for_update_strict() when that difference matters (e.g. a UI
    Slot showing "check failed" vs. "up to date")."""
    try:
        return check_for_update_strict(channel, manifest_base, timeout)
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None


def check_for_update_strict(
    channel: str | None = None, manifest_base: str = DEFAULT_MANIFEST_BASE, timeout: int = 10
) -> dict | None:
    """Same as check_for_update(), but raises on a real failure instead of
    swallowing it. `channel` defaults to whatever pythia.cfg has configured
    (see pythia.util.local_config), itself defaulting to "stable"."""
    if channel is None:
        from pythia.util.local_config import load_local_config  # noqa: PLC0415

        channel = load_local_config()["channel"]

    manifest_url = f"{manifest_base}/{channel}.json"
    req = urllib.request.Request(manifest_url, headers={"User-Agent": "Pythia"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if channel in _COMMIT_COMPARED_CHANNELS:
        remote_commit = data.get("commit")
        if not remote_commit or remote_commit == BUILD_COMMIT:
            return None
    else:
        if _version_tuple(data["version"]) <= _version_tuple(VERSION):
            return None

    return {
        "channel": channel,
        "version": data.get("version"),
        "download_url": data.get("download_url"),
        "release_notes_url": data.get("release_notes_url"),
    }
