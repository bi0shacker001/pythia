"""Prints the dependencies a standalone build installs, one per line, from pyproject.toml.

The standalone builds (packaging/pyinstaller/pythia.spec) leave out PyGObject and dbus-python: Pythia's Qt
UI does not use them (GTK was removed on 2026-08-04 and every remaining import of them is optional), and
both need system libraries no frozen program can carry. Environment markers are passed through for pip
to evaluate on the building system.

Usage: python packaging/freeze_requirements.py > requirements.txt
"""

import sys
from pathlib import Path

import tomllib

LEFT_OUT = {"pygobject", "dbus-python"}


def main() -> int:
    left_out = LEFT_OUT | ({"pyside6"} if "--qt-free" in sys.argv[1:] else set())
    project = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text("utf-8"))
    for requirement in project["project"]["dependencies"]:
        name = requirement.split(";")[0].strip().split("[")[0]
        for sep in "<>=!~ ":
            name = name.split(sep)[0]
        if name.lower() not in left_out:
            sys.stdout.write(requirement + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
