"""
Generic/unknown-engine fallback library plugin -- no engine-specific signal, just the
folder itself. Used when nothing more specific (Ren'Py, Steam, ...) matched.

Unlike the other library plugins, this one has no structural "is this a root" rule of
its own -- during a library-wide scan, scanning.py only ever calls this on direct
children of the scan root (see the plan's Detection + Scanning section). When a
path is onboarded directly (the user pointed at it explicitly), that positional
restriction doesn't apply and this always matches as the final catch-all.

Moved verbatim from detectors/generic.py, adapted to the LibraryPlugin interface only.
"""

from pathlib import Path

from pythia.plugins import LibraryPlugin

# Folder names that are common internal-engine/publisher-tooling structure (bin,
# logs, redist installers, ...), not a separate game of their own -- used by
# looks_like_pure_container() below to tell "a wrapper folder full of unrelated
# games" (e.g. Manual/Extracted/{Flashpoint, yansim}) apart from "a real game whose
# own install just happens to have zero files directly at its root, only
# subfolders" (e.g. a Ubisoft/Uplay install: Output/, Support/, bin/, bin_plus/,
# data_final/, logs/, uplay_download/ and nothing else at the top level). Neither
# signal alone is reliable (see the 2026-08-02 real-library import); requiring both
# -- no files directly present, AND none of the subfolder names look like internal
# engine structure -- is what keeps this from misfiring on real installs.
_STRUCTURE_FOLDER_NAMES = {
    "bin", "bin_plus", "binaries", "data", "data_final", "logs", "log", "support",
    "output", "config", "cfg", "saves", "savedata", "cache", "temp", "tmp", "redist",
    "_commonredist", "engine", "content", "common", "system", "system32", "game",
    "gamedata", "assets", "installer", "install",
}

# Extensions that only ever show up as game *assets*, never as the whole content
# of a real install by themselves -- used to catch a bare asset/backup folder
# (confirmed real case, 2026-08-03: "Pictures", a picture-asset backup the user
# keeps alongside MGQ Paradox for merging chapters, not a game at all -- a flat
# folder of nothing but .png files). Deliberately not used to require a specific
# platform's executable (an earlier version of this check wrongly required an
# .exe, which would have rejected every real non-Windows game too) -- this only
# rejects a folder that is *exclusively* this kind of file, nothing else.
_ASSET_ONLY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tga", ".psd",
    ".txt", ".pdf", ".doc", ".docx", ".md",
}


class GenericLibraryPlugin(LibraryPlugin):
    id = "pythia.library.generic"
    display_name = "Generic (unknown engine)"
    version = "1.0.0"
    author = "pythia"

    def match(self, path: Path):
        if not path.is_dir():
            return None
        try:
            has_content = any(path.iterdir())
        except OSError:
            has_content = False
        if not has_content:
            return None

        if self._looks_like_asset_only_dump(path):
            return None

        return {
            "kind": "unknown",
            "name": None,
            "version_label": None,
            "platforms": ["windows"] if self._os_looks_windows(path) else [],
            "external_id": None,
            "extra": {},
        }

    def _looks_like_asset_only_dump(self, path: Path) -> bool:
        """True only for a flat folder whose files are *exclusively* asset-type
        extensions (see _ASSET_ONLY_EXTENSIONS) with no subfolders at all --
        deliberately narrow, so it only catches an actual pure asset dump
        (confirmed real case: "Pictures") and never a real game that merely
        lacks a detectable Windows executable (a real non-Windows title should
        still match here with platforms=[], not be rejected outright)."""
        try:
            entries = list(path.iterdir())
        except OSError:
            return False
        if any(e.is_dir() for e in entries):
            return False
        files = [e for e in entries if e.is_file()]
        return bool(files) and all(f.suffix.lower() in _ASSET_ONLY_EXTENSIONS for f in files)

    def _os_looks_windows(self, path: Path, max_depth: int = 3) -> bool:
        """Very light heuristic: an .exe somewhere within a few folders of the root
        strongly suggests a Windows install. Checks a few levels deep, not just the
        root, since many real installs (Unreal/Unity-style layouts) nest their actual
        executable under bin/ or Binaries/Win64/ (2026-08-02 real-library finding).
        Doesn't claim to detect Linux native builds yet."""
        try:
            for entry in path.iterdir():
                if entry.is_file() and entry.suffix.lower() == ".exe":
                    return True
                if entry.is_dir() and max_depth > 0 and self._os_looks_windows(entry, max_depth - 1):
                    return True
            return False
        except OSError:
            return False

    def looks_like_pure_container(self, path: Path) -> bool:
        """True if `path` looks like a folder that only *organizes* other games
        rather than being a game install itself -- no real files directly inside it
        (dotfiles like a stray ".windows-serial" marker don't count -- a real-library
        case, 2026-08-02: MGQ Paradox's wrapper folder has exactly one such file and
        nothing else at its own level), and none of its subfolder names look like
        internal engine/publisher structure (see _STRUCTURE_FOLDER_NAMES).
        scanning.py uses this to keep descending through pure organizational wrapper
        folders (e.g. Manual/Extracted/, MGQ Paradox/) instead of onboarding the
        wrapper itself as one bogus "game"."""
        try:
            entries = list(path.iterdir())
        except OSError:
            return False

        if any(e.is_file() and not e.name.startswith(".") for e in entries):
            return False

        subdirs = [e for e in entries if e.is_dir()]
        if not subdirs:
            return False

        return not any(d.name.lower() in _STRUCTURE_FOLDER_NAMES for d in subdirs)


PLUGIN_CLASSES = [GenericLibraryPlugin]
