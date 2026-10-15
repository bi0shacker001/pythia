"""
Real F95zone update-checking, ported directly from the reference clone's
`modules/api.py` (`fast_check`/`full_check`) rather than reimplemented from
guesswork -- this is the actual "replace F95Checker" core (PROJECT_STATUS.md
§6/§7's stated remaining gap).

**Correction to an earlier, wrong assumption**: this does NOT need the user's
F95zone login/session at all. `fast_check`/`full_check` hit F95Checker's own
public caching/indexer API (`api.f95checker.dev`, confirmed real from
`modules/api.py`'s `api_host` constant), not f95zone.to itself, and both calls
pass `cookies=False` in the real source -- login is only needed for
account-specific things (bookmarks/watched-thread import, downloads), which
this module doesn't do. `pythia/ui/desktop/f95_login.py`'s webview session is
still real, useful infrastructure for those account-specific features and for
general browsing -- it's just not a prerequisite for update-checking.

What's deliberately NOT ported: F95Checker's own `Game` dataclass (tightly
coupled to its async GUI/image-cache runtime, same reasoning `f95.py`'s
docstring already gives for not reusing `modules/db.py` directly) and the full
`Tag` enum (150+ purely cosmetic tag-name entries, not load-bearing for
"is there an update" -- tags are returned here as raw ints, not resolved to
names). `Type`/`Status` are ported in full since they're small and their real
values are load-bearing (an update-check result's `type`/`status` fields need
correct real int->name mapping, not a guess).
"""

import datetime
import enum
import json
import time
import urllib.error
import urllib.request

from pythia.plugins.library.f95 import F95_DOMAIN

API_HOST = "https://api.f95checker.dev"
API_FAST_CHECK_URL = API_HOST + "/fast?ids={ids}"
API_FULL_CHECK_URL = API_HOST + "/full/{id}?ts={ts}"
API_FAST_CHECK_MAX_IDS = 10
F95_THREADS_PAGE = f"https://{F95_DOMAIN}/threads/"

_REQUEST_RETRIES = 3
_REQUEST_TIMEOUT = 30


class F95ApiError(RuntimeError):
    """Raised on a confirmed-bad response from api.f95checker.dev -- either a
    known downtime marker in the raw body, or an explicit INDEX_ERROR in the
    parsed JSON (both real, ported from api.py's raise_api_error())."""


# Real int -> name mapping, ported from common/structs.py's Status IntEnumHack.
class Status(enum.IntEnum):
    Normal = 1
    Completed = 2
    OnHold = 3
    Abandoned = 4
    Unchecked = 5
    Custom = 6
    Unknown = 7


# Real int -> name mapping, ported from common/structs.py's Type IntEnumHack.
class Type(enum.IntEnum):
    Misc = 1
    ADRIFT = 2
    CheatMod = 3
    Flash = 4
    HTML = 5
    Java = 6
    Collection = 7
    Mod = 8
    Others = 9
    QSP = 10
    RAGS = 11
    ReadMe = 12
    RPGM = 13
    RenPy = 14
    Request = 15
    Tads = 16
    Tool = 17
    Tutorial = 18
    Unity = 19
    UnrealEng = 20
    WebGL = 21
    WolfRPG = 22
    Unchecked = 23
    Comics = 24
    GIF = 25
    Manga = 26
    Pinup = 27
    SiteRip = 28
    Video = 29
    CG = 30
    Godot = 31
    Unknown = 32


def _raise_api_error(res: bytes | dict):
    if isinstance(res, bytes):
        if any(
            marker in res
            for marker in (
                b"<title>api.f95checker.dev | 502: Bad gateway</title>",
                b"<title>api.f95checker.dev | 521: Web server is down</title>",
            )
        ):
            raise F95ApiError("F95Checker cache API is currently unreachable, retry in a few minutes.")
        return
    if isinstance(res, dict) and (index_error := res.get("INDEX_ERROR")):
        raise F95ApiError(f"F95Checker cache API returned error: {index_error!r}")


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Pythia (github.com/bi0shacker001/pythia)"})
    retries = _REQUEST_RETRIES
    while True:
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            retries -= 1
            if not retries:
                raise
            time.sleep(1)


def fast_check(thread_ids: list[int]) -> dict[int, int]:
    """{thread_id: last_changed_unix_ts} for every id given -- a cheap, batched
    check for "has anything changed since I last full-checked this thread"
    (real API, batched by API_FAST_CHECK_MAX_IDS, same limit the real client
    respects). Callers should only full_check() ids whose last_changed here is
    newer than what they already have on record."""
    results: dict[int, int] = {}
    ids = [int(i) for i in thread_ids]
    for start in range(0, len(ids), API_FAST_CHECK_MAX_IDS):
        batch = ids[start : start + API_FAST_CHECK_MAX_IDS]
        res = _fetch(API_FAST_CHECK_URL.format(ids=",".join(str(i) for i in batch)))
        _raise_api_error(res)
        data = json.loads(res)
        _raise_api_error(data)
        for thread_id in batch:
            last_changed = int(data.get(str(thread_id), 0))
            if last_changed <= 0:
                raise F95ApiError(f"No valid last_changed returned for thread {thread_id}")
            results[thread_id] = last_changed
    return results


