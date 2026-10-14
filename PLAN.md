# Pythia: current state and the real path to full implementation

## Implementation review update — 2026-08-07 (Codex continuation)

- Web UI notes now call the real datadir/cache write path and report backend errors.
- Per-game rescan and selected-game bulk rescan/reconciliation/export are backed by
  real backend operations. Cache rebuild now calls `games.rebuild_cache()` and reports
  the number of indexed games. Source refresh re-runs enabled library plugins against
  local variants and persists changed source metadata; a universal plugin update-check
  contract remains unimplemented and is labelled unavailable rather than succeeding.
- Instance configuration now persists backend selection, Wine/Proton metadata,
  DXVK/VKD3D/esync/fsync choices, environment, command prefix, pre/post scripts, and
  monitoring include/exclude lists through the existing global → game → instance
  cascade. Native launch plans consume environment/wrapper/script settings. Non-native
  backend choices persist but resolve to `backend_unavailable`; the Lutris-derived
  Wine/Proton runners are not yet adapted into this launch-plan executor.
- Removed the shipped page's “Pass 5 final mockup” title and a hard-coded local
  downloads path. Handheld reviewed-plan apply remains intentionally unavailable:
  there is no safe transactional update/save snapshot/rollback executor yet, so the UI
  no longer claims the operation was queued.
- **F95Checker plugin migration completed:** the former separate
  `pythia.library.f95` and `pythia.downloader.f95checker` packages are now one
  multi-capability `pythia.library.f95` / “F95Checker” package (v1.1.0) declaring
  both `library` and `downloader`. The substantial cache-API fast/full checker was
  recovered from Git history into the package as `f95_update_check.py`, updated to use
  the authoritative datadir/cache write path, and extended to retain thread URL,
  changelog, and download-link metadata with observations. Global and selected-game
  update checks now run as backend jobs and drive real UI results. The package ZIPs
  were rebuilt and discovery was verified. Offline mocked tests cover API batching
  and update persistence. Masked/CAPTCHA links use a real authenticated QtWebEngine
  resolver that mirrors F95Checker's `css_redirect` behavior: inject saved F95
  cookies, load `/masked/.../`, wait for `a.host_link`, then open the resolved URL.
  A restored QtWebEngine sign-in dialog captures the F95zone session in Pythia's
  existing synced, unencrypted credential store (same accepted tradeoff documented
  in `credentials.py`); the Downloads page exposes sign-in plus real
  direct/masked links retained by update checks.
- **First optional plugin dependency:** plugin manifests now accept validated
  `optional_dependencies` declarations (`id`, optional minimum version, and reason).
  Discovery reports installed/enabled/compatible state without loading either
  plugin, and Plugin Details renders that relationship. F95Checker declares the
  installable `pythia.downloader.automator` as an optional enhancement; F95 remains
  fully usable without it and retains browser fallback. Download Automator ships as
  an installable ZIP under `packaging/optional-plugins/`, not a protected built-in.
  Its first real adapter safely handles resolved HTTP(S) downloads, authenticated
  per-host cookie headers, resumable `.part` files, progress, cancellation, and a
  configurable destination. CAPTCHA/masked interaction stays in F95's webview before
  the resolved URL is handed to the automator. Host-specific premium login adapters
  beyond cookie-session reuse remain incremental plugin work.

This is the authoritative project plan. It replaces
`C:\Users\jon\.claude\plans\ethereal-puzzling-mist.md` (archived as
`pythia-plan-ARCHIVE-2026-08-05.md`), a 3,654-line append-only session log whose
Milestone 6 documented "shipped and verified" work on the `lutris-base` branch — a
branch later discovered to be a from-scratch fake that was never actually forked from
Lutris, despite its name. None of those claims are true of the current `lutris-core`
codebase, and the old doc was never corrected to say so, then stopped being updated
entirely after 2026-08-04 — meaning a full day-plus of real architecture (everything in
Part 1 below) had zero coverage in it. See "What this replaces" at the bottom for the
full account.

Everything below is either verified against real code, or explicitly marked
`[UNVERIFIED]`/`[BUG]`. Nothing is asserted as done without having been seen working.
**Keep it that way** — this is the status gate. Any future session that changes
architecture updates this file in the same pass, not after the fact.

**"Full implementation" means**: Pythia manages a personal, multi-system,
Syncthing-synced game library (versions, saves, launcher entries) across machines that
each have their own folder layout, with a real UI (web-validated, then built into the
Qt desktop shell) covering library management, reconciliation, cross-system sync
visibility, and plugin-based extensibility (engines, sources, downloaders, managers,
metadata). Windows is a first-class target from the start (not a later addition) —
storage handling in particular must stay capability-based rather than built around one
Linux filesystem, so macOS support stays possible later without redesigning the
abstraction.

**Reversed 2026-08-05 — the "never launches anything" boundary is gone by default.**
Every earlier design pass (Milestones 1-7) deliberately kept Pythia from launching
games itself, hands-off-only to Lutris/Heroic/Steam/etc. That's now backwards: Pythia
already has Lutris's actual runner code from the `lutris-core` import — "there's no
reason to leave it to lutris." **Default mode is "the new lutris"**: Pythia owns real
runner/prefix execution and is what you click Play in, same as Lutris does today.
A **settings toggle** downgrades this to the old hands-off "update and patch manager"
behavior for anyone who wants Pythia to stay pure bookkeeping — default-on full
replacement, opt-out to the narrower mode, not the other way around. This directly
elevates `runners/` (Step 1 in Part 2) from "someday" to core, default-path work — see
the reordered Part 2 below.

