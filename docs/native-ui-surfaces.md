# Native UI migration: source review

The owner asked on 2026-11-27 to move Pythia to the shared UI and review user-facing
surfaces. Live application and device testing is paused; this is a code review,
not a claim that the screens work on hardware. The existing Python backend stays.

`python utils/audit_native_ui.py` inventories every schema operation and references
from native screen sources separately from transport/probe references. A call in
that report is evidence of wiring, not evidence that the complete task is usable.
Use this list and SPEC decisions 13–16 before making the native UI the packaged
default or deleting the current front ends.

| Surface | Native source path | Remaining before replacement |
|---|---|---|
| Library | `LibraryScreen.kt` | Artwork, system browsing, sorting, collections, large-library performance review |
| Search and filters | `LibraryScreen.kt` | Platform/store/runner facets, keyboard/pad focus review |
| Game details and running | `LibraryScreen.kt`, `GameActions.kt` | Version choice, saves, history, store links, update actions |
| How it runs | `LibraryScreen.kt`, `GameActions.kt` | Current choice marking, runner setup and full launch-path parity |
| Library folders | `LibraryScreen.kt` | Folder picker, scan cadence, watching and Look/Add workflows |
| Review queue | Not yet wired | Resolve scan decisions, Look proposals, duplicate clusters and merging |
| Activity | `LibraryScreen.kt` | Job detail/log/progress, per-job busy state and event replay review |
| Accounts | `IntegrationsPages.kt` | Disconnect/clear credential route where the backend supports it; sign-in feedback and capability review |
| Plugins | `IntegrationsPages.kt` | Capability pages, repositories, installed tooling, input/mode integrations |
| Devices and pairing | `DevicesPage.kt` | Source review of expiry/reconnect and comparison races; remote library, launch and sync |
| Saves and backups | `DevicesPage.kt` | Per-game save locations, backup outcomes and complete history review |
| Settings and modes | Not yet wired | Manager/agent/service modes, startup, library/data location, UI and log options |
| Error console | Diagnostics place | Semantic event/log presentation, bounded text, copy/export diagnostics |
| Tray and single instance | Existing Python owner | Native window show/hide/close lifecycle, flags still reaching the running process |
| Updater and packaging | Existing Python packages | Bundle native executable, preserve update lanes and auto-start; macOS window support |
| Input and accessibility | Shared components + Compose controls | Initial focus, fallback when an opened game disappears, pad actions, touch target/contrast/text size review |

The native window now gives Library, Activity and Devices primary tabs. Folders,
Accounts, Plugins, Save backups and Diagnostics live in More, with the current
secondary page named below the tabs. This separates frequent tasks from setup
instead of presenting eight equally prominent filter chips as navigation.

Game notes have a separate editor for this computer's record. Cancel discards the
draft; a failed save retains it with the backend's reason. The dialog closes and
the game details refresh only after a successful answer.
The library retains its scroll position and requests focus on the opened game
when returning from its page. Runtime keyboard/pad verification remains open.

For each surface, source review must follow a press to the backend, distinguish
loading/empty/unavailable/error states, retain failure feedback, prevent duplicate
mutations, and verify cancellation/reconnection does not retain stale state. The
native screens use the shared library's palette, focus selection, switches and
loading components; Pythia does not fork their implementation.

Pairing offer ownership is serialized through withdrawal and bound to its
original backend connection. Leaving during startup waits for the backend start
to settle before cleanup. Core coroutine tests cover rapid mode replacement,
reconnection during cleanup, cancelled queued starts and caller cancellation.
These are source/CI contracts; expiry presentation and device pairing still need
runtime review. Transport cancellation is not rendered as an ordinary failure.
First-use pairing explains network access with the existing desktop copy before
any of its three entry actions can start. Cancel makes no mutation; failed or
malformed explanation-state reads and failed consent writes block startup.

Remaining ownership limit: if the IPC connection disappears while the backend
survives, the native client cannot withdraw that connection's offer. It refuses
to redirect cleanup to a new connection, which could stop another client's offer.
Backend-owned per-client lease/expiry cleanup with an offer generation guard is
still required before claiming an offer can never outlive a lost/closed UI.