def run_update_check(log=None) -> dict:
    """Real update-check pass over every f95-sourced game this system tracks,
    populating the datadir schema's `available_updates[]` field (reserved for
    exactly this since Milestone 3, previously unpopulated). Only ever writes
    this system's own entries, same rule as every other write in datadir.py.

    Skips full_check() for a thread when fast_check() shows nothing has
    changed since the last full check recorded here (`f95_last_full_check`,
    a new, additive top-level entry field) -- same optimization the real
    client makes, avoiding a full_check call per game on every run."""
    from pythia import cache, datadir
    from pythia.identity import get_system_uuid

    log = log or (lambda msg: None)
    uuid = get_system_uuid()

    entries_by_thread: dict[int, dict] = {}
    for entry in datadir.list_all_entries():
        if entry.get("system_uuid") != uuid:
            continue
        thread_id = next(
            (p.get("id") for p in entry.get("platform_ids", []) if p.get("platform") == "f95"),
            None,
        )
        if thread_id is not None:
            entries_by_thread[int(thread_id)] = entry

    if not entries_by_thread:
        return {"checked": 0, "updated": 0, "errors": []}

    thread_ids = list(entries_by_thread.keys())
    errors = []
    checked = 0
    updated = 0

    try:
        last_changes = fast_check(thread_ids)
    except (F95ApiError, OSError) as exc:
        return {"checked": 0, "updated": 0, "errors": [f"fast_check failed: {exc}"]}

    for thread_id, entry in entries_by_thread.items():
        last_changed = last_changes.get(thread_id)
        if last_changed is None:
            continue
        if last_changed <= (entry.get("f95_last_full_check") or 0):
            continue  # nothing new since our last full check

        checked += 1
        log(f"Checking F95zone thread {thread_id} ({entry.get('canonical_name')})...")
        try:
            result = full_check(thread_id, last_changed)
        except (F95ApiError, OSError) as exc:
            errors.append(f"thread {thread_id}: {exc}")
            continue

        entry["f95_last_full_check"] = last_changed
        if result is None:
            log(f"  -> thread {thread_id} not found (privated/moved/deleted).")
            saved_path = datadir.save_entry(entry)
            cache.upsert_entry(entry, saved_path.name)
            continue

        installed_version = None
        for group in entry.get("versions", []):
            for variant in group.get("variants", []):
                if (variant.get("source") or {}).get("kind") == "f95":
                    installed_version = group.get("version")

        available_updates = [u for u in entry.get("available_updates", []) if u.get("source") != "f95checker"]
        if installed_version and result["version"] and result["version"] != installed_version:
            available_updates.append(
                {
                    "platform": "unknown",
                    "version": result["version"],
                    "source": "f95checker",
                    "detected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
            )
            updated += 1
            log(f"  -> update available: {installed_version} -> {result['version']}")
            datadir.add_timeline_event(entry, "f95_update_detected", f"{installed_version} -> {result['version']}")
        entry["available_updates"] = available_updates

        saved_path = datadir.save_entry(entry)
        cache.upsert_entry(entry, saved_path.name)

    return {"checked": checked, "updated": updated, "errors": errors}


def full_check(thread_id: int, last_changed: int) -> dict:
    """Full, real thread data for one game -- name/version/developer/type/status/
    tags/changelog/downloads/etc, straight from the real cache API. Returns None
    if the thread is gone (403/404 -- privated, moved, or deleted, same cases
    the real client treats as "thread not found", left for the caller to decide
    what to do about rather than assumed here)."""
    url = API_FULL_CHECK_URL.format(id=thread_id, ts=last_changed)
    req = urllib.request.Request(url, headers={"User-Agent": "Pythia (github.com/bi0shacker001/pythia)"})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            res = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise

    _raise_api_error(res)
    thread = json.loads(res)
    _raise_api_error(thread)

    try:
        thread_type = Type(int(thread["type"]))
    except ValueError:
        thread_type = Type.Unknown
    try:
        thread_status = Status(int(thread["status"]))
    except ValueError:
        thread_status = Status.Unknown

    downloads = json.loads(thread["downloads"])
    downloads = tuple((label, [tuple(link) for link in links]) for label, links in downloads)

    return {
        "id": thread_id,
        "name": thread["name"],
        "version": thread["version"] or "N/A",
        "developer": thread["developer"],
        "type": thread_type,
        "status": thread_status,
        "url": F95_THREADS_PAGE + str(thread_id),
        "last_updated": int(thread["last_updated"]),
        "last_full_check": last_changed,
        "score": float(thread["score"]),
        "votes": int(thread["votes"]),
        "tags": json.loads(thread["tags"]),  # raw tag ints -- see module docstring
        "unknown_tags": json.loads(thread["unknown_tags"]),
        "description": thread["description"],
        "changelog": thread["changelog"],
        "image_url": thread["image_url"],
        "previews_urls": json.loads(thread.get("previews_urls", "[]")),
        "downloads": downloads,
        "reviews_total": int(thread.get("reviews_total", "0")),
        "reviews": json.loads(thread.get("reviews", "[]")),
    }
