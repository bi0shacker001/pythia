# Pythia — Project Status (for external review)

**Date:** 2026-08-03
**Purpose of this document:** a complete, honest snapshot of what Pythia is, what's
actually built and verified, what's stubbed, and what's not started — intended to be
handed to someone with no prior context for independent assessment.

---

## 1. What Pythia is

Pythia is a **multi-system game library manager** — not a launcher. Steam, Lutris,
Playnite, EmulationStation, GOG Galaxy, Epic's launcher, etc. remain the software that
actually *runs* games. Pythia's job is to know what games exist, what versions/variants
of them the user has, where, on which platforms, and to keep that picture in sync
across several of the user's own machines (a desktop, a handheld like a GPD Win 4,
etc.) without ever conflicting.

**Pythia never launches a game's own executable.** This is an absolute, repeatedly
enforced design boundary (see §9). What it does do: identify games, track
versions/variants, pull ownership/metadata from storefront accounts, patch/prepare
engine runtimes (e.g. adding missing native Linux ARM64 libs to Ren'Py games), hand off
to a launcher that then runs the game, and (in progress) act as a full F95Checker
replacement for the adult-game-update-tracking workflow.

### Origin

The project began as `renpatchpy.py`, a single-purpose script adding native Linux
x86_64/ARM64 engine libraries to Ren'Py game installs (still present, unchanged, at
`pythia/renpy_patch/`). It grew into the current scope across one very long working
session. Long-term vision (stated by the user, not yet built): this is step one of a
broader Linux-compatibility-layer manager (Wine/Proton/FEX-Emu version management for
ARM64 Linux handhelds).

---

## 2. Architectural pivot: built on a fork of Lutris

Partway through the session, the user made a deliberate decision to stop building
Pythia's own scaffolding from scratch and instead **fork Lutris**
(github.com/lutris/lutris, GPLv3) as the real foundation, repurposing its actual code
rather than reimplementing the same ideas:

> "Lutris has one function, to launch games. But it also has everything we need, and
> uses it differently. It has a sources system. It has a runners system... It's a
> stable core we can repurpose."

**How this was actually done:**
- `lutris/` is a **read-only reference clone** in the workspace (never imported from
  directly).
- Real code is ported piece by piece into `pythia/`, decoupled from GTK (`gi`), renamed
  (`lutris`→`pythia` for internal paths/branding; external URLs like `lutris.net`'s
  public API are kept, since that's a genuine external data source, not Lutris's own
  branding), and adapted to write into Pythia's own `local.db`/`cache.db`/
  `datadir/*.json` storage instead of Lutris's `pga.db`.
- **`database/` (`pga.db`) is explicitly NOT ported.** It's a single-machine SQLite
  file with no cross-system-merge concept — exactly what Pythia's own sync-safe
  `datadir/*.json` model exists to avoid. This was a deliberate, reasoned decision, not
  an oversight.
- GPLv3 obligations apply to whatever's actually derived from Lutris's code. This is
  accepted as a consequence, not something being engineered around — Pythia itself is
  intended as LGPL, which is GPL-compatible.
- All of this work lives on a `lutris-base` branch of the *existing* Pythia repo
  (`github.com/bi0shacker001/pythia`), not a separate fork repository.

Work also proceeds against Lutris's real GitHub API assets and a second reference
clone, `f95checker/` (github.com/WillyJL/F95Checker, GPLv3), for the F95zone
integration (§7).

---

## 3. Core data model

### Cross-system sync

- `datadir/{system_uuid}-{game_id}-{sanitized_name}.json` — one file per (system,
  game) pair. **Single-writer-per-file**: a system only ever writes files whose name
  starts with its own UUID. This is the entire sync-safety model — point Syncthing (or
  any file-sync tool) at `datadir/` and two systems can never produce a write conflict,
  because they never write the same file.
- `local.db` (SQLite) — durable per-system identity (`system_uuid`, settings,
  game-id counter). Never synced.
- `cache.db` (SQLite) — rebuildable local index over `datadir/`. Safe to delete and
  rebuild at any time.
- Schema is at **v3**, with a chained migration path from v1. Current shape (see
  `pythia/datadir.py`): `canonical_name`, `platform_ids[]` (cross-platform ID mapping,
  e.g. `{"platform": "steam", "id": "..."}`), `ownership[]` (separate from
  `platform_ids` — see §3.1), `versions[] → variants[]` (a version can have multiple
  variants: different mod sets, languages, translation patches), per-variant `source`
  (which plugin/platform this specific on-disk install came from).

