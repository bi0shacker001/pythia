"""
itch.io library plugin -- identifies games installed via the itch.io desktop app,
which leaves a .itch/receipt.json.gz marker inside the game's install folder.

2026-08-03: the marker's existence/location is corroborated via web research (search
results converging on ".itch/receipt.json.gz" as the itch app's install receipt),
not first-hand verified against a real local install -- none exists anywhere in this
library yet (the user's own copy of the pivot test case, Love and Sex: Second Base,
is Steam-only on disk). The exact internal JSON schema of receipt.json.gz is
unverified beyond that, so parsing it is deliberately defensive/best-effort: is_root()
only depends on the file's existence, which is the corroborated part; match() itself
degrades gracefully (kind="itch" with no other detail) if the file can't be parsed as
expected. Should be re-verified against a real receipt file the first time one is
available, per this project's "ground in real samples" rule.
"""

import gzip
import json
from pathlib import Path

from pythia.plugins import LibraryPlugin
from pythia.plugins.registry import register_builtin

_RECEIPT_REL_PATH = (".itch", "receipt.json.gz")


class ItchLibraryPlugin(LibraryPlugin):
    id = "pythia.library.itch"
    display_name = "itch.io"
    version = "1.0.0"
    author = "pythia"

    def is_root(self, path: Path) -> bool:
        return path.joinpath(*_RECEIPT_REL_PATH).is_file()

    def _read_receipt(self, path: Path) -> dict | None:
        receipt_path = path.joinpath(*_RECEIPT_REL_PATH)
        try:
            with gzip.open(receipt_path, "rt", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, EOFError, json.JSONDecodeError):
            return None

    def match(self, path: Path):
        if not self.is_root(path):
            return None

        receipt = self._read_receipt(path) or {}
        game = receipt.get("game") or {}
        game_id = game.get("id")

        return {
            "kind": "itch",
            "name": game.get("title"),
            "version_label": None,
            # itch uploads are platform-specific but the receipt schema for which
            # platform *this* install is hasn't been verified -- leaving this empty
            # rather than guessing "windows" the way the Steam plugin's no-manifest
            # fallback does (that one's grounded in "this is the common case,"
            # this one isn't grounded in anything yet).
            "platforms": [],
            "external_id": int(game_id) if isinstance(game_id, int) else None,
            "extra": {"itch_game_id": game_id} if game_id is not None else {},
        }


plugin = register_builtin(ItchLibraryPlugin())