**Real UI redesign research exists for this pivot**:
`C:\Users\jon\Downloads\pythia-research\pass5\` (`pythia_lutris_replacement_ui_handoff_for_claude.md`
+ `pythia_lutris_replacement_ui_mockups.html`, ChatGPT-authored, reviewed and read in full
2026-08-06) is a concrete, detailed redesign proposal for exactly this pivot — an
8-screen interactive HTML mockup (Library, Game/Instances, Configure dialog, Install or
add, Installer workflow, Compatibility & Runtimes, Jobs/Running, Handheld) plus a
written handoff doc. Folded into Step 5 below. Explicitly a **direction**, not pixel-
exact instructions — real Qt implementation maps concepts, not DOM/CSS.

**Standing principle, applies to everything below**: Pythia manages technical
artifacts, not legal/ownership status. It must never infer piracy/authorization,
require storefront verification before managing user-supplied files, or contact
services to establish legitimacy. Technical lineage (source instance, file hashes,
patch order, tool/version, rollback relationship) is legitimate reproducibility data,
not legal provenance, and is treated as such everywhere in the design.

### Static distribution and download-security audit (2026-08-07)

Read-only review of the existing `Pythia-0.0.1-win-x64-portable.zip`, its
PyInstaller analysis/collection manifests, the current plugin loader, the new
F95 integration, and the optional Download Automator found the following. These
are **confirmed gaps, not implemented claims**; no replacement ZIP was built.

**Release artifact drift (release blocker):** the existing portable archive
predates the current source. It still contains the obsolete separate
`pythia.downloader.f95checker` package, the old minimal F95 library package,
`Pass 5 final mockup` branding, and several fake-success/toast-only flows that
have since been corrected in source. The optional Download Automator is also not
distributed by the current Windows packaging path. Never present that ZIP as a
build containing the current implementation.

**Plugin trust boundary (release blocker):** installed ZIP discovery currently
accepts a package with no `index.json` hash record, because it compares hashes
only when a recorded hash exists. Installed packages are merged after built-ins,
so a third-party ZIP can also reuse a protected built-in ID and shadow its
catalog/code path. Installation executes `plugin.py` before the package has
completed registry-level category/type/OS cross-validation. Required follow-up:
require an indexed hash for installed ZIPs, reject collisions with protected IDs,
complete structural validation before executing plugin code, and make the UI
explicit that installation grants native-code-equivalent privileges. The remote
index's same-origin SHA-256 is corruption detection, not publisher authenticity;
signatures/trusted publisher keys remain deferred.

**Download Automator threat model (release blocker before general availability):**
the generic HTTP adapter follows arbitrary HTTP(S) URLs and redirects without
blocking loopback, private, link-local, or other local destinations. Add URL and
DNS/IP validation at every redirect hop, HTTPS-by-default policy, redirect limits,
domain-scoped cookie-jar handling (never a manually forwarded cross-host Cookie
header), response-size/free-space limits, collision-safe filenames, cancellation,
and an explicit final confirmation before overwriting. It also lacks checksum/
signature verification, Content-Disposition filename support, retry/backoff, and
completed-download quarantine/scanning integration. `auto_extract` is declared in
the schema but unused and must not appear functional; extraction must remain off
until safe archive containment and bomb/size limits exist.

**Archive extraction:** `pythia/core/runners.py`, the SteamCMD plugin, inherited
Libretro/update extraction, and generic extraction helpers contain raw ZIP
`extractall()` paths without an explicit destination-containment audit. Tar paths
using Python's `filter="data"` are better constrained. Centralize safe extraction
before enabling any automatic post-download extraction.

**Credentials/privacy:** host and F95 cookies are intentionally stored in the
synced, unencrypted credentials directory. Premium download sessions materially
raise the sensitivity of that accepted tradeoff. Add per-account/per-host clear
and expiry controls, cookie attributes/domain/path preservation, prominent sync
disclosure, and a local-only credential option before calling premium login
production-ready. The unnamed Qt WebEngine profiles are off-the-record (good),
but both login/resolver webviews still need navigation/request allowlists and
clear origin indicators.

**Upstream F95 scope correction:** current F95Checker supports update metadata,
download links, F95zone Donor DDL (including file size/SHA-1 verification and
resumable transfer), and RPDL torrents. It explicitly does not manage game folders
or automatically install/update games. The present Pythia port covers cached
update checking, login, and masked-link resolution, but not donor DDL file listing,
DDL session/link acquisition, verified transfer, RPDL account/search/torrents,
alerts/inbox, bookmark/watched-thread imports, or the browser-extension bridge.
Those remain real porting work. The generic Download Automator is a new Pythia
capability, not functionality inherited by claiming a full F95Checker port.

**Windows artifact hardening/size:** `pythia.exe` is unsigned. Microsoft
SmartScreen therefore cannot carry publisher reputation between releases. The
onedir payload is about 723 MB unpacked; roughly 665 MB and 3,700 files are
PySide6 because the spec uses `collect_all("PySide6")`, including development
applications such as Designer, Assistant, Linguist, `qmlls`, and `qmlformat`.
Replace this with a tested allowlist of the actually imported Qt modules,
WebEngine resources/locales, and required platform/image plugins. Add release
signing, checksums, an SBOM/third-party notices, and a reproducible artifact
manifest. The PyInstaller missing-module report also shows broad unused Qt and
setuptools import surface caused by `collect_all`; it is not evidence that every
reported optional module must be installed.

**Remaining decorative UI discovered during trace:** the handheld capabilities
screen still renders hard-coded sample providers/readiness, the Tools modal shows
hard-coded versions and inactive buttons, Report issue/Documentation only say
they “would” open a browser, and source-based import remains an honest stub.
These must be backed by registry/tool metadata and real actions or clearly marked
unavailable before release.

---

## Part 1: Where we actually are (verified 2026-08-05)

### Foundation — real and working
- **Branch**: `lutris-core`, built by direct file-copy import from a real Lutris clone
  (`G:\Support\GameManagement\RenPyPatch\lutris`, stock upstream), not a git-history
  merge, not the earlier `lutris-base`/`bullshit-codebase-that-is-trash` fake.
- **Rebrand**: lutris→pythia sweep done, except real external services (`lutris.net`
  API calls in `services/lutris.py` etc. keep their real name — they denote an external
  data source, not our own identity).
- **Licensing**: `LICENSE`/`pyproject.toml` currently say GPL-3.0 (inherited directly
  from importing Lutris's own GPLv3 code). Pythia's own intent was always to be LGPL
  (not a decision tied to F95Checker specifically) — but LGPL can't legally be the
  license of a work that directly incorporates GPLv3 Lutris code as extensively as
  `lutris-core` now does; the combined work has to be GPLv3. `[OPEN — needs a real
  decision]`: either accept GPL-3.0 for the whole project (simplest, matches what's
  already checked in), or split cleanly into an LGPL "core" and a GPLv3
  "Lutris-derived" component with a real module boundary — not yet decided or acted on.
- **Cascading config system is real** (`pythia/config.py`'s `PythiaConfig`, confirmed
  present in current code — a near-verbatim port of Lutris's own `LutrisConfig`, not
  just claimed in the invalid old Milestone 6). Three levels, highest wins:
  `game → runner → system`, each a separate YAML file, with both a cascaded
  (merged, for reading effective values) and raw (own-level only, for editing/saving)
  view of each section — real, working, proven design. `pythia/core/instance_marker.py`
  and `pythia/core/runners.py` are the only other `pythia/core/` modules; there is no
  `pythia/core/config.py` (the old invalid plan's claimed location) — it lives at
  `pythia/config.py` instead.
- **Variant identity is more solid than previously thought**: `onboarding.py`'s
  `_merge_version()` mints a real, stable, randomly-generated 12-hex-char `id` per
  variant (`new_game_id()[:12]`, same collision-safe generator as `game_id`) — this
  was wrongly listed as a fully open gap in an earlier pass of this doc. Confirmed
  live in `get_games()`'s output (`variant_id` field). The real, narrower remaining
  gap: matching for *updates* is still done by on-disk `path` alone, so a moved/
  renamed install still mints a fresh variant (and a fresh id) instead of being
  recognized as the same install that moved — the id itself is stable across
  rescans of an unchanged path, just not across a move.
- **GTK/PyGObject fully removed**: lazy-imported where still structurally needed,
  GTK-independent pieces extracted to `pythia/util/` (`app_singleton.py`,
  `notifier.py`, `xdg.py`, `jobs.py`, `image_utils.py`, `media_loader.py`,
  `progress_info.py`, `log_buffer.py`), `GLib` scheduling replaced by
  `threading.Timer` (`pythia/util/jobs.py`).
- **`pythia/monitored_command.py`**: real thread-based port (blocking `wait()` +
  blocking stdout-read loop) off GLib child-watch/fcntl; real Windows `fallback_cwd`
  bug (was hardcoded `/tmp`) found and fixed.
- **Database**: `games` + `variants` two-table schema (game-level identity vs.
  per-install rows), `pythia/database/games.py` rewritten to keep Lutris's exact
  function signatures but write through `datadir/*.json` (authoritative,
  Syncthing-synced) + `cache.db` (rebuildable SQL index). `save_entry_and_sync_cache()`
  is the single shared write path used by onboarding, scanning, *and* reconciliation —
  no parallel write paths. Verified live: real scan of the user's actual library
  (`G:/games/adult/renpy`) onboarded 33 real games with zero errors; reconciliation
  found and merged a real duplicate (BeingADik/BeingADIK).
- **Game ID minting is collision-safe** (`pythia/identity.py`'s `new_game_id()`):
  a random 16-hex-char slice, not a per-system counter or a reused platform ID — this
  was a real gap in the pre-`lutris-core` design, fixed 2026-08-04, confirmed still
  correct in current code just now (re-checked directly, not assumed).
- **Naming-heuristic fixes from real-library testing carried forward and confirmed
  present**: `_is_generic_part_leaf()`/`_find_meaningful_ancestor_name()` (recovers
  the real title from nested-subfolder installs like `BeingADik\Chap3+\10.0-sancho`,
  which used to lose their name to the leaf folder alone) and `_extract_version_only()`
  (underscore-separated versions like `love_of_magic_book1_v1_3_3_win64`) are all
  still in `pythia/onboarding.py`, confirmed just now.
- **Debug-mode multi-system sync simulation**: `pythia/identity.py`'s
  `PYTHIA_DATADIR_OVERRIDE` env var (separate from `PYTHIA_DATA_DIR`) lets two local
  instances with different system UUIDs share one datadir folder, simulating real
  cross-device sync without needing a second machine — confirmed still present. Real,
  useful tool for verifying Step 0-era and future multi-system claims.
- **Onboarding is cross-system-aware** (`pythia/onboarding.py`'s `find_candidates()`):
  external-ID and fuzzy-name matching search every known system's entries
  (`datadir.list_all_entries()`), not just this system's own — so if another synced
  system already tracks a game, this system converges on its existing `game_id`
  instead of minting a duplicate. Also a real, previously-flagged gap, fixed
  2026-08-03, confirmed still correct in current code just now. Path matching stays
  own-system-only on purpose (a path match from another system's file would be
  coincidence, not signal).
- **Plugin architecture**: 24 real ported plugins across 8 categories (12 engine
  detectors, 6 library plugins, 2 downloaders, 1 patcher, 1 source, `direct` manager
  deliberately not ported since Lutris's own `linux` runner covers direct-launch).
  Registry supports built-in + pip entry-point + drop-in-folder discovery. Standing
  design posture: Pythia is meant to be extended by people other than the original
  user, so a broken third-party plugin must never take the whole app down (every
  discovery path wraps loads in try/except and skips failures per-plugin), enablement
  is an allowlist not a blocklist (empty means "everything discovered is enabled"),
  and multiple plugins per category is the normal case, not an edge case.
- **F95 live update-checking is real and packaged**:
  `pythia/plugin_sources/library/f95/f95_update_check.py` contains the substantial
  F95Checker-derived cache-API implementation and ships inside
  `pythia/plugins/builtin/pythia.library.f95.zip`. Library/watchlist import and the
  downloader facet share that one plugin identity. Direct acquisition remains
  link-metadata/browser based; masked/CAPTCHA resolution is intentionally interactive.
- **Cross-platform metadata pulling is real but unexposed**: `platform_support()`
  (what platforms a storefront listing claims to support) and `list_dlc()` are real,
  working implementations in `pythia/plugins/library/steam.py` and
  `.../gog.py` (confirmed present just now) — Steam's is backed by its public,
  unauthenticated `appdetails` API. Neither is wired into any CLI command or UI, and
  neither is cached — a live network call per lookup shouldn't get bolted onto the
  Library page without caching/offline handling first (see Step 7).
- **Cover art is not built** (confirmed, not `[UNVERIFIED]`): grepped
  `pythia/plugins/` for `get_cover_art`, no matches. The web mockup's cover-art-
  forward design language currently has nothing behind it but monogram badges (see
  Step 5).
- **UI**: three real front ends selected by `pythia.ui.mode.resolve_mode()`
  (explicit hint > saved preference > screen-size heuristic > desktop default; `web`
  is explicit-hint-only, never auto-selected):
  - `pythia/ui/desktop/app.py` — QtWidgets desktop shell. **Currently a genuine
    minimal placeholder** (rebuilt from scratch after the old one was explicitly ripped
    out — it had drifted stale across many iterations and kept getting re-tweaked
    instead of replaced). This is meant to be the eventually-shipped default, but is
    not built out yet.
  - `pythia/ui/bigpicture/` — QML, imports cleanly, **not deeply exercised**
    `[UNVERIFIED]`.
  - `pythia/ui/webview/app.py` + `pythia/ui/desktop/web/index.html` — the real
    fast-iteration design surface ("UI-next"), QtWebEngine-hosted. This is where
    actual UI work happens before anything gets built into the Qt desktop shell for
    real.
  - `pythia/ui/backend.py` — the one `Backend(QObject)` bridging all three front ends
    (Signals/Slots via QWebChannel for the web UI, direct calls from Qt/QML).
  - `pythia/ui/singleton.py` + `--relaunch` CLI flag: kills a tracked running instance
    via PID file, waits for real exit, then starts fresh — lets UI iteration relaunch
    without a manual kill each time.
- **Headless CLI** (`pythia/app.py`): argparse entry point covering list/export/
  install/exec/URI-form (`pythia:rungame/rungameid/install`). **Real gap**: no
  `ScriptInterpreter`-driven installs yet (`ScriptInterpreter` still subclasses
  `GObject.Object` — not ported), and the *old* `pythia/cli.py`'s scan/onboard/list/
  merge commands were never carried into this new entry point.

### Web UI — real status, page by page
- **Library**: real, live, backed by `Backend.games` (`gamesChanged` signal, no
  polling).
- **Plugins**: real, live (`get_all_plugins()`, `set_plugin_enabled()`).
- **Systems & sync**: real — both the system cards and the "Cross-system status" table
  are built from real per-game `system_uuid` data already flowing through
  `refreshGames()`.
- **Reconciliation**: real — wired to `refresh_merge_candidates()`/
  `mergeCandidatesReady`/`merge_games()`, with working Merge/Skip actions. This is the
  real UI surface for a gap identified well before `lutris-core` existed: automatic
  scanning only ever auto-links by exact path/ID match, never by name similarity (by
  original design — never silently guess a link), so same-titled installs onboarded
  separately used to just sit as permanently separate, unlinked games with no way to
  fix it. Reconciliation's fuzzy `canonical_name` matching is that fix.
- **Downloads & intake**: `[BUG — open]` still 100% static dummy rows
  (`renderDownloads()` has a hardcoded 3-item array).
- **Activity & jobs**: `[BUG — open]` still 100% static dummy data (a hardcoded
  `jobs` array; the event-log timeline below it is also hand-written).
- **"Needs attention" nav / library summary badge**: the filter itself now means
  "status !== ok" instead of the never-true literal `'pending'`, but
  **`[BUG — open]`**: after a `--relaunch`, the console/log showed no scan activity
  and the game list rendered empty, while the sidebar's "9 items need review" notice
  banner (`pythia/ui/desktop/web/index.html`, the `.notice.pending` block near the top
  of `#view-library`) is **hardcoded static HTML**, not driven by `librarySummary`'s
  real computed count — so it keeps showing "9 items need review" regardless of what's
  actually in the database, including when the database is empty. Second, separate bug
  from the empty list itself.

### Cross-platform / Windows
- Real, honest platform-gating fixes: Windows `AMD64` architecture recognition
  (`pythia/util/linux.py`'s `get_arch()`), Linux-only system-tool/library probing
  skipped with one clean log line instead of a warning per tool, `/sys/class/drm`
  absence downgraded from error to debug, the DBus-unavailable message split so it
  only reads as an install error on actual Linux, Vulkan loader name made
  platform-aware (`vulkan-1.dll`/`libvulkan.so.1`/`libvulkan.1.dylib`).
- **Not near-term scope, even though Windows is first-class in principle**: real
  Windows-native GPU enumeration, display management, etc. are future work — being a
  first-class target doesn't mean building every native subsystem out immediately.

---

## Part 1.5: Four-pass UI research + fixes (2026-08-06)

Four background research/critique passes ran against the real current `index.html`
and the real Lutris source, plus a real design-source lookup for the Configure
dialog. All four reports are real, sourced, and saved to disk — not summarized from
memory here, read them directly for full detail:

- `C:\Users\jon\Downloads\pythia-research\pass6\deep-research-report-2.md` — UI
  *patterns* for not-yet-built screens (instances, installer jobs, compatibility
  management, session monitoring), plus Qt implementation guidance and large-library
  search/filter UX. **Real, directly-portable finds**: Lutris's actual
  `gui/download_queue.py` (name-keyed job-coalescing — reusable pattern for Jobs &
  activity), `gui/dialogs/runner_install.py` (a complete working answer for
  Compatibility & Runtimes — fetch+merge+sort+state-derived button+usage-count
  drill-down), `database/saved_searches.py` (a real, simple, already-portable
  "Saved Searches" feature Pythia doesn't have), `gui/widgets/game_bar.py`
  (validates the Play/Stop action-bar design already built). Confirmed gap: Lutris's
  own `util/process.py` has zero Windows equivalent — process/session monitoring on
  Windows is new work, not a port, named explicitly for future effort estimation.
- `C:\Users\jon\Downloads\pythia-research\pass6\lutris-accessibility-requirements-audit.md`
  — full Lutris feature audit (the "expanded" baseline), accessibility deep-dive,
  general launcher requirements. **Real gaps found with zero PLAN.md coverage
  before this**: category/tag management (real many-to-many schema in Lutris),
  saved searches (see above), the uninstall dialog's independent delete-files/
  remove-from-library choice, the move-game dialog, controller/joypad detection,
  and — the largest single gap — **26 emulator/ROM-console runners** (RPCS3, Yuzu,
  Xenia, etc.) that this plan's Step 1 compatibility-system rewrite never mentioned
  despite Pythia's own stated NAS/ROM ambitions. Also: a real Windows accessibility
  risk (MinGW Qt builds disable IA2, degrading NVDA screen-reader support vs. MSVC
  builds — unverified which this project's build will use), and a citation
  correction — `battleye`/`eac` fields actually live in `runners/wine.py`, not
  `sysoptions.py` as stated earlier in this doc. Ends with a 10-item dependency-
  ordered gap list; controller support and missing library-organization primitives
  (categories/tags/bulk-ops) rank highest since the Handheld shell already commits
  to needing them.
- Two more focused critiques of the *current* `index.html` (not future screens) —
  findings acted on immediately, real fixes shipped this pass:
  - **Wasted-space diagnosis**: root cause was containers sized for a worst-case
    mockup screenshot rather than this library's actual (usually short) content.
    Fixed: dropped `.library-layout`'s forced `min-height:690px` (was stretching a
    ~400px-content detail panel to 690px on every game); shrunk grid-mode's cover-art
    placeholder from `aspect-ratio:3/4` to `1:1` (was a full decorative rectangle
    with zero real art behind it); narrowed the list column from `43%` to
    `minmax(280px,320px)` (was sized for long names that are usually short); gave
    list rows a real "Ready" label instead of blank space for the healthy-majority
    case; removed the Overview tab's redundant "Status" stat card (already shown in
    the header's tag row).
  - **Holistic craft critique**: found a real CSS bug — `.eyebrow{display:none}`
    (an unscoped rule from a later editing pass) was silently killing the detail
    panel's source/engine label from an earlier pass; fixed by scoping it to
    `.page-head .eyebrow`. Also found one stray full-color emoji (🎮, the Handheld
    toggle) breaking an otherwise-consistent monochrome icon language; replaced with
    a matching glyph. **Deferred, real but lower-priority** (radius values 4-12px
    used interchangeably for the same role, font-weights 650-900 with no consistent
    meaning, shadow/elevation applied by which editing pass touched a component
    rather than by meaning, per-component grid-column widths drifting 34-46px for
    the same "icon column" role, clickable-vs-static visual language not
    consistently differentiated) — real technical debt, written down, not fixed
    this pass since it's pure polish rather than a functional/structural bug.

## Part 1.6: Second research round — original-vision architecture + navigation (2026-08-06)

Three more real, sourced passes. Full reports in `pythia-research/pass6/`.

- `cross-system-sync-and-transfer.md` — **the original founding-vision items directly**
  (inter-device config sync, on-demand instance transfer with selectable mods).
  Confirmed a real, previously-flagged architecture gap: `PythiaConfig`'s files live
  in `settings.CONFIG_DIR`, genuinely separate from the synced `datadir/`. Real
  precedent (chezmoi, yadm, Syncthing's ignore patterns) all resolve per-machine-vs-
  shared config from one shared *definition*, never by syncing raw values — GOG
  Galaxy (blanket sync) and Playnite (no sync) are the real negative cases.
  **Recommendation**: a three-value per-option `sync` flag (`local`/`synced`/
  `synced-as-hint`) on each config option, routed through the already-proven
  `datadir.save_entry()` path — no new transport needed. For instance transfer: no
  real tool (MO2, Vortex, Steam depots, rsync, Syncthing) treats "mod selection" as
  a semantic unit — the already-real `variant_id` + per-variant `mods: []` should
  stay the selection unit end-to-end. **Concrete clarification from the project
  owner**: "selectable mods" means the actual packed mod archive (e.g. the real zip
  file for Being a DIK's "scrappy" mod), not a metadata reference — the file itself
  is the transfer unit, reapplicable manually or automatically on the receiving
  system. A "Send to another system" screen is buildable directly on today's shipped
  schema — no Milestone 8 §A schema evolution required first.

  **Architecture correction from the project owner (2026-08-07): synchronization
  and transfer are core, never plugins.** Pythia's durable identity, onboarding,
  cross-system matching, reconciliation, availability model, and receiving-side
  import all rely on synchronization being present and semantically consistent.
  Consequently there will be no `TransferPlugin`, optional transfer capability, or
  plugin-controlled transfer state. The transfer manifest, transaction state
  machine, peer/system authorization, staging, progress, verification, conflict
  handling, destination import, cleanup, and recovery belong under Pythia core and
  must remain available even when every third-party plugin is disabled or broken.
  Syncthing, shared-folder, and possible rclone integrations are **internal core
  transport backends** selected by core policy/configuration. They may wrap separate
  upstream executables, but they are shipped and tested as part of Pythia's supported
  core behavior and cannot redefine game/variant identity or bypass onboarding.
- `install-patch-pipeline.md` — compatibility patching, generalized from the
  original ARM-patcher origin. Read the real, already-imported
  `pythia/installer/commands.py` (1033 lines, Lutris's real 20+ directives,
  including an already-working DLC-diff-onto-existing-install path in
  `gogdl_setup()`) and confirmed the one real blocker to porting it at all:
  `interpreter.py` imports `gi.repository.GObject` for one custom signal — small,
  mechanical fix, same pattern as the already-completed GLib→`threading.Timer` swap.
  **Recommendation, extended by direct owner clarification**: patches are not
  static prepackaged file diffs — they're general installer scripts scoped to an
  *existing* variant, meaning the same real directives (`execute`/`task`/fetch/
  conditional file replacement) support genuinely **programmatic** patches: check
  for an updated translation, fetch it if newer, replace files, re-run the
  translator tool — all as one user-triggered script, not a one-shot static patch.
  This also covers reapplying a compatibility patch (e.g. a no-DVD fix) after an
  official update from physical media overwrites it — user-initiated, not automatic,
  matching the project's content-neutral standing principle. Schema: an additive
  `applied_patches` record keyed by the real stable `variant_id` (same JSON-column
  pattern as `mods`), plus `apply_mode: "new_variant" | "patch_in_place"` on
  `available_updates[]` entries. No new script format — patch scripts are installer
  scripts targeting an existing variant instead of creating a new one.
- `a029d5f79b6693b1c`'s navigation-structure report (no separate file — delivered
  inline) — real, concrete finding: the sidebar mixed three different *kinds* of
  nav items (destinations, filters-on-the-current-page, a live-state indicator)
  with identical visual treatment, so nothing signaled which was which. Found a
  literal dead menu item as direct evidence: "Duplicates / merge review" sat next
  to the real "Reconciliation queue" item and was a complete no-op — someone had
  already tried renaming it in-place and never finished. **Implemented for real**:
  Reconciliation moved into the Library group and relabeled "Possible duplicates"
  (same "filtered actionable queue" shape as Needs setup/Updates, previously
  separated from them for no real reason); Sources given a real collapsible
  facet-toggle so it visually reads as "narrows the list above" rather than "go
  here," matching Lutris's own real sidebar precedent (`gui/widgets/sidebar.py`);
  the junk-drawer "Manage" group split into "Activity" (Jobs & activity — time-
  sensitive) and "System" (Compatibility & runtimes, Systems & sync — infrequent
  config); "Extend" renamed "Extensions" with Settings dropped from the sidebar
  entirely (it already has two real homes: the topbar gear icon and Edit ▸
  Preferences — a third, mislabeled one wasn't helping); the dead menu item removed.
  **Not yet done**: the report also confirmed the three-tier config cascade is
  currently worse than PLAN.md describes — the game-level Configure tier doesn't
  exist in the UI at all yet, only instance-level, so today it's really four
  scattered config surfaces, not three clean tiers (real work, see Step 5 below).
  Settings sub-nav content-completeness findings (Interface/Advanced/Sync panes
  genuinely thin relative to a comparable real settings surface; Managers/
  Downloaders missing rows for real backlog-named tools) — real, not acted on this
  pass, since the 13-pane split itself is confirmed deliberate and correct.
- **Two real bugs caught by the user after the restructure landed**: clicking the
  Library sidebar filters (Needs setup/Updates) never highlighted the active row —
  root cause was `navigate()` only ever toggling `.active` by `data-nav`, never by
  `data-filter-nav`, *and* early-returning entirely when already viewing Library (the
  common case), skipping the sync logic altogether. Fixed with a real `syncNavActive()`
  helper called from `navigate()` (including its early-return path) and on initial
  `setup()`. Also added a real **Filters button** in the global search bar (a
  popover with Status/Source/Engine selects) so filtering doesn't require the
  sidebar — reuses the exact same `state.filterStatus`/`state.source`/`state.engine`
  and `populateFilterOptions()` real data path as the existing library-panel filters,
  kept in sync both directions rather than being a second, separate filter system.
- **Game-level Configure tier closed** (was the "not yet done" gap noted above):
  `openConfigureDialog(g, scope)` now takes a real `scope` param (`'game'` or
  `'instance'`, default `'instance'`), with differentiated title, footer copy,
  working-directory row, and Instance-tab content per scope — game scope shows a
  list of every instance with its own "Configure…" entry point instead of a single
  path field. Reached from the "•••" Game actions modal's new "Configure game…"
  button. Three real tiers now exist in the UI, not four scattered surfaces.
- **Saved Searches** (ported the *concept*, not the storage, from Lutris's real
  `database/saved_searches.py` — Lutris persists to a dedicated `pga.db` table,
  but Pythia's `cache.db` equivalent is fully rebuilt from `datadir/*.json` on
  every startup, so that storage would be silently wiped; used the existing
  `identity.get_setting`/`set_setting("saved_searches", ...)` durable-state
  pattern instead, same shape as `enabled_plugins`/`ui_mode`). Real backend Slots
  in `pythia/ui/backend.py`: `get_saved_searches()`, `save_search(name, search)`,
  `remove_saved_search(name)`. Frontend: save/apply/remove rows inside the new
  Filters popover. Hit and fixed a real `ReferenceError` from `refreshSavedSearches`
  being accidentally scoped inside `setup()` while being called from the top-level
  QWebChannel bootstrap — the Qt app bridges real JS console errors into its own
  process stderr as `[web console] <file>:<line>: <error>`, which is how this was
  caught; moved the function to top-level scope and reverified via relaunch with a
  clean stderr log (no `[web console]` errors). Feature is now real and working
  end-to-end.
- **Game-detail redundancy fix, grounded in real Lutris source** (2026-08-06) — user
  flagged a live screenshot as "terrible": the same ~5 facts about the selected game
  (name, version, status, source/engine, system count) were independently re-derived
  and printed in 4-5 separate places (list row, detail header, Overview stat cards,
  bottom action bar), ~300px of dead space on the Overview tab, and game actions
  split across two disconnected toolbars (detail-header "Rescan install"/"•••" vs.
  the bottom action bar's Instances/Configure). A research agent traced the exact
  duplication in `index.html`'s `renderLibrary()`/`renderDetail()`/`updateActionBar()`
  against Lutris's real `game_bar.py`/`game_actions.py`, and separately (per a direct
  follow-up: "there's lots of information presented, but no configuration immediately
  available") found that Lutris doesn't split actions across two toolbars at all — it
  hangs one contextual action popover directly off the Play button
  (`get_popover_box`, `game_bar.py` lines 97-138/239), filtered to what's currently
  relevant. **Implemented**: `.detail-header` deleted entirely (name/eyebrow/status
  tag were 100% redundant with the action bar) along with its now-dead CSS, plus the
  already-dead `.detail-hero`/`.cover-large`/`.detail-copy` rules the agent surfaced
  as leftover from an earlier design pass; Overview's two duplicate stat cards
  ("Latest installed"/"Systems") replaced with real, previously-unused data (a "Last
  updated" card wired to the real `entry.updated_at` timestamp — this was also a
  live bug fix, `adaptGame()` had been hardcoding `updated:0` instead of reading the
  real field — and a "Configuration" card with an inline `Configure…` button so
  configuration is reachable from the top of Overview, not just the bottom bar);
  inline ✎ edit affordance extended from Canonical name to the Engine row; the bottom
  action bar's separate Instances/Configure buttons and the header's Rescan/•••
  modal consolidated into one `▾ More` popover (reuses the existing `detailAction
  ('more', g)` modal, now also covering Instances and Rescan) anchored next to Play,
  matching Lutris's one-popover-off-Play model, with Configure kept as its own
  always-visible primary-adjacent button since it's the single most common action.
- **Fixed real "purely for display" dead buttons found while doing the above**: the
  Plugins page's "Details" button had zero handler (now opens a real modal with the
  plugin's actual fields); the "Remove plugin" confirmation's Remove button had zero
  `onConfirm` callback — clicking it silently closed the dialog and did nothing, no
  backend removal Slot exists in `registry.py` to call anyway — now wired to an
  honest toast explaining removal isn't implemented, matching the codebase's existing
  stub pattern (`bulkAction()`, the Install-plugin modal) instead of a dialog that
  pretends to act. Same treatment applied to the consolidated action menu's "Edit
  metadata" (now opens a real rename modal, honestly toasts that persistence isn't
  wired to a backend Slot yet) and "Review removal" (real confirm dialog, honest
  "not built yet" toast — no game-removal Slot exists in `backend.py` either) buttons,
  which previously rendered with no `onclick` at all.
- **Settings' capability panes made real, then a real plugin-loader rewrite**
  (2026-08-06). First pass: the "PLUGIN CAPABILITIES" panes (Sources/Downloaders/
  Metadata/Managers/Engine detection/Mods/Patchers) were 100% static HTML — every
  toggle hardcoded on, every status hardcoded "Connected"/"Ready" — so a plugin
  disabled on the real Plugins page had zero effect on what these panes claimed
  about it (user-reported: "pretty much everything in the settings menu is just
  for show"). Made them render from the same real `plugins` array, filtered by
  backend category. **User correction, twice-refined**: (1) the semantics were
  wrong — a capability-pane toggle must not be the same whole-plugin on/off as the
  Plugins page; a multi-category plugin (Steam = library+manager+metadata) can
  stay enabled overall while one specific capability is switched off from that
  capability's own pane. Added real per-`(plugin_id, category)` capability
  enablement (`registry.set_capability_enabled()`/`is_capability_enabled()`,
  distinct from the existing whole-plugin `enabled_plugins` allowlist), plus a
  `configurable`/`config_schema`-driven Configure button per row (greyed out
  when a plugin declares no `config_schema` — real today, since no built-in
  currently declares one). (2) Deeper correction: disabling a plugin globally
  must mean it's **not loaded at all**, not just hidden from a list — the old
  registry (`register_builtin()` self-registering an *instance* as an import-time
  side effect) instantiated every plugin unconditionally regardless of enabled
  state, so "disabled" was purely cosmetic. **Real fix, adapted directly from
  Lutris's actual runner loader** (`lutris/runners/__init__.py`'s `__all__` list
  of names + `import_runner()` returning a class, nothing instantiated until
  needed): `pythia/plugins/registry.py` rewritten so `BUILTIN_PLUGIN_MODULES`
  lists built-in plugin module paths the same way Lutris's `__all__` lists
  runner names; `register_builtin()` is gone entirely — built-ins are not a
  special path, indistinguishable from external (pip entry-point) or folder
  (drop-in manifest) plugins except for `registry.is_protected()` (can't be
  uninstalled, computed from the same module list, not a separate hardcoded id
  set). Metadata (id/display_name/version/author/categories/config_schema) reads
  straight off each plugin *class* via `issubclass()`, never instantiating;
  `get_plugin_instance()`/`for_category()` only ever construct a plugin that is
  both known and enabled. Verified for real (not just read): disabling Steam
  removes it from `for_category("library")`'s results and confirms it never
  enters the process's instance cache, while it still appears in the
  metadata-only listing so the Plugins page can offer it with an off toggle.
  Touched 22 built-in plugin files (mechanical: `register_builtin(X())` →
  `PLUGIN_CLASSES = [X]`) plus every category `__init__.py` that previously
  imported each plugin module directly just for the registration side effect
  (those imports are gone — `BUILTIN_PLUGIN_MODULES` is now the single source of
  truth for what exists).

## Part 2: What "full implementation" still requires — ordered

Items are ordered by real dependency, not just priority — each block generally needs
the one above it.

### Step 0 — Fix the two bugs just found (blocks trusting anything else) — `[DONE, 2026-08-06]`
1. `[BUG, FIXED]` Root-caused the empty-game-list-after-relaunch via a real Explore-agent
   trace of the actual write/read paths, not guesswork: the `settings` vs `identity`
   data-dir split named as "prime suspect" turned out to be architecturally irrelevant
   — `Backend.games` reads via `datadir.list_known_games()` directly, never touches
   `cache.db`/`get_games()` at all, and `DB_PATH` already resolves through
   `identity.app_data_dir()` regardless. The real bug: `datadir.list_all_entries()`
   silently swallowed `OSError`/`ValueError` per-file with no logging, and
   `load_entry`/`save_entry` used the platform-default text encoding instead of
   explicit UTF-8 — on Windows a game name outside the default codepage could raise a
   `UnicodeDecodeError` (a `ValueError` subclass) that got silently dropped, presenting
   as "empty library after relaunch" with zero error surfaced. Fixed in
   `pythia/datadir.py`: explicit `encoding="utf-8"` on both `read_text`/`write_text`,
   and a `logger.warning` on skip instead of a silent `continue`.
2. `[BUG, VERIFIED ALREADY FIXED]` The "N items need review" notice banner and the
   sidebar's "Needs attention" count were checked against `renderLibrary()`
   (`index.html`) — both are already real and dynamic (`allAttention`/`attention`
   computed live from `games`/`filteredGames()`, written into `#librarySummary`,
   `#attentionNoticeText`, `#allCount`/`#setupCount`/`#updatesCount` on every render).
   The static HTML text PLAN.md's earlier pass saw was only the pre-JS-render
   skeleton, immediately overwritten on load — no fix was needed here.

### Step 1 — Real game launching, default-on (the Lutris-replacement pivot)
`[NEW, 2026-08-05]` — elevated from the old "Step 6 backlog" framing because it's now
the default, headline behavior, not a someday extra. The standing "Pythia never
launches anything" boundary (repeatedly reinforced across Milestones 1-7) is reversed
for the default case.

**First real slice landed, 2026-08-06** — direct native launching, both Windows and
Linux (user: "remember we're cross-platform"), wired into the actual live UI
(`backend.py`/`index.html`), not the dormant GTK `game_actions.py`/`Game` code path
that turned out to belong to an inactive front end. New `pythia/launch.py`:
`find_primary_executable()` (a real, honest heuristic — top-level `*.exe` on Windows,
top-level `*.sh` or the executable-bit fallback on Linux, excluding known non-game
binaries like `unins000`/`python`; returns `None` rather than guessing wrong) plus
`launch_native()` (a detached `subprocess.Popen`, correct per-OS invocation).
**Genuine convenience over Lutris's own model**: Lutris requires the user to manually
pick the `exe` in a runner's config (`runners/linux.py`'s `game_options` — no
autodetection at all); Pythia's autodetect is a real, confirmed-useful addition, not
a shortcut around something Lutris already solved. A real, persisted mode switch
(`launch.get_launch_mode()`/`set_launch_mode()`, `identity.local.db`, same pattern as
everything else user-configurable) between `direct` (default — Pythia launches the
game itself) and `manager` (the old hands-off behavior, unchanged, still available
from a real Settings toggle in General: "When you press Play"). `backend.py` gained
`get_launch_mode`/`set_launch_mode`/`launch_game_direct` Slots; `playGame()` and the
action bar's Play-button label both branch on the real mode instead of always
assuming manager hand-off. The existing `ManagerPlugin` hand-off path
(`find_manager_for_game`/`open_in_manager`) is untouched, exactly as this section
already called for.

**Still open** (this was the *first* slice, not the whole step): the Wine/Proton-
adapted-from-Lutris execution path below (for anything that isn't a native
Windows/Linux install), WSL-backed execution, and the version-management layer
(Proton-GE builds, Box64/FEX-Emu) are all still real, not-started work.

**The scanner and "Add to Pythia" flow were also dummies, fixed the same day**
(2026-08-06, user: "the scanner, game adder, and all that are apparently dummies").
Real cause: `backend.py` already had substantial, working onboarding/scanning
infrastructure (`start_onboard`/`start_scan`/`resolveDecision` Slots,
`onboardComplete`/`scanComplete`/`decisionNeeded` Signals) that zero UI code called
— every "Add to Pythia"/"Add game folder"/"Scan" modal just showed
`showModal(...)` with no `onConfirm` callback, so confirming fell through to the
generic "Prototype action completed without changing real files" toast, which is
what tipped this off. Fixed: a real native folder-picker Slot (`pick_folder()`,
`QFileDialog.getExistingDirectory`) plus `openScanModal()`/`openAnalyzeFolderModal()`
wired to `start_scan()`/`start_onboard()`, and a real `openDecisionModal()` for
`decisionNeeded` (an ambiguous match against an existing tracked game — lets the
user link to a suggested match or confirm "new game", calling `resolveDecision()`
either way). Real signal listeners added for `scanComplete`/`onboardComplete` too.
Swept the rest of the file for the same missing-`onConfirm` pattern and gave every
remaining one an explicit, honest "not built yet" toast naming the specific gap
(watch-folder, correct-engine, source-based import, canonical-name rename) instead
of relying on the generic fallback text, since that generic text was itself
confusing about *what* hadn't happened.

**Real regression, found immediately by the user actually using the fix, same day**:
the plugin-registry rewrite above broke `scanning.py` (`library.generic_plugin` no
longer existed -- it was an eager-import side effect that `register_builtin()`'s
removal took with it) and `backend.py`'s F95 import path (same pattern). Both fixed
by looking the plugin up through the registry properly (`get_plugin_instance(id)`,
or by id inside `enabled_plugins()`) instead of a module attribute. The crash was
**completely invisible to the user** -- it only printed a traceback to a terminal
they weren't watching, no signal or toast ever fired. Root cause, fixed for real:
every background worker in `backend.py` (8 of them) called `threading.Thread(...)`
directly with no error handling. Added `Backend._run_worker()` (wraps the target,
catches any exception, emits a new `workerError` signal) and routed all 8 through
it.

**Activity & jobs and the Event log are now real**, not static (user: "scans should
be jobs, appearing in the activity and jobs panel, and the event log" / "that should
log everything done. Games added, removed, updated..."). A thin, honest client-side
view over real signals — `logLine`/`scanComplete`/`onboardComplete`/`workerError`/
`mergeComplete`/`accountSyncComplete` — since `backend.py` has no real job-Property
of its own yet (still Step 2 work). `refreshGames()` diffs the game-id set against
its previous state on every real `gamesChanged` to log real additions/removals.

**Real Python `logging` output now reaches the UI too** (user: "we need console
logging back" — most of the app just wasn't visible to someone not running from a
terminal). `pythia.util.log.get_log_contents()` already existed for exactly this
("see the Qt UI's own log window once it exists" — it never did, for the web UI) but
was never called from anywhere. New `Backend._ui_log_handler` attaches to the root
logger and re-emits every record (from *any* module's own `logging.getLogger
(__name__)`, not just backend.py's own `logLine.emit()` calls) as a real `logLine`
signal; the frontend backfills the Event log from `get_log_contents()` on startup,
then streams live from there.

**Still open, actively being investigated with the user (2026-08-06)**: a real scan
reported "0 installs processed" despite a game_id appearing to be found. Root cause
not yet confirmed — `onboard()`'s success path unambiguously returns `status: "ok"`
server-side (traced through `onboarding.py`), so either the `results` payload isn't
crossing the QWebChannel boundary intact for this shape, or something else is
mismatched between what `_scan_worker`'s own per-item log line saw and what
`scanComplete`'s emitted list contains. Added a temporary raw-payload diagnostic
line to the Event log (`[diagnostic] scanComplete raw payload: ...`) to pin this
down from the user's next real scan instead of guessing further blind.

**Adapt, don't reinvent — same reuse discipline already applied to the plugin system.**
Lutris's real runner code (`lutris-core`'s imported `runners/`) is genuinely high-
quality and already sitting in the repo; the job is repurposing it, the same way
Lutris's plugin-shaped patterns were adapted (not rewritten) for Pythia's own plugin
architecture. This is **not** a from-scratch build.

**But it does need to become a real cross-platform compatibility system, not stay a
straight port of Lutris's Linux-only scope.** Lutris's runner code assumes Linux
running Wine/Proton for Windows games; Pythia's actual users run Windows natively too,
where the picture is different: Windows-native execution needs no compatibility layer
at all, and **WSL** is a real path for running Linux-only tools/games from Windows —
neither case exists in Lutris's own runner model. Concretely: adapt Lutris's runner
code as the **Linux-side implementation** (and keep using it as the reference/source
for Proton-GE builds etc. there), while adding real Windows-native and WSL-backed
paths as siblings under the same abstraction — one compatibility layer, several real
backends, not a Linux tool wearing a Windows costume. Worth naming this something
other than "runners" in the code/UI, since that's Lutris's narrower Linux-Wine-specific
term for what's now a broader cross-platform concept (a **compatibility system**).

- **Settings toggle**: a real, persisted mode switch (`identity.get_setting`/
  `set_setting`, same pattern as everything else user-configurable) between
  **"Lutris replacement"** (default — Pythia owns execution and is what launches a
  game) and **"update and patch manager"** (the old hands-off behavior — Pythia tracks
  and hands off to an external `ManagerPlugin`, never launches directly). Every real
  launch path checks this setting; nothing hardcodes one behavior.
- **Wire up the adapted Linux/Wine-Proton execution path** for real launches in the
  default mode first (it's the one Lutris already solved) — this is distinct from,
  and comes before, the deeper version-management work below (installing/updating
  Proton-GE builds etc.); the immediate need is "press Play, the game runs," using
  whatever runner config already exists on a tracked variant.
- **Version management** (Wine/Proton/DXVK version management, emulator cores): keep
  "prefix" (one Wine/Proton environment), "prefix-data group" (a named, reusable set
  of prefix data separate from save sync), and "runtime configuration" as three
  distinct objects, not one conflated concept. Concrete lower-effort path already
  identified: `AUNaseef/protonup` (PyPI) already solves Proton-build management
  programmatically; use it directly or as a reference rather than designing from
  scratch. **Box64/Box86 and FEX-Emu** (x86→ARM64 userspace translation) are the
  direct sibling of this project's original ARM work (the Ren'Py ARM patcher this
  whole project grew out of) and belong in this same version-tracking model when ARM
  support is picked up again.
- The existing `ManagerPlugin` hand-off boundary (Part 1's Plugin architecture entry)
  is **not deleted** — it becomes what "update and patch manager" mode uses. Both
  modes share the same tracked-game data; only what happens when you press Play
  differs.

### Step 2 — Finish the web UI (Downloads & intake, Activity & jobs)
- **Activity & jobs**: needs a real jobs/task-tracking concept on the backend
  (`Backend` currently has no notion of a running "job" beyond ad hoc signals like
  `scanComplete`/`mergeComplete`) before the page can be anything but static. Concrete
  shape: a `jobs` list Property + `jobsChanged` signal on `Backend`, populated by
  wrapping the existing async operations (scan, onboard, merge, plugin sync) with a
  shared job-tracking helper, surfaced the same way `mergeCandidates` was wired up.
- **Downloads & intake**: depends on a real downloader/watcher concept actually
  running (F95Checker watcher, SteamCMD) — currently these are plugins that *can*
  download but nothing watches a folder and emits `downloadDetected` in practice yet.
  Needs: confirm whether any downloader plugin currently fires `downloadDetected` for
  real `[UNVERIFIED]`; if not, that's the real prerequisite, not just a UI wiring job.
  The real underlying gap is a **persistent local job/acquisition model**
  (noticed → verified → extracted → staged → committed), not just "a file appeared" —
  `pythia/plugins/downloader/f95checker.py` is currently only 35 lines, nowhere near
  this. For the F95Checker piece specifically, the intended integration order is:
  (1) a stable export/plugin API from F95Checker itself if one exists, (2) failing
  that, a version-checked, read-only SQLite adapter against a safe snapshot of its
  `db.sqlite3`, (3) F95Checker's existing limited RPC only for what it already
  supports (e.g. adding a thread URL) — never write to F95Checker's own database.
  A second real prerequisite: F95-style sources have no purchase gate, so "ownership"
  in the Steam/GOG sense is meaningless there — what's actually needed is a
  **watchlist** (which specific threads/titles the user told Pythia to track, the
  same shape F95Checker's own watched-list feature already is). This concept doesn't
  exist anywhere in the schema or `DownloaderPlugin` interface yet
  (`search()`/`list_versions()`/`get_download_info()`/`launch()`, none of which is
  "the user's chosen subset") — real, concrete, not started.

### Step 3 — Close the headless-CLI gap
- Port `pythia/cli.py`'s scan/onboard/list/merge commands into `pythia/app.py` so
  scripting doesn't require the Qt UI to exist at all — this was real, working
  functionality in the pre-lutris-core codebase and has no equivalent in the new
  headless entry point yet.
- Decide and implement the `ScriptInterpreter`-driven install path (currently still
  GTK-coupled, not ported) — needed for both the headless `install` command and any
  future installer UI.

### Step 4 — Verify the account/credential-dependent backend slots
- `sync_account`/`import_f95` (Backend slots) exist but haven't been exercised
  against real credentials/network `[UNVERIFIED]` — needs a real pass with actual
  account credentials before trusting the Accounts settings page.
- Confirm `pythia/ui/bigpicture/` QML actually renders and functions beyond "imports
  cleanly" `[UNVERIFIED]`.

### Step 5 — Build the real desktop (QtWidgets) UI, re-centered on launching
Re-scoped 2026-08-06 around the Step 1 pivot and the pass5 redesign research (see
Part 1). Still governed by the standing design rule: reuse the `pythia/ui/theme.py`
token set (colors/type), not any mockup's exact layout/DOM — the pass5 HTML is a
direction to map into native Qt widgets, not something to reproduce pixel-for-pixel.
This turns the current placeholder `MainWindow` into the real, shipped default UI
`pythia.ui.mode.resolve_mode()` already assumes it will become.

**Real progress in the web UI ("UI-next"), 2026-08-06** — validating the redesign
there first, per the standing web-first rule, before any Qt work starts:
- Sidebar restructured for real into Library (All games/Needs setup/Updates, all
  three backed by live `g.status` filtering) / Running (present, genuinely empty
  until Step 1 gives it real session data) / Sources (built live from `games`, not
  hardcoded) / Manage (Compatibility & runtimes, Jobs & activity, Systems & sync,
  Reconciliation).
- A real persistent bottom action bar (`#libraryActionBar`) replaces the old hero's
  primary action — shows the selected game's art/name/version, an instance picker
  built from real version/variant data, status. **Play is wired to real backend
  calls that already existed but were never actually called from the frontend**:
  `find_manager_for_game()` → confirm → `open_in_manager()` (both real
  `pythia/ui/backend.py` Slots). Previously `detailAction('manager', …)` was a pure
  toast, not a real call — this was a genuine, pre-existing wiring gap, now closed.
  Honest current limit: only reaches games with a configured external manager: direct
  Pythia-owned launching (the actual Step 1 work) doesn't exist yet, so an
  unconfigured game gets a real, specific message instead of a fake success toast.
- Toolbar "+ Add" renamed to "+ Install or add" with task-oriented copy (Import an
  installed game / Scan a folder or launcher / Search installers and sources /
  Review detected downloads), same real choices underneath — not yet the mockup's
  full 6-choice set, since some of those (search installers, install from script)
  have no real backend yet and weren't worth faking.
- **Configure dialog is real, and its fields are ported from Lutris's actual config
  system, not invented** (corrected 2026-08-06 — the first pass guessed at vague
  fields like "Performance profile"/"Save profile" that don't correspond to anything
  real; the fix was reading `lutris/sysoptions.py` and `runners/wine.py`'s real
  `game_options` directly, the same "adapt, don't invent" discipline as everywhere
  else in this project). Six tabs, opened per-instance from the action bar's Configure
  button, reusing the existing `.settings-layout`/`.form-row` components:
  - **Game options**: Executable/Arguments/Working directory — Lutris's real base
    `game_options` shape (`wine.py`'s `exe`/`args`/`working_dir`).
  - **Compatibility**: backend selection (a real Pythia addition — Lutris doesn't
    have this since a game's runner is fixed at creation, not runtime-switchable)
    plus, contextually, the real Wine/Proton fields from `wine.py`: Wine prefix,
    Wine version, architecture (win32/win64), DXVK, VKD3D, Esync, Fsync.
  - **System options**: real fields from `sysoptions.py`'s Display/CPU/Audio
    sections — GPU, FPS counter (MangoHud), restore resolution on exit, disable
    desktop effects, **Prevent sleep** (the real Lutris label — the first pass had
    this as "Disable screensaver," close but not the actual name), restrict CPU
    cores, Feral GameMode, PulseAudio latency.
  - **Integrations**: not a Lutris concept — kept as real, scoped Pythia additions
    (Cloud save sync — Lutris's own real system option, relabeled honestly as
    "through this system's own datadir"; Launcher projection — real PLAN.md
    backlog item, not invented on the spot).
  - **Instance**: genuinely Pythia-specific (Lutris has no instance concept) —
    unchanged from the first pass.
  - **Advanced**: real fields from `sysoptions.py`'s "Game execution" section —
    environment variables, locale, command prefix, pre-launch/post-exit scripts
    (kept from the first pass, these were already real), include/exclude
    processes for session monitoring.
  Populated with real data where it exists (working directory from the variant's
  real `path`, instance name from real version/language data); fields with no
  backend yet are genuinely empty/default controls, not "not implemented" text.
- **A background analytical review of the actual rendered UI (2026-08-06)** — using a
  separate Claude Code instance focused on structure/layout, not aesthetics — found
  and this session then fixed for real:
  - The detail hero and the new bottom action bar were duplicating the same
    name/version/status in two places one screen apart — the exact "hero +
    inspector" pattern the redesign was supposed to remove, just with a second
    surface bolted on rather than replacing it. Hero collapsed into a compact
    `.detail-header` (no monogram, no duplicate action row); the action bar is now
    the only place Play/Instances/Configure live.
  - **The Play button always said "Play" but always actually opened a hand-off
    confirmation modal** (direct launch doesn't exist yet) — actively misleading,
    not just generic. Fixed: label now resolves live via `find_manager_for_game()`
    and reads "Open in `<Manager>`" when a real hand-off target exists, "Play"
    otherwise — no more fixed, wrong-every-time label.
  - Grid-mode's status dot was color-only (a bare colored circle with only a
    hover `title`, no accessible name) — real accessibility gap, one screen away
    from the correctly-built list-mode equivalent. Marked `aria-hidden="true"` since
    the adjacent `.game-card-state` text already carries the real signal.
  - Several icon-only buttons (edit-name, variant "•••", modal close ×) had no
    accessible name at all, inconsistent with other icon buttons in the same file
    that already do this correctly. Fixed.
  - Two leftover fake-looking static values from earlier sanitization passes: the
    topbar Jobs icon's hardcoded "2" badge (now real, driven by `jobs.length`,
    hidden at zero) — a real miss in the earlier "strip mock data" pass.
  - Lower-priority findings **deliberately not acted on this pass** (explicitly
    aesthetic, not structural, per direction): duplicate `--ok`/`--positive`-style
    CSS token aliasing, type-scale inconsistency across headings/labels, control-
    height/padding drift (34px vs 36px etc.). Real, written down, not urgent.
- **Not yet done**: the Game/Instances focused view, Compatibility & Runtimes' actual
  content (page exists, empty — real backends aren't built), Installer-as-persistent-
  job, Jobs/Running session merge, Handheld shell updates (the same hero-duplication
  and Play-label issues likely exist there too — `.hh-detail` mirrors the same
  decorative-header shape and hasn't been reviewed yet), and the settings page's
  ~7 near-identical hand-duplicated capability-list blocks (sources/downloaders/
  metadata/managers/engines/mods/patchers) that could be one generalized component.
  Real, scoped follow-up work, not started.

**Shell structure** (`QMainWindow` + `QMenuBar`/`QToolBar`, `QSplitter` of a source-tree
sidebar + central library, `QListView`/`QTableView` for grid/list, `QDialog` for
Configure/Install — not a web dashboard ported into Qt):
- Sidebar re-centered on launching, not administration: Library (All games/Favorites/
  Recently played/Installed here/Needs setup/Updates) → Running (live sessions) →
  Sources (per-plugin, e.g. Steam/GOG/F95 watchlist/Local) → Manage (Compatibility &
  runtimes/Jobs & activity/Systems & sync/Reconciliation). Plugins/Settings live in the
  menu bar, not competing with library nav.
- A persistent **bottom action bar** for the selected game replaces the current web
  mockup's large hero/inspector as the default view: art, name, instance/launch-config
  picker, a dominant **Play** button (becomes Stop while running, Finish setup when
  broken), Instances/Configure buttons, effective backend/status on the right.
- A **Game/Instances focused view** (reached from Instances/Configure/"Show details")
  for deeper per-game work: Launch (effective config before pressing Play) /
  Instances / Compatibility / Updates / Saves / Systems / Details — supporting the
  library+action-bar flow, not replacing it for normal use.
- **Install or add** replaces "Downloads & intake" as the top-level action: search
  installers/sources, install from file/script, import an installed game, scan a
  folder/launcher, add manually, review detected downloads — the user picks a task,
  not a plugin category.
- **Installer workflow** becomes a persistent, inspectable job (Review → Acquire →
  Verify → Install → Configure stages, live log, explicit commit only after
  verification) — ties directly into the `installer/` port (Step 7) and the
  acquisition-job model (Step 2).
- **Compatibility & runtimes** becomes a first-class global management surface
  (Backends/Runtime versions/Prefix-data groups/Emulator cores/Component updates) —
  this is where Step 1's compatibility-system work gets a real UI, separate from any
  one game's Configure dialog.
- **Jobs & activity merges with running-session tracking**: installs, scans, sync,
  and transfers share one persistent activity surface; a running game additionally
  stays visible in the sidebar and a toolbar session indicator while the user browses
  elsewhere.
- Handheld stays a genuinely separate QML shell (not a responsive rearrangement of
  desktop), same launch-first flow: Library → select → Play, with Instances/Configure
  reachable from its own action bar.

**Configure dialog** — one dialog, opened per-instance (`Configure · <Game> ·
<Instance>`, changes apply to that instance only), six tabs: Game options
(executable/working dir/arguments/launch config/env vars) / Compatibility (backend/
runtime/architecture, contextually filtered — Wine-specific controls never shown for a
Native Windows instance) / System options (GPU/display/performance/screensaver) /
Integrations (Runtime Companions/save profile/launcher projection) / Instance (name/
parent/storage method/Instance Manager) / Advanced (process matchers, pre/post
commands). This is the *instance*-level editor specifically — see the three-tier
config cascade below for where game- and global-level settings live instead.

**Three-tier config cascade** (explicit user decision, 2026-08-06): three separate
entry points, not one dialog with a scope toggle — **Global** settings live in the
app's Settings menu; a **"Configure"** action on a *game* (not an instance) opens
game-wide defaults shared by every instance of that game; a **"Configure"** action on
an *instance's* own menu item opens the instance-level dialog described above.
Priority, lowest to highest: global → game → instance (an instance-level value wins
over that same field's game-level value, which wins over global). Real implementation
path: extend `pythia/config.py`'s `PythiaConfig` (already a real, working 2-level
cascade — `game → runner → system`, three separate YAML files, cascaded-vs-raw views
proven in production) with a new `instance` level on top, keyed by the real, already-
stable per-variant `id` (see Part 1's variant-identity correction) — same mechanism
Lutris itself uses for its own `game → runner → system` cascade, extended one level
deeper rather than redesigned.

**UX principles governing the redesign** — from a second research doc,
`C:\Users\jon\Downloads\pythia-research\pass5\deep-research-report.md` (a sourced
comparison of Lutris/Bottles/Heroic/PlayOnLinux/Steam/GOG Galaxy plus usability-heuristic
grounding, reviewed 2026-08-06). Concrete, binding rules for every screen in Step 5, not
just restated background:
- **Execution ownership is always visible.** The Play button's own label states how a
  game will launch when it isn't a plain native run — "Launch via Steam" rather than a
  bare "Play" when handing off; during a session, status reads "Running under Proton
  8.0-1" / "Running native Windows", not just a generic "Running" pill.
- **Never color-alone for status.** Every state that isn't routine-healthy needs visible
  words, not just a colored dot — `● Needs sign-in` must be spelled out, matching the
  status-pill discipline already in the current web UI's copy rules.
- **Filesystem clone/copy method is chosen silently, narrated visibly.** Never ask the
  user to pick CoW-vs-full-copy up front (ties to the storage-capability backlog item) —
  detect what's available, use the fastest real method, and just say what's happening
  ("Creating snapshot…" vs "Copying files…").
- **Modal for critical/blocking flows (install wizard, destructive confirmations),
  non-modal for anything the user should be able to browse past** (game details/config
  panes) — matches the Configure-dialog-as-modal / Instances-panel-as-page split already
  designed above.
- **Progressive disclosure, not fewer features.** Advanced/rarely-needed fields (process
  matchers, custom environment variables, raw prefix paths) stay behind an explicit
  "Advanced" affordance; the default view shows only what a normal launch decision needs.
- **Accessible naming is a real requirement, not a nice-to-have**: every icon-only
  control needs a real accessible name in the Qt sense (`QWidget.setAccessibleName`/
  `setToolTip`, not just a tooltip as the only explanation) — "Configure Being a DIK",
  not just a bare gear icon.
- **Redownload vs link-only is per-source, not a blanket rule** — corrected 2026-08-06:
  the research draft's "don't try to re-download an externally-owned game" guidance is
  too absolute for Pythia specifically, since real headless acquisition tools already
  exist for some platforms (SteamCMD, already a real shipped downloader; **lgogdownloader**
  as the equivalent real candidate for GOG, not yet built). The actual rule: where a
  real downloader exists for that source, acquisition is a normal path, not an
  exception; "Link to External" (note the existing install path, never attempt to
  re-fetch) is the fallback specifically for sources with no headless acquisition tool
  at all (Ubisoft Connect/EA App/Battle.net, already noted in the backlog as needing
  their own vendor client just to detect install state, let alone acquire).

### Step 6 — Cover art / metadata pipeline (confirmed not built)
- No `get_cover_art()` exists anywhere in `pythia/plugins/` (confirmed by direct grep,
  not assumed). Real, scoped, not started: a `MetadataPlugin.get_cover_art()` method
  plus an on-disk art cache, sourced from Steam's public CDN (free, no-auth, any known
  AppID) and SteamGridDB (community-run, covers non-Steam engines, needs one real API
  call to confirm response shape first — see Step 8). Same caching/offline-behavior
  question as `platform_support()`/`list_dlc()` above applies here too — worth solving
  once for all three rather than three separate times.

### Step 7 — Remaining ports (real, scoped, not yet started)
- `installer/` (F95/Lutris-style install-script interpretation) — the one Lutris
  subsystem where the storage coupling is central, not incidental; budget as its own
  chunk of work, not a quick swap. More load-bearing now than before Step 1's pivot:
  a real Lutris replacement needs real install scripts, not just launch capability.

### Step 8 — Backlog (real, scoped, roughly ordered by effort/value, not started)
- Two known, deferred naming-heuristic gaps in onboarding (both real, neither fixed
  since first found): sequel-number-vs-version ambiguity (`Far Cry 5`/`Far Cry 6`
  reads as one game with two versions; `Cyberpunk 2077` reads as name "Cyberpunk"
  version "2077" — genuinely hard to resolve from the string alone), and shallow
  `.exe`-in-root-only platform detection missing nested Unreal/Unity-style installs
  (EA/Epic/Ubisoft folders still show unknown platforms).
- Surface `platform_support()`/`list_dlc()` (real, working, currently unexposed — see
  Part 1) in the UI, with real caching/offline behavior — not a live call per view.
- Deterministic merge semantics (no per-field revision/origin metadata — a later
  rescan can silently clobber a manual correction).
- Minor, flagged-but-not-fixed naming collision: `entry["source"]`/variant `source`
  (which plugin *identified* this install) and `SourcePlugin` (generic NAS/web/cloud
  fetch, unrelated) use the same word for two different concepts — worth renaming the
  former (e.g. to `identification`) the next time the schema needs a real migration
  anyway, not a standalone change.
- Stable variant identity (variants are still identified by filesystem `path` alone —
  a moved/renamed install looks like a new variant).
- Steam shortcut generation (`shortcuts.vdf` via `ValvePython/vdf`) and
  EmulationStation integration (`gamelist.xml` via stdlib `xml.etree.ElementTree`) —
  both confirmed low-risk, well-documented formats, real libraries identified,
  neither started.
- **Launcher projection as a first-class adapter**: read/plan/apply/validate entries
  into Lutris/Playnite/Pegasus/Steam shortcuts, preserving user edits via a
  three-way reconciliation rather than blind overwrite — a natural extension of the
  existing `ManagerPlugin` category (which currently only *hands off* to a launcher,
  never projects entries into one). Distinct from, and a superset of, plain Steam
  shortcut generation above.
- SteamGridDB cover-art source — confirmed to exist, exact response shape needs one
  real live API call before implementation. **VNDB** (Visual Novel Database, API v2/
  Kana) is a real additional metadata-source candidate specifically well-suited to
  this library's actual composition (JP-VN-engine-heavy) — Steam/GOG/SteamGridDB all
  cover it poorly or not at all.
- Watch-folder downloader candidates beyond F95Checker/SteamCMD: **JDownloader2**
  (generic mirror-host downloader — Mega/Mixdrop/Gofile, common on adult-game release
  posts) and **qBittorrent** (full Web API, good fit for "watch for completed"
  integration) — both real, neither started.
- **lgogdownloader** — real, existing headless GOG acquisition CLI, the direct GOG
  equivalent of the already-shipped SteamCMD downloader. Real prerequisite for GOG to
  be a genuine redownload-capable source rather than link-only in the default launch
  UI (see Step 5's UX-principles note on redownload-vs-link-only being per-source).
- Remaining Lutris services (Ubisoft Connect, EA App, Battle.net, Humble, Amazon) —
  real scope, Ubisoft/EA App specifically blocked on needing their own vendor client
  just to detect install state.
- F95 masked-download-link resolution is now real and interactive (authenticated
  QtWebEngine + `a.host_link`, matching F95Checker). Fully headless CAPTCHA bypass is
  intentionally not claimed; direct automatic download-job execution remains open.
- Save copy-forward/backup — adapt **Ludusavi** (19,000+ games, machine-readable
  JSON, bulk API, already covers Steam/GOG/Epic/Heroic/Lutris/custom entries) rather
  than reimplementing save-path discovery from scratch.
- ROM specialization (the original ~14TB NAS use case: remote-only/cached-local/
  evicted states) — **RomM** as the recommended source adapter, RomVault's
  DAT-vs-physical-path separation as a real lesson to apply; `SourcePlugin` exists as
  an interface but needs a concrete `search`/`get_item`/`fetch`/`verify` shape built
  against the explicit constraint of never loading 14TB into memory.
- Structured mod profiles (Thunderstore-style package+version+dependency+profile,
  beyond the simple `mods: [{name, version}]` tags already shipped) — today's simple
  tags stay correct for the common F95-variant case, this is additive scope.
- Naming-filter template system (still the original minimal placeholder heuristic) —
  real, open, unchanged in scope from prior research.
- Instance managers (MO2/Vortex hand-off) — real integration cost confirmed
  non-trivial (MO2 has no official API, only an unofficial community bridge; Vortex's
  official API is a JS extension system requiring its own bridge) — budget
  accordingly when picked up, don't assume a simple Python call.
- Storage-capability cloning — confirmed no native Windows CoW path exists for the
  common NTFS case (ReFS-only, and ReFS isn't the default even on modern Windows);
  full-copy is correctly the *expected* Windows default, not a degraded fallback.
  Linux handhelds' actual filesystem (Btrfs/XFS support `--reflink`, ext4 doesn't)
  still needs checking on the real devices.
- Canonical game-instance model (a real, substantial schema evolution beyond the
  current `games`/`variants` split), instance managers and runtime companions as new
  plugin categories, and cross-system transfer (transport still an open design
  space) — all fully spec'd, not started, not blocking anything shipped today.
- Marker-file synced-copy detection (real physical evidence that a folder was
  onboarded by another system and synced here by something outside Pythia — OneDrive,
  rsync, a NAS mount — already partly integrated into `find_candidates()` above via
  `instance_marker.read_marker()`) — extending this further is fully spec'd, not
  started beyond that one integration point.

---

## Part 1.7: Plugin packaging — final architecture (2026-08-07, verified)

Rebuilt from the ground up this pass after three real corrections to the previous
design. Every built-in ships as a real, pre-built `.zip` — there is no runtime code
path anywhere in the app that imports or reads loose plugin source. This section is
the authoritative description; keep it in sync with `pythia/plugins/registry.py`'s
and `pythia/plugins/packaging.py`'s own module docstrings, which carry the same
account in more detail.

**Source layout.** Each built-in's real, hand-authored source is a loose directory
at `pythia/plugin_sources/<category>/<name>/`:
- `plugin.json` — manifest: `id`, `display_name`, `version`, `author`, and a
  required `categories` list (e.g. `["library", "manager"]`).
- `plugin.py` — the entry point; self-registers via a module-level
  `PLUGIN_CLASSES = [...]` list. **One package, potentially several classes**
  (2026-08-07 correction, e.g. the rpgmaker package's MV/MZ/VX Ace engine classes
  all live under one `pythia.engine.rpgmaker` manifest id) — a plugin is not
  identified or split apart by classname; the whole package is one enable/disable
  unit, and every class it declares gets instantiated and independently checked
  against a category ("we just run through all the classes and track which ones
  hit, and if there's multiple, we surface it").
- `type-<category>.py` — one per declared category, each declaring that facet's
  own `CONFIG_SCHEMA` (a list, possibly empty). **This is the plugin's real,
  authoritative config schema** (2026-08-07 correction: "the type-*.py file
  contains the config schema for that type. It's where it HAS to be") — never a
  Python class attribute.
- `os-generic.py` or `os-windows.py`/`os-linux.py` — required OS-support
  declaration; file presence alone is the declaration, no exec needed to check it.
- `assets/` — optional, real binary tool files a plugin bundles verbatim (e.g.
  `pythia.mod.locale_remulator` bundles InWILL/Locale_Remulator's own real
  `LRProc.exe` + hook DLLs, LGPL-3.0, extracted to a private cache dir at runtime
  since a native `.exe` can't run from inside a zip the way Python can via
  zipimport).
- Any other `.py` file the package needs as a sibling module, importable from
  `plugin.py`/a `type-*.py` via a plain top-level `import` (e.g. the Ren'Py engine
  plugin's `renpy_patch_engine.py`, the original ARM-patch tool, now living inside
  the plugin instead of the main app — "renpy_patch doesn't NEED to be internal to
  the project... put it in the renpy module").

**Self-containment rule** (2026-08-07): a plugin may import stdlib, third-party
libraries actually installed as dependencies, and `pythia.plugins`' own base
classes (`EnginePlugin`, `RuntimeModPlugin`, etc. — the plugin interface itself,
inherited methods like `get_credential`/`set_credential`/`get_config`/`set_config`/
`data_dir()`). It may **not** import any other `pythia.*` application module
(`pythia.core`, `pythia.identity`, `pythia.downloads`, `pythia.renpy_patch`, a
sibling plugin's helper module, etc.) — "Plugins need to be completely
self-contained. They cannot reference anything outside of themselves, besides
stdlib and other basics." Concretely, this pass fixed 5 real violations:
- `library/__init__.py`'s module-level `_DEFAULT_PRIORITY` list used to import
  every built-in library class directly just to read `.id` off it (executing all
  their `plugin.py`s on every `import pythia.plugins.library`) — now plain id
  string literals.
- `steamcmd`/`egs` plugins used to call `pythia.core.runners.ensure_tool()` to
  download their external tool binary — now each has its own self-contained
  stdlib-only download+extract (`_ensure_steamcmd()`/`_ensure_legendary()`),
  writing into `self.data_dir()`.
  "steamcmd should provide the steamcmd tool."
- `steamcmd`/`f95checker` `launch()` used to call `pythia.downloads.launch_and_watch()`
  — now they just start the process (`subprocess.Popen`) and return the handle;
  watching for new files is the caller's (Pythia's own download manager's) job.
- `local_path` used to call `pythia.identity.get_setting/set_setting` directly for
  its configured roots list — now `self.get_config("roots")`/`self.set_config(...)`,
  the same generic per-plugin config channel every other config field uses.
- `renpy_patch_engine.py` (549 lines, pure stdlib, the original ARM-patch tool) used
  to live at `pythia/renpy_patch/patch.py` and get imported by the Ren'Py engine
  plugin from the main app tree — now it's a sibling module inside the plugin's own
  `plugin_sources/engine/renpy/` directory, bundled into the same zip.

**Where a plugin gets storage/config it didn't ask a human for.** Added to
`PluginMeta` (`pythia/plugins/__init__.py`) this pass: `get_config(key, default)`/
`set_config(key, value)` (generic per-plugin config, the same `plugin_config:<id>`
storage the Configure UI already reads/writes) and `data_dir()` (a private,
writable directory — Pythia computes a real default; a `"data_dir"` entry in the
plugin's own stored config overrides it through that same channel). "Pythia may
provide a default, and we can override it using the configuration menu. The plugin
will see that information... without knowing where it came from, because it
doesn't matter." These are opt-in — most plugins (every detect-only engine plugin,
for instance) call neither and have zero on-disk footprint of their own.

**Build step.** `scripts/build_plugins.py` zips each `plugin_sources/<category>/<name>/`
directory, byte-for-byte, into a committed artifact at
`pythia/plugins/builtin/<id>.zip` — re-run and commit whenever `plugin_sources/`
changes; the app itself never regenerates these.

**Discovery — metadata only, until something is actually enabled.**
`registry._load_and_validate_package()` reads ONLY `plugin.json` (manifest +
declared `categories`) and each declared category's `type-<category>.py`
`CONFIG_SCHEMA`, plus checks `os-*.py` file presence — **plugin.py itself is never
read or exec'd at discovery time** (2026-08-07: "we just read the metadata files,
and plugins are loaded when enabled"). `registry.all_plugin_entries()` is the
metadata catalog (id → categories/display_name/version/author/config_schema) that
powers the Plugins-page listing and `for_category()`'s membership check, entirely
without loading a single plugin's real code. `registry.get_plugin_instances(id)`
is the one place plugin.py actually gets loaded (via zipimport) and every one of
its `PLUGIN_CLASSES` instantiated — called only for a plugin that's both enabled
and actually needed by a category query. `discover_builtin_zips()`/
`discover_installed_zips()` both scan real `.zip` files only (`pythia/plugins/builtin/`
for built-ins, `app_data_dir()/plugins/` for user-installed/third-party) — the same
path, same validation, same zero-loose-source guarantee for both.

Verified end-to-end after this pass: all 23 built-in packages load with zero
`get_plugin_load_errors()`, every category's `for_category()` call returns the
expected instance counts (engine: 14 instances across 11 packages, library: 6,
mod: 1, patcher: 1, source: 1, downloader: 2, manager: 2, metadata: 2), and a full
app launch (`bin/pythia --ui web`) produces a clean startup log.

**Deferred, not urgent** (from the plugin-system research pass, still open):
runtime isolation for `exec()`'d plugin code — a hanging or malicious `plugin.py`
can currently freeze the whole app at load time. Real build scripts existing now
partially sets up Task #21 (GitHub Pages-hosted official plugin index) — still not
started.

---

## Part 1.8: Real logging + a real distributable build system (2026-08-07)

### Logging fix

`pythia/util/log.py`'s shared `logger` used to be `logging.getLogger(__name__)`
(`"pythia.util.log"`, a leaf logger) with `propagate = False` — ~130 files import
and log through this one shared object, but none of those records ever reached
the real root logger, where `Backend._UiLogHandler` listens for the web UI's
Event log. Fixed by renaming to `logging.getLogger("pythia")` (one-line change,
fixes every existing call site at once) and leaving `propagate` at its default
`True`. Added `set_debug_logging(enabled)` (real DEBUG-level toggle, previously
nonexistent — `DEBUG_FORMATTER` was defined but never used) plus a `PYTHIA_DEBUG`
env var read at import time (works against a packaged, `--windowed` build with no
console, no rebuild needed). Wired a `--debug`/`-v` flag into both
`pythia.ui.app.run()` and `pythia.app.run()` (the latter already had an inert
`-d/--debug` argparse flag that only set INFO→DEBUG level, never swapped
formatters — now calls the real `set_debug_logging()`). Added `sys.excepthook`
in both entry points so a crash before the Qt event loop even starts still gets
logged somewhere durable. Removed a stray `logging.basicConfig()` in vendored
`util/wine/extract_icon.py` (import-time side effect that would have started
duplicating console output once propagation was fixed). Verified: `bin/pythia
--ui web` now shows real cross-subsystem INFO output (not just the plugin
registry's own logging), `PYTHIA_DEBUG=1` shows DEBUG-level detail.

### Windows: PyInstaller onedir + Inno Setup installer

`packaging/windows/{entry.py,pythia.spec,pythia.iss,icon.ico}` +
`scripts/build_windows.py`. Real, tested: built and launched the actual frozen
`pythia.exe` this session (`dist/windows/pythia/`, ~697MB — `collect_all("PySide6")`
pulls in modules Pythia doesn't use like Qt3D/Multimedia; a real follow-up is
trimming this with explicit hidden-imports instead of collect-all, deferred since
correctness mattered more than size for a first working build). Fixed two real
crash bugs surfaced by actually running the frozen build (neither was visible in
dev mode): `pythia/util/test_config.py` had a dead, unguarded top-level `import gi`
(deleted, unreferenced anywhere); `pythia/monitored_command.py`'s
`WRAPPER_SCRIPT = get_wrapper_script_location()` raised unconditionally at
*module import time* if `share/pythia/bin/pythia-wrapper` wasn't found at one of
its candidate paths — crashed the whole app on startup in the frozen build. Now
returns `None` and only raises when a caller actually tries to use a monitored
terminal command without it. `PySide6` added to `pyproject.toml` as a real
dependency (previously undeclared, lazily imported); `dbus-python`/`PyGObject`/
`evdev`/`pypresence`/`protobuf` marked `sys_platform == 'linux'` (real audit found
them vestigial or already `try/except`-guarded everywhere they're used — see the
research notes preserved in this session's own history). Inno Setup script
written (per-user install to `%LOCALAPPDATA%\Programs\Pythia`, no admin) but not
compiled/tested locally — no Inno Setup on this dev box; `.github/workflows/
release.yml` installs it via `choco` in CI.

### Linux: AppImage + XDG self-integration + rpm/deb (written, not tested here)

*(PyInstaller doesn't cross-compile — none of this can be built or verified on
the Windows dev box this session ran on; correctness depends on a real CI run or
WSL/Linux box.)*

`packaging/linux/{entry.py,pythia.spec}` (console kept, unlike Windows —
no `--windowed` gap on Linux). `scripts/build_appimage.sh` assembles a real
`AppDir` from the PyInstaller onedir output and runs `appimagetool` — replaces
(doesn't adapt) the old GTK/linuxdeploy-based `utils/appimage/`, deleted this
pass along with `pythia.spec` (RPM, GTK/meson-oriented), `setup.py` (broken,
`from lutris import __version__`), the `appimage:` Makefile target, and the three
fully Lutris-branded workflows (`publish-appimage.yml`, `publish-lutris-ppa.yml`,
`publish-ppa.yml`) — per the user: "there's no reason to keep them around until we
[create new ones]."

New `pythia/util/xdg_install.py` + a real `--xdg-install` CLI flag on
`pythia.app`: copies the `.desktop` file (with `Exec=` rewritten to this
instance's actual running path — an AppImage has no fixed install location),
the hicolor icon set, and the `.pythia` MIME association into the user's real
`~/.local/share`, then runs `update-desktop-database`/`update-mime-database` if
present. Explicit/opt-in (not run automatically on every launch) — the standard
self-integration pattern AppImages use instead of requiring AppImageLauncher.

`scripts/build_linux_packages.sh` builds both `.rpm` and `.deb` via `fpm` (not
hand-written spec/control files) from the same PyInstaller onedir output,
installing the frozen bundle under `/opt/pythia/` with a `/usr/bin/pythia`
wrapper script — retires the old native-system-Python-plus-GTK-typelib RPM
approach entirely.

### App icon / branding (deliberate placeholder)

`scripts/generate_icons.py` generates a classic magenta/black checkerboard
"missing texture" icon via Pillow (multi-res `.ico`, Linux hicolor PNG set +
scalable SVG) — **deliberately not** `share/pythia/media/logo.png` (a real,
decent-looking asset that would look like finished branding). Per the user:
"We haven't created icons and stuff yet, so that'll be a good reminder." Also
generates `net.pythia.Pythia.desktop`/`.metainfo.xml`/mime XML, Pythia-branded
replacements for the Lutris originals (which are still present alongside these,
untouched — only the packaging *tooling* that referenced them was removed, not
the old assets themselves).

### CI

New `.github/workflows/release.yml`: on a `v*` tag (or manual dispatch), builds
Windows (portable zip + Inno installer) and Linux (AppImage + rpm + deb) in
parallel jobs, publishes everything to a GitHub Release. Not run this session
(no tag pushed) — reviewed for correctness, not executed.

### Explicitly out of scope this pass

macOS/other-unix, Flatpak (no manifest exists — separate future effort), code
signing (Windows Authenticode, RPM/DEB GPG) — all per direct user instruction,
not oversights.

### Windows build revision: config file, not multiple executables (same day)

First pass built 4 separate .exe targets (standard UI / UI-next × debug/non-debug)
via PyInstaller's `MERGE()`. **Reverted** — the user redirected: "We're gonna use
a config file on windows... It controls debug, ui selection, and etc. We won't
give a console, we'll write a log to the appdata folder's logs folder." One
executable now, `packaging/windows/pythia.spec` back to a single `Analysis`/`EXE`/
`COLLECT`, `console=False` always.

New `pythia/util/local_config.py`: an INI file (`pythia.cfg`, stdlib
`configparser`) with `debug`/`ui` keys, checked at two real locations in order —
next to the running executable (the portable bundle's own copy,
`packaging/windows/pythia.cfg`, copied there by `scripts/build_windows.py` as a
**post-build step**, not via PyInstaller's `datas`, because `datas` always nests
under `_internal/` in onedir mode and "next to the executable" means a true
sibling of `pythia.exe`), or next to `local.db` (`app_data_dir()` — an installed
build's per-user copy, auto-created with real defaults on first run if missing).
CLI flags (`--debug`, `--ui`) still take precedence over the config file when
both are present. Wired into both `pythia.ui.app.run()` and `pythia.app.run()`.
Verified end-to-end: editing `debug = true` in the portable bundle's `pythia.cfg`
and relaunching with zero CLI flags produced real DEBUG-level log output.

`pythia/util/log.py`'s `LOG_FILENAME` moved from its own independently-resolved
cache dir (a lowercase `"pythia"` folder that happened to collide harmlessly with
`identity.app_data_dir()`'s `"Pythia"` on case-insensitive Windows, but was never
actually the same code path) to `app_data_dir() / "logs" / "pythia.log"` — a real
`logs/` subfolder, and now uses `identity.app_data_dir()` directly instead of
duplicating its resolution logic.

### GitHub repo: plugin index + simple app update-check (same day)

Per the user: "we need to configure github sites to hold modules/plugin to
allow for updates, and we wanna build an autoupdate system... Keep the systems
simple. Decouple plugins from the actual application storage, so we just update
ourselves when we need to." **The repo (`bi0shacker001/pythia`) is currently
private** ("to prevent people from grabbing it while it's working. It'll be made
public with 1.0") — confirmed via `gh api repos/.../pages` (404) and a real
attempt to enable it (`gh api repos/.../pages -X POST`, `422 Your current plan
does not support GitHub Pages for this repository`). Everything below is
written and locally verified correct, but **not fetchable by a real install
until the repo goes public** — no code changes will be needed when that happens,
just flip the repo visibility and (if it doesn't self-enable) toggle Pages on
under Settings → Pages → Deploy from branch → `master` /docs.

**Plugin index** (`docs/plugins/`, decoupled entirely from the app's own release
cycle — a plugin can update independently of a Pythia version bump):
`scripts/publish_plugin_index.py` copies every real `pythia/plugins/builtin/*.zip`
into `docs/plugins/` and writes `docs/plugins/index.json` (id/display_name/
version/categories/sha256/download path) — static files only, no GitHub API, no
auth. New `.github/workflows/publish-plugin-index.yml` runs it automatically
whenever `plugin_sources/` changes. App-side client: `pythia/plugins/
remote_index.py` (`fetch_index()`/`install_from_index()` — downloads, verifies
the real sha256 against what the index declared, then reuses the existing
`packaging.install_plugin_zip()` path, same as a manually-picked local zip) plus
two new backend Slots (`check_plugin_index()`, `install_plugin_from_index()`) —
UI wiring in `index.html` for browsing/installing from the remote index is not
built yet (deliberately deferred, "keep it simple" — the backend capability is
real and callable, the Plugins-page UI for it is a separate follow-up).

**App update-check** (`pythia/util/update_check.py`): deliberately check-only,
no auto-apply — "a portable zip, an Inno installer, an AppImage, and rpm/deb all
update differently enough that one 'just do it' mechanism would either be wrong
for some of them or need real per-channel handling not built yet."
`check_for_update()`/`check_for_update_strict(channel)` fetch
`docs/app/<channel>.json` and compare; a new backend Slot
(`check_for_app_update()`) exposes it. Verified locally against `file://`
manifests for both comparison modes described below.

### Release channels: latest / nightly / unstable / stable (same day)

"We also need channels. Latest, Nightly, Unstable, and Stable builds" —
"'latest' is just the newest build (we build every commit). Nightly is built
daily, Unstable is for testing for the next stable build, and stable builds are
actual releases" — "We also must never delete old builds. We keep the whole
history."

`.github/workflows/build.yml` (replaces the earlier tag-only `release.yml`): a
`setup` job determines the channel from the trigger context — a branch push
(`lutris-core`) is `latest`, the daily cron (`0 6 * * *` UTC) is `nightly`, a
pushed tag containing a `-` (e.g. `v1.2.3-rc.1`) is `unstable`, a plain `vX.Y.Z`
tag is `stable`. **Every run gets its own uniquely-tagged GitHub Release, never
overwritten**:
- `latest`: tag `build-<UTC timestamp to the second>-<short sha>` (per the
  user's exact format request).
- `nightly`: tag `nightly-<UTC date>`.
- `unstable`/`stable`: the pushed tag itself (already unique).

The Windows/Linux build jobs stamp `pythia/pythia/build_info.py`
(`BUILD_CHANNEL`, `BUILD_COMMIT` — defaults to `"dev"`/`None` for a normal
source checkout, real values only exist in a CI-built artifact) before running
PyInstaller, so a running build always knows which channel and commit it was
built from. The `publish` job creates the permanent release, then overwrites
**only** the small pointer file `docs/app/<channel>.json` (version, commit,
download/release-notes URLs) to point at the newest build on that channel —
committed onto the real tip of the default branch (fetched fresh, not whatever
detached ref triggered a tag build) so a tag-triggered run can never
force-push over branch history. Every actual build stays permanently reachable
from the repo's Releases page regardless of how many newer ones on the same
channel get published later.

`pythia/util/update_check.py` compares differently per channel: `stable`/
`unstable` have real, comparable version strings (semver-style, a `-rc.1`
suffix stripped before parsing) — newer-than comparison, same as before.
`latest`/`nightly` don't (the same base version stays fixed between real
releases) — compared by whether the manifest's `commit` differs from the
running build's own `BUILD_COMMIT` instead. `pythia/util/local_config.py`
gained a `channel` key (`pythia.cfg`, default `stable`) selecting which
manifest `check_for_update()` reads by default.

Real version reset the same day: `0.5.23` (inherited from the Lutris fork's own
version number) was never Pythia's actual version — reset to `0.0.1` in
`pythia/__init__.py`/`pyproject.toml`/the generated metainfo (now pulled
dynamically from `pythia.__version__` in `scripts/generate_icons.py` instead of
a second hardcoded literal, so this can't drift again).

### x86/ARM matrix for Windows and Linux (same day)

Previously x86_64-only on both platforms (`windows-latest`, `ubuntu-latest`,
`appimagetool-x86_64.AppImage` hardcoded, `fpm` packages hardcoded to
`amd64`/`x86_64`). `.github/workflows/build.yml`'s `windows` and `linux` jobs
are now a real matrix: Windows `x64`(`windows-latest`)/`arm64`
(`windows-11-arm`), Linux `x86_64`/`aarch64` (`ubuntu-latest`/
`ubuntu-24.04-arm`) — PyInstaller doesn't cross-compile, so each leg genuinely
builds on a real machine of its own architecture. `scripts/build_windows.py`,
`scripts/build_appimage.sh`, `scripts/build_linux_packages.sh`,
`packaging/windows/pythia.iss` all read the real host architecture
(`platform.machine()`/`uname -m`) instead of a hardcoded string, for both the
output filename and (deb vs. rpm's different naming conventions for the same
hardware) the package metadata.

**Flagged, not silently assumed**: PySide6/QtWebEngine's ARM64 Windows wheel
support is genuinely less mature than x64 or Linux ARM64 — that leg has
`continue-on-error: true` so a real failure there (e.g. `pip install .` finding
no matching wheel) doesn't block the `publish` job from shipping the other
three legs that did succeed. Linux ARM64 has no equivalent risk (PySide6 has
solid `aarch64` Linux wheels). None of this has been verified against a real
CI run yet — written correct, not yet observed working, same honesty standard
as the rest of this build system's Linux side.

### Real bugs found by external review testing a genuinely clean install (same day)

**Methodology gap, fixed first**: every "clean environment" test run so far this
session had reused a dev-mode `%APPDATA%\Pythia` where migrations had already
run — `PYTHIA_DATA_DIR` only isolates `pythia.identity.app_data_dir()`;
`pythia.settings`'s own `CONFIG_DIR` (holding `pythia.conf`, where
`migration_version` actually lives) reads `%APPDATA%` directly via
`pythia.util.xdg.get_user_config_dir()`, bypassing that override entirely. Real
isolation needs `APPDATA` itself redirected, not just `PYTHIA_DATA_DIR` — this
is now the standard test recipe for anything touching first-run behavior.

With that fixed, four real, previously-invisible bugs surfaced:

1. **`No module named 'pythia.migrations.mess_to_mame'`** — `pythia/migrations/
   __init__.py` and `pythia/runners/__init__.py` both resolve real submodules via
   `importlib.import_module()`/`__import__()` with a computed string, invisible
   to PyInstaller's static analysis. Fixed via `collect_submodules("pythia.
   migrations")`/`collect_submodules("pythia.runners")` in both specs, plus an
   explicit `"moddb"` hiddenimport (`pythia/util/moddb.py`'s own dynamic import
   of a real third-party package). Every other `importlib.import_module()`/
   `__import__()` call site in the app was audited (three total, not a blind
   whole-repo collect) before deciding this was the complete list.
2. **`PermissionError` opening the log file, only visible as a generic
   PyInstaller dialog** — `pythia/util/log.py`'s `RotatingFileHandler` opens its
   file at MODULE IMPORT TIME, before any exception handler exists. Fixed with a
   try/except falling back to a PID-suffixed filename (never blocks startup),
   plus a genuinely early `pythia/util/crash_handler.py` (stdlib-only,
   `install_minimal_crash_handler()`) installed as the very first thing both
   frozen entry points do, before importing `pythia.ui.app`'s own heavier
   exception hook — writes to `app_data_dir()/logs/crash.log`, wrapped so this
   handler itself can never be the thing that fails.
3. **`--ui bigpicture` exited silently within ~6 seconds in the frozen build**
   (found via the review's own required smoke matrix, not the original report) —
   `pythia/ui/bigpicture/qml/*.qml` was never added to either spec's `datas`
   (unlike the web UI's `index.html`, which was). `QQmlApplicationEngine` fails
   silently (a Qt-level `qWarning`, not a Python exception) when it can't find
   its root QML file, so neither crash hook had anything to catch. Confirmed via
   a real before/after test (dev-mode `python bin/pythia --ui bigpicture` stayed
   running; the frozen build didn't, until the `datas` entry was added).
4. **No Windows version resource** — `FileVersion`/`ProductVersion`/
   `CompanyName` were never set at all (not stale Lutris values — just absent),
   so Explorer's Properties dialog and Inno Setup's `GetFileVersion()` had
   nothing authoritative to read. Fixed via a real `VSVersionInfo` built in
   `packaging/windows/pythia.spec` from `pythia.__version__`, passed to `EXE(...,
   version=version_info)`. Verified against the actual built `.exe`'s real
   Windows version metadata (`Get-Item ... | .VersionInfo`), not just the spec
   compiling.

All four verified against a genuinely isolated `APPDATA`, including the full
smoke matrix the review asked for (bare launch, `--ui web`, `--ui web --debug`,
`--ui desktop`, `--ui bigpicture`, `--relaunch`) — every mode reaches its UI, no
crash log, correct version in both the log and the exe's own metadata.

---

## What this replaces, and why

`ethereal-puzzling-mist.md` documented real design work (the original data model's
rationale, UI research, and prior feasibility research) that's genuinely still valid
and is folded into Part 1/Part 2 above. But its Milestone 6 documented "shipped and
verified" work on the `lutris-base` branch, which was discovered to be a from-scratch
fake never actually forked from Lutris — none of those claims are true of the current
`lutris-core` codebase. The doc was never corrected to say so, and it stopped being
updated entirely after 2026-08-04, meaning the entire real `lutris-core` rebuild
(everything in Part 1 above) had no coverage in it at all. That combination — an
actively wrong milestone plus a day-plus of undocumented architecture — is what let
implementation drift from the plan without anyone noticing, and why continuing to
append to the same file wasn't going to fix it. This document is meant to stay a
living status gate instead: update it in the same pass as any architecture change,
not after the fact.
