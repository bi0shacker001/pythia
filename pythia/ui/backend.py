"""
Qt backend adapter -- the *only* module either UI front end (bigpicture or desktop)
talks to. Wraps pythia.onboarding/scanning/datadir with Qt Signals and runs long
operations on a background thread, mirroring the log-callback pattern
renpy_patch.patch's own GUI already uses, translated to Qt's signal/slot mechanism.

Note on `decisionNeeded`: onboarding.onboard()'s decision-resolution is per-path and
synchronous. For a single onboard() (start_onboard), this adapter blocks the worker
thread on a real ambiguous match and waits for resolveDecision() -- exactly mirroring
cli.py's interactive `onboard` command. For a bulk scan (start_scan),
scanning.scan_library() itself has no pause points (matching how `pythia scan` already
behaves non-interactively) -- ambiguous results are instead collected and surfaced as a
review queue *after* the scan completes, one decisionNeeded at a time, each resolvable
the same way (resolving one just re-onboards that specific path with the corrected
decision).
"""

import json
import logging
import re
import threading
import uuid
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from pythia import datadir, downloads, onboarding, reconciliation, scanning
from pythia.database import games as games_db
from pythia.identity import get_system_uuid


class _UiLogHandler(logging.Handler):
    """Attached to the root logger (2026-08-06, user: "we need console logging
    back" -- every module's own `logging.getLogger(__name__)` call already
    exists and already writes to the real log file pythia.util.log sets up
    (get_log_contents()'s own docstring even says it was meant to back "the
    Qt UI's own log window once it exists" -- it never did, for the web UI).
    This re-emits every record that reaches the root logger as a real
    logLine signal, so the app's actual internal logging becomes visible in
    the UI itself instead of only a terminal the user isn't necessarily
    running from."""

    def __init__(self, emit_fn):
        super().__init__()
        self._emit_fn = emit_fn
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record):
        try:
            self._emit_fn(self.format(record))
        except Exception:
            pass


def _normalize_path(path_str: str) -> str:
    """QML folder dialogs hand back file:// URLs, not plain paths -- accept either."""
    if path_str.startswith("file:"):
        return QUrl(path_str).toLocalFile()
    return path_str


