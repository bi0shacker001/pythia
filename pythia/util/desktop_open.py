"""Open files and web links through the desktop without a GUI toolkit."""

import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from pythia.util.log import logger


def local_path(value: str) -> str:
    if not value.startswith("file:"):
        return os.path.expanduser(value)
    url = urlsplit(value)
    if url.netloc and url.netloc.lower() != "localhost":
        return url2pathname(f"//{url.netloc}{url.path}")
    return url2pathname(url.path)


def open_path(value: str) -> bool:
    path = str(Path(value).absolute())
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        return subprocess.run([opener, path], check=False, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("The desktop could not open the requested path")
        return False


def open_web(value: str) -> bool:
    try:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname:
            return False
        return webbrowser.open(value)
    except (ValueError, OSError, webbrowser.Error):
        return False
