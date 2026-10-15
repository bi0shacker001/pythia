"""Pythia's tracked games, handed to the agent as this computer's games.

The agent's own scanner (droidtop-agent's, run in this process) finds the
stores' installs and the game folders; the games Pythia tracks join it where
the scanner did not find them, by key or folder (droidtop-agent DESIGN
section 16). The agent keeps them, so they stay this computer's games when
droidtop-agent serves after Pythia closed. They travel to paired handhelds
like any other install, and their saves are found the same way.

A game's key is the agent's (its DESIGN section 7): a store's id where
Pythia knows one (`steam:<appid>`, `gog:<id>`, `itch:<id>`, `epic:<app
name>`), else its title as `title:<words>`.
"""

import os

from pythia import datadir
from pythia.droidtop.native import AgentError, available, submodule
from pythia.identity import get_system_uuid
from pythia.util.log import logger

# Pythia's platform names for the stores whose ids are the agent's keys.
STORE_KEYS = {"steam": "steam", "gog": "gog", "itch": "itch", "egs": "epic"}


def title_key(title: str) -> str:
    """The agent's key for a game without a store id: its title in lower case
    with only letters and digits (droidtop-agent core `library::title_key`)."""
    words, word = [], []
    for ch in title.lower():
        if ch.isalnum():
            word.append(ch)
        elif word:
            words.append("".join(word))
            word = []
    if word:
        words.append("".join(word))
    return "title:" + " ".join(words)


def game_key(entry: dict) -> str:
    for pid in entry.get("platform_ids") or []:
        store = STORE_KEYS.get(pid.get("platform"))
        if store and pid.get("id"):
            return f"{store}:{pid['id']}"
    return title_key(entry.get("canonical_name") or "")


def _newest_variant(entry: dict):
    """The version and variant installed most recently whose folder exists."""
    best = None
    for group in entry.get("versions") or []:
        for variant in group.get("variants") or []:
            path = variant.get("path")
            if not path or not os.path.isdir(path):
                continue
            stamp = variant.get("detected_at") or ""
            if best is None or stamp >= best[0]:
                best = (stamp, group.get("version"), variant)
    return best


def scanned_games(entries: list[dict], system_uuid: str) -> list[dict]:
    """This system's tracked games as the agent's ScannedGame, with folders."""
    out = []
    for entry in entries:
        if entry.get("system_uuid") != system_uuid:
            continue
        newest = _newest_variant(entry)
        if newest is None:
            continue
        _, version, variant = newest
        title = entry.get("canonical_name") or os.path.basename(variant["path"])
        # A version label that only repeats the title (a store install
        # Pythia found no version for) is no version.
        if not version or title_key(version) == title_key(title):
            version = None
        install = {"installed": True, "path": variant["path"], "launcher": "pythia"}
        if version:
            install["version"] = version
        out.append(
            {
                "key": game_key(entry),
                "title": title,
                "platform": "pc",
                "install": install,
                "base": variant["path"],
            }
        )
    return out


def export(rescan: bool = True) -> dict:
    """Hands this system's tracked games to the agent (``droidtop_agent.scan``),
    in place of the ones handed before. The agent keeps them and joins them
    into every scan, whichever program serves, so closing Pythia does not
    take them off the handhelds. Returns the agent's count of games and
    changes. Slow when it rescans."""
    games = scanned_games(datadir.list_all_entries(), get_system_uuid())
    return submodule("scan").set_manager_games("Pythia", games, rescan=rescan)


def export_if_serving() -> None:
    """export() when this process serves handhelds; nothing otherwise."""
    if not available():
        return
    try:
        if submodule("service").serving().get("state") == "serving":
            export()
    except AgentError as ex:
        logger.warning("Could not hand the library to the droidtop agent: %s", ex)
