# Pythia web UI

The browser-based interface (`pythia --ui web`, rendered by `pythia/ui/webview/app.py` in
QtWebEngine). It is the fast-iteration test surface and the reference for the native desktop
UI: a piece lands and is checked here first. Since 2026-10-14 every page reads
`pythia.ui.backend.Backend` over QWebChannel; nothing is sampled, mocked or invented. A page
whose feature has no backend yet still calls its slot, and draws the "unavailable" answer it
gets back.

Pythia runs on desktop PCs, laptops and Windows handhelds (GPD Win 4 and the like), so the
page has two shells: the desktop shell (menu bar, sidebar, inspector) and the handheld shell
(rail, big targets, arrow-key and D-pad focus movement, bottom rail under 820px). Both draw
from the same model and the same backend.

## Files

| File | What |
|---|---|
| `index.html` | the markup of both shells and the page's own stylesheet (shell, pages, handheld) |
| `tokens.css` | every colour and number the themes, text size, motion and focus ring are made of |
| `components.css` | the shared components: focus ring, notices, dialogs, flashes, state blocks, menus, the error console |
| `components.js` | the component library (`window.UI`) |
| `console.js` | the error console (`window.ErrorConsole`) |
| `app.js` | the model (`adaptGame`), the backend bridge, the pages of both shells, the menu bar |

## The data path (one for every page)

`call(slot, ...args)` resolves to the slot's result. Every result is a status dict: `ok`,
`unavailable` (the feature has no backend yet; `message` says so) or `error` (`message` is the
reason). No bridge, no answer and a thrown exception all become an `error` result, so a page
always has something honest to draw. A slot whose work touches the disk or the network
(`get_game_saves`, `get_save_backups`, `restore_save_copy`) answers `pending` with a request id
at once and runs on a thread of its own (`Backend._answer_later`); its result arrives as the
`asyncResult` signal, and `call()` resolves with it, so a page treats it like any other slot. `loadInto(host, slot, args, draw)` is the loading path:
skeletons, the call, then `draw` on `ok` or the state block for the answer (with Retry on an
error). Older slots that answer with a bare list (`get_all_plugins`, `get_downloader_plugins`,
`find_manager_for_game`) count as good answers.

Failures are logged on both sides through one path: the backend's `_guarded` decorator logs
a slot's exception with its name and redacted arguments and returns an `error`; `_unavailable`
logs the attempt; the page's own errors (`window.onerror`, unhandled rejections,
`console.error`, `console.warn`) go to `Backend.report_web_error` and land in Pythia's logger.
That logger's handler keeps every record as a UI log entry (`get_ui_log`, live via `uiLog`),
which the Activity page's event log and the error console both read.

## Pages and the slots behind them

