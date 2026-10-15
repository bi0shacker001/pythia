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

Every slot a page reads goes through the same contract (2026-10-14, the web UI
wiring): a dict with `status` "ok", "unavailable" (the feature has no backend yet;
`message` says so in plain words) or "error" (`message` is the reason). A slot that
raises is caught by `_guarded`, logged with its name and redacted arguments, and
reported to the page as an "error" result -- a page never sees a raw exception and
never has to guess why it got nothing. Everything logged through Pythia's logger
(pythia.util.log) is also kept in `_ui_log` and emitted as `uiLog`, which the web
UI's Activity page and its error console (--error-console / `error_console = True`
in pythia.conf) both read; there is no second logging path.
"""

import datetime
import functools
import json
import logging
import threading
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from pythia import datadir, downloads, onboarding, reconciliation, scanning
from pythia.database import games as games_db
from pythia.identity import get_system_uuid
from pythia.util.log import logger

_UI_LOG_LIMIT = 500
_JOB_LIMIT = 100
# Slot arguments that are secrets: never written to the log, shown as "***".
_SECRET_ARG_SLOTS = {"set_account_credential": {2}}


def _normalize_path(path_str: str) -> str:
    """QML folder dialogs hand back file:// URLs, not plain paths -- accept either."""
    if path_str.startswith("file:"):
        return QUrl(path_str).toLocalFile()
    return path_str


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


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _redact(name: str, args: tuple) -> str:
    secret = _SECRET_ARG_SLOTS.get(name, set())
    shown = ["***" if i in secret else repr(a) for i, a in enumerate(args)]
    return ", ".join(shown)


def _guarded(fn):
    """Every slot a page calls: an exception becomes a logged, typed error result
    instead of a silent null over QWebChannel."""

    @functools.wraps(fn)
    def wrapper(self, *args):
        try:
            return fn(self, *args)
        except Exception as exc:  # noqa: BLE001 -- the page needs the reason, whatever it was
            logger.exception(
                "%s(%s) failed: %s", fn.__name__, _redact(fn.__name__, args), exc, extra={"ui_slot": fn.__name__}
            )
            return {"status": "error", "slot": fn.__name__, "message": str(exc) or exc.__class__.__name__}

    return wrapper


_LEVELS = {"error": logging.ERROR, "warn": logging.WARNING, "info": logging.INFO, "debug": logging.DEBUG}


class _UiLogHandler(logging.Handler):
    """Turns every record of Pythia's logger into one UI log entry. The backend's
    own messages carry `ui_source` ("pythia" for progress, "web" for the page's
    errors) and `ui_slot` as record extras; everything else is "python"."""

    def __init__(self, backend):
        super().__init__(level=logging.NOTSET)
        self._backend = backend

    def emit(self, record):
        level = record.levelname.lower()
        if level == "warning":
            level = "warn"
        entry = {
            "ts": datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc).isoformat(),
            "level": level,
            "source": getattr(record, "ui_source", "python"),
            "slot": getattr(record, "ui_slot", None),
            "message": record.getMessage(),
        }
        self._backend._keep(entry)


