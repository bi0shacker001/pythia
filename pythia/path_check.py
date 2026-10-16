"""Checking a path typed or picked in a dialog before anything starts.

UX review 2026-10-16 (tracker coordination/pythia/ux-review-2026-10-16.md),
A2: a missing or unreadable path started a job that then failed. The Add,
Scan, Watch and Export dialogs now ask this first, keep their button disabled
while it gives a reason, and show the reason under the field.

``check(path, want)`` returns "" when the path will do, or the reason it will
not, in words for the person. ``want`` is what the dialog needs:

- ``game``: a folder Pythia can read, or an archive it unpacks (Add);
- ``folder``: a folder Pythia can read (Scan, Watch);
- ``writable``: a folder Pythia can write into (Export);
- ``program``: a file Pythia can run, or nothing (Watch's optional program).
"""

import os
import sys
from pathlib import Path

from pythia import archives

WANTS = ("game", "folder", "writable", "program")


def _readable_folder(path: Path) -> str:
    if not os.access(path, os.R_OK | os.X_OK):
        return f"Pythia cannot read {path}."
    try:
        next(os.scandir(path), None)
    except OSError as ex:
        return f"Pythia cannot read {path}: {ex.strerror or ex}."
    return ""


def check(path_str: str, want: str) -> str:
    if want not in WANTS:
        raise ValueError(f"unknown path check {want!r}")
    text = (path_str or "").strip()
    if not text:
        return "" if want == "program" else "Type a path, or choose one with Browse."
    path = Path(os.path.expanduser(text))
    if not path.is_absolute():
        start = "from the drive" if sys.platform == "win32" else "with /"
        return f"Give the whole path, starting {start}."
    if not path.exists():
        return f"Nothing is at {path}."
    if want == "program":
        if not path.is_file():
            return f"{path} is a folder, not a program."
        if sys.platform != "win32" and not os.access(path, os.X_OK):
            return f"{path} is not marked as a program that can run."
        return ""
    if want == "game" and archives.is_archive(path):
        return "" if os.access(path, os.R_OK) else f"Pythia cannot read {path}."
    if not path.is_dir():
        if want == "game":
            return f"{path} is a file; add its folder, or a .zip, .7z or .rar."
        return f"{path} is a file, not a folder."
    reason = _readable_folder(path)
    if reason or want != "writable":
        return reason
    if not os.access(path, os.W_OK):
        return f"Pythia cannot write into {path}."
    return ""