| Page | Backend |
|---|---|
| Library, game inspector | `games` + `gamesChanged`; `open_path`, `start_onboard` (rescan, add another folder), `rename_game`, `forget_game`, `save_notes`, `find_manager_for_game` / `open_in_manager`; Engine & Compatibility: `get_platform_status` (the engine plugin's own report) and `enable_platform` (confirmed first); `set_engine_kind`, `apply_update` answer unavailable |
| Downloads & intake | `get_intake` + `intakeChanged` (files the watcher saw, scan decisions, the watcher), `start_watch`/`stop_watch` + `downloadDetected`, `forget_detected`; `get_downloader_plugins` for the cards |
| Reconciliation | decisions from `decisionNeeded`/`get_intake`, resolved with `resolveDecision` after a confirm; possible duplicates from `refresh_merge_candidates` + `mergeCandidatesReady` (`reason` "parts" marks part folders of one game), folded with `merge_games` through the danger dialog; "Not the same" is remembered in the page's own storage |
| Systems & sync | the `system_uuid`s in the synced folder, `get_system_info`, `get_app_info`, `rebuild_index`; cross-system table from the entries; "Add system" asks `get_transfer_settings` (unavailable) |
| Launcher entries | `get_launcher_entries` (unavailable, drawn as such) |
| Save backups | `get_save_backups`: the copies of saves the droidtop agent kept when a sync with a handheld would have overwritten saves that differ; "Put back…" is `restore_save_copy` after a confirm (the saves there now are kept first); "Back up now" is `back_up_saves_now` (unavailable) |
| Game inspector > Saves | `get_game_saves`: where the droidtop agent finds the game's saves (the Ludusavi manifest by key or title) and the files there now; "Download the manifest and look" asks again with `fetch` |
| Systems & sync > Handhelds | `get_agent_status`, `get_handhelds`, `pair_with_code` + `pairingFinished`, `show_pairing_code` / `pairing_status` / `cancel_pairing`, `set_primary_handheld`, `set_handheld_launch`, `forget_handheld`, `handheldsChanged`. Without the droidtop agent module each answers unavailable, titled "The droidtop agent module is not installed" |
| Activity & jobs | `get_jobs` + `jobsChanged` (the backend's own operations from call to completion), `clear_finished_jobs`; event log from `get_ui_log` + `uiLog`; `export_diagnostics` |
| Settings > General | `get_system_info`, `get_ui_mode`/`set_ui_mode`, `set_option` (resident watcher: unavailable), `move_app_data` (unavailable) |
| Settings > Library | `get_library_folders`, `get_scan_settings` (unavailable) |
| Settings > Sources … Patchers | `get_capabilities(category)`: the plugins of one category with that category's own state (a source's account, a launcher's presence, an engine plugin's tooling); `set_plugin_enabled`; accounts through `get_account_plugins`, `set_account_credential`, `sync_account`; `get_tools` (unavailable) |
| Settings > Plugins | `get_all_plugins`, `set_plugin_enabled`, `get_plugin_repositories` (unavailable) |
| Settings > Systems & sync | `get_system_info`, `set_sync_folder`, `set_option` (conflict warnings), `get_transfer_settings`, `get_storage_roles` (the last four unavailable) |
| Settings > Interface | theme, text size, startup shell, library views, density and reduce motion are the page's own preferences (localStorage) |
| Settings > Advanced | `get_app_info` (schema version, log file), `set_log_level`, `get_ui_config`, `export_diagnostics`, `rebuild_index`, `reset_identity` (unavailable) |
| Menu bar | File: add/scan/import (`sync_account` of a connected source)/`quit_app`; Library: `sync_all_accounts`, `check_updates` (the F95zone pass); Tools: `rebuild_index`, `get_tools`; Help: `get_app_info`, `open_url` |
| Handheld shell | the same model and slots: home (attention, jobs, recent), library, attention with a per-game review page (rescan, open folder, remove), downloads, systems, settings |

## Error console (testing aid)

Off by default. On with `pythia --ui web --error-console`, or permanently with
`error_console = True` under `[pythia]` in `pythia.conf`. Ctrl+Shift+E, the ⚠ button in the
top bar (desktop) or the ⚠ button beside Add (handheld) toggle it; it docks at the bottom or on
the right and keeps its filters. Each entry has a time, a level (`error`, `warn`, `info`,
`debug`), a source (`python`: Pythia's logger and slot failures; `pythia`: progress lines;
`web`: the page's own errors) and the slot when one is involved. Level and source filters,
Copy (the visible lines as text), Clear (also clears the backend's buffer, `clear_ui_log`).
It shows the same log the Activity page shows; there is no second logging path.

## Tokens (`tokens.css`)

- Colour roles per theme (`html[data-theme]` = `ink` default, `parchment`, `cobalt`): the
  page's own `--page`, `--surface-0..3`, `--border`, `--text-primary/secondary/tertiary`,
  `--accent`, `--positive`, `--warning`, `--critical-token`, `--informational` and their soft
  plates, plus the notice tones: for each of `info`, `caution`, `danger` an `-ink`, a `-plate`
  and an `--on-<tone>`; `--danger-fill`/`--on-danger-fill` are the risky button. Caution
  (amber) and danger (red) hues are locked; a theme only moves their lightness. Info follows
  the accent unless the accent is amber, where info uses neutral ink and the circle-i glyph.
- `--text-scale` (1.0 to 1.5, Settings > Interface > Text size) scales the root font size;
  every font size in `index.html` is in rem. At 1.3 and above `html[data-large-text="1"]`
  stacks the library's two panes and lets rows wrap.
- `--motion-fast` 150ms and `--motion-dialog` 200ms; `prefers-reduced-motion` and
  `html[data-motion="off"]` (Reduce motion) zero both; skeletons and progress bars stop.
- The focus ring: `--ring-outer` 3px in `--focus` over `--ring-inner` 1px in `--focus-inner`,
  keyboard only (`:focus-visible`).

## Components (`components.js`, `window.UI`)

| Component | Rule |
|---|---|
| `notice({tone, title, body, action, details, source, placement})` | A warning or note. Tone `info`, `caution` or `danger`, a glyph per tone (circle-i, triangle, octagon), the tone read first by a screen reader. Title states the condition (40 characters or fewer), body the outcome (90 or fewer), at most one action; the why goes to `details`, opened by **Info**. A notice is state, not dismissable. |
| `confirm({tone, title, body, node, details, cancel, proceed})` | The dialog card. Cancel left and focused; the proceed button has no fill. |
| `confirmDanger({title, body, recommendation, items, details, proceed})` | Steam-shaped: octagon glyph, the outcome, one "We recommend Cancel …" line, the affected items, then **Cancel** (left, focused) and the risky button (right, danger fill). Esc, the scrim and Cancel return false. Every one-way action (remove, fold) goes through it. |
| `prompt({title, value, hint, kind, multiline, save, pick, options})` | Steam's rename dialog: title, field, small Cancel and Save; **Browse** on the left when a native picker exists. Enter saves, Esc cancels. `kind: 'select'` draws a list. Returns the text or null. |
| `infoSheet({title, details, node})` | The Info sheet: details in full with **Copy**, or structured content. |
| `flash(text, tone, {private})` | A corner acknowledgement, past tense, 4 s, appended to Recent (the bell) unless private. |
| `stateBlock({kind, glyph, title, body, actions, details})` | `loading` (skeletons), `empty` (a next action), `unavailable` (circle-i, the backend's reason) or `error` (octagon, the reason, Retry). |
| `fromResult(result, {what, retry, actions})` | A backend result that is not `ok` turned into its state block. |
| `bindSwitch(node, on, onChange, label)` | The page's `.toggle` markup as a real `role=switch`; an unavailable option switches back. |
| `menu(anchor, items)` | The `⋯` menu; Esc and a click outside close it; focus returns to the anchor. |
| `fmt` | dates, times, counts and "12s ago" read the same everywhere. |

## Copy

- Labels are three words or fewer: verbs for actions, nouns for settings.
- Say the outcome, not the mechanism; the why goes in Info. No internal names where a
  person reads (datadir, cache.db, UUID, onboarding): the UI says library folder, index,
  computer, add, review. Exceptions stay where they already were in the page (Reconciliation,
  cache.db under Advanced).
- A feature with no backend says "Not built yet" with the backend's reason and, where one
  exists, the action that works today (Scan a folder, Watch a folder).
- `components.js` warns in the console when a notice title, body or a label is over budget.

## Real-data notes

- Onboarding used to store the folder name as the version when it found no version token
  (102 of 162 scanned games); `preview()` now stores `None`, and the UI shows older records'
  name-as-version as "no version".
- Part folders of one game (Thief of Hearts/Part1 + Part2, Fetish Locator/Week 1 + 3) are
  linked at scan time (`onboarding._sibling_part_entry`); records split before that show in
  Reconciliation as "Parts of one game".

## Checking a change

`G:\dev\coordinator\scratch\pythia-ui\wire-in\shoot.ps1 <steps.json> <out-dir> [--error-console]`
(coordinator scratch) drives this UI offscreen against the isolated test data and saves one
PNG per step. Nothing in the screenshots is staged: the unavailable states, the failed job
and the console entries come from the backend's own answers.
