# Pythia web UI

The browser-based interface (`pythia --ui web`, rendered by `pythia/ui/webview/app.py` in
QtWebEngine). It is the reference for the native desktop UI: every component, token and
behaviour below is what the native UI implements, one for one. It shows only what
`pythia.ui.backend.Backend` reports; nothing is sampled, mocked or invented.

## Files

| File | What |
|---|---|
| `index.html` | the shell: top bar, sidebar, main, flash host |
| `tokens.css` | every colour and number: roles, three themes, type scale, spacing, radii, motion |
| `components.css` | the components' styles, reading tokens only |
| `components.js` | the component library (`window.UI`) |
| `app.js` | the model (`adaptGame`), the backend bridge, jobs, the router and the pages |

## Tokens (`tokens.css`)

- **Colour roles**: `--page`, `--chrome`, `--surface-0..3`, `--border`, `--border-strong`,
  `--text`, `--text-2` (supporting, 4.5:1 or better), `--text-3` (captions), `--accent`,
  `--accent-hover`, `--accent-soft`, `--on-accent`, `--selection`, `--scrim`, `--shadow`,
  `--focus-outer`, `--focus-inner`.
- **Notice tones**: for each of `info`, `caution`, `danger` an `-ink`, a `-plate` and an
  `--on-<tone>`. `--danger-fill` and `--on-danger-fill` are the risky button. The caution
  (amber) and danger (red) hues are locked; a theme may only move their lightness. Info
  follows the accent unless the accent is amber (Ink, Parchment), where info uses neutral
  ink and the circle-i glyph tells it apart.
- **Themes**: `html[data-theme]` is `ink` (default, dark), `parchment` (light) or `cobalt`
  (dark). A theme redefines colour roles only.
- **Type**: `--t-small` 14px, `--t-body` 15px, `--t-row` 16px, `--t-title` 20px,
  `--t-page` 26px, `--t-hero` 32px, all in rem. Nothing that carries information is under
  `--t-small`. `--text-scale` (1.0 to 1.5, Settings > Interface > Text size) scales the root
  font size; at 1.3 and above `html[data-large-text="1"]` lets rows wrap and stacks the
  library's two panes.
- **Spacing**: `--s1` 4, `--s2` 8, `--s3` 12, `--s4` 16, `--s5` 24, `--s6` 32; `--gutter` 24
  (16 under 760px).
- **Sizes**: rows 48px minimum (56 with a body), controls 40px, icon-only targets 44px, a
  thing's plate 64px, the hero plate 96px.
- **Radii**: `--r-crisp` 3px (chips, rows), `--r-panel` 6px, `--r-dialog` 8px.
- **Motion**: `--motion-fast` 150ms, `--motion-dialog` 200ms. `prefers-reduced-motion` and
  `html[data-motion="off"]` (Settings > Interface > Reduce motion) set both to 0.
- **Focus**: one two-tone ring (`--ring-outer` 3px in `--focus-outer` over `--ring-inner`
  1px in `--focus-inner`), keyboard only (`:focus-visible`).

## Components (`components.js`, `window.UI`)

