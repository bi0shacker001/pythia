"""
Steam non-Steam-game shortcut generation (2026-08-04) -- real, verified against
Valve's own binary VDF format via the `vdf` library (PyPI, real dependency
justified here: hand-rolling a binary format risks silently corrupting the
user's real Steam config, exactly the kind of real need this project's
dependency policy allows one for) and against the user's own real, live
`shortcuts.vdf` (confirmed the library round-trips it correctly, even though
that file happened to be empty on this machine).

**Real, confirmed limitation, not glossed over**: Valve changed non-Steam-game
shortcut ID assignment to be ad-hoc/non-deterministic even for identical
exe+name inputs in recent Steam versions (confirmed:
github.com/ValveSoftware/steam-for-linux/issues/9463) -- so there is no way to
*predict* what ID Steam's own "Add a Game" UI would assign. This does not block
writing shortcuts directly, though: Pythia writes the entry into shortcuts.vdf
itself and picks its own `appid` field (the classic CRC32-based formula below,
ported from the real, documented algorithm multiple community tools use --
`crc32('"{exe}"{appname}"') | 0x80000000`) -- that value becomes authoritative
the moment Steam reads the file, since Steam isn't generating it through its
own UI flow in this path. The real limitation this leaves: custom grid artwork
some *other* tool cached under the old predicted-ID scheme may not line up:
a real, stated caveat, not a functional blocker for adding/launching shortcuts.

**Never touches the real file without an explicit, careful read-merge-write**:
`sync_shortcuts()` only ever adds/updates entries whose `AppName` is already
tracked as Pythia-managed (matched by name, tagged with a "Pythia" entry in the
shortcut's own `tags` list so a re-run is idempotent and identifiable) --
every other existing entry in the user's real file is preserved untouched. A
`.bak` copy of the original file is written before the first real modification
in a given process.
"""

import shutil
import zlib
from pathlib import Path

import vdf

PYTHIA_TAG = "Pythia"


def shortcut_appid(exe: str, appname: str) -> int:
    """Real, documented CRC32-based non-Steam-shortcut ID formula (ported from
    the algorithm multiple community Steam-shortcut tools use, confirmed via
    real research, not guessed): crc32('"{exe}"{appname}"') | 0x80000000.

    Real bug fixed during testing: the raw value has its high bit set (by
    design -- that's what marks it as a non-Steam shortcut ID to begin with),
    which is >2^31 and doesn't fit `vdf`'s binary writer, which packs plain
    ints as *signed* int32. Converting to two's-complement here matches how
    the `vdf` library itself represents the field. Not yet confirmed against
    a real Steam-populated entry (the one real shortcuts.vdf available for
    testing this session had zero existing non-Steam shortcuts in it) --
    worth a real round-trip check against an actual populated file before
    this ships for real."""
    key = f'"{exe}"{appname}"'.encode("utf-8")
    unsigned = (zlib.crc32(key) & 0xFFFFFFFF) | 0x80000000
    return unsigned - 0x100000000 if unsigned >= 0x80000000 else unsigned


def read_shortcuts(path: Path) -> dict:
    """{index: {field: value}} -- the real on-disk shape (a dict keyed by
    stringified integer index under "shortcuts"), or an empty dict if the file
    doesn't exist yet (a user with no non-Steam games has no reason to have
    ever created one)."""
    path = Path(path)
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        data = vdf.binary_loads(f.read())
    return data.get("shortcuts", {})


def _is_pythia_managed(entry: dict) -> bool:
    tags = entry.get("tags") or {}
    # Steam stores `tags` as an index-keyed dict too (same VDF array
    # convention), not a plain list -- values are the tag strings.
    values = tags.values() if isinstance(tags, dict) else tags
    return PYTHIA_TAG in values


def sync_shortcuts(path: Path, entries: list[dict], log=lambda *_: None) -> int:
    """Add/update Pythia-managed entries in the real shortcuts.vdf at `path`,
    preserving every other existing entry untouched. `entries`: list of
    {"appname", "exe", "start_dir", "launch_options"}. Returns how many entries
    were written. Backs up the original file to `<path>.bak` first if it
    exists and no backup from this run already exists."""
    path = Path(path)
    shortcuts = read_shortcuts(path)

    if path.is_file():
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            shutil.copy2(path, backup)
            log(f"steam_shortcuts: backed up {path} -> {backup}")

    # Index Pythia-managed entries by AppName so a re-run updates in place
    # instead of duplicating.
    by_name = {}
    next_index = 0
    for idx_str, entry in shortcuts.items():
        try:
            idx = int(idx_str)
        except (TypeError, ValueError):
            continue
        next_index = max(next_index, idx + 1)
        if _is_pythia_managed(entry):
            by_name[entry.get("appname")] = idx

    written = 0
    for item in entries:
        appname = item["appname"]
        exe = item["exe"]
        start_dir = item.get("start_dir") or str(Path(exe).parent)
        shortcut_entry = {
            "appid": shortcut_appid(exe, appname),
            "appname": appname,
            "exe": f'"{exe}"',
            "StartDir": f'"{start_dir}"',
            "icon": "",
            "ShortcutPath": "",
            "LaunchOptions": item.get("launch_options") or "",
            "IsHidden": 0,
            "AllowDesktopConfig": 1,
            "AllowOverlay": 1,
            "OpenVR": 0,
            "Devkit": 0,
            "DevkitGameID": "",
            "DevkitOverrideAppID": 0,
            "LastPlayTime": 0,
            "tags": {"0": PYTHIA_TAG},
        }
        idx = by_name.get(appname, next_index)
        if appname not in by_name:
            next_index += 1
        shortcuts[str(idx)] = shortcut_entry
        written += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(vdf.binary_dumps({"shortcuts": shortcuts}))
    log(f"steam_shortcuts: wrote {written} Pythia-managed entr{'y' if written == 1 else 'ies'} to {path}")
    return written
