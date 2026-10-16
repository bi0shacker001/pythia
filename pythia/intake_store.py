"""Scan decisions not yet made, kept across restarts (UX review A16).

A scan that finds a folder which may be a game already in the library asks
the person (Reconciliation, the handheld's Attention). Those questions were
kept in memory only, so quitting Pythia lost them and the folder stayed
recorded on its own. They are kept here, in Pythia's own data folder on this
computer (they are this computer's folders), and read back at start. A
decision whose folder is gone is dropped then.
"""

import json
import os
from pathlib import Path

from pythia import settings
from pythia.util.log import logger

FILE_NAME = "intake-decisions.json"


def _path() -> Path:
    return Path(settings.DATA_DIR) / FILE_NAME


def load() -> list[dict]:
    """The decisions waiting, oldest first, without those whose folder is gone."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as ex:
        logger.warning("Could not read the waiting scan decisions (%s): %s", _path(), ex)
        return []
    decisions = data.get("decisions") if isinstance(data, dict) else None
    if not isinstance(decisions, list):
        return []
    kept = [d for d in decisions if isinstance(d, dict) and d.get("path") and Path(d["path"]).exists()]
    if len(kept) != len(decisions):
        logger.info("Dropped %d waiting scan decision(s) whose folder is gone", len(decisions) - len(kept))
    return kept


def save(decisions: list[dict]) -> None:
    """Writes the decisions waiting, replacing the file whole so a crash never leaves half of it."""
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".json.part")
        part.write_text(json.dumps({"decisions": decisions}, indent=1, default=str), encoding="utf-8")
        os.replace(part, path)
    except OSError as ex:
        logger.warning("Could not keep the waiting scan decisions (%s): %s", path, ex)