### 3.1. Ownership semantics (a deliberately non-obvious design decision)

"Ownership" means **"available for this user to download from this platform,"** not a
legal/moral possession concept:
- On gated platforms (Steam, GOG, Epic), "available to download" and "in my library"
  are the same thing.
- On ungated platforms (F95zone), everything is downloadable by anyone — so what
  matters is a **watchlist** (which specific titles the user has told Pythia they care
  about), not "ownership" in the gated-platform sense.

### 3.2. Real, confirmed gaps in this model (not yet fixed)

- **`game_id` allocation is not collision-safe.** It's a simple per-system counter,
  with external platform IDs (e.g. a Steam AppID) sometimes reused directly as the
  `game_id`. An external architecture review (independently commissioned by the user)
  flagged this; the fix (random 52-bit IDs + a separate `external_ids[]` list) is
  designed but **not implemented**.
- **Onboarding only searches this system's own records**, not the merged
  cross-system view. This means if System B already tracks a game, System A onboarding
  the same install today can mint a duplicate `game_id` rather than recognizing System
  B's existing one. This is a real, foundational bug given the whole point of a shared
  `datadir/`. **Not fixed.**
- **No deterministic merge semantics for shared fields.** No per-field revision/origin
  metadata — a later automated rescan could silently clobber a manually-corrected name
  with no way to tell which write should win. **Not fixed.**
- **No stable variant identity.** Variants are identified by filesystem `path` alone.
  A moved/renamed install looks like a brand-new variant. **Not fixed.**

---

## 4. Plugin architecture

Eight plugin categories (`pythia/plugins/`): `library` (detect/identify installs),
`engine` (identify what built an install — Ren'Py, Unity, RPG Maker, etc. — and
remediate fixable platform gaps), `manager` (hand off to a launcher, e.g. Steam's
`steam://rungameid/`), `downloader` (acquisition), `metadata` (pull public platform
data without needing ownership), `source` (generic on-demand fetch, e.g. a NAS),
`mod`, `patcher` (file-level patches, e.g. bundled fan-translation tools).

**Discovery is fully dynamic** (`pythia/plugins/registry.py`), via three paths:
1. Built-in plugins self-register at import time.
2. Third-party pip-installed packages declaring an `importlib.metadata` entry point.
3. **Drop-in folders** (`app_data_dir()/plugins/*/plugin.json`) — a Playnite-style
   manifest + code folder requiring no `pip install`, with a manifest-only listing
   mode that reads metadata without executing any code. Added specifically because the
   entry-point path alone was judged too high a bar for casual third-party plugins.

Enablement is an **opt-in allowlist** (`enabled_plugins` in `local.db`) — empty/unset
means everything is enabled (fresh-install default; also doubles as a one-click
"reset to everything on" debug mode).

