"""Prints Pythia's version, from pyproject.toml: the one place it is written. Installers and release tags
take it from here.

    python packaging/version.py            0.5.23            the version
    python packaging/version.py --display  0.5.23-dev.170    with the build CI stamped (pythia/_build.py)
    python packaging/version.py --package  0.5.23~dev.170    the same for deb and rpm, where ~ sorts before 0.5.23

A copy no CI stamped (build 0) prints the plain version for all three."""

import re
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]


def build() -> int:
    found = re.search(r"^CODE = (\d+)$", (ROOT / "pythia" / "_build.py").read_text("utf-8"), re.M)
    return int(found.group(1)) if found else 0


if __name__ == "__main__":
    base, code = version(), build()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--display" and code:
        print(f"{base}-dev.{code}")
    elif mode == "--package" and code:
        print(f"{base}~dev.{code}")
    else:
        print(base)
