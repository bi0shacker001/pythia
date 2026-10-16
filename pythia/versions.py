"""Ordering versions as droidtop does (UX review A13; owner decision 3,
docs/SPEC.md 1.3), for the backend: the page's own copy is
pythia/ui/desktop/web/versions.js, and both follow droidtop's
Versions.compareLoose (runtime-common).

Dotted parts compare as numbers where they are numbers (0.10 after 0.9) and
as text where they are not (0.8.3b after 0.8.3); a missing part is zero (1.2
after 1.1.9); an empty version is the oldest. A label orders only when it is a
version number (1.2, v1.2, 1.0b2, 2.0rc3); "Final" or "Ch.3" do not.
"""

import functools
import re

_ORDERABLE = re.compile(r"^v?\d+(?:[._]\d+)*(?:[a-z]{1,3}\d*)?$", re.IGNORECASE)


def compare_loose(a: str | None, b: str | None) -> int:
    a, b = a or "", b or ""
    if not a or not b:
        return (len(a) > len(b)) - (len(a) < len(b))
    left = re.split(r"[._-]", re.sub(r"^v(?=\d)", "", a, flags=re.IGNORECASE))
    right = re.split(r"[._-]", re.sub(r"^v(?=\d)", "", b, flags=re.IGNORECASE))
    for i in range(max(len(left), len(right))):
        lpart = left[i] if i < len(left) else "0"
        rpart = right[i] if i < len(right) else "0"
        lnum = int(re.match(r"\d*", lpart).group() or 0)
        rnum = int(re.match(r"\d*", rpart).group() or 0)
        if lnum != rnum:
            return -1 if lnum < rnum else 1
        ltext, rtext = re.sub(r"^\d+", "", lpart), re.sub(r"^\d+", "", rpart)
        if ltext != rtext:
            return -1 if ltext < rtext else 1
    return 0


def orderable(label: str | None) -> bool:
    return not label or bool(_ORDERABLE.match(label.strip()))


def latest(labels: list[str | None]) -> str | None:
    """The newest of ``labels``, or None when there are none or they cannot be ordered."""
    labels = [label for label in labels if label is not None]
    if not labels or (len(labels) > 1 and not all(orderable(label) for label in labels)):
        return None
    return max(labels, key=functools.cmp_to_key(compare_loose)) or None


def in_use(entry: dict) -> str | None:
    """The version an entry counts as installed: its Current when set, else the latest installed."""
    if entry.get("current_version"):
        return entry["current_version"]
    return latest([group.get("version") or "" for group in entry.get("versions") or []])