class Backend(QObject):
    logLine = Signal(str)
    # Dict and list payloads are declared as QVariant, not a bare Python object:
    # QWebChannel (the web UI) can only serialize QVariant values, and QML reads
    # both the same way.
    decisionNeeded = Signal("QVariant")  # a preview-shaped dict: {"path", "name", "candidates", ...}
    onboardComplete = Signal("QVariant")
    scanComplete = Signal("QVariant")
    gamesChanged = Signal()
    downloadDetected = Signal(str)  # a new file's path, from a watched Downloads folder
    mergeCandidatesReady = Signal("QVariant")
    mergeComplete = Signal("QVariant")
    accountSyncComplete = Signal(str, "QVariant")
    updateCheckComplete = Signal("QVariant")
    platformEnableComplete = Signal("QVariant")
    jobsChanged = Signal("QVariant")  # the whole job list, newest first
    intakeChanged = Signal("QVariant")  # {"detected": [...], "decisions": [...]}
    uiLog = Signal("QVariant")  # one {"ts", "level", "source", "slot", "message"} entry

    def __init__(self, error_console: bool = False):
        super().__init__()
        self._decision_event = threading.Event()
        self._decision_result = None
        self._awaiting_live_decision = False
        self._watch_stop_event = None
        self._watcher = None
        self._games = []
        self._lock = threading.Lock()
        self._ui_log: list[dict] = []
        self._jobs: list[dict] = []
        self._detected: list[dict] = []
        self._decisions: list[dict] = []
        self._error_console = error_console
        self._log_handler = _UiLogHandler(self)
        logger.addHandler(self._log_handler)
        self.refresh_games()

    # ---- UI log (one buffer: the event log, the error console and logLine) ------
    def _keep(self, entry: dict):
        """Called by the log handler for every record, from any thread."""
        with self._lock:
            self._ui_log.append(entry)
            del self._ui_log[:-_UI_LOG_LIMIT]
        self.uiLog.emit(entry)

    def _record(self, level: str, source: str, message: str, slot=None):
        """One path for everything the UI shows: Pythia's logger, whose handler
        keeps the entry and emits uiLog."""
        logger.log(_LEVELS.get(level, logging.INFO), "%s", message, extra={"ui_source": source, "ui_slot": slot})

    def _log(self, message: str):
        """A progress line: the old logLine signal plus the UI log, one call."""
        self.logLine.emit(message)
        self._record("info", "pythia", message)

    def _unavailable(self, slot: str, message: str) -> dict:
        """A feature with no backend yet answers honestly, and the attempt is logged
        so a run's console shows what the page asked for."""
        self._record("warn", "python", f"{slot}: not available ({message})", slot)
        return {"status": "unavailable", "slot": slot, "message": message}

    # ---- jobs: the backend's own operations, start to finish -------------------
    def _job_start(self, kind: str, title: str, detail: str = "") -> str:
        job = {
            "id": f"{kind}-{len(self._jobs) + 1}-{int(datetime.datetime.now().timestamp() * 1000)}",
            "kind": kind,
            "title": title,
            "detail": detail,
            "state": "running",
            "started_at": _now(),
            "ended_at": None,
            "summary": "",
            "log": [],
        }
        with self._lock:
            self._jobs.insert(0, job)
            del self._jobs[_JOB_LIMIT:]
        self.jobsChanged.emit(_jsonable(self._jobs))
        return job["id"]

    def _job_log(self, job_id: str, line: str):
        with self._lock:
            job = next((j for j in self._jobs if j["id"] == job_id), None)
            if job is not None:
                job["log"].append(line)
        self._log(line)

    def _job_finish(self, job_id: str, summary: str, ok: bool = True):
        with self._lock:
            job = next((j for j in self._jobs if j["id"] == job_id), None)
            if job is not None:
                job["state"] = "done" if ok else "failed"
                job["summary"] = summary
                job["ended_at"] = _now()
        self.jobsChanged.emit(_jsonable(self._jobs))
        self._record("info" if ok else "warn", "pythia", summary)

    def _emit_intake(self):
        watcher = self._watcher and {k: v for k, v in self._watcher.items() if k != "job"}
        self.intakeChanged.emit(
            _jsonable({"detected": self._detected, "decisions": self._decisions, "watcher": watcher})
        )

    # ---- games list -----------------------------------------------------------
    def refresh_games(self):
        games = datadir.list_known_games()
        self._games = [{"game_id": gid, "entries": _jsonable(entries)} for gid, entries in sorted(games.items())]
        self.gamesChanged.emit()

    @Property("QVariantList", notify=gamesChanged)
    def games(self):
        return self._games

    # ---- onboard a single path (interactive) -----------------------------------
    @Slot(str)
    def start_onboard(self, path_str: str):
        path_str = _normalize_path(path_str)
        job = self._job_start("onboard", f"Add {Path(path_str).name or path_str}", path_str)
        threading.Thread(target=self._onboard_worker, args=(path_str, job), daemon=True).start()

    def _onboard_worker(self, path_str: str, job: str):
        pv = onboarding.preview(path_str)
        if pv["status"] != "ok":
            self._job_log(job, f"error: {pv['message']}")
            self._job_finish(job, pv["message"], ok=False)
            self.onboardComplete.emit(_jsonable(pv))
            return

        decision = None
        if pv["candidates"]["certain"] is None and pv["candidates"]["suggested"]:
            decision = self._ask_live(pv)

        self._job_log(job, f"Onboarding '{pv['path']}' ...")
        result = onboarding.onboard(path_str, decision=decision)
        self.refresh_games()
        ok = result.get("status") == "ok"
        self._job_finish(
            job, f"Added {result.get('name') or Path(path_str).name}" if ok else result.get("message", ""), ok
        )
        self.onboardComplete.emit(_jsonable(result))

    # ---- scan a library (automatic; ambiguous results reviewed afterward) -----
    @Slot(str)
    def start_scan(self, path_str: str):
        path_str = _normalize_path(path_str)
        job = self._job_start("scan", f"Scan {path_str}", "Finds every game folder inside")
        threading.Thread(target=self._scan_worker, args=(path_str, job), daemon=True).start()

    def _scan_worker(self, path_str: str, job: str):
        self._job_log(job, f"Scanning '{path_str}' ...")
        try:
            results = scanning.scan_library(path_str)
        except OSError as exc:
            self._job_finish(job, f"Could not scan {path_str}: {exc}", ok=False)
            self.scanComplete.emit([])
            return
        for r in results:
            if r["status"] == "ok":
                self._job_log(job, f"  -> game_id {r['game_id']}")
            else:
                self._job_log(job, f"  error: {r['message']}")
        self.refresh_games()
        ok = sum(1 for r in results if r["status"] == "ok")
        bad = len(results) - ok
        self._job_finish(job, f"{ok} game{'s' if ok != 1 else ''} found" + (f", {bad} not identified" if bad else ""))
        self.scanComplete.emit(_jsonable(results))

        for r in results:
            if r["status"] == "ok" and r["candidates"]["suggested"]:
                # Normalized to the same {"path", "name", "candidates"} shape
                # _ask_live's preview dict uses -- onboard()'s own result dict has
                # "path" meaning the *datadir JSON file*, not the game folder
                # (game_path), so it can't be forwarded as-is here.
                self._queue_decision(
                    _jsonable({"path": r["game_path"], "name": r["name"], "candidates": r["candidates"]})
                )

    def _queue_decision(self, preview: dict):
        with self._lock:
            self._decisions = [d for d in self._decisions if d.get("path") != preview.get("path")]
            self._decisions.append(preview)
        self._emit_intake()
        self.decisionNeeded.emit(preview)

    # ---- decision resolution ---------------------------------------------------
    def _ask_live(self, preview: dict):
        """Block the calling (background) thread until the UI resolves this decision."""
        self._decision_event.clear()
        self._decision_result = None
        self._awaiting_live_decision = True
        self._queue_decision(_jsonable(preview))
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
        with self._lock:
            self._decisions = [d for d in self._decisions if d.get("path") != path_str]
        self._emit_intake()
        if self._awaiting_live_decision:
            self._decision_result = decision
            self._decision_event.set()
            return
        # No live onboard is waiting -- this is a post-scan review item. Resolving it
        # re-onboards that path directly with the corrected decision.
        job = self._job_start("onboard", f"Record {Path(path_str).name}", path_str)
        threading.Thread(target=self._reonboard_worker, args=(path_str, decision, job), daemon=True).start()

    def _reonboard_worker(self, path_str, decision, job):
        result = onboarding.onboard(path_str, decision=decision)
        self.refresh_games()
        ok = result.get("status") == "ok"
        self._job_finish(
            job, f"Recorded {result.get('name') or Path(path_str).name}" if ok else result.get("message", ""), ok
        )
        self.onboardComplete.emit(_jsonable(result))

    # ---- download watching -----------------------------------------------------
    @Slot(str, str)
    def start_watch(self, command_str: str, watch_dir_str: str):
        """Launch `command_str` (a single executable path -- no argument splitting
        yet) and watch `watch_dir_str` for new files, emitting downloadDetected for
        each. See downloads.launch_and_watch; this doesn't move or import anything
        itself, that's a separate start_onboard() call once the user picks one."""
        watch_dir_str = _normalize_path(watch_dir_str)
        self.stop_watch()
        self._watch_stop_event = threading.Event()
        job = self._job_start("watch", f"Watching {watch_dir_str}", f"Running {command_str}" if command_str else "")
        self._watcher = {"program": command_str, "folder": watch_dir_str, "started_at": _now(), "job": job}
        self._emit_intake()
        threading.Thread(
            target=self._watch_worker, args=(command_str, watch_dir_str, self._watch_stop_event, job), daemon=True
        ).start()

    def _on_new_file(self, path: Path):
        with self._lock:
            self._detected.insert(0, {"path": str(path), "at": _now()})
        self._emit_intake()
        self.downloadDetected.emit(str(path))

    def _watch_worker(self, command_str, watch_dir_str, stop_event, job):
        try:
            if command_str:
                downloads.launch_and_watch([command_str], Path(watch_dir_str), self._on_new_file, stop_event=stop_event)
            else:
                downloads.watch(Path(watch_dir_str), self._on_new_file, stop_event=stop_event)
        except OSError as e:
            self._job_finish(job, f"Could not watch '{watch_dir_str}': {e}", ok=False)
            self._watcher = None
            self._emit_intake()
            return
        self._job_finish(job, "Stopped watching")
        self._watcher = None
        self._emit_intake()

    @Slot()
    def stop_watch(self):
        if self._watch_stop_event is not None:
            self._watch_stop_event.set()

    @Slot(str)
    def forget_detected(self, path: str):
        with self._lock:
            self._detected = [d for d in self._detected if d["path"] != path]
        self._emit_intake()

    # ---- reconciliation ---------------------------------------------------------
    @Slot()
    def refresh_merge_candidates(self):
        threading.Thread(target=self._merge_candidates_worker, daemon=True).start()

    def _merge_candidates_worker(self):
        try:
            candidates = reconciliation.find_merge_candidates()
        except Exception as exc:  # noqa: BLE001 -- reported to the page, not swallowed
            self._record("error", "python", f"refresh_merge_candidates failed: {exc}", "refresh_merge_candidates")
            self.mergeCandidatesReady.emit({"status": "error", "message": str(exc)})
            return
        self.mergeCandidatesReady.emit(_jsonable(candidates))

    @Slot(str, str)
    def merge_games(self, surviving_id: str, retired_id: str):
        job = self._job_start("merge", "Fold two records into one", f"{retired_id} into {surviving_id}")
        threading.Thread(target=self._merge_worker, args=(surviving_id, retired_id, job), daemon=True).start()

    def _merge_worker(self, surviving_id, retired_id, job):
        result = reconciliation.merge(surviving_id, retired_id)
        self.refresh_games()
        ok = result.get("status") == "ok"
        self._job_finish(job, "Folded" if ok else result.get("message", ""), ok)
        self.mergeComplete.emit(_jsonable(result))

    # ---- notes ------------------------------------------------------------------
    @Slot(str, str, result="QVariant")
    @_guarded
    def save_notes(self, game_id: str, text: str):
        """Update this system's own entry for `game_id`'s free-text notes field
        (part of the schema since Milestone 3). No-op if this system doesn't own
        that game_id -- notes are per-system-written, same as everything else."""
        uuid = get_system_uuid()
        for entry in datadir.list_all_entries():
            if entry.get("system_uuid") == uuid and entry.get("game_id") == game_id:
                entry["notes"] = text
                games_db.save_entry_and_sync_cache(entry)
                self.refresh_games()
                return {"status": "ok"}
        return {
            "status": "error",
            "message": "This computer has no record of that game, so notes cannot be saved here.",
        }

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
    @_guarded
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
                "status": "ok",
                "system_uuid": this_uuid,
                "sync_folder": str(datadir.datadir_path()),
                "app_data_folder": str(app_data_dir()),
                "other_systems": [{"uuid": u, "count": c} for u, c in others.items()],
            }
        )

    @Slot(result="QVariant")
    @_guarded
    def get_downloader_plugins(self):
        from pythia.plugins import DownloaderPlugin
        from pythia.plugins import downloader as downloader_plugins
        from pythia.plugins.registry import get_enabled_plugin_ids

        allowed = get_enabled_plugin_ids()
        return _jsonable(
            [
                {
                    "id": p.id,
                    "display_name": p.display_name,
                    "enabled": allowed is None or p.id in allowed,
                    # Whether the plugin can fetch without opening its own window.
                    "headless": type(p).get_download_info is not DownloaderPlugin.get_download_info,
                    "platforms": list(p.platforms),
                }
                for p in downloader_plugins.all_plugins()
            ]
        )

    @Slot(result="QVariant")
    @_guarded
    def get_all_plugins(self):
        """Every registered plugin, every category -- real inventory (2026-08-03),
        for the "Plugins" page in any front end. `origin` distinguishes built-in
        from third-party (pip entry-point or drop-in folder), matching the
        registry's own three discovery paths rather than guessing."""
        from pythia.plugins.registry import (
            all_plugins,
            categories_of,
            discover_external,
            discover_folder_plugins,
            get_enabled_plugin_ids,
        )

        allowed = get_enabled_plugin_ids()
        external_ids = {p.id for p in discover_external()}
        folder_ids = {p.id for p in discover_folder_plugins()}

        def origin(pid: str) -> str:
            if pid in folder_ids:
                return "Drop-in folder"
            if pid in external_ids:
                return "Python package"
            return "Built-in"

        return _jsonable(
            [
                {
                    "id": p.id,
                    "display_name": p.display_name,
                    "categories": categories_of(p),
                    "version": p.version,
                    "author": p.author,
                    "enabled": allowed is None or p.id in allowed,
                    "origin": origin(p.id),
                }
                for p in sorted(all_plugins(), key=lambda p: p.display_name)
            ]
        )

    @Slot(str, result="QVariant")
    @_guarded
    def get_capabilities(self, category: str):
        """The plugins of one category with that category's own real state: a game
        source's account, a launcher's presence on this computer, an engine
        plugin's tooling. A plugin whose check raises is listed with the reason
        instead of taking the page down."""
        from pythia.plugins.context import PluginContext
        from pythia.plugins.registry import CATEGORY_CLASSES, for_category, get_enabled_plugin_ids

        if category not in CATEGORY_CLASSES:
            return {"status": "error", "message": f"Unknown plugin category '{category}'."}
        allowed = get_enabled_plugin_ids()
        ctx = PluginContext(log=self._log)
        out = []
        for p in sorted(for_category(category), key=lambda p: p.display_name):
            item = {
                "id": p.id,
                "display_name": p.display_name,
                "version": p.version,
                "enabled": allowed is None or p.id in allowed,
                "state": {},
            }
            try:
                if category == "library":
                    item["state"] = {
                        "has_account": bool(p.credential_fields),
                        "connected": p.is_connected() if p.credential_fields else None,
                    }
                elif category == "manager":
                    item["state"] = {"available": bool(p.is_available(ctx)), "sources": list(p.compatible_sources)}
                elif category == "engine":
                    caps = p.capabilities(ctx)
                    item["state"] = {"installed": caps.get("installed"), "tool_version": caps.get("version")}
                elif category == "downloader":
                    item["state"] = {"watching": (self._watcher or {}).get("folder"), "platforms": list(p.platforms)}
            except NotImplementedError:
                item["state"] = {"unknown": "This plugin does not report its state."}
            except Exception as exc:  # noqa: BLE001 -- one plugin's failure is that row's state
                self._record(
                    "warn", "python", f"get_capabilities({category}): {p.id} failed: {exc}", "get_capabilities"
                )
                item["state"] = {"error": str(exc)}
            out.append(item)
        return _jsonable({"status": "ok", "category": category, "plugins": out})

    @Slot(str, bool, result="QVariant")
    @_guarded
    def set_plugin_enabled(self, plugin_id: str, enabled: bool):
        from pythia.plugins.registry import all_plugins, get_enabled_plugin_ids, set_enabled_plugin_ids

        allowed = get_enabled_plugin_ids()
        current = set(allowed) if allowed is not None else {p.id for p in all_plugins()}
        if enabled:
            current.add(plugin_id)
        else:
            current.discard(plugin_id)
        set_enabled_plugin_ids(list(current))
        return {"status": "ok"}

    @Slot(result="QVariant")
    @_guarded
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
    @_guarded
    def set_account_credential(self, plugin_id: str, key: str, value: str):
        from pythia.plugins import library as library_plugins

        for p in library_plugins.all_plugins():
            if p.id == plugin_id:
                p.set_credential(key, value)
                return {"status": "ok"}
        return {"status": "error", "message": f"No game source named '{plugin_id}'."}

    @Slot(str)
    def sync_account(self, plugin_id: str):
        job = self._job_start(f"sync:{plugin_id}", f"Sync {plugin_id}", "Owned games")
        threading.Thread(target=self._sync_account_worker, args=(plugin_id, job), daemon=True).start()

    def _sync_account_worker(self, plugin_id: str, job: str):
        from pythia.plugins import library as library_plugins

        plugin = next((p for p in library_plugins.all_plugins() if p.id == plugin_id), None)
        if plugin is None:
            self._job_finish(job, f"No game source named '{plugin_id}'", ok=False)
            self.accountSyncComplete.emit(
                plugin_id, {"status": "error", "message": f"No game source named '{plugin_id}'."}
            )
            return
        try:
            result = onboarding.sync_owned(plugin)
        except Exception as exc:  # noqa: BLE001 -- a store's failure is the job's result
            self._record("error", "python", f"sync_account({plugin_id}) failed: {exc}", "sync_account")
            logger.exception("sync_account(%s) failed", plugin_id)
            result = {"status": "error", "message": str(exc)}
        self.refresh_games()
        ok = result.get("status") == "ok"
        self._job_finish(
            job,
            f"{result.get('linked', 0)} linked, {result.get('created', 0)} new" if ok else result.get("message", ""),
            ok,
        )
        self.accountSyncComplete.emit(plugin_id, _jsonable(result))

    @Slot(result="QVariant")
    @_guarded
    def sync_all_accounts(self):
        """Library > Refresh all sources: every connected game source, one job each.
        Zero connected sources is a real answer, not an error."""
        from pythia.plugins import library as library_plugins

        started = []
        for p in library_plugins.enabled_plugins():
            if p.credential_fields and p.is_connected():
                self.sync_account(p.id)
                started.append(p.id)
        return {"status": "ok", "started": started}

    @Slot(str)
    def import_f95(self, db_path: str):
        """One-shot F95Checker db.sqlite3 import (see plugins/library/f95.py) --
        previously CLI-only (`pythia f95-import`), never reachable from the
        desktop UI at all until now. Reuses accountSyncComplete since the
        result shape (linked/created/status) is identical to a real account
        sync's, even though F95's import isn't an ongoing sync."""
        job = self._job_start("sync:f95", "Import from F95Checker", db_path)
        threading.Thread(target=self._import_f95_worker, args=(db_path, job), daemon=True).start()

    def _import_f95_worker(self, db_path: str, job: str):
        from pythia.plugins.library import f95 as f95_module
        from pythia.plugins.library.f95 import plugin as f95_plugin

        if db_path:
            f95_module.set_db_path(db_path)
        try:
            result = onboarding.sync_owned(f95_plugin)
        except Exception as exc:  # noqa: BLE001
            self._record("error", "python", f"import_f95 failed: {exc}", "import_f95")
            logger.exception("import_f95 failed")
            result = {"status": "error", "message": str(exc)}
        self.refresh_games()
        ok = result.get("status") == "ok"
        self._job_finish(
            job,
            f"{result.get('linked', 0)} linked, {result.get('created', 0)} new" if ok else result.get("message", ""),
            ok,
        )
        self.accountSyncComplete.emit(f95_plugin.id, _jsonable(result))

    @Slot()
    def check_updates(self):
        """Library > Check all updates: the real F95zone pass (plugins/library/
        f95_updates.run_update_check) over every game with an F95zone id. Other
        stores have no update check yet; the result says how many were checked."""
        job = self._job_start("updates", "Check for updates", "Games with an F95zone id")
        threading.Thread(target=self._check_updates_worker, args=(job,), daemon=True).start()

    def _check_updates_worker(self, job: str):
        from pythia.plugins.library import f95_updates

        try:
            result = f95_updates.run_update_check(log=lambda line: self._job_log(job, line))
        except Exception as exc:  # noqa: BLE001
            self._record("error", "python", f"check_updates failed: {exc}", "check_updates")
            logger.exception("check_updates failed")
            self._job_finish(job, str(exc), ok=False)
            self.updateCheckComplete.emit({"status": "error", "message": str(exc)})
            return
        self.refresh_games()
        errors = result.get("errors") or []
        summary = f"{result.get('checked', 0)} checked, {result.get('updated', 0)} with an update"
        if errors:
            summary += f", {len(errors)} failed"
        self._job_finish(job, summary, ok=not errors)
        self.updateCheckComplete.emit(_jsonable({"status": "ok", **result}))

    @Slot(result=str)
    def get_system_uuid(self) -> str:
        return get_system_uuid()

    @Slot(result=str)
    def get_ui_mode(self) -> str:
        from pythia.identity import get_setting

        return get_setting("ui_mode") or ""

    @Slot(str)
    def set_ui_mode(self, mode: str):
        from pythia.identity import set_setting

        set_setting("ui_mode", mode)

    # ---- launcher hand-off --------------------------------------------------
    @Slot(str, result="QVariant")
    @_guarded
    def find_manager_for_game(self, game_id: str):
        """Every (manager plugin, source kind) this game could be handed off to --
        lets the UI show/hide an "Open" action and pick which source to use if more
        than one applies, without launching anything itself (see
        pythia.plugins.manager.find_for_game())."""
        from pythia.plugins import manager as manager_plugins
        from pythia.plugins.context import PluginContext

        games = datadir.list_known_games()
        entries = games.get(game_id, [])
        candidates = manager_plugins.find_for_game(entries)
        ctx = PluginContext(log=self._log)
        return _jsonable(
            [
                {
                    "manager_id": p.id,
                    "manager_name": p.display_name,
                    "kind": d["kind"],
                    "available": bool(p.is_available(ctx)),
                }
                for p, d in candidates
            ]
        )

    @Slot(str, str)
    def open_in_manager(self, game_id: str, manager_id: str):
        """Real, side-effecting hand-off -- launches an external application (the
        manager plugin's own tool, e.g. Steam). Never the game's own executable;
        Pythia's "never launches anything" boundary stays on the game itself. Callers
        should confirm with the user before calling this, same as cli.py's `open`."""
        threading.Thread(target=self._open_in_manager_worker, args=(game_id, manager_id), daemon=True).start()

    def _open_in_manager_worker(self, game_id: str, manager_id: str):
        from pythia.plugins import manager as manager_plugins
        from pythia.plugins.context import PluginContext

        games = datadir.list_known_games()
        entries = games.get(game_id, [])
        candidates = manager_plugins.find_for_game(entries)
        match = next((c for c in candidates if c[0].id == manager_id), None)
        if match is None:
            self._record(
                "warn", "python", f"No manager plugin '{manager_id}' available for this game.", "open_in_manager"
            )
            return
        plugin, detection = match
        ctx = PluginContext(log=self._log)
        ok = plugin.open(detection, ctx)
        self._log("Hand-off attempted." if ok else "Hand-off failed or not supported for this game.")

    # ---- engine enablement (EnginePlugin.platform_status / enable) ----------------
    def _own_install(self, game_id: str):
        """This computer's first installed variant of a game: (entry, group, variant)."""
        uuid = get_system_uuid()
        for entry in datadir.list_all_entries():
            if entry.get("system_uuid") != uuid or entry.get("game_id") != game_id:
                continue
            for group in entry.get("versions", []):
                for variant in group.get("variants", []):
                    if variant.get("path"):
                        return entry, group, variant
        return None, None, None

    @Slot(str, result="QVariant")
    @_guarded
    def get_platform_status(self, game_id: str):
        """What this install needs per platform, from the engine plugin that owns its
        kind. Honest answers: no install here, no engine plugin for the kind, or the
        plugin's own report."""
        from pythia.plugins import engine as engine_plugins
        from pythia.plugins.context import PluginContext

        entry, _group, variant = self._own_install(game_id)
        if variant is None:
            return {"status": "ok", "installed": False, "engine": None, "platforms": {}}
        kind = (variant.get("source") or {}).get("kind")
        plugin, _detection = engine_plugins.identify(Path(variant["path"]), current_kind=kind)
        if plugin is None:
            return {"status": "ok", "installed": True, "engine": None, "kind": kind, "platforms": {}}
        try:
            report = plugin.platform_status(Path(variant["path"]), PluginContext(log=self._log))
        except NotImplementedError:
            return {"status": "ok", "installed": True, "engine": plugin.display_name, "kind": kind, "platforms": {}}
        return _jsonable(
            {
                "status": "ok",
                "installed": True,
                "engine": plugin.display_name,
                "engine_id": plugin.id,
                "kind": kind,
                "path": variant["path"],
                "platforms": report,
            }
        )

    @Slot(str, str)
    def enable_platform(self, game_id: str, platform: str):
        """Run the engine plugin's own remediation for one platform (Ren'Py's
        renpy_patch, for instance). Writes into the game folder; the page confirms first."""
        job = self._job_start("enable", f"Enable {platform}", game_id)
        threading.Thread(target=self._enable_worker, args=(game_id, platform, job), daemon=True).start()

    def _enable_worker(self, game_id: str, platform: str, job: str):
        from pythia.plugins import engine as engine_plugins
        from pythia.plugins.context import PluginContext

        _entry, _group, variant = self._own_install(game_id)
        plugin = None
        if variant is not None:
            kind = (variant.get("source") or {}).get("kind")
            plugin, _detection = engine_plugins.identify(Path(variant["path"]), current_kind=kind)
        if plugin is None:
            self._job_finish(job, "No engine plugin can prepare this install.", ok=False)
            self.platformEnableComplete.emit(
                {"status": "error", "game_id": game_id, "message": "No engine plugin can prepare this install."}
            )
            return
        try:
            ctx = PluginContext(log=lambda line: self._job_log(job, line))
            result = plugin.enable(Path(variant["path"]), [platform], ctx)
        except NotImplementedError:
            result = {"status": "error", "message": f"{plugin.display_name} cannot prepare installs yet."}
        except Exception as exc:  # noqa: BLE001
            self._record("error", "python", f"enable_platform({game_id}, {platform}) failed: {exc}", "enable_platform")
            logger.exception("enable_platform failed")
            result = {"status": "error", "message": str(exc)}
        ok = result.get("status", "ok") == "ok"
        self._job_finish(job, f"{platform} ready" if ok else result.get("message", ""), ok)
        self.platformEnableComplete.emit(_jsonable({"game_id": game_id, "platform": platform, **result}))

    # ---- the web UI's own needs (2026-10-14, pythia/ui/desktop/web) -------------
    # Small, real operations the browser page cannot do itself: native pickers,
    # opening a folder, renaming or forgetting a record, rebuilding the index.
    @Slot(str, result=str)
    def pick_folder(self, title: str) -> str:
        from PySide6.QtWidgets import QFileDialog

        return QFileDialog.getExistingDirectory(None, title or "Choose a folder") or ""

    @Slot(str, result=str)
    def pick_file(self, title: str) -> str:
        from PySide6.QtWidgets import QFileDialog

        path, _filter = QFileDialog.getOpenFileName(None, title or "Choose a file")
        return path or ""

    @Slot(str, result="QVariant")
    @_guarded
    def open_path(self, path: str):
        """Open a folder (or file) in the system's own file manager."""
        from PySide6.QtGui import QDesktopServices

        if not path:
            return {"status": "error", "message": "No folder to open."}
        if not Path(path).exists():
            return {"status": "error", "message": f"{path} does not exist on this computer."}
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            return {"status": "error", "message": f"The system could not open {path}."}
        return {"status": "ok"}

    @Slot(str, result="QVariant")
    @_guarded
    def open_url(self, url: str):
        """Help links: the browser opens http(s) only."""
        from PySide6.QtGui import QDesktopServices

        if not url.startswith(("http://", "https://")):
            return {"status": "error", "message": "Only web links can be opened."}
        if not QDesktopServices.openUrl(QUrl(url)):
            return {"status": "error", "message": f"The system could not open {url}."}
        return {"status": "ok"}

    @Slot(str, str, result="QVariant")
    @_guarded
    def rename_game(self, game_id: str, name: str):
        """Change this system's own canonical name for a game; other systems keep
        theirs (one writer per file). Recorded in the entry's history."""
        name = (name or "").strip()
        if not name:
            return {"status": "error", "message": "A name is needed."}
        uuid = get_system_uuid()
        for entry in datadir.list_all_entries():
            if entry.get("system_uuid") == uuid and entry.get("game_id") == game_id:
                old = entry.get("canonical_name") or ""
                if old == name:
                    return {"status": "ok"}
                entry["canonical_name"] = name
                datadir.add_timeline_event(entry, "renamed", f"{old} -> {name}")
                games_db.save_entry_and_sync_cache(entry)
                self.refresh_games()
                return {"status": "ok"}
        return {"status": "error", "message": "This computer has no record of that game, so it cannot be renamed here."}

    @Slot(str, result="QVariant")
    @_guarded
    def forget_game(self, game_id: str):
        """Remove this system's record of a game. Files on disk are untouched."""
        games_db.delete_game(game_id)
        self.refresh_games()
        return {"status": "ok"}

    @Slot(result="QVariant")
    @_guarded
    def rebuild_index(self):
        games_db.rebuild_cache()
        self.refresh_games()
        return {"status": "ok"}

    @Slot(result="QVariant")
    @_guarded
    def get_app_info(self):
        import platform
        import sys

        from pythia import settings
        from pythia.util.log import LOG_FILENAME

        return {
            "status": "ok",
            "version": settings.VERSION,
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "os": platform.system(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "schema_version": datadir.SCHEMA_VERSION,
            "log_file": LOG_FILENAME,
            "config_file": settings.CONFIG_FILE,
            "issues_url": "https://github.com/bi0shacker001/pythia/issues",
            "docs_url": "https://github.com/bi0shacker001/pythia#readme",
        }

    @Slot(result="QVariant")
    @_guarded
    def get_ui_config(self):
        """What the page needs to know at start: whether the error console is on
        (--error-console, or `error_console = True` in pythia.conf) and the log level."""
        from pythia import settings

        return {
            "status": "ok",
            "error_console": self._error_console or settings.sio.read_bool_setting("error_console", False),
            "log_level": logging.getLevelName(logger.level).lower(),
        }

    @Slot(str, result="QVariant")
    @_guarded
    def set_log_level(self, level: str):
        from pythia import settings

        value = {"normal": logging.INFO, "verbose": logging.INFO, "debug": logging.DEBUG, "info": logging.INFO}.get(
            level
        )
        if value is None:
            return {"status": "error", "message": "Log level must be Normal or Debug."}
        logger.setLevel(value)
        settings.write_setting("log_level", logging.getLevelName(value).lower())
        return {"status": "ok"}

    # ---- the UI log, jobs and intake: what the pages read on load ---------------
    @Slot(result="QVariant")
    @_guarded
    def get_ui_log(self):
        with self._lock:
            return {"status": "ok", "entries": list(self._ui_log)}

    @Slot(result="QVariant")
    @_guarded
    def clear_ui_log(self):
        with self._lock:
            self._ui_log.clear()
        return {"status": "ok"}

    @Slot(str, str, str, result="QVariant")
    @_guarded
    def report_web_error(self, level: str, message: str, context: str):
        """The page's own errors (window.onerror, unhandled rejections, console.error)
        come here so they land in Pythia's log and the one UI log."""
        level = level if level in ("error", "warn", "info") else "error"
        self._record(level, "web", f"{message}" + (f" ({context})" if context else ""), None)
        return {"status": "ok"}

    @Slot(result="QVariant")
    @_guarded
    def get_jobs(self):
        with self._lock:
            return _jsonable({"status": "ok", "jobs": list(self._jobs)})

    @Slot(result="QVariant")
    @_guarded
    def clear_finished_jobs(self):
        with self._lock:
            self._jobs = [j for j in self._jobs if j["state"] == "running"]
        self.jobsChanged.emit(_jsonable(self._jobs))
        return {"status": "ok"}

    @Slot(result="QVariant")
    @_guarded
    def get_intake(self):
        """Downloads & intake: files the watcher saw and scan results waiting for a
        decision. Both lists are real and both start empty."""
        with self._lock:
            watcher = self._watcher and {k: v for k, v in self._watcher.items() if k != "job"}
            return _jsonable(
                {
                    "status": "ok",
                    "detected": list(self._detected),
                    "decisions": list(self._decisions),
                    "watcher": watcher,
                }
            )

    @Slot(str, result="QVariant")
    @_guarded
    def export_library_report(self, dest_dir: str):
        """Bulk > Export report: this computer's records as one JSON file."""
        dest = Path(_normalize_path(dest_dir))
        if not dest.is_dir():
            return {"status": "error", "message": f"{dest} is not a folder."}
        path = dest / f"pythia-library-{datetime.date.today().isoformat()}.json"
        path.write_text(json.dumps(self._games, indent=2), encoding="utf-8")
        return {"status": "ok", "path": str(path), "games": len(self._games)}

    @Slot(str, result="QVariant")
    @_guarded
    def export_diagnostics(self, dest_dir: str):
        """Settings > Advanced > Export diagnostics: the log file plus app, system
        and plugin facts. No credentials are included."""
        from pythia.util.log import LOG_FILENAME

        dest = Path(_normalize_path(dest_dir))
        if not dest.is_dir():
            return {"status": "error", "message": f"{dest} is not a folder."}
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        report = dest / f"pythia-diagnostics-{stamp}.json"
        data = {
            "app": self.get_app_info(),
            "system": self.get_system_info(),
            "plugins": self.get_all_plugins(),
            "ui_log": self.get_ui_log().get("entries", []),
            "jobs": self.get_jobs().get("jobs", []),
        }
        report.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        copied = None
        if Path(LOG_FILENAME).is_file():
            copied = dest / f"pythia-{stamp}.log"
            copied.write_bytes(Path(LOG_FILENAME).read_bytes())
        return {"status": "ok", "report": str(report), "log": str(copied) if copied else None}

    @Slot()
    def quit_app(self):
        from PySide6.QtWidgets import QApplication

        QApplication.instance().quit()

    # ---- features with no backend yet -------------------------------------------
    # Each page still calls its slot through the normal path; the slot answers
    # "unavailable" with the reason and logs the attempt. When the feature lands,
    # only the slot body changes.
    @Slot(result="QVariant")
    @_guarded
    def get_save_backups(self):
        return self._unavailable("get_save_backups", "Save backups are not built yet.")

    @Slot(result="QVariant")
    @_guarded
    def get_launcher_entries(self):
        return self._unavailable("get_launcher_entries", "Pythia does not write launcher entries yet.")

    @Slot(result="QVariant")
    @_guarded
    def get_tools(self):
        return self._unavailable("get_tools", "Downloadable tools are not managed on this platform yet.")

    @Slot(result="QVariant")
    @_guarded
    def get_library_folders(self):
        return self._unavailable(
            "get_library_folders",
            "Pythia does not keep a list of library folders yet; scan a folder when you add games.",
        )

    @Slot(result="QVariant")
    @_guarded
    def get_plugin_repositories(self):
        return self._unavailable(
            "get_plugin_repositories", "Plugin repositories are planned; plugins are installed by hand today."
        )

    @Slot(result="QVariant")
    @_guarded
    def get_scan_settings(self):
        return self._unavailable(
            "get_scan_settings",
            "Scan behaviour cannot be changed yet; a scan links exact matches and asks about the rest.",
        )

    @Slot(str, str, result="QVariant")
    @_guarded
    def set_engine_kind(self, game_id: str, kind: str):
        return self._unavailable(
            "set_engine_kind",
            "Correcting an engine by hand is not built yet; rescan after enabling the right engine plugin.",
        )

    @Slot(str, str, result="QVariant")
    @_guarded
    def apply_update(self, game_id: str, version: str):
        return self._unavailable("apply_update", "Pythia records updates but does not download or apply them yet.")

    @Slot(result="QVariant")
    @_guarded
    def reset_identity(self):
        return self._unavailable("reset_identity", "Resetting this computer's identity is not built yet.")

    @Slot(bool, result="QVariant")
    @_guarded
    def set_resident_watcher(self, enabled: bool):
        return self._unavailable(
            "set_resident_watcher", "A resident watcher is not built yet; use Watch a folder on the Downloads page."
        )

    @Slot(str, result="QVariant")
    @_guarded
    def set_sync_folder(self, path: str):
        return self._unavailable("set_sync_folder", "The library folder is set by PYTHIA_DATADIR_OVERRIDE for now.")

    @Slot(str, str, result="QVariant")
    @_guarded
    def set_option(self, key: str, value: str):
        """Switches on the Settings pages whose feature has no backend yet. Each key
        gains a body when its feature lands; until then the page's switch asks,
        hears "unavailable" and goes back."""
        messages = {
            "resident_watcher": "A resident watcher is not built yet; use Watch a folder on the Downloads page.",
            "warn_conflicts": "Conflict warnings between computers are not built yet.",
            "scan_link_exact": "Scan behaviour cannot be changed yet.",
            "scan_uncertain": "Scan behaviour cannot be changed yet.",
            "serve_lan": "Game transfer between computers is planned; no transfer protocol is chosen yet.",
        }
        if key not in messages:
            return {"status": "error", "message": f"Unknown option '{key}'."}
        return self._unavailable(f"set_option:{key}", messages[key])

    @Slot(str, result="QVariant")
    @_guarded
    def move_app_data(self, path: str):
        return self._unavailable(
            "move_app_data", "Moving the application data is not built yet; set PYTHIA_DATA_DIR before starting Pythia."
        )

    @Slot(result="QVariant")
    @_guarded
    def get_transfer_settings(self):
        return self._unavailable(
            "get_transfer_settings", "Game transfer between computers is planned; no transfer protocol is chosen yet."
        )

    @Slot(result="QVariant")
    @_guarded
    def get_storage_roles(self):
        return self._unavailable("get_storage_roles", "Storage roles arrive with save backups and game transfer.")
