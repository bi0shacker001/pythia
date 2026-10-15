"""droidtop-agent's scanner (``droidtop_agent.scan``) over a folder Pythia
scans: what its rules say a folder is, where Pythia's own walk only sees
folders. Refusals raise AgentError."""

from collections import defaultdict
from pathlib import Path

from pythia.droidtop.native import submodule


def rom_folders(root: Path) -> dict[Path, dict]:
    """Every folder under ``root`` that holds only ROMs by the agent's rules
    (ROM system folders named the ES-DE way, ``psx/``, ``3ds/``, ``gc/``...,
    and any folder of them), each with its ``systems`` and its number of
    ``roms``. A folder that also holds a PC game is not one. Slow: the agent
    walks the tree."""
    root = Path(root)
    found = submodule("scan").scan_with(game_folders=[str(root)])
    roms: dict[Path, list[str]] = defaultdict(list)
    has_other: set[Path] = set()
    for item in found.get("games") or []:
        game = item.get("game") or {}
        install = game.get("install") or {}
        if not install.get("path"):
            continue
        rom = install.get("launcher") == "emulator"
        # Every folder between the scanned one and the game (the game's own,
        # for a PC game, is where it starts).
        for folder in Path(install["path"]).parents if rom else (Path(install["path"]), *Path(install["path"]).parents):
            if folder == root or root not in folder.parents:
                break
            if rom:
                roms[folder].append(game.get("platform") or "")
            else:
                has_other.add(folder)
    return {
        folder: {"systems": sorted(set(platforms)), "roms": len(platforms)}
        for folder, platforms in roms.items()
        if folder not in has_other
    }
