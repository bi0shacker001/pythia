"""Unpacking a downloaded game archive so it can be added like a folder.

Droidtop/tracker#538, the Windows UI test: a .zip that arrived in a watched
download folder could not be added (onboarding takes folders only). Adding
one now unpacks it beside itself, into a folder named after it, and adds that
folder. The archive is the person's download, not a game folder Pythia
tracks, so writing its contents out is what adding it asks for.

.zip is unpacked with Python's own zipfile; .7z and .rar need 7-Zip (its
`7z.exe` on Windows, `7z`/`7zz` on Linux), and without it the error says so.
No member may land outside the target folder (absolute paths and `..` are
refused), and an existing folder of that name is never written into.
"""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar")


class ArchiveError(Exception):
    """The archive could not be unpacked; the message says why."""


def is_archive(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in ARCHIVE_SUFFIXES


def target_for(archive: Path) -> Path:
    """Where ``archive`` unpacks: beside it, named after it without its suffix."""
    return archive.with_suffix("")


def _seven_zip() -> str | None:
    if sys.platform == "win32":
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base and Path(base, "7-Zip", "7z.exe").is_file():
                return str(Path(base, "7-Zip", "7z.exe"))
    return shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")


def _check_members(names: list[str], target: Path) -> None:
    root = target.resolve()
    for name in names:
        dest = (root / name).resolve()
        if name.startswith(("/", "\\")) or (dest != root and root not in dest.parents):
            raise ArchiveError(f"the archive has a file that would land outside its folder: {name}")


def unpack(archive: Path) -> Path:
    """Unpacks ``archive`` into target_for(archive) and returns that folder.
    When the archive holds one top-level folder and nothing else, that folder
    is the game's, and is returned."""
    archive = Path(archive)
    target = target_for(archive)
    if target.exists():
        raise ArchiveError(f"{target} already exists; add that folder, or move it away first")
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(archive) as zf:
                _check_members(zf.namelist(), target)
                zf.extractall(target)
        except zipfile.BadZipFile as ex:
            raise ArchiveError(f"{archive.name} is not a readable .zip ({ex})") from ex
    elif suffix in (".7z", ".rar"):
        tool = _seven_zip()
        if not tool:
            raise ArchiveError(f"unpacking {suffix} needs 7-Zip; install it, or unpack {archive.name} yourself")
        listing = subprocess.run([tool, "l", "-ba", "-slt", str(archive)], capture_output=True, text=True, check=False)
        names = [line[7:] for line in listing.stdout.splitlines() if line.startswith("Path = ")]
        _check_members(names, target)
        done = subprocess.run(
            [tool, "x", "-y", f"-o{target}", str(archive)], capture_output=True, text=True, check=False
        )
        if done.returncode != 0:
            raise ArchiveError(f"7-Zip could not unpack {archive.name}: {(done.stderr or done.stdout).strip()[-300:]}")
    else:
        raise ArchiveError(f"{archive.name} is not an archive Pythia unpacks (.zip, .7z, .rar)")
    entries = [p for p in target.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return target
