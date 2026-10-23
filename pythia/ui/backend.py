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
import os
import threading
from pathlib import Path
from typing import Any

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
    """QML folder dialogs hand back file:// URLs, not plain paths -- accept either.
    A leading ~ is the home folder, as in the dialogs' example paths
    (pythia.path_check checks the same path)."""
    if path_str.startswith("file:"):
        return QUrl(path_str).toLocalFile()
    return os.path.expanduser(path_str) if path_str.startswith("~") else path_str


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


# How long one read of the agent's events waits for the next one, and the
# kinds after which the handheld panels redraw (a sync, an arrival, a launch,
# a save copy).
_AGENT_EVENTS_WAIT_S = 30
_HANDHELD_EVENT_KINDS = {"session", "library", "arrived", "launch", "saves"}


def _off_the_main_thread(fn):
    """A slot whose work reads or writes the library on disk, asks plugins or
    uses the network: it runs on a thread of its own through
    Backend._answer_later, so the window never waits on it. The page's call()
    waits for the answer the same way."""

    @functools.wraps(fn)
    def wrapper(self, *args):
        return self._answer_later(fn.__name__, lambda: fn(self, *args))

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
    handheldsChanged = Signal()  # a handheld was paired, forgotten or changed
    modesChanged = Signal()  # manager mode turned on or off (pythia.modes)
    pairingFinished = Signal("QVariant")  # {"status": "ok", "paired": name} or an error result
    asyncResult = Signal("QVariant")  # a slow slot's answer: {"request": id, ...its result}
    lookChanged = Signal("QVariant")  # a Look's live counts, its proposals, or that it was added (pythia.look)
    padAction = Signal(str)  # a controller press, named (pythia.pad), only while Pythia has the focus

    def __init__(self, error_console: bool = False):
        super().__init__()
        self._decision_event = threading.Event()
        self._decision_result = None
        self._awaiting_live_decision = False
        self._watch_stop_event = None
        self._watcher: dict[str, Any] | None = None
        self._async_requests = 0
        self._games = []
        self._lock = threading.Lock()
        self._ui_log: list[dict] = []
        self._jobs: list[dict] = []
        self._detected: list[dict] = []
        # Scan decisions waiting survive a restart (pythia.intake_store; UX review A16).
        from pythia import intake_store

        self._decisions: list[dict] = intake_store.load()
        self._error_console = error_console
        self._pad = None
        from pythia.look import LookSlot

        self._look = LookSlot()
        self._log_handler = _UiLogHandler(self)
        logger.addHandler(self._log_handler)
        self.refresh_games()
        self._follow_agent_events()
        # The handhelds' state changed (a pairing, a session): droidtop-agent's
        # folder gets it too while the agent is off (pythia.droidtop.takeover).
        self.handheldsChanged.connect(self._sync_with_agent)

    def _sync_with_agent(self):
        from pythia.droidtop import takeover

        takeover.sync_soon()

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

    def _unavailable(self, slot: str, message: str, title: str | None = None) -> dict:
        """A feature with no backend yet answers honestly, and the attempt is logged
        so a run's console shows what the page asked for. `title` names a different
        reason than "not built yet" (a missing optional module, say)."""
        self._record("warn", "python", f"{slot}: not available ({message})", slot)
        result = {"status": "unavailable", "slot": slot, "message": message}
        if title:
            result["title"] = title
        return result

    def _follow_agent_events(self):
        """While this process serves droidtop handhelds, what the agent's service
        does (a handheld asked to start a game, synced, a session ended, a
        version arrived) goes into the one UI log as source "agent", so the
        Activity page and the error console show it; the handheld panels redraw
        after a sync or an arrival. One background thread; nothing without the
        agent module."""
        from pythia import droidtop

        if not droidtop.available():
            return

        def run():
            from pythia.droidtop import AgentError, service

            seen = 0
            while True:
                try:
                    new = service.events(seen, wait=_AGENT_EVENTS_WAIT_S)
                except AgentError as ex:
                    self._record("warn", "agent", f"What the droidtop agent does is not shown here: {ex}")
                    return
                except Exception as ex:  # noqa: BLE001 -- a background thread: log once and stop
                    logger.exception("Following the droidtop agent's events failed: %s", ex)
                    return
                if not new:
                    continue
                seen = new[-1]["seq"]
                for event in new:
                    level = event.get("level") if event.get("level") in ("info", "warn") else "info"
                    self._record(level, "agent", event.get("message", ""), event.get("kind"))
                if any(event.get("kind") in _HANDHELD_EVENT_KINDS for event in new):
                    self.handheldsChanged.emit()
                # A favourite marked or unmarked on a handheld reaches this computer's records, and so the
                # other computers sharing the library folder (pythia.favourites).
                if any(event.get("kind") == "library" for event in new):
                    try:
                        from pythia import favourites

                        if favourites.take_from_handhelds():
                            self.refresh_games()
                    except Exception as ex:  # noqa: BLE001 -- a background thread: say so, keep following
                        logger.exception("Taking favourites from the handhelds failed: %s", ex)

        threading.Thread(target=run, name="droidtop-events", daemon=True).start()

    def _answer_later(self, slot: str, work) -> dict:
        """A slot whose work touches the disk or the network answers at once with
        {"status": "pending", "request": id}; `work()` runs on a thread of its own
        and its result (or the error it raised, logged like _guarded's) arrives
        as asyncResult with the same id. The page's call() waits for it, so to a
        page a slow slot reads like any other."""
        with self._lock:
            self._async_requests += 1
            request = f"{slot}-{self._async_requests}"

        def run():
            from pythia.droidtop import AgentError

            try:
                result = work()
            except AgentError as exc:  # the agent refused, in words a page can show: no traceback
                self._record("warn", "python", f"{slot}: {exc}", slot)
                result = {"status": "error", "slot": slot, "message": str(exc)}
            except Exception as exc:  # noqa: BLE001 -- the page needs the reason, whatever it was
                logger.exception("%s failed: %s", slot, exc, extra={"ui_slot": slot})
                result = {"status": "error", "slot": slot, "message": str(exc) or exc.__class__.__name__}
            self.asyncResult.emit(_jsonable({**result, "request": request}))

        threading.Thread(target=run, name=slot, daemon=True).start()
        return {"status": "pending", "slot": slot, "request": request}

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
        return str(job["id"])

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
        from pythia import favourites
        from pythia.covers import cover_url

        self._games = [
            {
                "game_id": gid,
                "entries": _jsonable(entries),
                "cover": cover_url(entries),
                "favourite": favourites.is_favourite(entries),
            }
            for gid, entries in sorted(games.items())
        ]
        self.gamesChanged.emit()
        # While this process serves droidtop handhelds, the library is theirs too.
        from pythia.droidtop import library as droidtop_library

        droidtop_library.export_soon()

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
        from pythia import archives

        if archives.is_archive(Path(path_str)):
            # A downloaded archive: unpacked beside itself, then added as that folder.
            try:
                folder = archives.unpack(Path(path_str))
            except (archives.ArchiveError, OSError) as ex:
                self._job_log(job, f"error: {ex}")
                self._job_finish(job, f"Could not unpack {Path(path_str).name}: {ex}", ok=False)
                self.onboardComplete.emit({"status": "error", "message": str(ex)})
                return
            self._job_log(job, f"Unpacked {Path(path_str).name} into {folder}")
            path_str = str(folder)
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
        """Scans a folder and records what it finds: the big-picture UI's
        scan (bigpicture/qml/HomeScreen.qml). The web UI looks first
        (start_look) and adds what the person keeps."""
        path_str = _normalize_path(path_str)
        job = self._job_start("scan", f"Scan {path_str}", "Finds every game folder inside")
        threading.Thread(target=self._scan_worker, args=(path_str, job), daemon=True).start()

    # ---- Look, Review, Add (pythia.look; UX review A16) ------------------------
    @Slot(str, result="QVariant")
    @_guarded
    def start_look(self, path_str: str):
        """Looks through a folder for games without writing anything; its
        proposals arrive as lookChanged, with live counts while it walks. A
        Look started while another runs replaces it (pythia.look.LookSlot)."""
        path_str = _normalize_path(path_str)
        job = self._job_start("scan", f"Look in {Path(path_str).name or path_str}", "Nothing is added until you Add")
        self.lookChanged.emit(_jsonable(self._look.start(job, path_str)))
        threading.Thread(target=self._look_worker, args=(path_str, job), daemon=True).start()
        return {"status": "ok", "job": job}

    def _look_worker(self, path_str: str, job: str):
        from pythia import look

        def progress(counts):
            shown = self._look.progress(job, counts)
            if shown is not None:
                self.lookChanged.emit(_jsonable(shown))

        try:
            result = look.look(path_str, progress=progress)
        except OSError as exc:
            self._job_finish(job, f"Could not look in {path_str}: {exc}", ok=False)
            if self._look.fail(job):
                self.lookChanged.emit({"status": "error", "message": str(exc)})
            return
        result["job"] = job
        if not self._look.finish(job, result):
            self._job_finish(job, "Replaced by a newer Look; nothing was added")
            return
        self._job_finish(job, result["summary"] + ". Review them, then Add.")
        self.lookChanged.emit(_jsonable(result))

    @Slot(result="QVariant")
    @_guarded
    def get_look(self):
        """The current Look: looking (with counts), its proposals, or none."""
        return _jsonable(self._look.get() or {"status": "none"})

    @Slot("QVariant", result="QVariant")
    @_guarded
    def add_from_look(self, rows):
        """Adds the games Review kept, as reviewed (pythia.look.add)."""
        rows = [dict(r) for r in rows or []]
        kept = [r for r in rows if r.get("keep", True)]
        title = f"Add {len(kept)} game{'s' if len(kept) != 1 else ''}"
        job = self._job_start("onboard", title, "From the reviewed Look")
        threading.Thread(target=self._add_worker, args=(rows, job), daemon=True).start()
        return {"status": "ok", "job": job}

    def _add_worker(self, rows, job):
        from pythia import look

        result = look.add(rows)
        for f in result["failures"]:
            self._job_log(job, f"not added: {f['name']} {f['path']}: {f['message']}")
        self.refresh_games()
        self._look.clear()
        text = f"Added {result['games']} game{'s' if result['games'] != 1 else ''} ({result['folders']} folders)"
        if result["failures"]:
            text += f", {len(result['failures'])} not added"
        self._job_finish(job, text, ok=not result["failures"] or bool(result["games"]))
        self.lookChanged.emit(_jsonable({**result, "status": "added", "summary": text}))

    @Slot()
    def discard_look(self):
        self._look.clear()
        self.lookChanged.emit({"status": "none"})

    @Slot(result="QVariant")
    @_guarded
    def start_store_scan(self):
        """Records the games the stores on this computer have installed (Steam,
        Epic, GOG, Amazon, itch, Battle.net, the Linux launchers), found by
        droidtop's scanner from the stores' own records (pythia.store_installs)."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("start_store_scan")
        job = self._job_start("scan", "Store games", "Finds the games each store on this computer has installed")
        threading.Thread(target=self._store_scan_worker, args=(job,), daemon=True).start()
        return {"status": "ok", "job": job}

    def _store_scan_worker(self, job: str):
        from pythia import store_installs
        from pythia.droidtop import AgentError

        try:
            counts = store_installs.record_new()
        except AgentError as ex:
            self._job_finish(job, f"Could not ask the stores: {ex}", ok=False)
            return
        for failure in counts["failures"]:
            self._job_log(job, f"  could not record {failure['folder']}: {failure['message']}")
        self.refresh_games()
        summary = (
            f"{counts['found']} store game{'s' if counts['found'] != 1 else ''} found: {counts['recorded']} recorded, "
            f"{counts['already']} already in the library"
        )
        if counts["failed"]:
            summary += f", {counts['failed']} failed"
        self._job_finish(job, summary, ok=not counts["failed"])

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
            elif r["status"] == "skipped":
                self._job_log(job, f"  left out: {r['message']}")
            else:
                self._job_log(job, f"  error: {r['message']}")
        self.refresh_games()
        ok = sum(1 for r in results if r["status"] == "ok")
        skipped = sum(1 for r in results if r["status"] == "skipped")
        bad = len(results) - ok - skipped
        self._job_finish(
            job,
            f"{ok} game{'s' if ok != 1 else ''} found"
            + (f", {skipped} folder{'s' if skipped != 1 else ''} left out (ROMs or a launcher)" if skipped else "")
            + (f", {bad} not identified" if bad else ""),
        )
        self.scanComplete.emit(_jsonable(results))

        for r in results:
            if r["status"] == "ok" and r["candidates"]["suggested"]:
                self._queue_decision(_jsonable(scanning.decision_for(r)))

    def _queue_decision(self, preview: dict):
        with self._lock:
            self._decisions = [d for d in self._decisions if d.get("path") != preview.get("path")]
            self._decisions.append(preview)
            self._keep_decisions()
        self._emit_intake()
        self.decisionNeeded.emit(preview)

    def _keep_decisions(self):
        """Writes the waiting decisions to disk; called with self._lock held."""
        from pythia import intake_store

        intake_store.save(_jsonable(list(self._decisions)))

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
            pending = next((d for d in self._decisions if d.get("path") == path_str), {})
            self._decisions = [d for d in self._decisions if d.get("path") != path_str]
            self._keep_decisions()
        self._emit_intake()
        if self._awaiting_live_decision:
            self._decision_result = decision
            self._decision_event.set()
            return
        # No live onboard is waiting -- this is a post-scan review item, which the
        # scan already recorded as a new game (`recorded`, the game id it made).
        recorded = (decision or {}).pop("recorded_game_id", None) or pending.get("recorded_game_id")
        if scanning.keeps_scan_record(decision, recorded):
            self._record("info", "pythia", f"Kept {Path(path_str).name} as a new game", "resolveDecision")
            return
        job = self._job_start("onboard", f"Record {Path(path_str).name}", path_str)
        threading.Thread(target=self._reonboard_worker, args=(path_str, decision, job, recorded), daemon=True).start()

    def _reonboard_worker(self, path_str, decision, job, recorded=None):
        result = scanning.resolve_decision(path_str, decision, recorded)
        ok = result.get("status") == "ok"
        self.refresh_games()
        self._job_finish(
            job, f"Recorded {result.get('name') or Path(path_str).name}" if ok else result.get("message", ""), ok
        )
        self.onboardComplete.emit(_jsonable(result))

    # ---- download watching -----------------------------------------------------
    @Slot(str, str, str)
    def start_watch_for(self, plugin_id: str, command_str: str, watch_dir_str: str):
        """start_watch for one downloader plugin: only that plugin's card and
        row say Watching (Droidtop/tracker#538 retest D4)."""
        self.start_watch(command_str, watch_dir_str, plugin_id)

    @Slot(str, str)
    def start_watch(self, command_str: str, watch_dir_str: str, plugin_id: str = ""):
        """Launch `command_str` (a single executable path -- no argument splitting
        yet) and watch `watch_dir_str` for new files, emitting downloadDetected for
        each. See downloads.launch_and_watch; this doesn't move or import anything
        itself, that's a separate start_onboard() call once the user picks one.
        Nothing starts while manager mode is off (pythia.modes)."""
        from pythia import modes

        manager = modes.manager_mode()
        if not manager["on"] and "downloads" in manager["pages"]:
            self._record("warn", "pythia", "Not watching: manager mode is off", "start_watch")
            return
        watch_dir_str = _normalize_path(watch_dir_str)
        self.stop_watch()
        self._watch_stop_event = threading.Event()
        job = self._job_start("watch", f"Watching {watch_dir_str}", f"Running {command_str}" if command_str else "")
        self._watcher = {
            "program": command_str,
            "folder": watch_dir_str,
            "plugin": plugin_id or None,
            "started_at": _now(),
            "job": job,
        }
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

    @Slot(str, "QVariant")
    def merge_cluster(self, surviving_id: str, retired_ids):
        """Folds every record of a Reconciliation cluster into one, one after
        another in one job (UX review A16: "Combine as versions")."""
        retired = [str(r) for r in retired_ids or [] if str(r) != surviving_id]
        job = self._job_start("merge", f"Combine {len(retired) + 1} records", f"into {surviving_id}")
        threading.Thread(target=self._merge_cluster_worker, args=(surviving_id, retired, job), daemon=True).start()

    def _merge_cluster_worker(self, surviving_id, retired, job):
        failed = []
        for retired_id in retired:
            result = reconciliation.merge(surviving_id, retired_id)
            if result.get("status") != "ok":
                failed.append(result.get("message", retired_id))
                self._job_log(job, f"not folded {retired_id}: {result.get('message')}")
        self.refresh_games()
        done = len(retired) - len(failed)
        text = f"Combined {done + 1} records into one" + (f", {len(failed)} not" if failed else "")
        self._job_finish(job, text, not failed)
        self.mergeComplete.emit({"status": "ok" if not failed else "error", "message": "; ".join(failed)})

    # ---- notes ------------------------------------------------------------------
    @Slot(str, str, result="QVariant")
    @_guarded
    @_off_the_main_thread
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

    @Slot(str, bool, result="QVariant")
    @_guarded
    def set_favourite(self, game_id: str, on: bool):
        """Marks a game the person's favourite, or not. It is the person's, so it
        reaches every computer sharing the library and every paired handheld
        (pythia.favourites); the newest mark wins wherever it was made."""
        from pythia import favourites

        result = favourites.set_favourite(game_id, on)
        self.refresh_games()
        return result

    @Slot(str, str, result="QVariant")
    @_guarded
    def set_current_version(self, game_id: str, version: str):
        """ "Set as current" (UX review A13): the version this computer treats as
        the game's current one, shown on rows and the hero instead of the
        latest installed, and the answer when versions cannot be ordered. An
        empty ``version`` clears it. Recorded in the entry's history."""
        uuid = get_system_uuid()
        for entry in datadir.list_all_entries():
            if entry.get("system_uuid") == uuid and entry.get("game_id") == game_id:
                labels = [g.get("version") or "" for g in entry.get("versions") or []]
                if version and version not in labels:
                    return {"status": "error", "message": f"{version} is not one of this game's versions here."}
                if version:
                    entry["current_version"] = version
                else:
                    entry.pop("current_version", None)
                datadir.add_timeline_event(entry, "current_set", version or "cleared")
                games_db.save_entry_and_sync_cache(entry)
                self.refresh_games()
                return {"status": "ok", "current": version or None}
        return {"status": "error", "message": "This computer has no record of that game."}

    # ---- a folder's kind and language (UX review A14) --------------------------
    @Slot(result="QVariant")
    @_guarded
    def get_folder_vocabulary(self):
        """The kinds a folder can be and the languages Pythia names, for the
        Versions section's choices and its folder names."""
        return {"status": "ok", "kinds": list(datadir.KINDS), "languages": dict(datadir.LANGUAGE_NAMES)}

    def _edit_variant(self, game_id: str, path: str, change) -> dict:
        uuid = get_system_uuid()
        for entry in datadir.list_all_entries():
            if entry.get("system_uuid") != uuid or entry.get("game_id") != game_id:
                continue
            for group in entry.get("versions") or []:
                for variant in group.get("variants") or []:
                    if variant.get("path") == path:
                        message = change(entry, group, variant)
                        if message:
                            return {"status": "error", "message": message}
                        games_db.save_entry_and_sync_cache(entry)
                        self.refresh_games()
                        return {"status": "ok"}
            return {"status": "error", "message": "That folder is not one of this game's folders here."}
        return {"status": "error", "message": "This computer has no record of that game."}

    @Slot(str, str, str, str, result="QVariant")
    @_guarded
    def set_variant_details(self, game_id: str, path: str, kind: str, language: str):
        """A folder's kind (game, mod, patch or translation) and language, as the
        person sets them; a rescan keeps them."""

        def change(entry, group, variant):
            if kind not in datadir.KINDS:
                return f"Kind is one of {', '.join(datadir.KINDS)}."
            if language and language not in datadir.LANGUAGE_NAMES and language != "mtl":
                return f"Pythia does not know the language {language!r}."
            variant["kind"] = kind
            variant["kind_set_by_person"] = True
            variant["language"] = language or None
            datadir.add_timeline_event(entry, "folder_described", f"{path}: {kind}, {language or 'no language'}")
            return None

        return self._edit_variant(game_id, path, change)

    @Slot(str, str, str, result="QVariant")
    @_guarded
    def link_variant_to_version(self, game_id: str, path: str, base_version: str):
        """Links a translation, patch or mod folder to the version it applies to
        (one action); an empty ``base_version`` unlinks it."""

        def change(entry, group, variant):
            labels = [g.get("version") or "" for g in entry.get("versions") or []]
            if base_version and base_version not in labels:
                return f"{base_version} is not one of this game's versions here."
            if base_version:
                variant["base_version"] = base_version
            else:
                variant.pop("base_version", None)
            datadir.add_timeline_event(entry, "folder_linked", f"{path} -> {base_version or 'unlinked'}")
            return None

        return self._edit_variant(game_id, path, change)

    # ---- store links on a game or a folder (pythia.store_links; UX review A15) ---
    def _edit_links(self, game_id: str, path: str, change) -> dict:
        """``change(holder, entry)`` on the game's entry (``path`` empty) or on its folder at ``path``."""
        uuid = get_system_uuid()
        for entry in datadir.list_all_entries():
            if entry.get("system_uuid") != uuid or entry.get("game_id") != game_id:
                continue
            holder: dict | None = entry
            if path:
                holder = next(
                    (v for g in entry.get("versions") or [] for v in g.get("variants") or [] if v.get("path") == path),
                    None,
                )
                if holder is None:
                    return {"status": "error", "message": "That folder is not one of this game's folders here."}
            result = change(holder, entry)
            if result.get("status") == "ok":
                games_db.save_entry_and_sync_cache(entry)
                self.refresh_games()
            return result
        return {"status": "error", "message": "This computer has no record of that game."}

    @Slot(str, str, str, result="QVariant")
    @_guarded
    def set_store_link(self, game_id: str, path: str, text: str):
        """Records a pasted store link or id on the game (``path`` empty) or on
        one folder. ``link`` says the store and whether Pythia checks it for
        updates (only F95zone does yet)."""
        from pythia import store_links

        try:
            link = store_links.parse(text)
        except store_links.LinkError as ex:
            return {"status": "error", "message": str(ex)}

        def change(holder, entry):
            store_links.put(holder, link)
            datadir.add_timeline_event(entry, "store_linked", f"{path or 'game'}: {link['platform']} {link['id']}")
            return {"status": "ok", "link": store_links.describe(link)}

        return self._edit_links(game_id, path, change)

    @Slot(str, str, str, result="QVariant")
    @_guarded
    def remove_store_link(self, game_id: str, path: str, platform: str):
        from pythia import store_links

        def change(holder, entry):
            if not store_links.remove(holder, platform):
                return {"status": "error", "message": f"No {platform} link there."}
            datadir.add_timeline_event(entry, "store_unlinked", f"{path or 'game'}: {platform}")
            return {"status": "ok"}

        return self._edit_links(game_id, path, change)

    @Slot(result="QVariant")
    @_guarded
    def describe_store_links(self):
        """Store names and which stores have an update check, for the page."""
        from pythia import store_links

        return {"status": "ok", "stores": store_links.STORE_NAMES, "update_checks": sorted(store_links.UPDATE_CHECKS)}

    @Slot(result="QVariant")
    @_guarded
    def read_clipboard(self):
        """The clipboard's text, for "Paste link"."""
        from PySide6.QtGui import QGuiApplication

        return {"status": "ok", "text": QGuiApplication.clipboard().text()}

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
    @_off_the_main_thread
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
    @_off_the_main_thread
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
                    item["state"] = {
                        "watching": downloads.downloader_watching(self._watcher, p.id),
                        "platforms": list(p.platforms),
                    }
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

    # ---- Start in (pythia.start_shell; UX review A8) --------------------------
    @Slot(result="QVariant")
    @_guarded
    def get_start(self):
        """``start_in`` (auto, desktop, handheld or last), ``shell`` (the one to
        start in now) and ``device`` (whether this is a handheld, and its
        maker and model)."""
        from pythia import start_shell

        device = start_shell.detect()
        return {
            "status": "ok",
            "start_in": start_shell.start_in(),
            "shell": start_shell.initial_shell(device),
            "device": device,
        }

    @Slot(str, result="QVariant")
    @_guarded
    def set_start_in(self, value: str):
        from pythia import start_shell

        start_shell.set_start_in(value)
        return {"status": "ok", "start_in": value}

    @Slot(str, result="QVariant")
    @_guarded
    def remember_shell(self, shell: str):
        """A confirmed, deliberate switch of shell (A4), what "Remember last" starts in."""
        from pythia import start_shell

        start_shell.remember(shell)
        return {"status": "ok"}

    # ---- the game page's primary slot (pythia.play; UX review A12) -------------
    def _entries_of(self, game_id: str) -> list[dict] | None:
        """The game's records as the library already holds them (refresh_games),
        or None to read them from disk."""
        with self._lock:
            game = next((g for g in self._games if g.get("game_id") == game_id), None)
        return list(game["entries"]) if game else None

    @Slot(str, result="QVariant")
    @_guarded
    @_off_the_main_thread
    def get_play_state(self, game_id: str):
        """Play, Install or Update for the game page's primary slot, each with
        whether it can run and why not, and the game's store page."""
        from pythia import play

        return play.state(game_id, self._entries_of(game_id))

    @Slot(str, result="QVariant")
    @_guarded
    def play_game(self, game_id: str):
        """Starts the game: through Steam (its steam:// link) or Pythia's runner."""
        from PySide6.QtGui import QDesktopServices

        from pythia import play

        result = play.play(game_id, self._entries_of(game_id))
        if result.get("status") == "ok" and result.get("url"):
            if not QDesktopServices.openUrl(QUrl(result["url"])):
                return {"status": "error", "message": "Steam did not open. Is it installed?"}
        if result.get("status") == "ok":
            self._record("info", "pythia", f"Started {game_id} ({result.get('via')})", "play_game")
        return result

    # ---- launcher hand-off --------------------------------------------------
    @Slot(str, result="QVariant")
    @_guarded
    @_off_the_main_thread
    def find_manager_for_game(self, game_id: str):
        """Every (manager plugin, source kind) this game could be handed off to --
        lets the UI show/hide an "Open" action and pick which source to use if more
        than one applies, without launching anything itself (see
        pythia.plugins.manager.find_for_game())."""
        from pythia.plugins import manager as manager_plugins
        from pythia.plugins.context import PluginContext

        entries = self._entries_of(game_id)
        if entries is None:
            entries = datadir.list_known_games().get(game_id, [])
        candidates = manager_plugins.find_for_game(entries)
        ctx = PluginContext(log=self._log)
        return _jsonable(
            {
                "status": "ok",
                "managers": [
                    {
                        "manager_id": p.id,
                        "manager_name": p.display_name,
                        "kind": d["kind"],
                        "available": bool(p.is_available(ctx)),
                    }
                    for p, d in candidates
                ],
            }
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

        entries = self._entries_of(game_id)
        if entries is None:
            entries = datadir.list_known_games().get(game_id, [])
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
        known = self._entries_of(game_id)
        for entry in known if known is not None else datadir.list_all_entries():
            if entry.get("system_uuid") != uuid or entry.get("game_id") != game_id:
                continue
            for group in entry.get("versions", []):
                for variant in group.get("variants", []):
                    if variant.get("path"):
                        return entry, group, variant
        return None, None, None

    @Slot(str, result="QVariant")
    @_guarded
    @_off_the_main_thread
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

    @Slot(result="QVariant")
    @_guarded
    def start_pad(self):
        """Starts reading game controllers (pythia.pad), once; each press then
        arrives as padAction while one of Pythia's windows has the focus.
        ``available`` is false, with ``reason``, when they cannot be read."""
        from pythia.pad import PadReader

        if self._pad is None:
            self._pad = PadReader(self.padAction.emit)
            self._pad.start()
        return {
            "status": "ok",
            "available": self._pad.problem is None,
            "reason": self._pad.problem,
            "pads": self._pad.pads(),
        }

    @Slot(str, str, result="QVariant")
    @_guarded
    def check_path(self, path_str: str, want: str):
        """Whether a dialog's path will do before anything starts
        (pythia.path_check; want is game, folder, writable or program).
        ``reason`` is empty when it will."""
        from pythia import path_check

        return {"status": "ok", "reason": path_check.check(_normalize_path(path_str), want)}

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
    @_off_the_main_thread
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
    @_off_the_main_thread
    def forget_game(self, game_id: str):
        """Remove this system's record of a game. Files on disk are untouched."""
        games_db.delete_game(game_id)
        self.refresh_games()
        return {"status": "ok"}

    @Slot(result="QVariant")
    @_guarded
    @_off_the_main_thread
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
    @_off_the_main_thread
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

    # ---- handhelds: this computer as the droidtop agent (pythia.droidtop) --------
    # Pythia's own droidtop module (pythia._native, on droidtop-agent's crates).
    # In a source tree that was never built there is none: every slot here
    # answers "unavailable" with how to build it.
    def _agent_unavailable(self, slot: str) -> dict:
        from pythia.droidtop import why_unavailable

        return self._unavailable(
            slot,
            why_unavailable() or "Pythia's droidtop module is not built.",
            title="Pythia's droidtop module is not built",
        )

    @Slot(result="QVariant")
    @_guarded
    @_off_the_main_thread
    def get_agent_status(self):
        """This computer as handhelds know it, whether this process serves
        them (or why not), the addresses a handheld can use, and droidtop-agent
        (running, starting at sign-in, turned off by Pythia). Off the main
        thread: it looks through the running processes."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("get_agent_status")
        from pythia.droidtop import launch, pair, service, store, takeover

        return _jsonable(
            {
                "status": "ok",
                "identity": store.identity(),
                "serving": service.serving(),
                "places": pair.places(),
                "agent": takeover.status(),
                "host_launch": launch.supported(),
            }
        )

    @Slot(result="QVariant")
    @_guarded
    @_off_the_main_thread
    def turn_agent_back_on(self):
        """droidtop-agent serves the handhelds again: its start at sign-in is
        put back as it was, with Pythia's state written into its folder, and
        Pythia stops serving them from its next start."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("turn_agent_back_on")
        from pythia.droidtop import takeover

        done = takeover.turn_agent_back_on()
        self.modesChanged.emit()
        kept = ", ".join(done["entries"]) or "no start entry kept"
        self._record("info", "pythia", f"droidtop-agent turned back on ({kept})")
        return {"status": "ok", **done}

    @Slot(result="QVariant")
    @_guarded
    def serve_from_pythia_again(self):
        """Sync with droidtop on, which is the person's consent (docs/SPEC.md
        1.7): Pythia takes droidtop-agent's place and serves now."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("serve_from_pythia_again")
        from pythia.droidtop import service, takeover

        takeover.serve_from_pythia_again()
        service.start_in_background()
        self.modesChanged.emit()
        return {"status": "ok"}

    @Slot(result="QVariant")
    @_guarded
    def get_handhelds(self):
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("get_handhelds")
        from pythia.droidtop import store

        return _jsonable({"status": "ok", "devices": store.devices()})

    @Slot(result="QVariant")
    @_guarded
    @_off_the_main_thread
    def get_handheld_games(self):
        """Each paired handheld with the games the shared library says are
        installed on it (what it last synced to this computer)."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("get_handheld_games")
        from pythia.droidtop import store

        return _jsonable({"status": "ok", "handhelds": store.games_by_device(store.library(), store.devices())})

    @Slot(str, result="QVariant")
    @_guarded
    def pair_with_code(self, code: str):
        """Pairs with a handheld showing `code`, on a job of its own (it waits
        on the network); pairingFinished says how it went."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("pair_with_code")
        job = self._job_start("pair", "Pair a handheld", "With the code the handheld shows")
        threading.Thread(target=self._pair_worker, args=(code.strip(), job), daemon=True).start()
        return {"status": "ok", "job": job}

    def _pair_worker(self, code: str, job: str):
        from pythia.droidtop import AgentError, pair

        try:
            name = pair.pair(code)
            result = {"status": "ok", "paired": name}
            self._job_finish(job, f"Paired with {name}")
        except AgentError as exc:
            result = {"status": "error", "message": str(exc)}
            self._job_finish(job, str(exc), ok=False)
        self.pairingFinished.emit(result)
        self.handheldsChanged.emit()

    @Slot(result="QVariant")
    @_guarded
    def show_pairing_code(self):
        """This computer shows a code for 10 minutes: the code, the addresses
        with their labels and the invitation text; pairing_status says how
        it is going."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("show_pairing_code")
        from pythia.droidtop import pair

        return _jsonable({"status": "ok", **pair.show()})

    @Slot(result="QVariant")
    @_guarded
    def pairing_status(self):
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("pairing_status")
        from pythia.droidtop import pair

        status = pair.status()
        if status.get("state") == "paired":
            self.handheldsChanged.emit()
        return _jsonable({"status": "ok", **status})

    @Slot(result="QVariant")
    @_guarded
    def cancel_pairing(self):
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("cancel_pairing")
        from pythia.droidtop import pair

        pair.cancel()
        return {"status": "ok"}

    # ---- one code on both screens (docs/SPEC.md 7.1, 7.2) ------------------------
    @Slot(result="QVariant")
    @_guarded
    @_off_the_main_thread
    def start_pair_offer(self):
        """This computer can be picked by a handheld or another computer while
        the pairing sheet is open; pair_compare_status follows it."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("start_pair_offer")
        from pythia.droidtop import pair

        return _jsonable({"status": "ok", **pair.offer()})

    @Slot(result="QVariant")
    @_guarded
    def pair_compare_status(self):
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("pair_compare_status")
        from pythia.droidtop import pair

        status = pair.compare_status()
        if status.get("state") == "paired":
            self.handheldsChanged.emit()
        return _jsonable({"status": "ok", **status})

    @Slot(result="QVariant")
    @_guarded
    @_off_the_main_thread
    def find_computers(self):
        """Other computers on this network offering to pair (another Pythia
        with its Devices page open)."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("find_computers")
        from pythia.droidtop import pair

        return _jsonable({"status": "ok", "computers": pair.find()})

    @Slot("QVariant", result="QVariant")
    @_guarded
    @_off_the_main_thread
    def pick_computer(self, computer):
        """Starts comparing with a computer find_computers listed: the code."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("pick_computer")
        from pythia.droidtop import AgentError, pair

        try:
            return _jsonable({"status": "ok", **pair.pick(dict(computer))})
        except AgentError as exc:
            return {"status": "error", "message": str(exc)}

    @Slot(bool, result="QVariant")
    @_guarded
    @_off_the_main_thread
    def confirm_pairing(self, same: bool):
        """The person's answer to "same code on both screens?"; waits for the
        other device's."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("confirm_pairing")
        from pythia.droidtop import AgentError, pair

        try:
            name = pair.confirm(bool(same))
        except AgentError as exc:
            return {"status": "error", "message": str(exc)}
        self.handheldsChanged.emit()
        return {"status": "ok", "paired": name}

    @Slot(result="QVariant")
    @_guarded
    def stop_pair_offer(self):
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("stop_pair_offer")
        from pythia.droidtop import pair

        pair.stop()
        return {"status": "ok"}

    @Slot(result="QVariant")
    @_guarded
    def pairing_network_explained(self):
        """Whether Pythia has said why pairing needs the network (SPEC 7.5)."""
        from pythia import settings

        return {"status": "ok", "explained": settings.read_bool_setting("pairing_network_explained", False)}

    @Slot(result="QVariant")
    @_guarded
    def mark_pairing_network_explained(self):
        from pythia import settings

        settings.write_setting("pairing_network_explained", True)
        return {"status": "ok"}

    @Slot(str, result="QVariant")
    @_guarded
    def device_syncs(self, device_id: str):
        """What syncs with a paired device (UX review AG6), from the sync model."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("device_syncs")
        from pythia.droidtop import store, syncs

        device = next((d for d in store.devices() if d.get("id") == device_id), None)
        if device is None:
            return {"status": "error", "message": "That device is not paired."}
        return _jsonable(
            {"status": "ok", "name": device.get("name"), "rows": syncs.what_syncs(device, store.settings())}
        )

    @Slot(str, result="QVariant")
    @_guarded
    def forget_handheld(self, device_id: str):
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("forget_handheld")
        from pythia.droidtop import store

        store.unpair(device_id)
        self.handheldsChanged.emit()
        return {"status": "ok"}

    @Slot(str, result="QVariant")
    @_guarded
    def set_primary_handheld(self, device_id: str):
        """The handheld whose saves win here when both changed; "" for the newest copy."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("set_primary_handheld")
        from pythia.droidtop import store

        store.set_primary(device_id or None)
        self.handheldsChanged.emit()
        return {"status": "ok"}

    @Slot(str, bool, result="QVariant")
    @_guarded
    def set_handheld_launch(self, device_id: str, allowed: bool):
        """Whether a handheld may start games here (to stream them with Steam Link)."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("set_handheld_launch")
        from pythia.droidtop import store

        store.set_launch(device_id, allowed)
        self.handheldsChanged.emit()
        return {"status": "ok"}

    @Slot(str, result="QVariant")
    @_guarded
    @_off_the_main_thread
    def device_offered(self, device_id: str):
        """What a paired device offers this computer, and its games
        (droidtop-agent DESIGN section 20)."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("device_offered")
        from pythia.droidtop import remote

        return _jsonable({"status": "ok", **remote.offered(device_id)})

    @Slot(str, str, str, result="QVariant")
    @_guarded
    def accept_offered_game(self, device_id: str, key: str, title: str):
        """Copies a game a paired device offers into this computer's first
        game folder, as a job with its progress."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("accept_offered_game")
        job = self._job_start("accept", f"Copy {title}", "Reaching the device")
        threading.Thread(target=self._accept_worker, args=(device_id, key, title, job), daemon=True).start()
        return {"status": "ok", "job": job}

    def _accept_worker(self, device_id: str, key: str, title: str, job: str):
        import json
        import tempfile

        from pythia.droidtop import AgentError, remote

        progress = Path(tempfile.gettempdir()) / f"pythia-accept-{job}.json"
        done = threading.Event()

        def watch():
            while not done.wait(1.0):
                try:
                    p = json.loads(progress.read_text())
                except (OSError, ValueError):
                    continue
                total = p.get("total") or 0
                with self._lock:
                    one = next((j for j in self._jobs if j["id"] == job), None)
                    if one is not None and total:
                        one["detail"] = f"{p.get('done', 0) * 100 // total}% of {total // (1024 * 1024)} MiB"
                self.jobsChanged.emit(_jsonable(self._jobs))

        threading.Thread(target=watch, daemon=True).start()
        try:
            accepted = remote.accept(device_id, key, title, progress)
        except AgentError as exc:
            self._job_finish(job, f"{title} was not copied: {exc}", ok=False)
        else:
            # The copy is a new game folder here: it joins Pythia's library the
            # way any added folder does, asking only when its game is unsure.
            self._job_log(job, f"Copied {title} into {accepted.get('folder')}")
            self._onboard_worker(str(accepted.get("folder")), job)
        finally:
            done.set()
            progress.unlink(missing_ok=True)

    @Slot(str, str, str, result="QVariant")
    @_guarded
    @_off_the_main_thread
    def play_on_device(self, device_id: str, key: str, title: str):
        """Asks a paired device to start a game there and stream it."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("play_on_device")
        from pythia.droidtop import remote

        started, said = remote.play_said(remote.play_on(device_id, key, title))
        return {"status": "ok" if started else "error", "message": said}

    @Slot(str, str, bool, result="QVariant")
    @_guarded
    def set_device_offer(self, device_id: str, what: str, on: bool):
        """What this computer offers a paired device: ``library`` or ``saves``
        (droidtop-agent docs/DESIGN.md section 20; saves need the library)."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("set_device_offer")
        if what not in ("library", "saves"):
            return {"status": "error", "message": "Only the library or the saves can be offered."}
        from pythia.droidtop import store

        store.set_offer(device_id, **{what: on})
        self.handheldsChanged.emit()
        return {"status": "ok"}

    # ---- features with no backend yet -------------------------------------------
    # Each page still calls its slot through the normal path; the slot answers
    # "unavailable" with the reason and logs the attempt. When the feature lands,
    # only the slot body changes.
    @Slot(result="QVariant")
    @_guarded
    def get_save_backups(self):
        """The copies of saves the droidtop agent keeps on this computer when a
        sync with a handheld would overwrite saves that differ: one row per
        copy, the newest from each device first."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("get_save_backups")
        from pythia.droidtop import saves

        return self._answer_later(
            "get_save_backups", lambda: {"status": "ok", "backups": self._save_backup_rows(saves)}
        )

    @staticmethod
    def _save_backup_rows(saves) -> list[dict]:
        rows = []
        for game in saves.copies():
            for copy in game["copies"]:
                stamp = copy["stamp"]
                when = (
                    f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}Z"
                    if len(stamp) >= 15
                    else None
                )
                rows.append(
                    {
                        "key": game.get("key"),
                        "title": game.get("title") or "",
                        "game": game.get("title") or "A game from a handheld, with no title known",
                        "system": ("Newest from " if copy["newest"] else "Older, from ") + copy["device"],
                        "last_snapshot": when,
                        "state": "To look at" if not copy["newest"] and not copy["reviewed"] else "Kept",
                        "size": f"{copy['files']} file{'' if copy['files'] == 1 else 's'}",
                        "path": copy["path"],
                    }
                )
        return rows

    @Slot(str, str, str, result="QVariant")
    @_guarded
    def restore_save_copy(self, key: str, path: str, title: str):
        """Puts a kept copy of saves back where the game keeps them; the agent
        keeps what is there now as this computer's own copy first."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("restore_save_copy")
        from pythia.droidtop import saves

        def work():
            files = saves.restore(key, path, title)
            self._record("info", "python", f"Put back {files} save files for {title or key}", "restore_save_copy")
            return {"status": "ok", "files": files}

        return self._answer_later("restore_save_copy", work)

    @Slot(str, bool, result="QVariant")
    @_guarded
    @_off_the_main_thread
    def get_game_saves(self, game_id: str, fetch: bool):
        """Where the droidtop agent finds this game's saves on this computer: the
        `patterns` it knows (None: none known) and the `files` there now. With
        `fetch`, the agent first downloads the Ludusavi manifest if it has none."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("get_game_saves")
        from pythia.droidtop import library, saves

        entries = self._entries_of(game_id)
        if entries is None:
            entries = datadir.list_known_games().get(game_id) or []
        if not entries:
            return {"status": "error", "message": "This game is no longer in the library."}
        mine = [e for e in entries if e.get("system_uuid") == get_system_uuid()]
        entry = (mine or entries)[0]
        key, title = library.game_key(entry), entry.get("canonical_name") or ""
        return {"status": "ok", "key": key, **saves.lookup(key, title, fetch=fetch)}

    @Slot(result="QVariant")
    @_guarded
    def back_up_saves_now(self):
        """Keeps a copy of the saves of every game this computer has in the
        library, where their save location is known (droidtop's saves crate and
        the Ludusavi manifest), as this computer's own copy on the Save backups
        page: the copy kept before moves into the dated archive."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("back_up_saves_now")
        from pythia.droidtop import library, saves

        def work():
            uuid = get_system_uuid()
            games = [
                {"key": library.game_key(e), "title": e.get("canonical_name") or ""}
                for e in datadir.list_all_entries()
                if e.get("system_uuid") == uuid
            ]
            done = saves.back_up_all(games)
            self._record(
                "info",
                "pythia",
                f"Backed up the saves of {done['backed_up']} game(s) ({done['files']} file(s)); "
                f"{done['no_saves']} had none here yet, {done['unknown']} have no known save location",
                "back_up_saves_now",
            )
            return {"status": "ok", **done, "games": len(games)}

        return self._answer_later("back_up_saves_now", work)

    @Slot(result="QVariant")
    @_guarded
    @_off_the_main_thread
    def get_launcher_entries(self):
        """What each launcher on this computer lists (droidtop's scanner reads the
        stores' own records), against the library: tracked, drifted (in the
        library under another folder) or not in the library."""
        from pythia import droidtop

        if not droidtop.available():
            return self._agent_unavailable("get_launcher_entries")
        from pythia import store_installs
        from pythia.droidtop import store

        name = store.identity().get("name") or ""
        return {"status": "ok", "system": name, "entries": store_installs.entries()}

    @Slot(str, str, result="QVariant")
    @_guarded
    @_off_the_main_thread
    def record_launcher_entry(self, folder: str, key: str):
        """Records a launcher's game folder in the library, with its store id."""
        from pythia import store_installs

        result = store_installs.record(folder, key)
        self.refresh_games()
        return _jsonable(result)

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

    # ---- modes (pythia.modes): agent mode forces manager mode ----------------
    @Slot(result="QVariant")
    @_guarded
    def get_modes(self):
        """Manager mode (``on``, ``locked`` while Pythia is the agent, the
        ``reason``, the ``pages`` it covers) and agent mode."""
        from pythia import modes

        return {
            "status": "ok",
            "manager": modes.manager_mode(),
            "agent": modes.agent_mode(),
            "sync": modes.sync_with_droidtop(),
            "service": modes.service_mode(),
        }

    @Slot(str, result="QVariant")
    @_guarded
    @_off_the_main_thread
    def set_service_mode(self, action: str):
        """Service mode (docs/SPEC.md 1.10): ``enable``, ``disable`` or
        ``uninstall`` windowcast's secure-screen helper. Windows asks for
        administrator approval; this waits for the person. The page shows the
        consent before ``enable``."""
        from pythia import modes, secure_screen

        if action not in ("enable", "disable", "uninstall"):
            return {"status": "error", "message": f"{action} is not enable, disable or uninstall"}
        try:
            done = secure_screen.change(action)
        except Exception as ex:  # noqa: BLE001 -- the reason is the answer
            return {"status": "error", "message": str(ex), "service": modes.service_mode()}
        self.modesChanged.emit()
        return {"status": "ok", "message": done, "service": modes.service_mode()}

    @Slot(bool, result="QVariant")
    @_guarded
    def set_manager_mode(self, on: bool):
        """Turns manager mode on or off; refused, with the reason, while Pythia
        is the agent (owner, 2026-10-16: "agent mode should force manager mode")."""
        from pythia import modes

        try:
            manager = modes.set_manager_mode(on)
        except modes.ModeLocked as ex:
            self._record("warn", "pythia", f"Manager mode stays on: {ex}", "set_manager_mode")
            return {"status": "error", "message": str(ex), "locked": True}
        self._record("info", "pythia", f"Manager mode {'on' if manager['on'] else 'off'}", "set_manager_mode")
        self.modesChanged.emit()
        return {"status": "ok", "manager": manager}

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
