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

import threading
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from pythia import datadir, downloads, onboarding, reconciliation, scanning


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


class Backend(QObject):
    logLine = Signal(str)
    decisionNeeded = Signal(object)  # a preview-shaped dict: {"path", "name", "candidates", ...}
    onboardComplete = Signal(object)
    scanComplete = Signal(object)
    gamesChanged = Signal()
    downloadDetected = Signal(str)  # a new file's path, from a watched Downloads folder
    mergeCandidatesReady = Signal(object)
    mergeComplete = Signal(object)

    def __init__(self):
        super().__init__()
        self._decision_event = threading.Event()
        self._decision_result = None
        self._awaiting_live_decision = False
        self._watch_stop_event = None
        self._games = []
        self.refresh_games()

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

    # ---- onboard a single path (interactive) -----------------------------------
    @Slot(str)
    def start_onboard(self, path_str: str):
        path_str = _normalize_path(path_str)
        threading.Thread(target=self._onboard_worker, args=(path_str,), daemon=True).start()

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
        threading.Thread(target=self._scan_worker, args=(path_str,), daemon=True).start()

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
        threading.Thread(target=self._reonboard_worker, args=(path_str, decision), daemon=True).start()

    def _reonboard_worker(self, path_str, decision):
        result = onboarding.onboard(path_str, decision=decision)
        self.refresh_games()
        self.onboardComplete.emit(_jsonable(result))

    # ---- download watching -----------------------------------------------------
    @Slot(str, str)
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
        threading.Thread(target=self._merge_candidates_worker, daemon=True).start()

    def _merge_candidates_worker(self):
        candidates = reconciliation.find_merge_candidates()
        self.mergeCandidatesReady.emit(_jsonable(candidates))

    @Slot(int, int)
    def merge_games(self, surviving_id: int, retired_id: int):
        threading.Thread(target=self._merge_worker, args=(surviving_id, retired_id), daemon=True).start()

    def _merge_worker(self, surviving_id, retired_id):
        result = reconciliation.merge(surviving_id, retired_id)
        self.refresh_games()
        self.mergeComplete.emit(_jsonable(result))