| Component | Rule |
|---|---|
| `notice({tone, title, body, action, details, source, placement})` | A warning or note. Tone `info`, `caution` or `danger`; a glyph per tone (circle-i, triangle, octagon), the tone read first by a screen reader (`role=status`, polite). Title states the condition (40 characters or fewer, never "Warning:"); body the outcome (90 or fewer); at most one action (3 words or fewer); the why and exact terms go in `details`, opened by **Info**. `placement` is `row` or `page` (one page banner at most). A notice is state: it is not dismissable and goes away when the condition clears. |
| `confirm({tone, title, body, details, cancel, proceed})` | The dialog card with an info or caution glyph. Cancel is on the left and has focus; the proceed button has no fill. Returns a boolean. |
| `confirmDanger({title, body, recommendation, items, details, cancel, proceed})` | Steam-shaped: octagon glyph and title, the outcome, one "We recommend <button>." line, the affected items, Details, then **Cancel** (left, focused) and the risky button (right, in the danger fill). No second-press arm: a mashed Enter cancels. Esc, the scrim and Cancel all return false. Every one-way action (remove, fold) goes through it and names what crosses over. |
| `prompt({title, value, hint, kind, multiline, save, pick})` | Steam's rename dialog: a title line, the field, small **Cancel** and **Save** on the right (and a **Browse** on the left when a picker exists). Enter saves (Ctrl+Enter when multiline), Esc cancels and keeps the old value. The placeholder is a noun phrase; the field holds no instructions. Returns the text or null. |
| `infoSheet({title, details})` | The Info sheet: details in full, with **Copy**. |
| `flash(text, tone, {private})` | A corner acknowledgement, past tense, no period ("Saved"); at least 4 s; never carries an action. Appended to Recent (the bell) unless `private`. |
| `stateBlock({kind, glyph, title, body, actions})` | `loading` (three skeleton rows, still under reduced motion), `empty` (title 32 characters or fewer, one line, a next action) or an error. Never a bare title, never an endless spinner. |
| `pageHeader({plate, title, byline, chips})` | The page's own header: a 64px plate when the page is about a thing (a glyph, never stand-in art), the title, one byline, chips. |
| `buttonBar([...])` | 0 to 3 buttons plus `⋯`. At most one primary. Destructive actions live in `⋯` after a rule. A disabled button says its reason in full ink. |
| `row({plate, title, line2, value, chips, trailing, run})` | A place (chevron), a setting (value or control) or a fact. A row that does nothing is not drawn. One state chip at most; caution and danger chips always carry a glyph. |
| `facts([[k, v, mono]])` | Key-value facts; empty values are not drawn. |
| `FilterModel({facets, sorts, text})` | One query per list: text, facets (shown only with the values the list holds and how many each has), one sort that flips direction on the second pick, blanks last both ways, and a "12 of 80" line with **Clear** while anything is active. Facet choices and the sort are remembered per list. |
| `menu(anchor, items)` | The `⋯` menu; Esc and a click outside close it; focus returns to the anchor. |

Every dialog traps Tab, closes on Esc and returns focus to what opened it. Every control is
reachable by keyboard; the bell, search (Ctrl+K) and settings sit in the top bar.

## Copy

- Labels are three words or fewer: verbs for actions, nouns for settings.
- Say the outcome, not the mechanism; the why goes in Info. No internal names where a person
  reads (datadir, cache.db, UUID, onboarding, reconciliation): the UI says library folder,
  index, computer id, add, review.
- Glossary: **Game source** (Steam, GOG, itch.io, Epic, F95zone), **Engine**, **Computer**
  (a system in the synced folder), **Review** (decisions and possible duplicates),
  **Needs attention**, **Owned**, **Installed**, **Turned off**, **Official** (built into
  Pythia) / **Unofficial** (a Python package or drop-in folder), **Fold** (merge one record
  into another).
- `components.js` warns in the console when a notice title, body or a label is over budget.

## Pages and the data behind them

| Page | Backend |
|---|---|
| Home | `games` (hero = the last opened game, else the newest), attention states derived from each record, `decisionNeeded`, `find_merge_candidates` |
| Library / game page | `games` with every system's entry; `open_path`, `start_onboard` (rescan), `rename_game`, `forget_game`, `save_notes`, `find_manager_for_game` / `open_in_manager` |
| Review | `decisionNeeded` + `resolveDecision`; `refresh_merge_candidates` + `merge_games` ("Not the same" is remembered in the page's own storage) |
| Downloads | jobs = the backend's operations from their call to their completion signal (`scanComplete`, `onboardComplete`, `accountSyncComplete`); `start_watch`/`stop_watch` + `downloadDetected` |
| Computers | the `system_uuid`s in the synced folder, `get_system_info`. Pairing, saves and versions per device are **not built**; the page says so in an info notice and shows nothing for them. |
| Plugins | `get_all_plugins`, `set_plugin_enabled`, `get_account_plugins`, `set_account_credential`, `sync_account` |
| Settings | `get_system_info`, `get_app_info`, `get_ui_mode`/`set_ui_mode`, `rebuild_index`; theme, text size, motion and library view are the page's own preferences |

A real-data note: when onboarding finds no version token it stores the folder name as the
version. The UI treats a version equal to the game's name as "no version number".

## Checking a change

`G:\dev\coordinator\scratch\pythia-ui\shoot.py` (coordinator scratch) drives this UI offscreen
against an isolated data folder and saves one PNG per step; run it before and after a change
and compare. Nothing in the screenshots is staged.
