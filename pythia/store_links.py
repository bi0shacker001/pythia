"""Store links on a game or on one of its folders (UX review A15; owner call 2).

A person pastes a link or an id: an F95zone thread, an itch.io page, a Steam
or GOG store page, or a DLsite work (an RJ code or its page). It is recorded as
a platform id, on the game (entry ``platform_ids``) or on one folder (that
variant's own ``platform_ids``). A folder's own link is what its update check
uses, so a translation with its own thread is checked against that thread,
not the base game's.

Only F95zone has an update check today (pythia.plugins.library.f95_updates).
A link to any other store is kept and says so ("no update check for this
store yet") rather than being refused.
"""

import re

UPDATE_CHECKS = {"f95"}
STORE_NAMES = {"f95": "F95zone", "itch": "itch.io", "steam": "Steam", "gog": "GOG", "dlsite": "DLsite"}

_F95 = re.compile(r"f95zone\.to/threads/(?:[^/]*\.)?(\d+)", re.IGNORECASE)
_STEAM = re.compile(r"store\.steampowered\.com/app/(\d+)", re.IGNORECASE)
_GOG = re.compile(r"gog\.com/(?:[a-z]{2}/)?game/([\w-]+)", re.IGNORECASE)
_ITCH = re.compile(r"^(?:https?://)?([\w-]+\.itch\.io/[\w-]+)", re.IGNORECASE)
_RJ = re.compile(r"\b((?:RJ|VJ|BJ|RE)\d{6,8})\b", re.IGNORECASE)


class LinkError(ValueError):
    """The text is not a link or id Pythia recognises; the message says what it takes."""


def parse(text: str) -> dict:
    """``platform`` and ``id`` for a pasted link or id."""
    text = (text or "").strip()
    if not text:
        raise LinkError("Paste a store link or id.")
    if m := _F95.search(text):
        return {"platform": "f95", "id": m.group(1)}
    if m := _STEAM.search(text):
        return {"platform": "steam", "id": m.group(1)}
    if m := _GOG.search(text):
        return {"platform": "gog", "id": m.group(1).lower()}
    if m := _ITCH.match(text):
        return {"platform": "itch", "id": "https://" + m.group(1).lower()}
    if m := _RJ.search(text):
        return {"platform": "dlsite", "id": m.group(1).upper()}
    raise LinkError(
        "Pythia takes an F95zone thread, an itch.io page, a Steam or GOG store page, "
        "or a DLsite work (RJ code or link)."
    )


def describe(link: dict) -> dict:
    """The link with its store's name and whether Pythia checks it for updates."""
    platform = link.get("platform") or ""
    checked = platform in UPDATE_CHECKS
    return {
        **link,
        "store": STORE_NAMES.get(platform, platform),
        "update_check": checked,
        "note": None if checked else "No update check for this store yet.",
    }


def put(holder: dict, link: dict) -> None:
    """Records ``link`` on an entry or a variant, replacing that store's earlier one."""
    ids = [p for p in holder.get("platform_ids") or [] if p.get("platform") != link["platform"]]
    ids.append({"platform": link["platform"], "id": link["id"]})
    holder["platform_ids"] = ids


def remove(holder: dict, platform: str) -> bool:
    before = holder.get("platform_ids") or []
    after = [p for p in before if p.get("platform") != platform]
    holder["platform_ids"] = after
    return len(after) != len(before)
