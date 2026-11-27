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
| Game details and running | `LibraryScreen.kt`, `GameActions.kt` | Version choice, notes editing, saves, history, store links, update actions |
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
| Input and accessibility | Shared components + Compose controls | Initial/restored focus, pad actions, touch target/contrast/text size review |

For each surface, source review must follow a press to the backend, distinguish
loading/empty/unavailable/error states, retain failure feedback, prevent duplicate
mutations, and verify cancellation/reconnection does not retain stale state. The
native screens use the shared library's palette, focus selection, switches and
loading components; Pythia does not fork their implementation.
