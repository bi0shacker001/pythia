# Pythia

A personal, multi-system game library manager. Pythia doesn't run your games --
Lutris, Steam, EmulationStation, and Playnite already do that well. It keeps track
of what versions of what games exist, where, on which of your systems, and on
which platforms, so those launchers can stay pointed at the right thing without
hand-editing entries every time something updates.

## Requirements

Pythia's core (scanning, onboarding, tracking, and the Ren'Py ARM/x86_64 patcher) and
its CLI (`scan`, `onboard`, `list`, `renpy-patch`) use **only the Python standard
library** -- any stock `python3` (3.10+) on Windows or Linux runs them, nothing to
install.

**The UI (`python -m pythia` with no subcommand) needs PySide6:**
```
pip install pyside6
```
Nothing else does. If PySide6 isn't installed, the CLI still works fine; only the
no-args UI launch prints a short message pointing back here instead of a traceback.
Installed automatically by the Windows installer, when one exists.

Nothing under `pythia/renpy_patch/` will ever gain a dependency -- that module is
stdlib-only, no exceptions.

## Usage

```
python -m pythia                              open the UI (auto-picks desktop or
                                               big-picture mode by screen size)
python -m pythia --ui desktop                  force desktop (QtWidgets) mode
python -m pythia --ui bigpicture                force big-picture (QML, touch-first) mode
python -m pythia scan <path>                    discover and track every game under a folder tree
python -m pythia onboard <path>                 identify and track a single game folder
python -m pythia list                           list every game this system knows about
python -m pythia renpy-patch --library <path> [--dry-run]
python -m pythia renpy-patch --game <path> [--dry-run]
```

### UI modes

Two front ends share one backend (`pythia/ui/backend.py`) and the same tracking
data -- neither is a separate app:

- **Big-picture** (`pythia/ui/bigpicture/`, Qt Quick/QML): large touch targets, few
  screens, mostly yes/pick-one/skip decisions. Built for small/handheld screens
  (defaults on below a ~900px shorter screen dimension) -- e.g. a Retroid Pocket 5
  running Pocknix. Uses the lightweight "Basic" Qt Quick Controls style and avoids
  shader effects (blur/drop-shadow/colorize), both deliberate choices to stay light
  on embedded/ARM GPUs.
- **Desktop** (`pythia/ui/desktop/`, QtWidgets): a minimal management window for now
  (game list + scan action) -- a fuller bulk-review/filter-editing console is future
  work, not yet built.

The chosen mode is remembered per system (not synced) and can be overridden any time
with `--ui`.

### Launching downloader tools

The "Downloads" screen (big-picture mode) launches an external tool (f95checker, a
browser, ...) as its own window and watches a folder you pick for new files --
Pythia never reimplements or embeds another tool's UI, just observes what shows up
and offers to import it. See `pythia/downloads.py`.

## How it identifies games

A folder is recognized as a game root by, in order: a Ren'Py install (`renpy/` +
`game/` subfolders), a Steam install (sitting under `steamapps/common/`, enriched
from the adjacent `appmanifest_*.acf`), or -- for direct children of whatever
folder you point `scan` at -- a generic fallback with no engine-specific metadata.
Nothing about *where* a game lives is ever decided by Pythia; you place things
however makes sense for you, and Pythia learns from that.

## Building a Windows executable

For testing on a machine without Python/PySide6 installed:

```
pip install pyinstaller
pyinstaller pythia.spec
```

Produces a self-contained `dist/pythia/pythia.exe` (plus its `_internal/` support
files -- ship the whole `dist/pythia/` folder, not just the .exe). Both
`pythia.exe scan/onboard/list/renpy-patch ...` and `pythia.exe --ui bigpicture|desktop`
work from it directly, no separate install step.

The build keeps a console window attached even in UI mode, since the same exe also
needs to print CLI output -- a cosmetic rough edge, not a bug; a windowed-only build
would silently swallow CLI output instead.

## Tracking data

Lives in a per-OS app-data folder (`%APPDATA%\Pythia` on Windows,
`~/.local/share/pythia` on Linux; override with `PYTHIA_DATA_DIR`):

- `local.db` -- this system's identity (a UUID, generated once and never changed)
  and settings. Never synced.
- `cache.db` -- a rebuildable index over `datadir/`. Safe to delete any time.
- `datadir/` -- one JSON file per (system, game) pair, named
  `{system-uuid}-{game-id}-{name}.json`. **Point Syncthing (or similar) at this
  folder to sync your library's tracking data across systems.** A system only ever
  writes files starting with its own UUID, so two systems can never write the same
  file and sync conflicts can't happen by construction.