A single plugin instance can implement multiple categories at once (e.g. a plugin
that's both a downloader and a mod source); the registry instantiates each plugin
exactly once and fans it into every category it implements.

**Root-cause architectural fix worth noting**: engine identity (what built an install
— Ren'Py, Unity) and distribution identity (where it's from — Steam, itch.io) were
originally conflated into one `LibraryPlugin.match()` call, causing real bugs (a
Ren'Py game sold on Steam losing either its engine info or its Steam AppID depending
on priority order). Fixed by fully separating the two: `EnginePlugin` identification
always runs regardless of what a `LibraryPlugin` found, and results are merged rather
than one overwriting the other.

---

## 5. Storefront/service integrations — what's real, what's not

| Service | Local install detection | Account-level ownership sync | Notes |
|---|---|---|---|
| **Steam** | ✅ Real, multi-library-folder aware | ✅ Real (Web API, user's own key) | Also finds every Steam library folder across drives via `libraryfolders.vdf`, not just one. Launcher hand-off (`ManagerPlugin`) is now reachable from the desktop UI's detail-panel "Open" button, not just the CLI |
| **itch.io** | ⚠️ Unverified (a plausible marker file, never confirmed against a real local install) | ✅ Real (API key) | |
| **GOG** | ✅ Real (`goggame-*.info` file, cross-platform; plus a Windows-only registry bonus signal, `HKLM\...\GOG.com\Games\<id>`, verified live) | ✅ Real (OAuth2, shared community client credential — see §8) | Real name resolution now shipped: `GogMetadataPlugin` (`api.gog.com/products/{id}`, verified live) auto-fills names for account-sync entries during `record_ownership()`. No `ManagerPlugin` built — the real registry inspection that found the detection signal above also showed GOG Galaxy isn't even installed on this test machine (offline-installer games), so a launcher hand-off was correctly judged not applicable rather than built speculatively |
| **Epic Games Store** | ✅ Real (`ProgramData\Epic\...\Manifests\*.item`) | ✅ Real, via a **downloaded `legendary` binary run as a subprocess** (not a Python import — see §8) | Real names included. `ManagerPlugin` hand-off shipped too (real, documented `com.epicgames.launcher://apps/<id>?action=launch` URI) |
| **F95zone** | N/A (not a distribution format) | ✅ Real, but as a **one-shot import** from a real `db.sqlite3`, not an ongoing account poll (F95Checker's database is a local snapshot, not a durable remote account) | See §7 for the fuller F95 story |
| **Ubisoft Connect** | ❌ Not attempted | ❌ **Explicitly out of scope** | Login flow requires JS-extracted `localStorage` values via an embedded browser — no simple "paste a code" path exists |
| **EA App (Origin)** | ❌ Not attempted | ❌ **Explicitly out of scope** | Even its core token exchange needs browser session cookies, not just a richer optional endpoint |

**Strategy governing this table**, stated directly by the user: prefer a real,
maintained third-party library over hand-rolling an OAuth client (Epic's `legendary`
is the worked example); for genuinely hostile platforms (Ubisoft, EA App), don't chase
full ownership sync at all — metadata-only or launcher-hand-off is the ceiling, if
ever built.

### Downloadable-tool infrastructure (`pythia/core/runners.py`)

Built to solve a real distribution problem: the standalone `pythia.exe` (PyInstaller
build — see §10) has no pip/Python environment to install an optional Python package
into at runtime. Rather than reimplementing this from scratch (an earlier attempt did
exactly that and was corrected), it's a real port of **Lutris's own runner-install
architecture**:
- Architecture-resolution fallback chain ported from `lutris/api.py`.
- Download/extract/make-executable ported from `lutris/runners/runner.py` (stdlib
  `tarfile`/`zipfile` only — no 7zip fallback yet, a stated simplification).
- **Two pluggable catalog sources**: a real, live query against Lutris's own
  `lutris.net/api/runners/<name>` (verified live — returns real GE-Proton/Wine-GE
  entries with real GitHub URLs) for genuine Lutris-hosted runners, and a static
  GitHub-releases catalog for other tools (currently just `legendary`).
- **Explicit architecture safety**: refuses to download a binary for an architecture
  it doesn't have a confirmed real build for (e.g. no `arm64` build of `legendary`
  exists upstream) rather than guessing.

This is the same base a future real Wine/Proton runner port would use — see §6.

---

## 6. What's explicitly NOT started

- **`runners/` port** (Wine/Proton/DXVK version management, emulator cores) — ties to
  the long-term Linux-compat-manager vision. The download-infrastructure base exists
  (§5); the actual runner definitions/launch-adjacent code do not. Per the user's
  explicit instruction, when this does happen, Lutris's actual `play()`/process-
  lifecycle code should be **kept present but disabled behind an experimental flag**,
  not stripped — in case the "never launches anything" boundary is ever revisited.
- **`installer/` port** (F95/Lutris-style install-script interpretation) — the one
  subsystem in Lutris's codebase judged to need real, deliberate rework rather than a
  quick adaptation, because its logic is centrally coupled to registering a game in
  Lutris's own database.
- **F95 live update-checking against f95zone itself**, and **the actual webview** to
  resolve masked/XPath-based download links (confirmed real in imported data — several
  `downloads` entries are literal XPath expressions, not URLs, so a page render is
  genuinely required). The user has confirmed a webview (QtWebEngine) will be used;
  not yet built. This is the real remaining core of "replace F95Checker entirely" —
  everything shipped so far (§7) is the surrounding interop layer, not the update
  engine.
- **Ubisoft/EA App metadata-only integration** — explicitly descoped from full
  ownership sync (§5), but a public-metadata plugin or launcher hand-off was never
  attempted either.
- **Naming-filter template system** — folder-name-to-game-name splitting is still a
  fixed best-effort heuristic (fuzz-tested, see §9), not a learned/configurable system.
- **Save copy-forward / backup**, **ROM specialization** (the original ~14TB NAS
  use case), **structured mod profiles**, **collision-safe game_id / cross-system
  onboarding search** (§3.2), **launcher-entry projection** (writing *into*
  Lutris/Playnite/Steam shortcuts, as opposed to just handing off to them).
- **Big-picture (QML/touch) UI visual polish.** All four screens (Home/Decision/
  Downloads/Settings — Settings didn't exist at all before this pass) now share real
  design tokens and a Lutris-informed source-filter-chip pattern, and Settings is
  backed by real data through new `backend.py` slots (verified against a live
  `QCoreApplication` test). It launches without error, but the user's own assessment
  after seeing it is **"ugly"** — needs a real visual pass against the validated
  desktop-mode mockup, not just a token substitution on the old layout skeleton.
  Tracked, not done.
- **Pythia's own packaging metadata** (`pyproject.toml`), a LICENSE file, and a
  third-party-plugin-authoring guide — flagged repeatedly as needed given the
  "meant to be used by others" posture, never started.

---

## 7. F95Checker integration — the fullest picture

The stated end goal: Pythia should be able to **fully replace F95Checker** for users
who want that, not run alongside it forever ("we don't want two softwares managing
library updates and stuff").

**Shipped and verified:**
- `pythia f95-import [--db-path]` — one-shot import from a real F95Checker
  `db.sqlite3`. Verified against the user's actual 129-game real watchlist (a copy,
  never the live production file), read-only (checksum-confirmed unmodified),
  idempotent on re-run.
- `pythia f95-export <path>` — writes a real, loadable F95Checker-compatible
  `db.sqlite3` back out. Honestly minimal-fidelity: fields Pythia never captured
  (tags, score, developer, ...) write as real empty/zero defaults, not fabricated
  values. Round-trip verified.
- **Real, official browser-extension compatibility** (`pythia/f95_rpc.py`) — F95Checker
  ships a published Chrome/Firefox extension hardcoded to talk to
  `http://127.0.0.1:57095`. Pythia now runs a stdlib-only local HTTP server on that
  exact port implementing the same real endpoints (`GET /games`, `POST /games/add`,
  etc.), confirmed against F95Checker's actual source rather than guessed. The
  genuine, unmodified official extension can point at Pythia instead of F95Checker.
  Verified with simulated real extension traffic (OPTIONS preflight, add-by-URL,
  confirm-it-landed).

**Not built:** live thread-update-checking against f95zone itself, and the webview
needed to resolve masked download links (see §6).

---

## 8. Licensing and third-party credential notes

- Pythia itself: intended as **LGPL** (LGPL is GPL-compatible, permitting combination
  with GPL(-or-stricter) code — this is *why* embedding real Lutris/F95Checker code is
  fine, not something being sidestepped).
- Lutris and F95Checker source: **GPLv3**. Both cloned as reference, code ported
  piece by piece with GTK/runtime coupling stripped.
- **`legendary` (Epic)**: a real, independently-maintained third-party GPLv3 tool
  (github.com/derrod/legendary), used as a downloaded binary, not embedded code.
- **Shared "community" OAuth credentials**: GOG and Epic both use hardcoded
  `client_id`/`client_secret` values that are the same across Lutris, Pythia, and (for
  Epic) the independent `legendary` project. Confirmed via real research: neither GOG
  nor Epic offers a self-serve third-party OAuth registration process — a GOG
  maintainer states directly on their own community forum "we have no way to register
  our own clients." These values are best understood as de-facto shared/reverse-
  engineered credentials belonging to each platform's own official client, reused
  informally across the open-source tooling ecosystem, not a secret unique to any one
  project. **This was an explicit user decision** ("use the shared community
  credential") after the tradeoff was surfaced, not a default assumption.

---

## 9. Standing behavioral/architectural rules (repeatedly enforced this session)

- **Never launches a game's own executable.** Refined but not dropped: running a
  *preparation* tool (a patcher, an installer) is allowed; handing off to a
  third-party launcher via its own URI/command is allowed (the other tool's mechanism
  runs the game, not Pythia). Reading binary file contents for detection is fine.
- **Don't fabricate or guess real-world facts** (file schemas, API shapes, licensing
  terms) — every non-trivial claim in this session was verified against real source
  code, real API responses, or the user's real (copied, not production) data before
  being built against. Several real bugs were caught specifically by this discipline
  (see below).
- **Credentials sync via `datadir/credentials/{system_uuid}.json`**, not `local.db`
  — a deliberate, explicitly-accepted tradeoff (no encryption at rest; anything with
  read access to the synced folder can read stored API keys/tokens) chosen because a
  login flow with real friction (GOG's OAuth dance especially) shouldn't have to be
  repeated on every synced device.
- **Fuzz-tested**: `tests/test_naming_fuzz.py` (stdlib `unittest`+`random`, no
  dependency) exercises the folder-name-parsing heuristic against ~2000 randomized
  adversarial inputs per function plus a real-name regression corpus. Found and fixed
  one real bug (a hand-typed path starting with `//` parsed as a UNC anchor on
  Windows, producing a silently empty canonical name).
- **A second real bug caught by testing, not review**: `onboarding.sync_owned()`
  originally only matched existing tracked entries via a numeric `external_id`, which
  a string-identified service (Epic's `AppName`) could never populate — every Epic
  game would have duplicated on every re-sync. Caught and fixed before shipping, with
  a real two-sync verification test.
- **A third, critical bug — found and fixed, worth flagging to a reviewer
  specifically**: `identify_all()` called every enabled `LibraryPlugin`'s `match()`
  with no exception handling, and `match()`'s base-class default was
  `raise NotImplementedError`. `F95LibraryPlugin` (account/watchlist-only, no
  path-based matching by design) never overrode it. **Net effect: every single
  `onboard`/`scan` call — CLI or UI — had been crashing since F95's library plugin
  was registered.** It slipped through because every regression check in this
  session had re-run `list` (reads already-tracked data) rather than exercising
  fresh onboarding end-to-end — a real gap in the verification discipline itself,
  worth a reviewer's attention: **testing what already works isn't the same as
  testing the actual failure mode.** Fixed at the root (the base-class default
  changed to `None`, matching every other "no answer" convention already in the
  codebase; `identify_all()` gained the same per-plugin isolation every other
  discovery path already had) and re-verified against a real scan of the user's
  actual library (4 real titles, 0 errors), not just the one crashing case.

---

## 10. Distribution model

- **Single CLI entrypoint**, no separate utility scripts: `python -m pythia
  <subcommand>` (source checkout) or the standalone `pythia`/`pythia.exe` binary
  (PyInstaller build, `pythia.spec`) — both dispatch through the same `argparse` tree
  in `pythia/cli.py`.
- **Not distributed via pip.** The user's explicit intent: install via the system's
  own package manager (a real OS package — `.deb`, winget, AUR, etc. — is not yet
  built; the standalone-binary foundation for one is).
- Verified end-to-end: the frozen `pythia.exe` runs standalone with zero Python/pip
  present on the machine, and every subcommand added this session is present in it.
- The UI (`python -m pythia` with no subcommand) needs PySide6; every CLI command
  works without it. `legendary` (Epic) is a downloaded binary, not a Python
  dependency at all (see §5).

---

## 11. Suggested focus areas for review

1. **The sync-safety model itself** (§3) — is single-writer-per-file actually
   sufficient, or are there sync-conflict edge cases not yet considered (e.g. a file
   deleted on one system while another is mid-write)?
2. **The three confirmed-but-unfixed data-model gaps** (§3.2) — collision-safe
   `game_id`, cross-system onboarding search, and merge semantics are all real,
   acknowledged, load-bearing gaps given the project's whole premise is multi-system
   sync.
3. **The shared-OAuth-credential decision** (§8) — reasonable given the real
   constraints found, but worth a second, independent opinion given it involves
   platform ToS territory this session couldn't fully resolve.
4. **Whether "explicitly out of scope" for Ubisoft/EA App (§5, §6) is the right
   long-term call**, or whether it should be revisited once/if an embedded browser is
   built for F95 anyway.
5. **The plugin architecture's actual extensibility** (§4) — it's designed for
   third-party use, but has never been exercised by an actual third party.
