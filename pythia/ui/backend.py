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

from pythia import cache, datadir, downloads, onboarding, reconciliation, scanning
from pythia.identity import get_system_uuid


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
    accountSyncComplete = Signal(str, object)

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

    @Slot(str, str)
    def merge_games(self, surviving_id: str, retired_id: str):
        threading.Thread(target=self._merge_worker, args=(surviving_id, retired_id), daemon=True).start()

    def _merge_worker(self, surviving_id, retired_id):
        result = reconciliation.merge(surviving_id, retired_id)
        self.refresh_games()
        self.mergeComplete.emit(_jsonable(result))

    # ---- notes ------------------------------------------------------------------
    @Slot(str, str)
    def save_notes(self, game_id: str, text: str):
        """Update this system's own entry for `game_id`'s free-text notes field
        (part of the schema since Milestone 3). No-op if this system doesn't own
        that game_id -- notes are per-system-written, same as everything else."""
        uuid = get_system_uuid()
        for entry in datadir.list_all_entries():
            if entry.get("system_uuid") == uuid and entry.get("game_id") == game_id:
                entry["notes"] = text
                saved_path = datadir.save_entry(entry)
                cache.upsert_entry(entry, saved_path.name)
                self.refresh_games()
                return

    # ---- direct-launch variants ----------------------------------------------
    @Slot(str, str, str)
    def set_variant_direct(self, game_id: str, variant_path: str, executable: str):
        """Flag (or clear, if `executable` is empty) a specific variant as
        direct-launch -- Pythia's one deliberate exception to never launching a
        game's own executable (see pythia.plugins.manager.direct). Doesn't affect
        source/version/update tracking, only how it launches. This system's own
        entry only, same as save_notes -- a variant's path is inherently
        system-local."""
        threading.Thread(
            target=self._set_variant_direct_worker, args=(game_id, variant_path, executable), daemon=True
        ).start()

    def _set_variant_direct_worker(self, game_id: str, variant_path: str, executable: str):
        uuid = get_system_uuid()
        for entry in datadir.list_all_entries():
            if entry.get("system_uuid") != uuid or entry.get("game_id") != game_id:
                continue
            changed = False
            for group in entry.get("versions", []):
                for variant in group.get("variants", []):
                    if variant.get("path") == variant_path:
                        variant["direct"] = bool(executable)
                        variant["executable"] = executable or None
                        changed = True
            if changed:
                saved_path = datadir.save_entry(entry)
                cache.upsert_entry(entry, saved_path.name)
                self.refresh_games()
            return

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

    @Slot(str, bool)
    def set_plugin_enabled(self, plugin_id: str, enabled: bool):
        from pythia.plugins.registry import all_plugins, get_enabled_plugin_ids, set_enabled_plugin_ids

        allowed = get_enabled_plugin_ids()
        current = set(allowed) if allowed is not None else {p.id for p in all_plugins()}
        if enabled:
            current.add(plugin_id)
        else:
            current.discard(plugin_id)
        set_enabled_plugin_ids(list(current))

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

    @Slot(str, str, str)
    def set_account_credential(self, plugin_id: str, key: str, value: str):
        from pythia.plugins import library as library_plugins

        for p in library_plugins.all_plugins():
            if p.id == plugin_id:
                p.set_credential(key, value)
                return

    @Slot(str)
    def sync_account(self, plugin_id: str):
        threading.Thread(target=self._sync_account_worker, args=(plugin_id,), daemon=True).start()

    def _sync_account_worker(self, plugin_id: str):
        from pythia.plugins import library as library_plugins

        plugin = next((p for p in library_plugins.all_plugins() if p.id == plugin_id), None)
        if plugin is None:
            return
        result = onboarding.sync_owned(plugin)
        self.refresh_games()
        self.accountSyncComplete.emit(plugin_id, _jsonable(result))

    @Slot(str)
    def import_f95(self, db_path: str):
        """One-shot F95Checker db.sqlite3 import (see plugins/library/f95.py) --
        previously CLI-only (`pythia f95-import`), never reachable from the
        desktop UI at all until now. Reuses accountSyncComplete since the
        result shape (linked/created/status) is identical to a real account
        sync's, even though F95's import isn't an ongoing sync."""
        threading.Thread(target=self._import_f95_worker, args=(db_path,), daemon=True).start()

    def _import_f95_worker(self, db_path: str):
        from pythia.plugins.library import f95 as f95_module
        from pythia.plugins.library.f95 import plugin as f95_plugin

        if db_path:
            f95_module.set_db_path(db_path)
        result = onboarding.sync_owned(f95_plugin)
        self.refresh_games()
        self.accountSyncComplete.emit(f95_plugin.id, _jsonable(result))

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
        threading.Thread(target=self._open_in_manager_worker, args=(game_id, manager_id), daemon=True).start()

    def _open_in_manager_worker(self, game_id: str, manager_id: str):
        from pythia.plugins import manager as manager_plugins
        from pythia.plugins.context import PluginContext

        games = datadir.list_known_games()
        entries = games.get(game_id, [])
        candidates = manager_plugins.find_for_game(entries)
        match = next((c for c in candidates if c[0].id == manager_id), None)
        if match is None:
            self.logLine.emit(f"No manager plugin '{manager_id}' available for this game.")
            return
        plugin, detection = match
        ctx = PluginContext(log=self.logLine.emit)
        ok = plugin.open(detection, ctx)
        self.logLine.emit("Hand-off attempted." if ok else "Hand-off failed or not supported for this game.")