def _redact_diagnostic_log(text: str) -> str:
    """Best-effort removal of common credentials from exported log text."""
    patterns = (
        (r"(?im)\b(authorization\s*:\s*)(?:bearer|token|basic)\s+[^\s,;]+", r"\1[REDACTED]"),
        (r"(?im)\b(cookie|set-cookie)(\s*:\s*)[^\r\n]+", r"\1\2[REDACTED]"),
        (r'(?i)(["\'](?:password|passwd|secret|access_token|refresh_token|api_key|apikey|token)["\']\s*[:=]\s*["\'])[^"\']+(["\'])', r"\1[REDACTED]\2"),
        (r"(?i)([?&](?:password|secret|access_token|refresh_token|api_key|apikey|token)=)[^&#\s]+", r"\1[REDACTED]"),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def _optional_plugin_zip_dir() -> Path:
    """Where optional plugins (scripts/build_optional_plugins.py's committed
    output) actually live. Same resolution trick registry._builtin_zip_dir()
    uses -- resolve relative to the real, currently-running pythia.plugins
    package location, which works unchanged whether frozen or run from
    source -- with a dev-mode fallback to the not-yet-packaging-bundled
    source location (packaging/optional-plugins/) since the Windows/Linux
    .spec files don't ship pythia/plugins/optional/ yet."""
    import pythia.plugins as plugins_pkg

    shipped = Path(plugins_pkg.__file__).parent / "optional"
    if shipped.is_dir():
        return shipped
    repo_root = Path(plugins_pkg.__file__).resolve().parents[2]
    return repo_root / "packaging" / "optional-plugins"


def _jsonable(obj):
    """Strip non-JSON-safe types (just Path, in practice) so results are safe to hand
    to QML or serialize -- everything else datadir/onboarding produce is already
    plain str/int/bool/dict/list."""
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    return obj


class Backend(QObject):
    logLine = Signal(str)
    decisionNeeded = Signal(object)  # a preview-shaped dict: {"path", "name", "candidates", ...}
    onboardComplete = Signal(object)
    scanComplete = Signal(object)
    gamesChanged = Signal()
    downloadDetected = Signal(str)  # a new file's path, from a watched Downloads folder
    mergeCandidatesReady = Signal(object)
    mergeCandidatesFailed = Signal(str)
    mergeComplete = Signal(object)
    accountSyncComplete = Signal(str, object)
    updateCheckComplete = Signal(object)
    maskedLinkResolved = Signal(object)
    f95LoginComplete = Signal(object)
    downloadHostLoginComplete = Signal(object)
    automatedDownloadProgress = Signal(object)
    engineEnableComplete = Signal(object)
    managerOpenComplete = Signal(object)
    workerError = Signal(str)  # a background operation crashed -- see _run_worker()
    syncStatusChanged = Signal(object)  # a real SyncService status/lifecycle event, see SyncBridge's on_status
    syncTransferOfferReceived = Signal(object)  # {"offer_id","offer","peer_system_uuid"} -- never auto-accepted
    syncTransferComplete = Signal(object)
    syncMetadataSynced = Signal(object)
    syncStagingComplete = Signal(object)

    def __init__(self):
        super().__init__()
        self._decision_event = threading.Event()
        self._decision_result = None
        self._awaiting_live_decision = False
        self._watch_stop_event = None
        self._mask_resolvers = []
        self._f95_login_dialogs = []
        self._download_host_login_dialogs = []
        self._download_cancel_events: dict[str, threading.Event] = {}
        self._games = []
        self.refresh_games()
        self._ui_log_handler = _UiLogHandler(lambda line: self.logLine.emit(line))
        self._ui_log_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self._ui_log_handler)

        # Real, opt-in core sync (2026-08-08, "optional core sync service and
        # native device/transfer workflow"). Constructing SyncBridge has zero
        # side effects -- see its own docstring -- so this is safe to do
        # unconditionally at Backend construction, the same way every other
        # dormant-until-enabled subsystem in this app is wired.
        from pythia.ui.sync_bridge import SyncBridge

        self._sync_bridge = SyncBridge(
            on_status=lambda event: self.syncStatusChanged.emit(_jsonable(event)),
            on_transfer_offer=self._on_sync_transfer_offer,
            on_metadata_requested=self._sync_mirrored_items_for_peer,
            on_metadata_reconciled=self._on_sync_metadata_reconciled,
            on_metadata_pull_requested=self._sync_local_metadata_items,
        )

    @Slot(result=str)
    def get_log_contents(self):
        """Backfill for the Event log on startup/page-open -- the real,
        persisted log file (pythia.util.log.LOG_FILENAME), not just what's
        happened since this process launched."""
        from pythia.util.log import get_log_contents

        return get_log_contents()

    def _run_worker(self, target, *args):
        """Every background operation goes through this instead of calling
        threading.Thread(...) directly. An uncaught exception in a bare worker
        thread used to just kill the thread silently -- Python prints a
        traceback to stderr, which is real, but nothing the UI is watching
        (stdout/stderr isn't visible to someone just using the app, only to
        whoever happens to be tailing the process's own log). Confirmed for
        real (2026-08-06): a real AttributeError in scan_library() crashed the
        scan thread with zero visible effect -- no toast, no error, the game
        just silently never appeared, and the user had no way to know why.
        Every worker now runs inside this wrapper, which logs (for whoever IS
        watching stderr) and emits a real `workerError` signal the UI can
        show as a toast, instead of failing invisibly."""

        def _wrapped():
            try:
                target(*args)
            except Exception as e:
                import traceback

                traceback.print_exc()
                self.workerError.emit(f"{target.__name__.lstrip('_')}: {e}")

        threading.Thread(target=_wrapped, daemon=True).start()

    # ---- games list -----------------------------------------------------------
    def refresh_games(self):
        games = datadir.list_known_games()
        self._games = [
            {"game_id": gid, "entries": _jsonable(entries)} for gid, entries in sorted(games.items())
        ]
        self.gamesChanged.emit()

    @Property("QVariantList", notify=gamesChanged)
    def games(self):
        return self._games

    # ---- native folder picker ---------------------------------------------------
    @Slot(result=str)
    def pick_folder(self):
        """A real native folder dialog for the web UI -- Chromium/QtWebEngine has
        no way to hand back an absolute OS path from an <input type=file>, so this
        exists purely to give the JS side one, the same way the QML front end's own
        FolderDialog does. Empty string means cancelled, not an error."""
        from PySide6.QtWidgets import QFileDialog

        return QFileDialog.getExistingDirectory(None, "Select a folder") or ""

    @Slot(result=str)
    def pick_diagnostics_file(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            None, "Export diagnostics", "pythia-diagnostics.json", "JSON (*.json)"
        )
        return path or ""

    @Slot(result=str)
    def pick_entries_export_file(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            None, "Export selected games", "pythia-selected-games.json", "JSON (*.json)"
        )
        return path or ""

    @Slot(result=str)
    def pick_f95_database_file(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            None, "Import F95Checker database", "", "SQLite databases (*.sqlite *.sqlite3 *.db);;All files (*)"
        )
        return path or ""

    # ---- plugin package install --------------------------------------------------
    @Slot(result=str)
    def pick_plugin_zip(self):
        """Native file dialog for choosing a plugin .zip package -- see
        pythia.plugins.packaging's own docstring for the real zip format
        (plugin.json + plugin.py + type-<category>.py, loaded via zipimport,
        never extracted)."""
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(None, "Select a plugin package", "", "Pythia plugin (*.zip)")
        return path or ""

    @Slot(str, result="QVariant")
    def install_plugin_zip(self, zip_path: str):
        import pythia.plugins.registry as registry
        from pythia.plugins import packaging

        try:
            manifest = packaging.read_manifest(zip_path)
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        if registry.is_protected(manifest["id"]):
            return {"status": "error", "message": "Built-in plugin IDs cannot be replaced."}
        try:
            result = packaging.install_plugin_zip(zip_path)
        except Exception as exc:
            return {"status": "error", "message": f"Could not install plugin: {exc}"}
        if not isinstance(result, dict):
            return {"status": "error", "message": "Plugin installer returned an invalid result."}
        if result.get("status") == "ok":
            # A freshly-installed (or updated -- same id, new zip) package
            # needs to actually show up without a restart, and an update
            # must not keep serving the OLD cached instance/class silently.
            try:
                registry.invalidate_installed_zip_cache(result.get("id") or manifest["id"])
            except Exception as exc:
                return {**result, "status": "partial", "message": f"The plugin was installed, but Pythia could not refresh its plugin cache: {exc}"}
        return result

    @Slot(str, result="QVariant")
    def uninstall_plugin_zip(self, plugin_id: str):
        import pythia.plugins.registry as registry
        from pythia.plugins import packaging

        try:
            result = packaging.uninstall_plugin(plugin_id)
        except Exception as exc:
            return {"status": "error", "message": f"Could not remove plugin: {exc}"}
        if not isinstance(result, dict):
            return {"status": "error", "message": "Plugin uninstaller returned an invalid result."}
        if result.get("status") == "ok":
            try:
                registry.invalidate_installed_zip_cache(plugin_id)
            except Exception as exc:
                return {**result, "status": "partial", "message": f"The plugin was removed, but Pythia could not refresh its plugin cache: {exc}"}
        return result

    # ---- optional plugins (2026-08-08, download-automator hardening pass --------
    # a real, foundational gap this closes: scripts/build_optional_plugins.py
    # already built a real, valid zip at
    # packaging/optional-plugins/pythia.downloader.automator.zip, but nothing
    # anywhere discovered or installed from it -- the plugin could never
    # actually become enabled through any real user action, making "optional
    # dependency" meaningless in practice. Deliberately NOT routed through
    # registry._builtin_entries() -- an optional plugin is never protected
    # and never auto-loaded; it only becomes a real, registry-visible package
    # once install_optional_plugin() below calls the exact same
    # packaging.install_plugin_zip() any third-party zip goes through. -------
    @Slot(result="QVariant")
    def list_optional_plugins(self):
        """Every optional plugin this build ships, cross-referenced against
        the real registry so the UI shows real installed/enabled state, never
        guessed. A single unreadable optional package is reported as an
        error entry, not allowed to blank the whole list."""
        from pythia.plugins import packaging, registry

        zip_dir = _optional_plugin_zip_dir()
        known = registry.all_plugin_entries()
        enabled_ids = registry.get_enabled_plugin_ids()
        result = []
        for zip_path in sorted(zip_dir.glob("*.zip")) if zip_dir.is_dir() else []:
            try:
                manifest = packaging.read_manifest(zip_path)
            except Exception as e:
                result.append({"id": zip_path.stem, "display_name": zip_path.stem, "status": "error", "message": str(e)})
                continue
            plugin_id = manifest["id"]
            installed_entry = known.get(plugin_id)
            result.append(
                {
                    "id": plugin_id,
                    "display_name": manifest.get("display_name", plugin_id),
                    "version": manifest.get("version", "0.0.0"),
                    "categories": manifest.get("categories", []),
                    "installed": installed_entry is not None,
                    "installed_version": installed_entry.get("version") if installed_entry else None,
                    "enabled": installed_entry is not None and (enabled_ids is None or plugin_id in enabled_ids),
                }
            )
        return _jsonable(result)

    @Slot(str, result="QVariant")
    def install_optional_plugin(self, plugin_id: str):
        import pythia.plugins.registry as registry
        from pythia.plugins import packaging

        zip_dir = _optional_plugin_zip_dir()
        match = None
        for zip_path in zip_dir.glob("*.zip") if zip_dir.is_dir() else []:
            try:
                manifest = packaging.read_manifest(zip_path)
            except Exception:
                continue
            if manifest.get("id") == plugin_id:
                match = zip_path
                break
        if match is None:
            return {"status": "error", "message": f"No optional plugin package found for {plugin_id!r}."}
        try:
            result = packaging.install_plugin_zip(str(match))
        except Exception as exc:
            return {"status": "error", "message": f"Could not install optional plugin: {exc}"}
        if not isinstance(result, dict):
            return {"status": "error", "message": "Optional plugin installer returned an invalid result."}
        if result.get("status") == "ok":
            try:
                registry.invalidate_installed_zip_cache(result.get("id") or plugin_id)
            except Exception as exc:
                return {**result, "status": "partial", "message": f"The optional plugin was installed, but Pythia could not refresh its plugin cache: {exc}"}
        return result

    # ---- remote plugin index (2026-08-07, GitHub Pages-hosted, decoupled ----------
    # from the app's own release cycle -- see pythia.plugins.remote_index's own
    # docstring) -------------------------------------------------------------------
    @Slot(result="QVariant")
    def check_plugin_index(self):
        """Every plugin the remote index currently lists, plus whether it's
        new (not installed at all) or an update (installed, different
        version) relative to what's already known locally. Returns
        {"status": "ok", "plugins": [...]} or {"status": "error", "message"}
        -- a real network failure (or the index not being reachable yet,
        e.g. this repo is still private) must not crash the Plugins page."""
        from pythia.plugins import registry
        from pythia.plugins.remote_index import fetch_index

        try:
            remote = fetch_index()
        except Exception as e:
            return {"status": "error", "message": str(e)}
        if not isinstance(remote, list):
            return {"status": "error", "message": "Remote plugin index returned an invalid catalog."}
        try:
            known = registry.all_plugin_entries()
        except Exception as exc:
            return {"status": "error", "message": f"Could not inspect installed plugins: {exc}"}
        if not isinstance(known, dict):
            return {"status": "error", "message": "Installed plugin registry returned an invalid catalog."}
        result = []
        for entry in remote:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
                return {"status": "error", "message": "Remote plugin index contained an invalid entry."}
            if not isinstance(entry.get("version"), str) or not entry["version"]:
                return {"status": "error", "message": f"Remote plugin {entry['id']!r} has no valid version."}
            local = known.get(entry["id"])
            if local is None:
                relation = "new"
            elif not isinstance(local, dict) or not isinstance(local.get("version"), str):
                return {"status": "error", "message": f"Installed plugin {entry['id']!r} has invalid metadata."}
            elif local["version"] != entry["version"]:
                relation = "update"
            else:
                relation = "current"
            result.append({**entry, "relation": relation})
        return {"status": "ok", "plugins": result}

    @Slot(str, result="QVariant")
    def install_plugin_from_index(self, plugin_id: str):
        import pythia.plugins.registry as registry
        from pythia.plugins.remote_index import fetch_index, install_from_index

        try:
            remote = fetch_index()
        except Exception as e:
            return {"status": "error", "message": str(e)}
        if not isinstance(remote, list):
            return {"status": "error", "message": "Remote plugin index returned an invalid catalog."}
        invalid = next(
            (p for p in remote if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not p["id"]),
            None,
        )
        if invalid is not None:
            return {"status": "error", "message": "Remote plugin index contained an invalid entry."}
        entry = next((p for p in remote if p["id"] == plugin_id), None)
        if entry is None:
            return {"status": "error", "message": f"{plugin_id!r} isn't in the remote index."}

        try:
            result = install_from_index(entry)
        except Exception as exc:
            return {"status": "error", "message": f"Could not install plugin from the index: {exc}"}
        if not isinstance(result, dict):
            return {"status": "error", "message": "Remote plugin installer returned an invalid result."}
        if result.get("status") == "ok":
            try:
                registry.invalidate_installed_zip_cache(result.get("id") or plugin_id)
            except Exception as exc:
                return {
                    **result,
                    "status": "partial",
                    "message": f"The plugin was installed, but Pythia could not refresh its plugin cache: {exc}",
                }
        return result

    # ---- app self-update check (2026-08-07, check only, no auto-apply --------
    # see pythia.util.update_check's own docstring for why) --------------------
    @Slot(result="QVariant")
    def check_for_app_update(self):
        from pythia.util.update_check import check_for_update_strict

        try:
            update = check_for_update_strict()
        except Exception as e:
            return {"status": "error", "message": str(e)}
        return {"status": "ok", "update": update}  # update is None if already current

    # ---- onboard a single path (interactive) -----------------------------------
    @Slot(str)
    def start_onboard(self, path_str: str):
        path_str = _normalize_path(path_str)
        self._run_worker(self._onboard_worker, path_str)

    def _onboard_worker(self, path_str: str):
        pv = onboarding.preview(path_str)
        if pv["status"] != "ok":
            self.logLine.emit(f"error: {pv['message']}")
            self.onboardComplete.emit(_jsonable(pv))
            return

        decision = None
        if pv["candidates"]["certain"] is None and pv["candidates"]["suggested"]:
            decision = self._ask_live(pv)

        self.logLine.emit(f"Onboarding '{pv['path']}' ...")
        result = onboarding.onboard(path_str, decision=decision)
        self.refresh_games()
        self.onboardComplete.emit(_jsonable(result))

    # ---- scan a library (automatic; ambiguous results reviewed afterward) -----
    @Slot(str)
    def start_scan(self, path_str: str):
        path_str = _normalize_path(path_str)
        self._run_worker(self._scan_worker, path_str)

    def _scan_worker(self, path_str: str):
        self.logLine.emit(f"Scanning '{path_str}' ...")
        results = scanning.scan_library(path_str)
        for r in results:
            if r["status"] == "ok":
                self.logLine.emit(f"  -> game_id {r['game_id']}")
            else:
                self.logLine.emit(f"  error: {r['message']}")
        self.refresh_games()
        self.scanComplete.emit(_jsonable(results))

        for r in results:
            if r["status"] == "ok" and r["candidates"]["suggested"]:
                # Normalized to the same {"path", "name", "candidates"} shape
                # _ask_live's preview dict uses -- onboard()'s own result dict has
                # "path" meaning the *datadir JSON file*, not the game folder
                # (game_path), so it can't be forwarded as-is here.
                self.decisionNeeded.emit(
                    _jsonable({"path": r["game_path"], "name": r["name"], "candidates": r["candidates"]})
                )

    # ---- decision resolution ---------------------------------------------------
    def _ask_live(self, preview: dict):
        """Block the calling (background) thread until the UI resolves this decision."""
        self._decision_event.clear()
        self._decision_result = None
        self._awaiting_live_decision = True
        self.decisionNeeded.emit(_jsonable(preview))
        self._decision_event.wait()
        self._awaiting_live_decision = False
        return self._decision_result

    @Slot(str, "QVariant")
    def resolveDecision(self, path_str: str, decision):
        # Assumes at most one operation (a single onboard, or a scan's post-review
        # queue) is being driven through the UI at a time -- reasonable for a single
        # window with one action in flight, same assumption the old tkinter GUI made
        # by disabling its Start button mid-run. Not safe for concurrent operations.
        decision = dict(decision) if decision else None
        if self._awaiting_live_decision:
            self._decision_result = decision
            self._decision_event.set()
            return
        # No live onboard is waiting -- this is a post-scan review item. Resolving it
        # re-onboards that path directly with the corrected decision.
        self._run_worker(self._reonboard_worker, path_str, decision)

    def _reonboard_worker(self, path_str, decision):
        result = onboarding.onboard(path_str, decision=decision)
        self.refresh_games()
        self.onboardComplete.emit(_jsonable(result))

    # ---- download watching -----------------------------------------------------
    @Slot(str, str, result="QVariant")
    def start_watch(self, command_str: str, watch_dir_str: str):
        """Launch `command_str` (a single executable path -- no argument splitting
        yet) and watch `watch_dir_str` for new files, emitting downloadDetected for
        each. See downloads.launch_and_watch; this doesn't move or import anything
        itself, that's a separate start_onboard() call once the user picks one."""
        watch_dir_str = _normalize_path(watch_dir_str)
        self._watch_stop_event = threading.Event()
        threading.Thread(
            target=self._watch_worker, args=(command_str, watch_dir_str, self._watch_stop_event), daemon=True
        ).start()

    def _watch_worker(self, command_str, watch_dir_str, stop_event):
        try:
            downloads.launch_and_watch(
                [command_str],
                Path(watch_dir_str),
                lambda p: self.downloadDetected.emit(str(p)),
                stop_event=stop_event,
            )
        except OSError as e:
            self.logLine.emit(f"Could not launch '{command_str}': {e}")

    @Slot()
    def stop_watch(self):
        if self._watch_stop_event is not None:
            self._watch_stop_event.set()

    # ---- reconciliation ---------------------------------------------------------
    @Slot()
    def refresh_merge_candidates(self):
        self._run_worker(self._merge_candidates_worker)

    def _merge_candidates_worker(self):
        try:
            candidates = reconciliation.find_merge_candidates()
            if not isinstance(candidates, list):
                raise TypeError("candidate discovery returned a non-list result")
        except Exception as exc:
            self.mergeCandidatesFailed.emit(f"Could not find duplicate games: {exc}")
            return
        self.mergeCandidatesReady.emit(_jsonable(candidates))

    @Slot(str, str)
    def merge_games(self, surviving_id: str, retired_id: str):
        self._run_worker(self._merge_worker, surviving_id, retired_id)

    def _merge_worker(self, surviving_id, retired_id):
        try:
            result = reconciliation.merge(surviving_id, retired_id)
            if not isinstance(result, dict):
                raise TypeError("merge returned an invalid result")
        except Exception as exc:
            result = {"status": "error", "game_id": surviving_id, "retired_game_id": retired_id, "message": f"Merge failed: {exc}"}
            self.mergeComplete.emit(result)
            return
        result = dict(result)
        result.setdefault("game_id", surviving_id)
        result.setdefault("retired_game_id", retired_id)
        if result.get("status") == "ok":
            try:
                self.refresh_games()
            except Exception as exc:
                self.logLine.emit(f"Games were merged but the library could not refresh: {exc}")
        self.mergeComplete.emit(_jsonable(result))

    # ---- notes ------------------------------------------------------------------
    @Slot(str, str, result="QVariant")
    def save_notes(self, game_id: str, text: str):
        """Update this system's own entry for `game_id`'s free-text notes field
        (part of the schema since Milestone 3). No-op if this system doesn't own
        that game_id -- notes are per-system-written, same as everything else."""
        uuid = get_system_uuid()
        for entry in datadir.list_all_entries():
            if entry.get("system_uuid") == uuid and entry.get("game_id") == game_id:
                entry["notes"] = text
                try:
                    games_db.save_entry_and_sync_cache(entry)
                except Exception as exc:
                    return {"status": "error", "message": f"Could not save notes: {exc}"}
                try:
                    self.refresh_games()
                except Exception as exc:
                    self.logLine.emit(f"Notes were saved but the library could not refresh: {exc}")
                return {"status": "ok", "message": "Notes saved."}
        return {"status": "error", "message": "This game has no writable entry on this system."}

    def _writable_entry(self, game_id: str):
        return next(
            (
                entry
                for entry in datadir.list_all_entries()
                if entry.get("system_uuid") == get_system_uuid() and entry.get("game_id") == game_id
            ),
            None,
        )

    @Slot(str, str, result="QVariant")
    def rename_game(self, game_id: str, canonical_name: str):
        name = canonical_name.strip()
        if not name:
            return {"status": "error", "message": "A game name cannot be empty."}
        entry = self._writable_entry(game_id)
        if entry is None:
            return {"status": "error", "message": "This game has no writable entry on this system."}
        old_name = entry.get("canonical_name") or game_id
        if old_name == name:
            return {"status": "ok", "message": "The name was already current."}
        entry["canonical_name"] = name
        datadir.add_timeline_event(entry, "game_renamed", f"{old_name} -> {name}")
        try:
            games_db.save_entry_and_sync_cache(entry)
        except Exception as exc:
            return {"status": "error", "message": f"Could not rename the game: {exc}"}
        try:
            self.refresh_games()
        except Exception as exc:
            self.logLine.emit(f"The game was renamed but the library could not refresh: {exc}")
        return {"status": "ok", "message": f"Renamed {old_name} to {name}."}

    @Slot(str, str, result="QVariant")
    def set_engine_override(self, game_id: str, engine_kind: str):
        """Store a manual engine identification independently of library source."""
        allowed = {""} | {item["kind"] for item in self.get_engine_options()}
        if engine_kind not in allowed:
            return {"status": "error", "message": f"Unknown engine: {engine_kind!r}."}
        entry = self._writable_entry(game_id)
        if entry is None:
            return {"status": "error", "message": "This game has no writable entry on this system."}
        if engine_kind:
            entry["engine_override"] = engine_kind
        else:
            entry.pop("engine_override", None)
        datadir.add_timeline_event(entry, "engine_corrected", engine_kind or "automatic detection")
        try:
            games_db.save_entry_and_sync_cache(entry)
        except Exception as exc:
            return {"status": "error", "message": f"Could not update engine identification: {exc}"}
        try:
            self.refresh_games()
        except Exception as exc:
            self.logLine.emit(f"Engine identification was saved but the library could not refresh: {exc}")
        return {"status": "ok", "message": "Engine identification updated."}

    @Slot(result="QVariant")
    def get_engine_options(self):
        """Real engine kinds exposed by enabled plugin classes, including packages
        such as RPG Maker which contribute several independently-detected kinds."""
        from pythia.plugins import engine as engine_plugins

        options = {}
        for plugin in engine_plugins.enabled_plugins():
            kind = getattr(plugin, "kind", None)
            if kind and kind != "base":
                options[kind] = {"kind": kind, "name": plugin.display_name, "plugin_id": plugin.id}
        return sorted(options.values(), key=lambda item: item["name"].casefold())

    def _engine_for_game(self, game_id: str):
        from pythia.plugins import engine as engine_plugins

        entry = self._writable_entry(game_id)
        if entry is None:
            return entry, None, None, None
        variant = next(
            (v for group in entry.get("versions", []) for v in group.get("variants", []) if v.get("path")),
            None,
        )
        if variant is None:
            return entry, None, None, None
        path = Path(variant["path"])
        override = entry.get("engine_override")
        if override:
            plugin = next((p for p in engine_plugins.enabled_plugins() if getattr(p, "kind", None) == override), None)
            return entry, variant, plugin, {"kind": override, "manual": True}
        current_kind = (variant.get("source") or {}).get("kind")
        plugin, detection = engine_plugins.identify(path, current_kind=current_kind)
        return entry, variant, plugin, detection

    @Slot(str, result="QVariant")
    def get_engine_status(self, game_id: str):
        """Live engine detection, capability, and per-platform readiness for UI."""
        from pythia.plugins.context import PluginContext

        entry, variant, plugin, detection = self._engine_for_game(game_id)
        if entry is None:
            return {"status": "error", "message": "This game has no writable entry on this system."}
        if variant is None:
            return {"status": "error", "message": "This game has no installed path on this system."}
        if plugin is None:
            return {"status": "unidentified", "message": "No enabled engine plugin matched this install."}
        path = Path(variant["path"])
        try:
            capabilities = plugin.capabilities(PluginContext(log=self.logLine.emit))
            platforms = plugin.platform_status(path, PluginContext(log=self.logLine.emit))
        except Exception as exc:
            return {"status": "error", "message": f"{plugin.display_name} could not inspect this install: {exc}"}
        return {
            "status": "ok",
            "plugin_id": plugin.id,
            "plugin_name": plugin.display_name,
            "kind": (detection or {}).get("kind", getattr(plugin, "kind", "unknown")),
            "manual": bool((detection or {}).get("manual")),
            "capabilities": _jsonable(capabilities),
            "platforms": _jsonable(platforms),
        }

    @Slot(str, str)
    def enable_engine_platform(self, game_id: str, platform: str):
        self._run_worker(self._enable_engine_platform_worker, game_id, platform)

    def _enable_engine_platform_worker(self, game_id: str, platform: str):
        from pythia.plugins.context import PluginContext

        _entry, variant, plugin, _detection = self._engine_for_game(game_id)
        if variant is None or plugin is None:
            self.engineEnableComplete.emit({"status": "error", "game_id": game_id, "message": "No engine plugin and installed path are available."})
            return
        try:
            result = plugin.enable(Path(variant["path"]), [platform], PluginContext(log=self.logLine.emit))
            summary = result.get("summary") if isinstance(result, dict) else str(result)
            self.engineEnableComplete.emit({"status": "ok", "game_id": game_id, "platform": platform, "message": summary or "Engine enablement completed."})
        except Exception as exc:
            self.engineEnableComplete.emit({"status": "error", "game_id": game_id, "platform": platform, "message": str(exc)})

    @Slot(str, result="QVariant")
    def dismiss_updates(self, game_id: str):
        entry = self._writable_entry(game_id)
        if entry is None:
            return {"status": "error", "message": "This game has no writable entry on this system."}
        count = len(entry.get("available_updates") or [])
        entry["available_updates"] = []
        datadir.add_timeline_event(entry, "updates_dismissed", f"Dismissed {count} observation(s)")
        try:
            games_db.save_entry_and_sync_cache(entry)
        except Exception as exc:
            return {"status": "error", "message": f"Could not dismiss update observations: {exc}"}
        try:
            self.refresh_games()
        except Exception as exc:
            self.logLine.emit(f"Update observations were dismissed but the library could not refresh: {exc}")
        return {"status": "ok", "dismissed": count, "message": f"Dismissed {count} update observation(s)."}

    @Slot()
    def quit_app(self):
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.quit()

    @Slot(str, result="QVariant")
    def resolve_f95_masked_link(self, masked_url: str):
        """Open the authenticated interactive resolver used by F95Checker."""
        from pythia import credentials
        from pythia.ui.f95_mask_resolver import F95MaskResolver

        try:
            raw = credentials.get_credential("pythia.library.f95", "session_cookies")
        except Exception as exc:
            return {"status": "error", "message": f"Could not read the saved F95zone session: {exc}"}
        try:
            cookies = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            cookies = {}
        if not isinstance(cookies, dict):
            cookies = {}
        if not cookies:
            return {"status": "error", "message": "Sign in to F95zone before resolving masked links."}
        try:
            resolver = F95MaskResolver(masked_url, cookies)
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        self._mask_resolvers.append(resolver)

        def finish(result):
            if resolver in self._mask_resolvers:
                self._mask_resolvers.remove(resolver)
            if result.get("status") == "ok":
                automated = self.start_automated_download(result["url"], "")
                if automated.get("status") != "pending":
                    opened = self.open_external_url(result["url"])
                    if opened.get("status") == "ok":
                        result["opened_in_browser"] = True
                    else:
                        result = {
                            "status": "error",
                            "url": result["url"],
                            "message": opened.get(
                                "message", "The resolved link could not be opened in the system browser."
                            ),
                        }
                else:
                    result["automated"] = True
            self.maskedLinkResolved.emit(result)

        resolver.resolved.connect(lambda url: finish({"status": "ok", "url": url}))
        resolver.failed.connect(lambda message: finish({"status": "error", "message": message}))
        try:
            resolver.show()
        except Exception as exc:
            if resolver in self._mask_resolvers:
                self._mask_resolvers.remove(resolver)
            return {"status": "error", "message": f"Could not show the masked-link resolver: {exc}"}
        return {"status": "pending", "message": "Interactive masked-link resolution opened."}

    @Slot(result="QVariant")
    def start_f95_login(self):
        from pythia import credentials
        from pythia.ui.f95_mask_resolver import F95LoginDialog

        try:
            raw = credentials.get_credential("pythia.library.f95", "session_cookies")
        except Exception as exc:
            return {"status": "error", "message": f"Could not read the saved F95zone session: {exc}"}
        try:
            existing = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        try:
            dialog = F95LoginDialog(existing)
        except Exception as exc:
            return {"status": "error", "message": f"Could not open F95zone sign-in: {exc}"}
        self._f95_login_dialogs.append(dialog)

        def saved(cookies):
            if dialog in self._f95_login_dialogs:
                self._f95_login_dialogs.remove(dialog)
            try:
                normalized_cookies = dict(cookies)
                credentials.set_credential(
                    "pythia.library.f95", "session_cookies", json.dumps(normalized_cookies)
                )
            except Exception as exc:
                self.f95LoginComplete.emit({"status": "error", "message": f"Could not save the F95zone session: {exc}"})
                return
            self.f95LoginComplete.emit({"status": "ok", "cookies": len(normalized_cookies)})

        def closed():
            if dialog in self._f95_login_dialogs:
                self._f95_login_dialogs.remove(dialog)

        dialog.sessionSaved.connect(saved)
        dialog.rejected.connect(closed)
        try:
            dialog.show()
        except Exception as exc:
            closed()
            return {"status": "error", "message": f"Could not show F95zone sign-in: {exc}"}
        return {"status": "pending", "message": "F95zone sign-in opened."}

    @Slot(result="QVariant")
    def clear_f95_session(self):
        from pythia import credentials

        try:
            credentials.set_credential("pythia.library.f95", "session_cookies", "")
        except Exception as exc:
            return {"status": "error", "message": f"Could not clear the F95zone session: {exc}"}
        return {"status": "ok", "message": "Cleared the saved F95zone session."}

    @Slot(str, result="QVariant")
    def open_external_url(self, url: str):
        from PySide6.QtGui import QDesktopServices

        target = QUrl(url)
        if not target.isValid() or target.scheme() not in {"http", "https"}:
            return {"status": "error", "message": "Only valid HTTP(S) links can be opened."}
        try:
            opened = QDesktopServices.openUrl(target)
        except Exception as exc:
            return {"status": "error", "message": f"The system browser could not open this link: {exc}"}
        if not opened:
            return {"status": "error", "message": "The system browser could not open this link."}
        return {"status": "ok"}

    @Slot(str, result="QVariant")
    def open_data_folder(self, kind: str):
        """Open one of Pythia's known data folders without accepting an
        arbitrary UI-supplied filesystem path."""
        from PySide6.QtGui import QDesktopServices

        requested = str(kind)
        if requested not in {"app", "database", "sync"}:
            return {"status": "error", "message": "Unknown Pythia data folder."}
        from pythia.identity import app_data_dir
        from pythia import settings

        try:
            folder = {
                "app": lambda: Path(app_data_dir()),
                "database": lambda: Path(settings.DB_PATH).parent,
                "sync": lambda: Path(datadir.datadir_path()),
            }[requested]()
        except Exception as exc:
            return {"status": "error", "message": f"Could not resolve that data folder: {exc}"}
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"status": "error", "message": f"Could not prepare that folder: {exc}"}
        try:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        except Exception as exc:
            return {"status": "error", "message": f"The system file manager could not open that folder: {exc}"}
        if not opened:
            return {"status": "error", "message": "The system file manager could not open that folder."}
        return {"status": "ok", "path": str(folder)}

    def _download_automator(self):
        from pythia.plugins import registry

        return next(
            (p for p in registry.for_category("downloader") if p.id == "pythia.downloader.automator"),
            None,
        )

    @Slot(str, result="QVariant")
    def start_download_host_login(self, url: str):
        from pythia.ui.f95_mask_resolver import DownloadHostLoginDialog

        try:
            plugin = self._download_automator()
        except Exception as exc:
            return {"status": "error", "message": f"Could not inspect downloader plugins: {exc}"}
        if plugin is None:
            return {"status": "unavailable", "message": "Install and enable Download Automator first."}
        try:
            identity = plugin.identify_host(url)
        except Exception as exc:
            return {"status": "error", "message": f"Could not identify the download host: {exc}"}
        if not isinstance(identity, dict):
            return {"status": "error", "message": "Download Automator returned an invalid host identity."}
        if not identity.get("supported"):
            return {"status": "error", "message": identity.get("reason", "Unsupported download host.")}
        host = identity.get("host")
        if not isinstance(host, str) or not host:
            return {"status": "error", "message": "Download Automator did not identify a host name."}
        try:
            dialog = DownloadHostLoginDialog(url, plugin.get_host_cookies(host))
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        self._download_host_login_dialogs.append(dialog)

        def saved(saved_host, cookies):
            if dialog in self._download_host_login_dialogs:
                self._download_host_login_dialogs.remove(dialog)
            try:
                normalized_cookies = dict(cookies)
                plugin.set_host_cookies(saved_host, normalized_cookies)
            except Exception as exc:
                self.downloadHostLoginComplete.emit(
                    {"status": "error", "host": str(saved_host), "message": f"Could not save the host session: {exc}"}
                )
                return
            self.downloadHostLoginComplete.emit(
                {"status": "ok", "host": str(saved_host), "cookies": len(normalized_cookies), "message": f"Saved the session for {saved_host}."}
            )

        dialog.sessionSaved.connect(saved)
        dialog.rejected.connect(lambda: self._download_host_login_dialogs.remove(dialog) if dialog in self._download_host_login_dialogs else None)
        dialog.show()
        return {"status": "pending", "host": host, "message": f"Sign-in opened for {host}."}

    @Slot(str, result="QVariant")
    def clear_download_host_session(self, url: str):
        try:
            plugin = self._download_automator()
        except Exception as exc:
            return {"status": "error", "message": f"Could not inspect downloader plugins: {exc}"}
        if plugin is None:
            return {"status": "unavailable", "message": "Install and enable Download Automator first."}
        try:
            identity = plugin.identify_host(url)
        except Exception as exc:
            return {"status": "error", "message": f"Could not identify the download host: {exc}"}
        if not isinstance(identity, dict):
            return {"status": "error", "message": "Download Automator returned an invalid host identity."}
        if not identity.get("supported"):
            return {"status": "error", "message": identity.get("reason", "Unsupported download host.")}
        host = identity.get("host")
        if not isinstance(host, str) or not host:
            return {"status": "error", "message": "Download Automator did not identify a host name."}
        try:
            plugin.set_host_cookies(host, {})
        except Exception as exc:
            return {"status": "error", "message": f"Could not clear the saved session: {exc}"}
        return {"status": "ok", "host": host, "message": f"Cleared the saved session for {host}."}

    @Slot(str, str, result="QVariant")
    def start_automated_download(self, url: str, suggested_name: str):
        try:
            plugin = self._download_automator()
        except Exception as exc:
            return {"status": "error", "message": f"Could not inspect downloader plugins: {exc}"}
        if plugin is None:
            return {"status": "unavailable", "message": "Install and enable the optional Download Automator plugin."}
        try:
            plan = plugin.prepare_download(url, suggested_name)
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        if not isinstance(plan, dict):
            return {"status": "error", "message": "Download Automator returned an invalid download plan."}
        if plan.get("status") != "ok":
            message = plan.get("message") or plan.get("reason") or "Download Automator could not prepare this link."
            return {"status": str(plan.get("status") or "error"), "message": str(message)}
        if not isinstance(plan.get("url"), str) or not plan["url"]:
            return {"status": "error", "message": "Download Automator returned a plan without a URL."}
        if not isinstance(plan.get("filename"), str) or not plan["filename"].strip():
            return {"status": "error", "message": "Download Automator returned a plan without a filename."}
        try:
            destination = plugin.get_config("download_dir") or str(Path.home() / "Downloads")
        except Exception as exc:
            return {"status": "error", "message": f"Could not read Download Automator settings: {exc}"}
        # 2026-08-08, download-automator hardening: a real per-download id +
        # cancel event -- previously there was no way to cancel a running
        # download at all (plugin.download() already accepted a
        # cancel_event parameter, but nothing here ever created or passed
        # one). Keyed so multiple concurrent downloads each get their own
        # real, independently-cancellable event, and the UI can correlate
        # every progress signal to the specific download it's showing.
        download_id = str(uuid.uuid4())
        cancel_event = threading.Event()
        self._download_cancel_events[download_id] = cancel_event
        try:
            self._run_worker(self._automated_download_worker, plugin, plan, destination, download_id, cancel_event)
        except Exception as exc:
            self._download_cancel_events.pop(download_id, None)
            return {"status": "error", "message": f"Could not start the download worker: {exc}"}
        return {
            "status": "pending",
            "download_id": download_id,
            "host": plan.get("host"),
            "filename": plan.get("filename"),
            "premium_session": plan.get("premium_session", False),
        }

    @Slot(str, result="QVariant")
    def cancel_automated_download(self, download_id: str):
        event = self._download_cancel_events.get(download_id)
        if event is None:
            return {"status": "error", "message": "No running download with that ID (it may have already finished)."}
        event.set()
        return {"status": "ok"}

    def _automated_download_worker(self, plugin, plan, destination, download_id, cancel_event):
        def progress(received, total):
            self.automatedDownloadProgress.emit(
                {
                    "status": "running",
                    "download_id": download_id,
                    "filename": plan["filename"],
                    "received": received,
                    "total": total,
                }
            )

        try:
            result = plugin.download(plan, destination, progress=progress, cancel_event=cancel_event)
        except Exception as exc:
            result = {"status": "error", "message": str(exc), "filename": plan["filename"]}
        finally:
            self._download_cancel_events.pop(download_id, None)
        if not isinstance(result, dict):
            result = {"status": "error", "message": "Download Automator returned an invalid completion result."}
        else:
            result = dict(result)
        result["download_id"] = download_id
        result.setdefault("filename", plan["filename"])
        self.automatedDownloadProgress.emit(_jsonable(result))

        # Real safe-extraction handoff (2026-08-08): only for a genuinely
        # successful download, only when the user opted in via the plugin's
        # own config_schema (auto_extract), and only for a real .zip -- an
        # unsupported archive type is reported honestly, never silently
        # skipped as if nothing was requested. Reported as its own,
        # separate "extract"-phase event AFTER the download's own
        # completion event, so the UI never conflates "downloaded" with
        # "extracted" as a single claim.
        if result.get("status") == "ok":
            try:
                auto_extract = bool(plugin.get_config("auto_extract"))
            except Exception as exc:
                self.automatedDownloadProgress.emit(
                    _jsonable(
                        {
                            "status": "error",
                            "download_id": download_id,
                            "phase": "extract",
                            "message": f"The download completed, but auto-extract settings could not be read: {exc}",
                        }
                    )
                )
                return
            if auto_extract:
                downloaded_path = Path(str(result.get("path") or ""))
                if not result.get("path"):
                    extraction = {"status": "error", "message": "The completed download did not report a file path."}
                elif downloaded_path.suffix.lower() == ".zip":
                    extract_dir = downloaded_path.with_suffix("")
                    try:
                        extraction = plugin.extract_download(str(downloaded_path), str(extract_dir))
                    except Exception as exc:
                        extraction = {"status": "error", "message": f"Extraction failed: {exc}"}
                    if not isinstance(extraction, dict):
                        extraction = {"status": "error", "message": "Download Automator returned an invalid extraction result."}
                else:
                    extraction = {
                        "status": "unsupported",
                        "message": f"Auto-extract is enabled, but {downloaded_path.suffix or '(no extension)'} archives aren't supported yet -- only .zip.",
                    }
                self.automatedDownloadProgress.emit(
                    _jsonable({**extraction, "download_id": download_id, "phase": "extract"})
                )

    @Slot(str, result="QVariant")
    def rescan_game(self, game_id: str):
        entry = self._writable_entry(game_id)
        if entry is None:
            return {"status": "error", "message": "This game is not installed on this system."}
        roots = sorted({v.get("path") for g in entry.get("versions", []) for v in g.get("variants", []) if v.get("path")})
        if not roots:
            return {"status": "error", "processed": 0, "errors": 1, "message": "This game has no installed paths to rescan."}
        results = []
        root_errors = 0
        for root in roots:
            try:
                scanned = scanning.scan_library(root)
                if not isinstance(scanned, list):
                    raise TypeError("scanner returned a non-list result")
                valid_rows = [row for row in scanned if isinstance(row, dict)]
                root_errors += len(scanned) - len(valid_rows)
                results.extend(valid_rows)
            except Exception as exc:
                root_errors += 1
                logging.getLogger(__name__).warning("Rescan failed for %s: %s", root, exc)
        try:
            self.refresh_games()
        except Exception as exc:
            root_errors += 1
            logging.getLogger(__name__).warning("Library refresh after rescan failed: %s", exc)
        errors = root_errors + sum(1 for row in results if row.get("status") != "ok")
        successful = sum(1 for row in results if row.get("status") == "ok")
        status = "ok" if not errors else ("partial" if successful else "error")
        return {"status": status, "processed": len(results), "errors": errors}

    @Slot(list, str, result="QVariant")
    def bulk_action(self, game_ids: list, action: str):
        ids = list(dict.fromkeys(str(x) for x in game_ids))
        if action == "rescan":
            rows = []
            for game_id in ids:
                try:
                    rows.append(self.rescan_game(game_id))
                except Exception as exc:
                    logging.getLogger(__name__).warning("Bulk rescan failed for %s: %s", game_id, exc)
                    rows.append({"status": "error", "message": str(exc)})
            errors = sum(
                max(1, int(row.get("errors", 0)))
                for row in rows
                if row.get("status") != "ok"
            )
            return {
                "status": "ok" if not errors else "partial",
                "processed": len(rows),
                "errors": errors,
            }
        if action == "reconcile":
            candidates = [c for c in reconciliation.find_merge_candidates()
                          if c.get("a", {}).get("game_id") in ids or c.get("b", {}).get("game_id") in ids]
            return {"status": "ok", "candidates": _jsonable(candidates), "count": len(candidates)}
        if action == "export":
            entries = [e for e in datadir.list_all_entries() if e.get("game_id") in ids]
            return {"status": "ok", "entries": _jsonable(entries), "count": len(entries)}
        return {"status": "unavailable", "message": f"Bulk {action} is not implemented yet."}

    @Slot(list, str, result="QVariant")
    def export_entries_to_file(self, game_ids: list, path_str: str):
        """Real, atomic UTF-8 JSON export of every known entry for
        `game_ids` -- shared here (2026-08-08, native UI review: "Keep
        serialization and file writing in a backend API if feasible so
        native and desktop shells share it") rather than reimplemented per
        front end. Same tmp-file + os.replace atomicity convention
        datadir.py's own writers use -- never leaves a half-written file on
        a crash mid-write. Returns {"status": "ok", "path", "count"} or
        {"status": "error", "message"}."""
        normalized = _normalize_path(path_str).strip()
        if not normalized:
            return {"status": "error", "message": "No export file was selected."}
        path = Path(normalized)
        ids = list(dict.fromkeys(str(x) for x in game_ids))
        entries = [e for e in datadir.list_all_entries() if e.get("game_id") in ids]
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(_jsonable(entries), indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return {"status": "error", "message": f"Couldn't write export file: {e}"}
        return {"status": "ok", "path": str(path), "count": len(entries)}

    @Slot(str, result="QVariant")
    def export_diagnostics_to_file(self, path_str: str):
        """Write a bounded, user-requested diagnostic snapshot atomically."""
        from datetime import datetime, timezone
        from pythia.util.log import get_log_tail

        path = Path(_normalize_path(path_str))
        try:
            log_text, log_truncated = get_log_tail(262_144)
            log_text = _redact_diagnostic_log(log_text)
            try:
                plugins = self.get_all_plugins()
            except Exception as exc:
                plugins = {"status": "error", "message": str(exc)}
            try:
                sync_status = self.get_sync_status()
            except Exception as exc:
                sync_status = {"status": "error", "message": str(exc)}
            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "system": self.get_system_info(),
                "launch_mode": self.get_launch_mode(),
                "ui_mode": self.get_ui_mode(),
                "plugins": plugins,
                "sync": sync_status,
                "recent_log": log_text,
                "log_truncated": log_truncated,
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(_jsonable(report), indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            try:
                tmp.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
                pass
            return {"status": "error", "message": f"Couldn't write diagnostics: {exc}"}
        return {"status": "ok", "path": str(path), "log_truncated": report["log_truncated"]}

    @Slot(result="QVariant")
    def rebuild_cache(self):
        try:
            games_db.rebuild_cache()
            self.refresh_games()
            return {"status": "ok", "games": len(datadir.list_known_games())}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    # ---- directly-launched process monitoring -----------------------------
    @Slot(bool, result="QVariant")
    def get_monitored_launches(self, include_finished: bool = True):
        from pythia import launch

        return _jsonable(launch.monitored_launches(include_finished=bool(include_finished)))

    @Slot(int, result="QVariant")
    def stop_monitored_launch(self, pid: int):
        from pythia import launch

        return _jsonable(launch.stop_monitored_launch(pid))

    @Slot(result="QVariant")
    def clear_finished_launches(self):
        from pythia import launch

        count = launch.clear_finished_launches()
        return {"status": "ok", "cleared": count, "message": f"Cleared {count} finished launch record(s)."}

    @Slot(result="QVariant")
    def refresh_sources(self):
        """Re-read enabled source manifests for every local variant."""
        from pythia.plugins import registry

        plugins = registry.for_category("library")
        checked = changed = errors = 0
        for entry in datadir.list_all_entries():
            if entry.get("system_uuid") != get_system_uuid():
                continue
            entry_changed = False
            for group in entry.get("versions", []):
                for variant in group.get("variants", []):
                    root = variant.get("path")
                    if not root:
                        continue
                    for plugin in plugins:
                        try:
                            observed = plugin.refresh(Path(root))
                            checked += 1
                            if observed and observed != variant.get("source_metadata"):
                                variant["source_metadata"] = _jsonable(observed)
                                changed += 1
                                entry_changed = True
                        except Exception:
                            errors += 1
            if entry_changed:
                try:
                    games_db.save_entry_and_sync_cache(entry)
                except Exception as exc:
                    errors += 1
                    self.logLine.emit(f"Could not save refreshed source metadata for {entry.get('game_id', 'unknown game')}: {exc}")
        try:
            self.refresh_games()
        except Exception as exc:
            errors += 1
            self.logLine.emit(f"Could not refresh the library after source refresh: {exc}")
        return {"status": "ok" if not errors else "partial", "checked": checked, "changed": changed, "errors": errors}

    @Slot(list)
    def start_update_check(self, game_ids: list):
        self._run_worker(self._update_check_worker, list(game_ids or []))

    def _update_check_worker(self, game_ids):
        from pythia.plugins import registry

        totals = {"checked": 0, "updated": 0, "errors": [], "plugins": []}
        try:
            capable = [plugin for plugin in registry.for_category("library") if hasattr(plugin, "check_updates")]
        except Exception as exc:
            totals.update(status="error", message=f"Could not load update providers: {exc}")
            self.updateCheckComplete.emit(totals)
            return
        if not capable:
            totals.update(status="unavailable", message="No enabled library plugin provides update checking.")
            self.updateCheckComplete.emit(totals)
            return
        for plugin in capable:
            try:
                result = plugin.check_updates(log=self.logLine.emit, game_ids=game_ids)
                if not isinstance(result, dict):
                    raise TypeError("provider returned a non-object result")
                checked = int(result.get("checked", 0))
                updated = int(result.get("updated", 0))
                if checked < 0 or updated < 0 or updated > checked:
                    raise ValueError("provider returned invalid update counts")
                provider_errors = result.get("errors") or []
                if not isinstance(provider_errors, list):
                    raise TypeError("provider errors must be a list")
                totals["checked"] += checked
                totals["updated"] += updated
                totals["errors"].extend(str(error) for error in provider_errors[:1000])
                totals["plugins"].append(plugin.id)
            except Exception as exc:
                totals["errors"].append(f"{plugin.id}: {exc}")
        totals["status"] = "ok" if not totals["errors"] else "partial"
        try:
            self.refresh_games()
        except Exception as exc:
            totals["errors"].append(f"Library refresh failed: {exc}")
            totals["status"] = "partial"
        self.updateCheckComplete.emit(_jsonable(totals))

    # 2026-08-04, Lutris-base port: the old direct-launch variant flag (set_variant_
    # direct) isn't ported -- Lutris's own "linux" runner already covers "just run
    # this executable directly, no compatibility layer needed" (see
    # pythia/runners/linux.py), so there's no reason to keep a second, narrower
    # mechanism for the same thing.

    # ---- settings -----------------------------------------------------------
    # Real backing for both front ends' Settings screens (2026-08-03 -- big-picture
    # had none at all until this point; desktop already talks to these modules
    # directly since it's plain QtWidgets/Python, but QML's only bridge to Python is
    # this `backend` context object, so these slots exist specifically for it).
    @Slot(result="QVariant")
    def get_system_info(self):
        from pythia.identity import app_data_dir

        this_uuid = get_system_uuid()
        others: dict = {}
        for entry in datadir.list_all_entries():
            uuid = entry.get("system_uuid")
            if uuid and uuid != this_uuid:
                others[uuid] = others.get(uuid, 0) + 1
        return _jsonable(
            {
                "system_uuid": this_uuid,
                "sync_folder": str(datadir.datadir_path()),
                "app_data_folder": str(app_data_dir()),
                "other_systems": [{"uuid": u, "count": c} for u, c in others.items()],
            }
        )

    @Slot(result="QVariant")
    def get_downloader_plugins(self):
        from pythia.plugins import downloader as downloader_plugins
        from pythia.plugins.registry import get_enabled_plugin_ids

        allowed = get_enabled_plugin_ids()
        return _jsonable(
            [
                {
                    "id": p.id,
                    "display_name": p.display_name,
                    "enabled": allowed is None or p.id in allowed,
                }
                for p in downloader_plugins.all_plugins()
            ]
        )

    @Slot(result="QVariant")
    def get_all_plugins(self):
        """Every registered plugin, every category -- real inventory (2026-08-03),
        for the "Plugins" page in any front end. `origin` distinguishes built-in
        from third-party (pip entry-point or an installed .zip package), matching
        the registry's own three discovery paths rather than guessing."""
        from pythia.plugins import packaging
        from pythia.plugins.registry import (
            all_plugin_entries,
            get_enabled_plugin_ids,
            is_capability_enabled,
            is_protected,
            optional_dependency_status,
        )

        allowed = get_enabled_plugin_ids()
        installed_zip_ids = set(packaging.list_installed_packages())

        def origin(pid: str) -> str:
            if is_protected(pid):
                return "Built-in"
            if pid in installed_zip_ids:
                return "Installed package"
            return "Python package"

        # Metadata only -- for a zip-based plugin this reads plugin.json +
        # type-<category>.py, never plugin.py, so a disabled plugin's real
        # code never runs just to appear in this list (2026-08-07, "we just
        # read the metadata files, and plugins are loaded when enabled").
        entries = all_plugin_entries()
        result = []
        for pid, entry in sorted(entries.items(), key=lambda kv: kv[1]["display_name"]):
            cats = entry["categories"]
            result.append(
                {
                    "id": pid,
                    "display_name": entry["display_name"],
                    "categories": cats,
                    "version": entry["version"],
                    "author": entry["author"],
                    "enabled": allowed is None or pid in allowed,
                    "origin": origin(pid),
                    # Per-category on/off (2026-08-06): a multi-category plugin can
                    # stay enabled overall while one specific capability is switched
                    # off from that capability's own settings pane -- distinct from
                    # `enabled`, which is whole-plugin (disabling that unloads it
                    # everywhere; see registry.get_enabled_plugin_ids' own docstring).
                    "capability_enabled": {c: is_capability_enabled(pid, c) for c in cats},
                    "configurable": bool(entry["config_schema"]),
                    "config_schema": entry["config_schema"],
                    "optional_dependencies": optional_dependency_status(pid),
                }
            )
        return _jsonable(result)

    @Slot(str, bool, result="QVariant")
    def set_plugin_enabled(self, plugin_id: str, enabled: bool):
        from pythia.plugins.registry import all_plugin_entries, get_enabled_plugin_ids, set_enabled_plugin_ids

        entries = all_plugin_entries()
        if plugin_id not in entries:
            return {"status": "error", "message": "Unknown plugin."}
        try:
            allowed = get_enabled_plugin_ids()
            current = set(allowed) if allowed is not None else set(entries)
            if enabled:
                current.add(plugin_id)
            else:
                current.discard(plugin_id)
            set_enabled_plugin_ids(sorted(current))
        except Exception as exc:
            return {"status": "error", "message": f"Couldn't save plugin state: {exc}"}
        return {"status": "ok", "plugin_id": plugin_id, "enabled": bool(enabled)}

    @Slot(str, str, bool, result="QVariant")
    def set_plugin_capability_enabled(self, plugin_id: str, category: str, enabled: bool):
        from pythia.plugins.registry import all_plugin_entries, set_capability_enabled

        entry = all_plugin_entries().get(plugin_id)
        if entry is None or category not in entry.get("categories", []):
            return {"status": "error", "message": "Unknown plugin capability."}
        try:
            set_capability_enabled(plugin_id, category, enabled)
        except Exception as exc:
            return {"status": "error", "message": f"Couldn't save capability state: {exc}"}
        return {"status": "ok", "plugin_id": plugin_id, "category": category, "enabled": bool(enabled)}

    @Slot(str, result="QVariant")
    def get_plugin_config(self, plugin_id: str):
        """Stored values for a plugin's own config_schema fields (PluginMeta's
        declarative option list) -- persisted the same way as every other local,
        durable UI setting, keyed per plugin so different plugins' schemas never
        collide."""
        from pythia.identity import get_setting

        raw = get_setting(f"plugin_config:{plugin_id}", None)
        if not raw:
            return {}
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}

    @Slot(str, str, result="QVariant")
    def save_plugin_config(self, plugin_id: str, config_json: str):
        from pythia.identity import set_setting
        from pythia.plugins.registry import all_plugin_entries

        entry = all_plugin_entries().get(plugin_id)
        if entry is None:
            return {"status": "error", "message": "Unknown plugin."}
        if len(config_json.encode("utf-8")) > 65_536:
            return {"status": "error", "message": "Plugin configuration is too large."}
        try:
            values = json.loads(config_json)
        except (TypeError, ValueError) as exc:
            return {"status": "error", "message": f"Plugin configuration is not valid JSON: {exc}"}
        if not isinstance(values, dict):
            return {"status": "error", "message": "Plugin configuration must be an object."}
        schema = entry.get("config_schema") or []
        fields = {row.get("key"): row for row in schema if isinstance(row, dict) and isinstance(row.get("key"), str)}
        unknown = sorted(set(values) - set(fields))
        if unknown:
            return {"status": "error", "message": f"Unknown plugin setting(s): {', '.join(unknown)}."}
        for key, value in values.items():
            field_type = fields[key].get("type", "text")
            if field_type == "bool" and not isinstance(value, bool):
                return {"status": "error", "message": f"Plugin setting {key!r} must be true or false."}
            if field_type != "bool" and not isinstance(value, (str, int, float)):
                return {"status": "error", "message": f"Plugin setting {key!r} has an unsupported value."}
        try:
            set_setting(f"plugin_config:{plugin_id}", json.dumps(values, ensure_ascii=False, separators=(",", ":")))
        except Exception as exc:
            return {"status": "error", "message": f"Could not save plugin configuration: {exc}"}
        return {"status": "ok", "plugin_id": plugin_id}

    @Slot(result="QVariant")
    def get_account_plugins(self):
        """Every LibraryPlugin declaring credential_fields (PluginMeta's generic
        secret-storage schema) -- same generic, zero-hardcoding pattern desktop's
        Settings Accounts section uses."""
        from pythia.plugins import library as library_plugins

        return _jsonable(
            [
                {
                    "id": p.id,
                    "display_name": p.display_name,
                    "connected": p.is_connected(),
                    "fields": [
                        {"key": f["key"], "label": f["label"], "secret": bool(f.get("secret"))}
                        for f in p.credential_fields
                    ],
                }
                for p in library_plugins.all_plugins()
                if p.credential_fields
            ]
        )

    @Slot(str, str, str, result="QVariant")
    def set_account_credential(self, plugin_id: str, key: str, value: str):
        from pythia.plugins import library as library_plugins

        plugin = next((p for p in library_plugins.all_plugins() if p.id == plugin_id), None)
        if plugin is None:
            return {"status": "error", "message": "Unknown account plugin."}
        declared = {field.get("key") for field in plugin.credential_fields if isinstance(field, dict)}
        if key not in declared:
            return {"status": "error", "message": "Unknown credential field."}
        try:
            plugin.set_credential(key, value)
        except Exception as exc:
            return {"status": "error", "message": f"Could not save the credential: {exc}"}
        return {"status": "ok", "plugin_id": plugin_id, "key": key}

    @Slot(str)
    def sync_account(self, plugin_id: str):
        self._run_worker(self._sync_account_worker, plugin_id)

    def _sync_account_worker(self, plugin_id: str):
        from pythia.plugins import library as library_plugins

        plugin = next((p for p in library_plugins.all_plugins() if p.id == plugin_id), None)
        if plugin is None:
            self.accountSyncComplete.emit(plugin_id, {"status": "error", "message": "Unknown account plugin."})
            return
        try:
            result = onboarding.sync_owned(plugin)
            if not isinstance(result, dict):
                raise TypeError("account plugin returned an invalid sync result")
        except Exception as exc:
            self.accountSyncComplete.emit(plugin_id, {"status": "error", "message": f"Account sync failed: {exc}"})
            return
        try:
            self.refresh_games()
        except Exception as exc:
            result = dict(result)
            result["status"] = "partial"
            result["message"] = f"Account data synced, but the library could not refresh: {exc}"
        self.accountSyncComplete.emit(plugin_id, _jsonable(result))

    @Slot(str)
    def import_f95(self, db_path: str):
        """One-shot F95Checker db.sqlite3 import (see plugins/library/f95.py) --
        previously CLI-only (`pythia f95-import`), never reachable from the
        desktop UI at all until now. Reuses accountSyncComplete since the
        result shape (linked/created/status) is identical to a real account
        sync's, even though F95's import isn't an ongoing sync."""
        self._run_worker(self._import_f95_worker, db_path)

    def _import_f95_worker(self, db_path: str):
        from pythia.identity import set_setting
        from pythia.plugins.registry import get_plugin_instance

        path = Path(_normalize_path(db_path)) if db_path else None
        if path is None or not path.is_file():
            self.accountSyncComplete.emit("pythia.library.f95", {"status": "error", "message": "Select an existing F95Checker database file."})
            return
        try:
            with path.open("rb") as source:
                header = source.read(16)
        except OSError as exc:
            self.accountSyncComplete.emit("pythia.library.f95", {"status": "error", "message": f"Could not read the F95Checker database: {exc}"})
            return
        if header != b"SQLite format 3\x00":
            self.accountSyncComplete.emit("pythia.library.f95", {"status": "error", "message": "The selected file is not a SQLite database."})
            return
        f95_plugin = get_plugin_instance("pythia.library.f95")
        if f95_plugin is None:
            message = "F95zone plugin is disabled or unavailable."
            self.logLine.emit(message)
            self.accountSyncComplete.emit("pythia.library.f95", {"status": "error", "message": message})
            return
        try:
            set_setting("f95_db_path", str(path))
            result = onboarding.sync_owned(f95_plugin)
            if not isinstance(result, dict):
                raise TypeError("F95Checker import returned an invalid result")
        except Exception as exc:
            self.accountSyncComplete.emit(f95_plugin.id, {"status": "error", "message": f"F95Checker import failed: {exc}"})
            return
        try:
            self.refresh_games()
        except Exception as exc:
            result = dict(result)
            result["status"] = "partial"
            result["message"] = f"F95Checker data imported, but the library could not refresh: {exc}"
        self.accountSyncComplete.emit(f95_plugin.id, _jsonable(result))

    @Slot(result=str)
    def get_system_uuid(self) -> str:
        return get_system_uuid()

    @Slot(str, result="QVariant")
    def remove_game(self, game_id: str):
        """Real removal (2026-08-06 -- the "Review removal" UI action was an
        honest "not built yet" stub until now). Only ever deletes THIS
        system's own tracked entry for game_id (datadir.remove_entry()'s own
        docstring explains why: another system's synced copy of the same
        game_id is that system's real record, not ours to delete)."""
        try:
            removed = datadir.remove_entry(get_system_uuid(), game_id)
        except Exception as exc:
            return {"status": "error", "message": f"Could not remove the local game record: {exc}"}
        if removed:
            try:
                self.refresh_games()
            except Exception as exc:
                self.logLine.emit(f"The game was removed but the library could not refresh: {exc}")
            return {"status": "ok", "message": "Removed the local game record."}
        return {"status": "error", "message": "No local record of this game to remove."}

    @Slot(result=str)
    def get_ui_mode(self) -> str:
        from pythia.identity import get_setting

        return get_setting("ui_mode") or ""

    @Slot(str, result="QVariant")
    def set_ui_mode(self, mode: str):
        from pythia.identity import set_setting

        if mode not in {"desktop", "bigpicture"}:
            return {"status": "error", "message": f"Unknown UI mode: {mode!r}."}
        try:
            set_setting("ui_mode", mode)
        except Exception as exc:
            return {"status": "error", "message": f"Could not save the UI mode: {exc}"}
        return {"status": "ok", "mode": mode}

    # ---- saved searches -----------------------------------------------------
    # Real, working precedent already exists in Lutris (database/saved_searches.py,
    # a dedicated pga.db table) -- but Pythia's cache.db is fully rebuilt from
    # datadir/*.json on every startup (see games.py's module docstring), so a saved
    # search can't live there without being silently wiped. It's durable local-only
    # UI state instead -- same shape as enabled_plugins/ui_mode -- so it's stored as
    # one JSON blob under identity.get_setting/set_setting rather than a new table.
    @Slot(result="QVariant")
    def get_saved_searches(self):
        from pythia.identity import get_setting

        raw = get_setting("saved_searches")
        try:
            searches = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            return []
        if not isinstance(searches, list):
            return []
        return [s for s in searches[:100] if isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("search"), str)]

    @Slot(str, str, result="QVariant")
    def save_search(self, name: str, search: str):
        from pythia.identity import get_setting, set_setting

        name = " ".join(name.split())
        if not name:
            return {"status": "error", "message": "A saved search needs a name."}
        if len(name) > 100 or len(search) > 4096:
            return {"status": "error", "message": "The saved-search name or query is too long."}
        try:
            searches = json.loads(get_setting("saved_searches") or "[]")
            if not isinstance(searches, list):
                raise ValueError("saved search data is not a list")
            searches = [s for s in searches if isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("search"), str)]
        except (TypeError, ValueError) as exc:
            return {"status": "error", "message": f"Could not read the saved searches: {exc}"}
        searches = [s for s in searches if s["name"] != name]
        searches.append({"name": name, "search": search})
        searches = searches[-100:]
        try:
            set_setting("saved_searches", json.dumps(searches))
        except Exception as exc:
            return {"status": "error", "message": f"Could not save the search: {exc}"}
        return {"status": "ok", "name": name}

    @Slot(str, result="QVariant")
    def remove_saved_search(self, name: str):
        from pythia.identity import get_setting, set_setting

        try:
            searches = json.loads(get_setting("saved_searches") or "[]")
            if not isinstance(searches, list):
                raise ValueError("saved search data is not a list")
            searches = [s for s in searches if isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("search"), str)]
        except (TypeError, ValueError) as exc:
            return {"status": "error", "message": f"Could not read the saved searches: {exc}"}
        searches = [s for s in searches if s["name"] != name]
        try:
            set_setting("saved_searches", json.dumps(searches))
        except Exception as exc:
            return {"status": "error", "message": f"Could not remove the saved search: {exc}"}
        return {"status": "ok", "name": name}

    # ---- launcher hand-off --------------------------------------------------
    @Slot(str, result="QVariant")
    def find_manager_for_game(self, game_id: str):
        """Every (manager plugin, source kind) this game could be handed off to --
        lets the UI show/hide an "Open" action and pick which source to use if more
        than one applies, without launching anything itself (see
        pythia.plugins.manager.find_for_game())."""
        from pythia.plugins import manager as manager_plugins

        games = datadir.list_known_games()
        entries = games.get(game_id, [])
        candidates = manager_plugins.find_for_game(entries)
        return _jsonable(
            [{"manager_id": p.id, "manager_name": p.display_name, "kind": d["kind"]} for p, d in candidates]
        )

    @Slot(str, str)
    def open_in_manager(self, game_id: str, manager_id: str):
        """Real, side-effecting hand-off -- launches an external application (the
        manager plugin's own tool, e.g. Steam). Never the game's own executable;
        Pythia's "never launches anything" boundary stays on the game itself. Callers
        should confirm with the user before calling this, same as cli.py's `open`."""
        self._run_worker(self._open_in_manager_worker, game_id, manager_id)

    def _open_in_manager_worker(self, game_id: str, manager_id: str):
        from pythia.plugins import manager as manager_plugins
        from pythia.plugins.context import PluginContext

        games = datadir.list_known_games()
        entries = games.get(game_id, [])
        candidates = manager_plugins.find_for_game(entries)
        match = next((c for c in candidates if c[0].id == manager_id), None)
        if match is None:
            message = f"No manager plugin '{manager_id}' is available for this game."
            self.logLine.emit(message)
            self.managerOpenComplete.emit({"status": "error", "game_id": game_id, "manager_id": manager_id, "message": message})
            return
        plugin, detection = match
        ctx = PluginContext(log=self.logLine.emit)
        try:
            ok = plugin.open(detection, ctx)
        except Exception as exc:
            message = f"Manager hand-off failed: {exc}"
            self.logLine.emit(message)
            self.managerOpenComplete.emit({"status": "error", "game_id": game_id, "manager_id": manager_id, "message": message})
            return
        message = "Manager hand-off completed." if ok else "Manager hand-off failed or is not supported for this game."
        self.logLine.emit(message)
        self.managerOpenComplete.emit({"status": "ok" if ok else "error", "game_id": game_id, "manager_id": manager_id, "message": message})

    # ---- direct launching ("Lutris replacement" mode) ------------------------
    @Slot(result=str)
    def get_launch_mode(self):
        """`direct` (default -- Pythia launches the game itself) or `manager`
        (the old hands-off behavior -- track and hand off, never launch). See
        pythia.launch's own docstring for the real PLAN.md Step 1 this backs."""
        from pythia import launch

        return launch.get_launch_mode()

    @Slot(str, result="QVariant")
    def set_launch_mode(self, mode: str):
        from pythia import launch

        try:
            launch.set_launch_mode(mode)
        except Exception as exc:
            return {"status": "error", "message": f"Could not save the launch mode: {exc}"}
        return {"status": "ok", "mode": mode}

    def _find_variant_for_game(self, game_id, variant_id: str | None):
        """Real instance resolution (2026-08-07, external UX review, Phase 1:
        "Make instance selection real" -- this used to always pick "the first
        variant with a path," ignoring whatever the user had selected in the
        action bar, because nothing about the selection ever reached here).
        `variant_id` -- when given -- is matched against every variant's real
        `id` (datadir._ensure_variant_ids()) across every entry for this
        game, not just this system's own. Falls back to the old
        first-with-a-path behavior only when no variant_id is given at all
        (a caller that hasn't been updated yet), never silently when a
        specific id was requested but not found -- that's a real "instance
        unavailable" case, not a reason to guess a different one."""
        games = datadir.list_known_games()
        entries = games.get(game_id, [])

        if variant_id:
            for entry in entries:
                variant = datadir.find_variant(entry, variant_id)
                if variant is not None:
                    return variant
            return None

        for entry in entries:
            for group in entry.get("versions", []):
                for v in group.get("variants", []):
                    if v.get("path"):
                        return v
        return None

    @Slot(str, result="QVariant")
    @Slot(str, str, result="QVariant")
    def get_launch_readiness(self, game_id, variant_id: str | None = None):
        """The exact same resolution launch_game_direct() itself uses,
        exposed read-only so the UI can show a real, specific readiness
        state instead of a blanket "Ready" (2026-08-07, external UX review,
        Phase 1: "'Ready' is therefore often an unsupported claim"). Returns
        {"ready", "state", "reason", "executable" (str|None), "detected"} --
        see pythia.launch.resolve_launch_plan()'s own docstring for the real
        state values."""
        from pythia import config_cascade, launch

        variant = self._find_variant_for_game(game_id, variant_id)
        if variant is None:
            return {
                "ready": False,
                "state": "instance_unavailable",
                "reason": "This instance isn't available on this system.",
                "executable": None,
                "detected": False,
            }
        keys = ("backend", "environment", "command_prefix", "pre_launch_script", "wait_pre_launch",
                "post_exit_script", "monitoring", "wine_prefix", "wine_version", "wine_arch",
                "dxvk", "vkd3d", "esync", "fsync")
        effective = {key: config_cascade.get_effective(key, game_id, variant_id)["value"] for key in keys}
        plan = launch.resolve_launch_plan(variant, effective)
        return {
            "ready": plan["ready"],
            "state": plan["state"],
            "reason": plan["reason"],
            "executable": str(plan["executable"]) if plan["executable"] else None,
            "detected": plan["detected"],
        }

    @Slot(str, str, result="QVariant")
    def get_effective_launch_plan(self, game_id, variant_id: str):
        """The full resolved plan, for Configure's "Effective launch plan"
        inspector (2026-08-07, Codex proposal: "Add an 'Effective launch
        plan' inspector displaying the final executable, arguments,
        environment, backend, prerequisites, wrappers, mutations, and
        monitoring rules."). Only executable/args/working_dir are real
        today (Phase 1) -- environment/backend/prerequisites/wrappers/
        monitoring are honestly reported as not yet implemented rather than
        fabricated, same "no false success" rule as everywhere else."""
        from pythia import config_cascade, launch

        variant = self._find_variant_for_game(game_id, variant_id)
        if variant is None:
            return {"ready": False, "state": "instance_unavailable", "reason": "This instance isn't available on this system."}
        keys = ("backend", "environment", "command_prefix", "pre_launch_script", "wait_pre_launch",
                "post_exit_script", "monitoring", "wine_prefix", "wine_version", "wine_arch",
                "dxvk", "vkd3d", "esync", "fsync")
        effective = {key: config_cascade.get_effective(key, game_id, variant_id)["value"] for key in keys}
        plan = launch.resolve_launch_plan(variant, effective)
        return {
            "ready": plan["ready"],
            "state": plan["state"],
            "reason": plan["reason"],
            "executable": str(plan["executable"]) if plan["executable"] else None,
            "args": plan["args"],
            "working_dir": str(plan["working_dir"]) if plan["working_dir"] else None,
            "detected": plan["detected"],
            "backend": plan.get("backend", "native"),
            "environment": plan.get("environment", {}),
            "prerequisites": [],
            "wrappers": plan.get("wrappers", []),
            "monitoring": plan.get("monitoring", {}),
        }

    @Slot(str, result="QVariant")
    @Slot(str, str, result="QVariant")
    def launch_game_direct(self, game_id, variant_id: str | None = None):
        """Real, direct launch of a SPECIFIC instance -- resolves via the
        same resolve_launch_plan() get_launch_readiness() already showed the
        user, so what Play does can never disagree with what Pythia claimed
        was ready. Only meaningful in `direct` launch mode; callers should
        check get_launch_mode() first, same as the web UI does."""
        from pythia import config_cascade, launch

        variant = self._find_variant_for_game(game_id, variant_id)
        if variant is None:
            return {"success": False, "message": "This instance isn't available on this system."}

        keys = ("backend", "environment", "command_prefix", "pre_launch_script", "wait_pre_launch",
                "post_exit_script", "monitoring", "wine_prefix", "wine_version", "wine_arch",
                "dxvk", "vkd3d", "esync", "fsync")
        effective = {key: config_cascade.get_effective(key, game_id, variant_id)["value"] for key in keys}
        plan = launch.resolve_launch_plan(variant, effective)
        if not plan["ready"]:
            return {"success": False, "message": plan["reason"], "state": plan["state"]}

        exe = plan["executable"]
        try:
            launch.launch_native(exe, args=plan["args"], working_dir=plan["working_dir"],
                                 environment=plan.get("environment"), wrappers=plan.get("wrappers"),
                                 pre_launch_script=plan.get("pre_launch_script", ""),
                                 wait_pre_launch=plan.get("wait_pre_launch", True),
                                 post_exit_script=plan.get("post_exit_script", ""))
        except OSError as e:
            return {"success": False, "message": f"Failed to launch {exe.name}: {e}"}
        return {"success": True, "message": f"Launched {exe.name}.", "exe": str(exe)}

    @Slot(str, str, "QVariant", result="QVariant")
    def save_variant_launch_config(self, game_id: str, variant_id: str, config):
        """Real persistence for Configure's launch-relevant fields (2026-08-07,
        external UX review, Phase 1: "Configuration appears to save but does
        not... This is a severe trust violation"). `config` is a dict that
        may contain any of datadir.VARIANT_LAUNCH_FIELDS ("executable",
        "args", "working_dir"); a key set to null/None clears that override
        back to auto-detection. Only ever writes this system's own entry
        file (datadir.update_variant_launch_config's own single-writer rule)
        -- a variant tracked only by another system can't be configured from
        here, and this returns a real error saying so rather than silently
        no-opping."""
        system_uuid = get_system_uuid()
        fields = {k: config.get(k) for k in ("executable", "args", "working_dir") if k in config}
        result = datadir.update_variant_launch_config(system_uuid, game_id, variant_id, **fields)
        if result.get("status") == "ok":
            self.refresh_games()
        return result

    # ---- config cascade (2026-08-07, Codex "Universal Game Configuration" ------
    # proposal: "Configuration follows the fixed cascade: global -> game ->
    # instance. Every effective value records its source, validation state, and
    # reset target.") -- see pythia.config_cascade's own docstring for the real
    # storage model. Generic key/value, so the Configure UI can grow new option
    # keys without new Slots for each one. ------------------------------------
    @Slot(list, str, str, result="QVariant")
    def get_effective_config(self, option_keys: list, game_id: str, variant_id: str):
        """{option_key: {"value","source_scope","raw"}} for every key in
        `option_keys` -- one round trip for a whole Configure pane instead of
        one Slot call per field."""
        from pythia import config_cascade

        return {key: config_cascade.get_effective(key, game_id=game_id, variant_id=variant_id) for key in option_keys}

    @Slot(str, str, str, str, "QVariant", result="QVariant")
    def set_config_value(self, scope: str, game_id: str, variant_id: str, option_key: str, value):
        """`scope` is "global", "game", or "instance". Setting `value` to
        null/None resets that scope's override for `option_key` back to
        whatever the next scope up provides -- real, not a fake "reset"
        button that just changes what's displayed."""
        from pythia import config_cascade

        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", option_key or ""):
            return {"status": "error", "message": "The configuration key is invalid."}
        if scope not in {"global", "game", "instance"}:
            return {"status": "error", "message": f"Unknown scope: {scope!r}"}
        if scope in {"game", "instance"} and not game_id:
            return {"status": "error", "message": "A game is required for this configuration scope."}
        if scope == "instance" and not variant_id:
            return {"status": "error", "message": "An instance is required for instance configuration."}
        try:
            if scope == "global":
                config_cascade.set_global(option_key, value) if value is not None else config_cascade.reset_global(option_key)
                return {"status": "ok"}
            if scope == "game":
                result = config_cascade.set_game(game_id, option_key, value)
            else:
                result = config_cascade.set_instance(game_id, variant_id, option_key, value)
        except Exception as exc:
            return {"status": "error", "message": f"Could not save {option_key}: {exc}"}
        if result.get("status") == "ok":
            try:
                self.refresh_games()
            except Exception as exc:
                self.logLine.emit(f"Configuration was saved but the library could not refresh: {exc}")
        return result

    # ---- sync / devices (2026-08-08, "optional core sync service and native
    # device/transfer workflow") -----------------------------------------------
    # All real logic lives in pythia.ui.sync_bridge.SyncBridge (Qt-independent,
    # separately unit-tested) -- these Slots are thin delegation plus the
    # datadir<->metadata bridging this app-specific integration needs.

    @staticmethod
    def _iso_to_epoch(value) -> int | None:
        import datetime

        try:
            return int(datetime.datetime.fromisoformat(value).timestamp())
        except (TypeError, ValueError):
            return None

    def _sync_local_metadata_items(self) -> list:
        """This system's own datadir entries + this system's own real
        tombstones, converted into pythia.core.sync.metadata's real record/
        tombstone shape. Never includes a mirrored copy of another system's
        entry -- only this system is ever the source of truth for its own
        records here. Used ONLY to publish outbound (see
        sync_metadata_with_device()) -- never as the receiving side's
        reconciliation state (see _sync_mirrored_items_for_peer() for that)."""
        from pythia.core.sync import metadata

        own_uuid = get_system_uuid()
        items = []
        for entry in datadir.list_all_entries():
            if entry.get("system_uuid") != own_uuid:
                continue
            items.append(metadata.create_record(dict(entry), revision=int(entry.get("sync_revision", 0))))
        for tombstone in datadir.list_tombstones():
            if tombstone.get("system_uuid") != own_uuid:
                continue
            items.append(
                metadata.create_tombstone(
                    own_uuid,
                    tombstone["game_id"],
                    tombstone["revision"],
                    deleted_at=self._iso_to_epoch(tombstone.get("deleted_at")),
                )
            )
        return items

    def _sync_mirrored_items_for_peer(self, peer_system_uuid: str) -> list:
        """This system's real, previously-mirrored records/tombstones FROM
        `peer_system_uuid` specifically -- the receiving side's real
        reconciliation state (2026-08-08, Codex sync review: "correctness
        repair required"). Without this, `metadata.reconcile()` has nothing
        to compare an incoming push's revision against and always treats it
        as brand new, letting a stale/older push silently overwrite a newer
        mirror or resurrect a real deletion."""
        from pythia.core.sync import metadata

        items = []
        for entry in datadir.list_mirrored_records(peer_system_uuid):
            items.append(metadata.create_record(dict(entry), revision=int(entry.get("sync_revision", 0))))
        for tombstone in datadir.list_mirrored_tombstones(peer_system_uuid):
            items.append(
                metadata.create_tombstone(
                    peer_system_uuid,
                    tombstone["game_id"],
                    tombstone["revision"],
                    deleted_at=tombstone.get("deleted_at") if isinstance(tombstone.get("deleted_at"), int) else self._iso_to_epoch(tombstone.get("deleted_at")),
                )
            )
        return items

    def _on_sync_metadata_reconciled(self, peer_system_uuid: str, result: dict) -> None:
        """Real persistence step for a peer's pushed metadata (called from
        SyncRuntime's background thread, not the GUI thread -- datadir I/O is
        thread-safe here the same way every _run_worker() background write
        already is). Only ever writes files under the peer's own
        system_uuid -- metadata_session.py already verified every incoming
        item's real ownership before this is ever called; never overwrites
        this system's own entries/trust/credentials/config.

        Uses save_mirrored_entry()/apply_mirrored_tombstone() (2026-08-08,
        Codex sync review repair), never save_entry()/remove_entry() -- those
        are for this system's OWN writes and would either re-increment the
        peer's real revision (corrupting future reconciliation) or fail to
        persist a real mirrored-tombstone record (letting a later stale push
        resurrect a real deletion after this process restarts)."""
        for item in result.get("upserts", []):
            payload = item.get("payload")
            if not isinstance(payload, dict) or payload.get("system_uuid") != peer_system_uuid:
                continue
            try:
                datadir.save_mirrored_entry(dict(payload), revision=item["revision"])
            except Exception:
                logging.getLogger(__name__).exception("Failed to persist synced entry from %s", peer_system_uuid)
        for item in result.get("tombstones", []):
            if item.get("owner_system_uuid") != peer_system_uuid:
                continue
            datadir.apply_mirrored_tombstone(peer_system_uuid, item["game_id"], item["revision"], item.get("deleted_at"))
        if result.get("upserts") or result.get("tombstones"):
            self.refresh_games()
        self.syncMetadataSynced.emit(_jsonable({"peer_system_uuid": peer_system_uuid, "result": result}))

    def _on_sync_transfer_offer(self, offer_id: str, offer: dict, peer_system_uuid: str) -> None:
        self.syncTransferOfferReceived.emit(
            _jsonable({"offer_id": offer_id, "offer": offer, "peer_system_uuid": peer_system_uuid})
        )

    @Slot(result="QVariant")
    def get_sync_status(self):
        return _jsonable(self._sync_bridge.get_status())

    @Slot(bool, result="QVariant")
    def set_sync_enabled(self, enabled: bool):
        try:
            return _jsonable(self._sync_bridge.set_enabled(enabled))
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @Slot(result="QVariant")
    def ensure_sync_started(self):
        """Safe to call every time the Devices & Sync page opens -- a real
        no-op while sync is disabled."""
        try:
            return _jsonable(self._sync_bridge.ensure_started())
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @Slot(str, result="QVariant")
    def set_sync_config(self, patch_json: str):
        try:
            return _jsonable(self._sync_bridge.set_config(json.loads(patch_json)))
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @Slot(str, result="QVariant")
    def set_sync_network_settings(self, patch_json: str):
        try:
            return _jsonable(self._sync_bridge.set_network_settings(json.loads(patch_json)))
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @Slot(str, result="QVariant")
    def set_sync_system_name(self, name: str):
        try:
            return _jsonable(self._sync_bridge.set_system_name(name))
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @Slot(str, result=str)
    def create_sync_pairing_invitation(self, friendly_name: str):
        try:
            return self._sync_bridge.create_pairing_invitation(friendly_name=friendly_name or None)
        except Exception:
            return ""

    @Slot(str, result="QVariant")
    def accept_sync_pairing_invitation(self, text: str):
        try:
            return _jsonable({"status": "ok", "trust": self._sync_bridge.accept_pairing_invitation(text)})
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @Slot(str, str, str, result="QVariant")
    def pair_sync_device_manually(self, system_uuid: str, device_id: str, friendly_name: str):
        try:
            return _jsonable(
                {"status": "ok", "trust": self._sync_bridge.pair_manually(
                    system_uuid=system_uuid, device_id=device_id, friendly_name=friendly_name
                )}
            )
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @Slot(result="QVariant")
    def list_sync_trusted_devices(self):
        return _jsonable(self._sync_bridge.list_trusted())

    @Slot(str, result=bool)
    def remove_sync_trusted_device(self, system_uuid: str):
        return self._sync_bridge.remove_trusted(system_uuid)

    @Slot(str, str, str, str)
    def offer_sync_transfer(self, system_uuid: str, root_path: str, game_id: str, display_name: str):
        self._run_worker(self._offer_sync_transfer_worker, system_uuid, _normalize_path(root_path), game_id, display_name)

    def _offer_sync_transfer_worker(self, system_uuid: str, root_path: str, game_id: str, display_name: str):
        try:
            result = self._sync_bridge.offer_and_send_game(
                system_uuid, root=root_path, game_id=game_id, display_name=display_name
            )
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}
        self.syncTransferComplete.emit(_jsonable({"system_uuid": system_uuid, "game_id": game_id, "result": result}))

    @Slot(str, bool, str, str, str, result=bool)
    def resolve_sync_transfer_offer(self, offer_id: str, accept: bool, destination: str, cache: str, message: str):
        return self._sync_bridge.resolve_offer(
            offer_id,
            accept=accept,
            destination=_normalize_path(destination) if destination else None,
            cache=_normalize_path(cache) if cache else None,
            message=message,
        )

    @Slot(result="QVariant")
    def pending_sync_transfer_offers(self):
        return _jsonable(self._sync_bridge.pending_offers())

    @Slot(result="QVariant")
    def known_sync_peers(self):
        return _jsonable(self._sync_bridge.known_peers())

    @Slot(result="QVariant")
    def sync_retry_state(self):
        """Real, honest next-attempt/attempts/last-error per trusted peer
        currently in backoff -- never guessed reachability."""
        return _jsonable(self._sync_bridge.retry_state())

    @Slot(result="QVariant")
    def sync_nat_status(self):
        """Real, honest protocol/external-endpoint/lease/last-error for the
        active NAT mapping, if any (2026-08-08, "internal NAT traversal and
        direct-route publication"). Also included in get_sync_status()'s
        own "nat" key -- this Slot exists for a lightweight standalone poll."""
        return _jsonable(self._sync_bridge.nat_status())

    @Slot()
    def retry_sync_nat_mapping(self):
        """Explicit, user-triggered retry -- a real UPnP SSDP+HTTP round
        trip can take a few seconds, so this always runs in the background
        worker, never the GUI thread."""
        self._run_worker(self._retry_sync_nat_mapping_worker)

    def _retry_sync_nat_mapping_worker(self):
        try:
            result = self._sync_bridge.retry_nat_mapping()
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}
        self.syncStatusChanged.emit(_jsonable({"event": "nat_retry_result", **result}))

    @Slot(str, result=bool)
    def cancel_sync_transfer(self, system_uuid: str):
        """Caller-driven cancellation (2026-08-08, Codex sync review) --
        works for a transfer this system is either sending to or receiving
        from `system_uuid`. Returns False if none is currently active."""
        return self._sync_bridge.cancel_transfer(system_uuid)

    @Slot(str)
    def push_sync_metadata_to_device(self, system_uuid: str):
        """PUSHES this system's own real state to `system_uuid` (precisely
        labeled -- 2026-08-08, Codex sync review, "do not call a one-way
        push 'Sync'"). See pull_sync_metadata_from_device() for the explicit
        opposite direction."""
        self._run_worker(self._push_sync_metadata_worker, system_uuid)

    def _push_sync_metadata_worker(self, system_uuid: str):
        local_items = self._sync_local_metadata_items()
        try:
            result = self._sync_bridge.sync_metadata_with_device(system_uuid, local_items)
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}
        self.syncMetadataSynced.emit(_jsonable({"peer_system_uuid": system_uuid, "direction": "push", "result": result}))

    @Slot(str)
    def pull_sync_metadata_from_device(self, system_uuid: str):
        """PULLS `system_uuid`'s own real state into this system's mirror of
        it, and persists the real classification the exact same way an
        inbound push already does (never a fake success)."""
        self._run_worker(self._pull_sync_metadata_worker, system_uuid)

    def _pull_sync_metadata_worker(self, system_uuid: str):
        local_mirrored_items = self._sync_mirrored_items_for_peer(system_uuid)
        try:
            result = self._sync_bridge.pull_metadata_from_device(system_uuid, local_mirrored_items)
            self._on_sync_metadata_reconciled(system_uuid, result)
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}
        self.syncMetadataSynced.emit(_jsonable({"peer_system_uuid": system_uuid, "direction": "pull", "result": result}))

    # ---- staging (NAS/cloud-synced folder) fallback -------------------------------
    @Slot(str)
    def export_sync_staging_snapshot(self, folder: str):
        self._run_worker(self._export_staging_worker, _normalize_path(folder))

    def _export_staging_worker(self, folder: str):
        try:
            result = self._sync_bridge.export_staging_snapshot(folder, self._sync_local_metadata_items())
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}
        self.syncStagingComplete.emit(_jsonable({"direction": "export", "folder": folder, "result": result}))

    @Slot(str)
    def import_sync_staging_snapshot(self, folder: str):
        self._run_worker(self._import_staging_worker, _normalize_path(folder))

    def _import_staging_worker(self, folder: str):
        try:
            result = self._import_staged_snapshot_and_reconcile(folder)
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}
        self.syncStagingComplete.emit(_jsonable({"direction": "import", "folder": folder, "result": result}))

    def _import_staged_snapshot_and_reconcile(self, folder: str) -> dict:
        """Reads a staged snapshot and reconciles each real owner's items
        against this system's own mirrored state of that owner, the same
        classify-then-persist separation the network push/pull paths use.
        Never lets a staged snapshot overwrite this system's OWN real data --
        an item claiming our own system_uuid is silently skipped, since a
        stale exported copy of our own data re-imported later must never
        regress what we actually have now."""
        from pythia.core.sync import metadata

        result = self._sync_bridge.import_staging_snapshot(folder)
        if result.get("status") != "ok":
            return result
        own_uuid = get_system_uuid()
        by_owner: dict[str, list] = {}
        for item in result["items"]:
            owner = item.get("owner_system_uuid")
            if not owner or owner == own_uuid:
                continue
            by_owner.setdefault(owner, []).append(item)
        applied = 0
        all_conflicts = []
        for owner, items in by_owner.items():
            local_items = self._sync_mirrored_items_for_peer(owner)
            reconciled = metadata.reconcile(local_items, items, peer_system_uuid=owner)
            self._on_sync_metadata_reconciled(owner, reconciled)
            applied += len(reconciled["upserts"]) + len(reconciled["tombstones"])
            all_conflicts.extend(reconciled["conflicts"])
        return {
            "status": "ok",
            "snapshot_id": result.get("snapshot_id"),
            "applied": applied,
            "conflicts": all_conflicts,
        }
