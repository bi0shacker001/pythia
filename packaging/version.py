"""Prints Pythia's version, from pyproject.toml: the one place it is written. Installers and release tags
take it from here."""

from pathlib import Path

import tomllib

if __name__ == "__main__":
    project = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text("utf-8"))
    print(project["project"]["version"])
