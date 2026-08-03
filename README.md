# Pythia

A multi-system game library manager. Pythia doesn't run your games -- Lutris, Steam,
EmulationStation, and Playnite already do that well. It keeps track of what versions
of what games exist, where, on which of your systems, on which platforms, who you
own or can get them from, and what's needed to make each one actually run.

## Requirements

Pythia's core (scanning, onboarding, tracking, plugin detection, and the Ren'Py
ARM/x86_64 patcher) and its CLI use **only the Python standard library** -- any stock
`python3` (3.10+) on Windows or Linux runs them, nothing to install. A few individual
plugins reach out to a platform's public web API (e.g. Steam's `appdetails`) using
nothing but `urllib` -- still stdlib, no extra dependency.

**The UI (`python -m pythia` with no subcommand) needs PySide6:**
```
pip install pyside6
```
Nothing else does. If PySide6 isn't installed, the CLI still works fine; only the
no-args UI launch prints a short message pointing back here instead of a traceback.

**`epic-connect`/`epic-sync` need `legendary`** (a real, actively-maintained,
third-party Epic Games Store CLI -- preferred over reimplementing Epic's OAuth
client ourselves). Nothing to install by hand: the first time it's needed, Pythia
downloads the real, official, architecture-matched standalone binary from
legendary's own GitHub releases into `app_data_dir()/tools/legendary/` and runs it
as a subprocess (same model as Lutris's own runners system -- a downloaded external
tool binary, not a Python import). This is why it's a *downloaded* dependency rather
than a `pip install`: the standalone `pythia.exe` build has no pip/Python
environment to install into at runtime. Only x86_64 Windows/Linux builds exist
today (no arm64 build is published upstream) -- `epic-connect`/`epic-sync` report a
clear "not available on this architecture" rather than downloading a mismatched
binary. Its own login session is stored locally (`app_data_dir()/legendary`, not
the synced `datadir/`) -- you only need to run `epic-connect` on one system, ever;
the resulting owned-games data syncs normally like everything else once that system
does.

Nothing under `pythia/renpy_patch/` will ever gain a dependency -- that module is
stdlib-only, no exceptions.

## Usage

```
python -m pythia                                open the UI (auto-picks desktop or
                                                 big-picture mode by screen size)
python -m pythia --ui desktop                   force desktop (QtWidgets) mode
python -m pythia --ui bigpicture                force big-picture (QML, touch-first) mode
python -m pythia scan <path>                    discover and track every game under a folder tree
python -m pythia onboard <path>                 identify and track a single game folder
python -m pythia list                           list every game this system knows about
python -m pythia status <path>                  engine platform support + detected patchers for one install
python -m pythia enable <path> [--dry-run]      run whatever engine remediation is fixable for one install
python -m pythia open <game_id> [--yes]         hand a game off to a manager plugin (e.g. the Steam client)
python -m pythia duplicates                     list tracked games that look like the same game
python -m pythia merge <a> <b>                  fold game_id b into game_id a
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
- **Desktop** (`pythia/ui/desktop/`, QtWidgets): a management window with a
  master-detail game view, a reconciliation dialog for merging duplicate entries,
  and a scan action.

The chosen mode is remembered per system (not synced) and can be overridden any time
with `--ui`.

### Launching downloader tools

The "Downloads" screen (big-picture mode) launches an external tool (f95checker, a
browser, ...) as its own window and watches a folder you pick for new files --
Pythia never reimplements or embeds another tool's UI, just observes what shows up
and offers to import it. See `pythia/downloads.py`.

## How it identifies games

Detection is split into two independent, always-both-consulted layers, not one
first-match-wins list (2026-08-03 -- an earlier version conflated these, which
caused a Steam-distributed Ren'Py game to lose either its engine identity or its
Steam AppID depending on plugin priority order):

- **Library plugins** (`pythia/plugins/library/`) find structural boundaries --
  where does an install start and stop -- and, where a real distribution/storefront
  shape exists, a `kind` and platform ID (Steam's `steamapps/common/` layout
  enriched from the adjacent `appmanifest_*.acf`; itch.io's `.itch/receipt.json.gz`
  marker). Everything else falls through to a generic direct-child fallback with no
  distribution signal.
- **Engine plugins** (`pythia/plugins/engine/`) always run afterward, independent of
  whatever a library plugin found, and identify what actually built the install from
  real file signatures: Ren'Py (`renpy/`+`game/` structure), Unity (`UnityPlayer.dll`),
  kirikiri/KAG3 (`.xp3` archives), RPG Maker MV/MZ/VX Ace (`rpg_core.js`/
  `rmmz_core.js`/`.rgss3a`), Buriko/BGI (`BGI.gdb`/`BGI.hvl`), CatSystem2
  (`cs2conf.dll`), Flash-as-AIR (`META-INF`+`mimetype`), Unreal Engine
  (`Engine/Binaries/`). If an engine matches, its kind wins as the install's primary
  identity (more actionable than a bare distribution kind); the distribution fact
  (Steam AppID, itch.io ID, ...) is preserved separately in `platform_ids`/
  `ownership`, never discarded.

Nothing about *where* a game lives is ever decided by Pythia; you place things
however makes sense for you, and Pythia learns from that.

## Plugins

Eight dynamically-discovered, independently enable-able categories, each a small
class-based interface in `pythia/plugins/`: **library** (detection), **manager**
(hands a game off to a launcher, e.g. `steam://rungameid/`, never launches it
directly), **engine** (identification + platform-support reporting + fixable-gap
remediation), **downloader** (acquisition, e.g. f95checker/SteamCMD), **metadata**
(pulls platform support/DLC from a storefront's public API, deliberately separate
from both detection and downloading), **source** (generic on-demand fetch -- NAS,
cloud storage), **mod** (mod identification/acquisition), **patcher** (detects/
manages file-level patches like a bundled fan-translation tool).

Built-in plugins ship inside `pythia` itself and self-register at import time;
third-party plugins are their own installable package declaring an
`importlib.metadata` entry point under a single `pythia.plugins` group -- one entry
point per plugin however many categories it implements (a plugin can legitimately
implement several at once; the registry in `pythia/plugins/registry.py`
instantiates it exactly once and fans it into every matching category). Which
plugins are active is controlled by an opt-in `enabled_plugins` allowlist in
`local.db`; empty/unset means everything discovered is enabled.

## Reconciliation

Automatic scanning only ever auto-links an exact path or external-ID match -- it
never guesses that two differently-named folders are the same game. That's
deliberately conservative, but it means genuine duplicates (a folder renamed
between versions, inconsistent capitalization) land as separate tracked games until
someone confirms they're the same. `pythia duplicates` lists candidates by name
similarity with real context (engine, path, version count) to judge each pair;
`pythia merge <a> <b>` folds `b` into `a` on this system's own records. The desktop
UI's "Review Duplicates" button does the same thing with a real dialog. Nothing is
ever merged automatically.

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
  file and sync conflicts can't happen by construction. `datadir/`'s location can
  also be overridden independently of the rest (`PYTHIA_DATADIR_OVERRIDE`) --
  pointing two differently-identified local instances at one shared folder
  simulates multi-system sync locally, without needing a second machine or a real
  sync tool.

Each game entry records nested `versions[] -> variants[]` (mods, language,
platforms, and the per-install `source` -- an install can come from more than one
place, e.g. a Steam copy and an Epic copy of the same game), plus top-level
`platform_ids[]` (what a game is known as on other platforms, useful for pulling
metadata even where you don't own it) and `ownership[]` (where you can actually get
it -- seeded from a real install existing, additive across every source found).
