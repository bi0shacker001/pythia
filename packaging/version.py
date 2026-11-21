"""Prints Pythia's version, from pyproject.toml: the one place it is written. Installers and release tags
take it from here."""

import tomllib
from pathlib import Path

if __name__ == "__main__":
    project = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text("utf-8"))
    print(project["project"]["version"])
