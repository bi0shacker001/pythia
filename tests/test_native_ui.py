"""Tests for pythia.ui.native. Two tiers:

- Pure adapter-logic tests (summarize_game) that don't need a QApplication.
- Real PySide6 model/widget tests, run offscreen (QT_QPA_PLATFORM=offscreen)
  since this environment has no real display -- skipped entirely if PySide6
  isn't installed, honestly reported rather than silently passing."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PySide6  # noqa: F401

    HAVE_QT = True
except ImportError:
    HAVE_QT = False


if HAVE_QT:
    from PySide6.QtCore import QObject, Signal

    _FakeBackendBase = QObject
else:
    _FakeBackendBase = object


class FakeBackend(_FakeBackendBase):
    """A stand-in exposing exactly the real pythia.ui.backend.Backend return
    shapes the native UI handlers were found (2026-08-08, Codex correctness
    review) to have misread: find_manager_for_game() -> list,
    get_engine_status()["platforms"] -> dict keyed by platform,
    rescan_game() -> {"status","processed","errors"} with no "message" key.
    Records every call so tests can assert on the exact arguments a handler
    passed, not just that "something" happened. Real Qt Signals for
    mergeCandidatesReady/mergeComplete (2026-08-08, reconcile-review depth)
    -- ReconcileDialog connects to these for real, so this needs to be a
    real QObject to test that wiring, not just record calls. Only defined
    when PySide6 is actually importable, so the plain-data (SummarizeGameTests)
    suite still collects even without it."""

    if HAVE_QT:
        mergeCandidatesReady = Signal(object)
        mergeCandidatesFailed = Signal(str)
        mergeComplete = Signal(object)
        syncStatusChanged = Signal(object)
        syncTransferOfferReceived = Signal(object)
        syncTransferComplete = Signal(object)
        syncMetadataSynced = Signal(object)
        syncStagingComplete = Signal(object)
        f95LoginComplete = Signal(object)
        downloadHostLoginComplete = Signal(object)
        accountSyncComplete = Signal(str, object)

    def __init__(self):
        if HAVE_QT:
            super().__init__()
        self.calls: list[tuple] = []
        self.find_manager_result: list = []
        self.engine_status_result = {"status": "ok", "plugin_name": "Test Engine", "manual": False, "platforms": {}}
        self.engine_options: list = []
        self.launch_readiness_result = {"ready": False, "state": "not_configured", "reason": ""}
        self.launch_result = {"success": True, "message": "Launched."}
        self.rescan_result = {"status": "ok", "processed": 1, "errors": 0}
        self.dismiss_updates_result = {"status": "ok", "dismissed": 1, "message": "Dismissed."}
        self.games: list = []
        self.saved_searches: list = []
        self.bulk_action_result = {"status": "ok", "processed": 1, "errors": 0}
        self.all_plugins: list = []
        self.export_result = {"status": "ok", "path": "/tmp/export.json", "count": 1}
        self.merge_calls: list = []
        self.check_plugin_index_result = {"status": "ok", "plugins": []}
        self.install_from_index_result = {"status": "ok", "id": "some.plugin", "display_name": "Some Plugin"}
        self.install_zip_result = {"status": "ok", "id": "some.plugin", "display_name": "Some Plugin"}
        self.uninstall_result = {"status": "ok", "id": "some.plugin"}
        self.launch_mode = "direct"
        self.ui_mode = "desktop"
        self.system_info_result = {
            "system_uuid": "system-a",
            "sync_folder": "/data/sync",
            "app_data_folder": "/data/app",
            "other_systems": [],
        }
        self.refresh_sources_result = {"status": "ok", "checked": 1, "changed": 0, "errors": 0}
        self.rebuild_cache_result = {"status": "ok"}
        self.effective_launch_plan_result = {
            "ready": True,
            "state": "ready",
            "reason": "",
            "executable": "/games/test/game.exe",
            "args": [],
            "working_dir": None,
            "backend": "native",
            "environment": {},
            "wrappers": [],
            "monitoring": {},
        }
        # Real, minimal in-memory cascade -- mirrors pythia.config_cascade's
        # own precedence (instance > game > global > default) closely enough
        # to test real inheritance/override/reset behavior, not just canned
        # single-shot fixtures.
        self._global_config: dict = {}
        self._game_config: dict = {}
        self._instance_config: dict = {}
        self.optional_plugins_result: list = []
        self.install_optional_plugin_result = {"status": "ok", "id": "pythia.downloader.automator"}
        self.start_automated_download_result = {
            "status": "pending",
            "download_id": "dl-1",
            "host": "example.invalid",
            "filename": "game.zip",
            "premium_session": False,
        }
        self.cancel_automated_download_result = {"status": "ok"}
        self.start_download_host_login_result = {"status": "pending", "host": "example.invalid"}
        self.clear_download_host_session_result = {"status": "ok", "host": "example.invalid"}
        self.start_f95_login_result = {"status": "pending", "message": "F95zone sign-in opened."}
        self.clear_f95_session_result = {"status": "ok", "message": "Cleared the saved F95zone session."}
        self.pick_f95_database_file_result = "/tmp/f95checker.db"
        self.start_onboard_calls: list = []
        self.sync_status_result = {
            "running": False,
            "config": {
                "enabled": False,
                "listen": True,
                "lan_discovery": True,
                "global_discovery": False,
                "relay_discovery": False,
                "nat_traversal": False,
            },
            "network_settings": {
                "listen_host": "0.0.0.0",
                "listen_port": 24030,
                "global_discovery_server": "",
                "relay_server": "",
            },
            "device_id": None,
            "system_uuid": None,
            "system_name": None,
            "peers": {},
            "pending_offers": {},
            "relay_connected": False,
            "retry_state": {},
            "nat": {
                "active": False,
                "protocol": None,
                "external_address": None,
                "external_port": None,
                "lifetime_seconds": None,
                "obtained_at": None,
                "last_error": None,
            },
            "dual_stack": {
                "ipv6_supported": True,
                "listening_families": ["IPv4", "IPv6"],
                "own_ipv6_candidates": [],
            },
        }
        self.sync_trusted_devices: list = []
        self.set_sync_enabled_result = {"status": "ok"}
        self.create_sync_pairing_invitation_result = ""
        self.accept_sync_pairing_invitation_result = {"status": "ok", "trust": {}}
        self.pair_sync_device_manually_result = {"status": "ok", "trust": {}}
        self.remove_sync_trusted_device_result = True
        self.resolve_sync_transfer_offer_result = True
        self.cancel_sync_transfer_result = False

    def ensure_sync_started(self):
        self.calls.append(("ensure_sync_started",))
        return {"status": "disabled"}

    def get_sync_status(self):
        return self.sync_status_result

    def set_sync_enabled(self, enabled):
        self.calls.append(("set_sync_enabled", enabled))
        return self.set_sync_enabled_result

    def set_sync_config(self, patch_json):
        self.calls.append(("set_sync_config", patch_json))
        return {"status": "ok"}

    def set_sync_network_settings(self, patch_json):
        self.calls.append(("set_sync_network_settings", patch_json))
        return {"status": "ok"}

    def set_sync_system_name(self, name):
        self.calls.append(("set_sync_system_name", name))
        return {"status": "ok", "system_name": name}

    def create_sync_pairing_invitation(self, friendly_name):
        self.calls.append(("create_sync_pairing_invitation", friendly_name))
        return self.create_sync_pairing_invitation_result

    def accept_sync_pairing_invitation(self, text):
        self.calls.append(("accept_sync_pairing_invitation", text))
        return self.accept_sync_pairing_invitation_result

    def pair_sync_device_manually(self, system_uuid, device_id, friendly_name):
        self.calls.append(("pair_sync_device_manually", system_uuid, device_id, friendly_name))
        return self.pair_sync_device_manually_result

    def list_sync_trusted_devices(self):
        return self.sync_trusted_devices

    def remove_sync_trusted_device(self, system_uuid):
        self.calls.append(("remove_sync_trusted_device", system_uuid))
        return self.remove_sync_trusted_device_result

    def offer_sync_transfer(self, system_uuid, root_path, game_id, display_name):
        self.calls.append(("offer_sync_transfer", system_uuid, root_path, game_id, display_name))

    def resolve_sync_transfer_offer(self, offer_id, accept, destination, cache, message):
        self.calls.append(("resolve_sync_transfer_offer", offer_id, accept, destination, cache, message))
        return self.resolve_sync_transfer_offer_result

    def pending_sync_transfer_offers(self):
        return {}

    def known_sync_peers(self):
        return {}

    def sync_retry_state(self):
        return {}

    def sync_nat_status(self):
        return self.sync_status_result.get("nat", {})

    def retry_sync_nat_mapping(self):
        self.calls.append(("retry_sync_nat_mapping",))

    def cancel_sync_transfer(self, system_uuid):
        self.calls.append(("cancel_sync_transfer", system_uuid))
        return self.cancel_sync_transfer_result

    def push_sync_metadata_to_device(self, system_uuid):
        self.calls.append(("push_sync_metadata_to_device", system_uuid))

    def pull_sync_metadata_from_device(self, system_uuid):
        self.calls.append(("pull_sync_metadata_from_device", system_uuid))

    def export_sync_staging_snapshot(self, folder):
        self.calls.append(("export_sync_staging_snapshot", folder))

    def import_sync_staging_snapshot(self, folder):
        self.calls.append(("import_sync_staging_snapshot", folder))

    def list_optional_plugins(self):
        return self.optional_plugins_result

    def install_optional_plugin(self, plugin_id):
        self.calls.append(("install_optional_plugin", plugin_id))
        return self.install_optional_plugin_result

    def start_automated_download(self, url, suggested_name):
        self.calls.append(("start_automated_download", url, suggested_name))
        return self.start_automated_download_result

    def cancel_automated_download(self, download_id):
        self.calls.append(("cancel_automated_download", download_id))
        return self.cancel_automated_download_result

    def start_download_host_login(self, url):
        self.calls.append(("start_download_host_login", url))
        return self.start_download_host_login_result

    def clear_download_host_session(self, url):
        self.calls.append(("clear_download_host_session", url))
        return self.clear_download_host_session_result

    def start_f95_login(self):
        self.calls.append(("start_f95_login",))
        return self.start_f95_login_result

    def clear_f95_session(self):
        self.calls.append(("clear_f95_session",))
        return self.clear_f95_session_result

    def pick_f95_database_file(self):
        self.calls.append(("pick_f95_database_file",))
        return self.pick_f95_database_file_result

    def import_f95(self, path):
        self.calls.append(("import_f95", path))

    def start_onboard(self, path):
        self.calls.append(("start_onboard", path))
        self.start_onboard_calls.append(path)

    def get_all_plugins(self):
        return self.all_plugins

    def get_launch_mode(self):
        return self.launch_mode

    def set_launch_mode(self, mode):
        self.calls.append(("set_launch_mode", mode))
        self.launch_mode = mode
        return {"status": "ok", "mode": mode}

    def get_ui_mode(self):
        return self.ui_mode

    def set_ui_mode(self, mode):
        self.calls.append(("set_ui_mode", mode))
        self.ui_mode = mode
        return {"status": "ok", "mode": mode}

    def get_system_info(self):
        return self.system_info_result

    def open_data_folder(self, kind):
        self.calls.append(("open_data_folder", kind))
        return {"status": "ok", "path": f"/data/{kind}"}

    def export_diagnostics_to_file(self, path):
        self.calls.append(("export_diagnostics_to_file", path))
        return {"status": "ok", "path": path}

    def refresh_sources(self):
        self.calls.append(("refresh_sources",))
        return self.refresh_sources_result

    def rebuild_cache(self):
        self.calls.append(("rebuild_cache",))
        return self.rebuild_cache_result

    def start_update_check(self, game_ids):
        self.calls.append(("start_update_check", tuple(game_ids)))

    def get_effective_launch_plan(self, game_id, variant_id):
        self.calls.append(("get_effective_launch_plan", game_id, variant_id))
        return self.effective_launch_plan_result

    def get_effective_config(self, option_keys, game_id, variant_id):
        self.calls.append(("get_effective_config", tuple(option_keys), game_id, variant_id))
        result = {}
        for key in option_keys:
            raw_instance = self._instance_config.get((game_id, variant_id), {}).get(key) if variant_id else None
            raw_game = self._game_config.get(game_id, {}).get(key) if game_id else None
            raw_global = self._global_config.get(key)
            raw = {"global": raw_global, "game": raw_game, "instance": raw_instance}
            if raw_instance is not None:
                result[key] = {"value": raw_instance, "source_scope": "instance", "raw": raw}
            elif raw_game is not None:
                result[key] = {"value": raw_game, "source_scope": "game", "raw": raw}
            elif raw_global is not None:
                result[key] = {"value": raw_global, "source_scope": "global", "raw": raw}
            else:
                result[key] = {"value": None, "source_scope": "default", "raw": raw}
        return result

    def set_config_value(self, scope, game_id, variant_id, option_key, value):
        self.calls.append(("set_config_value", scope, game_id, variant_id, option_key, value))
        if scope == "global":
            bucket = self._global_config
            key = option_key
            if value is None:
                bucket.pop(key, None)
            else:
                bucket[key] = value
        elif scope == "game":
            bucket = self._game_config.setdefault(game_id, {})
            if value is None:
                bucket.pop(option_key, None)
            else:
                bucket[option_key] = value
        elif scope == "instance":
            bucket = self._instance_config.setdefault((game_id, variant_id), {})
            if value is None:
                bucket.pop(option_key, None)
            else:
                bucket[option_key] = value
        else:
            return {"status": "error", "message": f"Unknown scope: {scope!r}"}
        return {"status": "ok"}

    def get_log_contents(self):
        return ""

    def export_entries_to_file(self, game_ids, path):
        self.calls.append(("export_entries_to_file", tuple(game_ids), path))
        return self.export_result

    def refresh_merge_candidates(self):
        self.calls.append(("refresh_merge_candidates",))

    def merge_games(self, surviving_id, retired_id):
        self.calls.append(("merge_games", surviving_id, retired_id))
        self.merge_calls.append((surviving_id, retired_id))

    def check_plugin_index(self):
        self.calls.append(("check_plugin_index",))
        return self.check_plugin_index_result

    def install_plugin_from_index(self, plugin_id):
        self.calls.append(("install_plugin_from_index", plugin_id))
        return self.install_from_index_result

    def install_plugin_zip(self, path):
        self.calls.append(("install_plugin_zip", path))
        return self.install_zip_result

    def uninstall_plugin_zip(self, plugin_id):
        self.calls.append(("uninstall_plugin_zip", plugin_id))
        return self.uninstall_result

    def dismiss_updates(self, game_id):
        self.calls.append(("dismiss_updates", game_id))
        return self.dismiss_updates_result

    def get_saved_searches(self):
        return list(self.saved_searches)

    def save_search(self, name, search):
        self.calls.append(("save_search", name, search))
        self.saved_searches = [s for s in self.saved_searches if s["name"] != name]
        self.saved_searches.append({"name": name, "search": search})
        return {"status": "ok", "name": name}

    def remove_saved_search(self, name):
        self.calls.append(("remove_saved_search", name))
        self.saved_searches = [s for s in self.saved_searches if s["name"] != name]
        return {"status": "ok", "name": name}

    def bulk_action(self, game_ids, action):
        self.calls.append(("bulk_action", tuple(game_ids), action))
        return self.bulk_action_result

    def get_system_uuid(self):
        return "system-a"

    def find_manager_for_game(self, game_id):
        self.calls.append(("find_manager_for_game", game_id))
        return self.find_manager_result

    def open_in_manager(self, game_id, manager_id):
        self.calls.append(("open_in_manager", game_id, manager_id))

    def get_engine_status(self, game_id):
        self.calls.append(("get_engine_status", game_id))
        return self.engine_status_result

    def get_engine_options(self):
        return self.engine_options

    def enable_engine_platform(self, game_id, platform):
        self.calls.append(("enable_engine_platform", game_id, platform))

    def get_launch_readiness(self, game_id, variant_id=None):
        self.calls.append(("get_launch_readiness", game_id, variant_id))
        return self.launch_readiness_result

    def launch_game_direct(self, game_id, variant_id=None):
        self.calls.append(("launch_game_direct", game_id, variant_id))
        return self.launch_result

    def rescan_game(self, game_id):
        self.calls.append(("rescan_game", game_id))
        return self.rescan_result

    def save_notes(self, game_id, text):
        self.calls.append(("save_notes", game_id, text))
        return {"status": "ok"}

    def rename_game(self, game_id, name):
        self.calls.append(("rename_game", game_id, name))
        return {"status": "ok"}

    def set_engine_override(self, game_id, kind):
        self.calls.append(("set_engine_override", game_id, kind))
        return {"status": "ok"}

    def resolveDecision(self, path, decision):
        self.calls.append(("resolveDecision", path, decision))


def _summary(**overrides):
    base = {
        "game_id": "g1",
        "canonical_name": "Test Game",
        "systems": ["system-a"],
        "platform_ids": [],
        "version_count": 1,
        "variant_count": 1,
        "has_updates": False,
        "notes": "",
        "engine_override": "",
        "editable": True,
        "variants": [{"variant_id": "v1", "version": "1.0", "path": "/games/test"}],
        "entries": [],
    }
    base.update(overrides)
    return base


class SummarizeGameTests(unittest.TestCase):
    """No QApplication needed -- summarize_game() is plain data shaping."""

    def _row(self):
        return {
            "game_id": "abc123",
            "entries": [
                {
                    "system_uuid": "system-a",
                    "canonical_name": "Example Game",
                    "platform_ids": [{"platform": "steam", "id": "111"}],
                    "versions": [{"version": "1.0", "variants": [{"path": "/games/example"}]}],
                    "notes": "Great game",
                    "engine_override": "renpy",
                    "available_updates": [],
                },
                {
                    "system_uuid": "system-b",
                    "canonical_name": "Example Game",
                    "platform_ids": [{"platform": "gog", "id": "222"}],
                    "versions": [{"version": "1.0", "variants": [{"path": "D:/games/example"}]}],
                    "notes": "",
                    "available_updates": [{"version": "1.1"}],
                },
            ],
        }

    def test_summary_merges_cross_system_fields(self):
        from pythia.ui.native.models import summarize_game

        summary = summarize_game(self._row(), own_system_uuid="system-a")
        self.assertEqual("Example Game", summary["canonical_name"])
        self.assertEqual(["system-a", "system-b"], summary["systems"])
        self.assertEqual(2, len(summary["platform_ids"]))
        self.assertEqual(2, summary["version_count"])
        self.assertEqual(2, summary["variant_count"])
        self.assertTrue(summary["has_updates"])
        self.assertEqual("Great game", summary["notes"])
        self.assertEqual("renpy", summary["engine_override"])
        self.assertTrue(summary["editable"])

    def test_summary_not_editable_when_no_own_entry(self):
        from pythia.ui.native.models import summarize_game

        summary = summarize_game(self._row(), own_system_uuid="system-c")
        self.assertFalse(summary["editable"])
        self.assertEqual("", summary["notes"])  # never fabricated from another system's entry

    def test_summary_handles_empty_row(self):
        from pythia.ui.native.models import summarize_game

        summary = summarize_game({"game_id": "empty", "entries": []})
        self.assertEqual("empty", summary["canonical_name"])
        self.assertEqual([], summary["systems"])
        self.assertFalse(summary["editable"])


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class GameListModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_model_refresh_and_roles(self):
        from pythia.ui.native.models import GAME_ID_ROLE, SUMMARY_ROLE, GameListModel

        model = GameListModel(own_system_uuid="system-a")
        rows = [
            {"game_id": "g1", "entries": [{"system_uuid": "system-a", "canonical_name": "Zeta"}]},
            {"game_id": "g2", "entries": [{"system_uuid": "system-a", "canonical_name": "Alpha"}]},
        ]
        model.refresh(rows)
        self.assertEqual(2, model.rowCount())

        index0 = model.index(0, 0)
        self.assertEqual("Zeta", model.data(index0))
        self.assertEqual("g1", model.data(index0, GAME_ID_ROLE))
        self.assertEqual("Zeta", model.data(index0, SUMMARY_ROLE)["canonical_name"])

        self.assertIsNotNone(model.summary_for_game_id("g2"))
        self.assertIsNone(model.summary_for_game_id("nonexistent"))

    def test_filter_proxy_sorts_stably_and_filters_by_text(self):
        from pythia.ui.native.models import GameFilterProxyModel, GameListModel

        model = GameListModel()
        rows = [
            {"game_id": "g2", "entries": [{"canonical_name": "Zeta", "platform_ids": [{"platform": "steam"}]}]},
            {"game_id": "g1", "entries": [{"canonical_name": "Alpha", "platform_ids": [{"platform": "gog"}]}]},
            {"game_id": "g3", "entries": [{"canonical_name": "Alpha", "platform_ids": []}]},  # tie on name
        ]
        model.refresh(rows)
        proxy = GameFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.sort(0)

        from pythia.ui.native.models import GAME_ID_ROLE

        rows = [
            (proxy.index(r, 0).data(), proxy.index(r, 0).data(GAME_ID_ROLE)) for r in range(proxy.rowCount())
        ]
        # Alpha/g1 sorts before Alpha/g3 (stable tie-break on game_id), both before Zeta.
        self.assertEqual([("Alpha", "g1"), ("Alpha", "g3"), ("Zeta", "g2")], rows)

        proxy.set_search_text("gog")
        self.assertEqual(1, proxy.rowCount())
        self.assertEqual("Alpha", proxy.index(0, 0).data())

        proxy.set_search_text("")
        self.assertEqual(3, proxy.rowCount())


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class NativeMainWindowSmokeTest(unittest.TestCase):
    """Offscreen smoke test: build a real Backend against an isolated data
    dir, construct the real MainWindow, and close it -- proves the whole
    slice actually wires together, not just that the pieces parse."""

    def test_main_window_constructs_and_closes_offscreen(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PYTHIA_DATA_DIR"] = tmp
            try:
                from PySide6.QtWidgets import QApplication

                app = QApplication.instance() or QApplication([])

                from pythia.ui.backend import Backend
                from pythia.ui.native.main_window import MainWindow

                backend = Backend()
                window = MainWindow(backend)
                window.show()
                app.processEvents()
                self.assertEqual(0, window.library_view.model.rowCount())
                window.close()
                app.processEvents()
            finally:
                from pythia.util.log import shutdown_logging

                shutdown_logging()
                os.environ.pop("PYTHIA_DATA_DIR", None)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class LibraryInspectorHandlerTests(unittest.TestCase):
    """Handler-level tests against FakeBackend's real Slot return shapes --
    2026-08-08, Codex correctness review requirement: "test the actual widget
    handlers, not only construction." Each test here is the regression test
    for one specific defect the review found."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _inspector(self, backend):
        from pythia.ui.native.main_window import LibraryInspector

        return LibraryInspector(backend)

    def test_zero_manager_candidates_shows_info_and_never_calls_open(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.find_manager_result = []
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())

        with mock.patch.object(QMessageBox, "information") as info:
            inspector._on_open_in_manager()
        info.assert_called_once()
        self.assertFalse(any(c[0] == "open_in_manager" for c in backend.calls))

    def test_single_manager_candidate_confirms_then_calls_with_real_id(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.find_manager_result = [{"manager_id": "steam", "manager_name": "Steam", "kind": "steam"}]
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            inspector._on_open_in_manager()
        self.assertIn(("open_in_manager", "g1", "steam"), backend.calls)

    def test_single_manager_candidate_declined_does_not_call(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.find_manager_result = [{"manager_id": "steam", "manager_name": "Steam", "kind": "steam"}]
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            inspector._on_open_in_manager()
        self.assertFalse(any(c[0] == "open_in_manager" for c in backend.calls))

    def test_multiple_manager_candidates_prompts_chooser_and_uses_chosen_id(self):
        from pythia.ui.native.main_window import QInputDialog, QMessageBox

        backend = FakeBackend()
        backend.find_manager_result = [
            {"manager_id": "steam", "manager_name": "Steam", "kind": "steam"},
            {"manager_id": "gog", "manager_name": "GOG Galaxy", "kind": "gog"},
        ]
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())

        with (
            mock.patch.object(QInputDialog, "getItem", return_value=("GOG Galaxy (gog)", True)),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
        ):
            inspector._on_open_in_manager()
        self.assertIn(("open_in_manager", "g1", "gog"), backend.calls)

    def test_engine_platform_dict_renders_and_fixable_only_gets_enable_button(self):
        from PySide6.QtWidgets import QPushButton

        backend = FakeBackend()
        backend.engine_status_result = {
            "status": "ok",
            "plugin_name": "Ren'Py",
            "manual": False,
            "platforms": {
                "windows": {"supported": True, "native": True, "requires": [], "fixable": False},
                "linux": {"supported": False, "native": False, "requires": ["libs"], "fixable": True},
            },
        }
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())

        enable_buttons = [b for b in inspector.platforms_container.findChildren(QPushButton) if b.text() == "Enable"]
        self.assertEqual(1, len(enable_buttons))
        enable_buttons[0].click()
        self.assertIn(("enable_engine_platform", "g1", "linux"), backend.calls)

    def test_selected_variant_id_reaches_readiness_and_launch(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        summary = _summary(
            variants=[
                {"variant_id": "v1", "version": "1.0", "path": "/a"},
                {"variant_id": "v2", "version": "2.0", "path": "/b"},
            ]
        )
        inspector.set_summary(summary)
        self.assertIn(("get_launch_readiness", "g1", "v1"), backend.calls)

        inspector.variant_combo.setCurrentIndex(1)
        self.assertIn(("get_launch_readiness", "g1", "v2"), backend.calls)

        backend.calls.clear()
        inspector._on_launch()
        self.assertIn(("launch_game_direct", "g1", "v2"), backend.calls)

    def test_no_local_variants_disables_launch_and_never_calls_backend(self):
        """2026-08-08, Codex review of the correctness repair: a remote-only
        game (no local variant) must NOT call get_launch_readiness()/
        launch_game_direct() with variant_id=None -- that falls back to
        Backend's legacy "first variant with a path across every known
        system," which can hand back a path that doesn't exist on this
        machine. Launch must be disabled outright instead."""
        backend = FakeBackend()
        inspector = self._inspector(backend)
        inspector.set_summary(_summary(variants=[]))

        self.assertFalse(inspector.launch_button.isEnabled())
        self.assertFalse(any(c[0] in ("get_launch_readiness", "launch_game_direct") for c in backend.calls))

        backend.calls.clear()
        inspector._on_launch()
        self.assertFalse(any(c[0] == "launch_game_direct" for c in backend.calls))

    def test_local_variant_enables_launch_and_calls_backend(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())
        self.assertTrue(inspector.launch_button.isEnabled())
        self.assertIn(("get_launch_readiness", "g1", "v1"), backend.calls)

    def test_rescan_error_status_shows_warning_not_success(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.rescan_result = {"status": "error", "processed": 0, "errors": 1}
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())

        with mock.patch.object(QMessageBox, "information") as info, mock.patch.object(QMessageBox, "warning") as warn:
            inspector._on_rescan()
        info.assert_not_called()
        warn.assert_called_once()

    def test_rescan_partial_status_shows_warning_not_success(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.rescan_result = {"status": "partial", "processed": 3, "errors": 1}
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())

        with mock.patch.object(QMessageBox, "information") as info, mock.patch.object(QMessageBox, "warning") as warn:
            inspector._on_rescan()
        info.assert_not_called()
        warn.assert_called_once()

    def test_rescan_ok_status_shows_success_not_warning(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.rescan_result = {"status": "ok", "processed": 2, "errors": 0}
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())

        with mock.patch.object(QMessageBox, "information") as info, mock.patch.object(QMessageBox, "warning") as warn:
            inspector._on_rescan()
        info.assert_called_once()
        warn.assert_not_called()


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class InspectorDepthTests(unittest.TestCase):
    """2026-08-08, native UI daily-use depth: versions/variants tree,
    systems/updates, timeline, all rendered from real (fake-backend-shaped)
    summary data, never fabricated."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _inspector(self, backend):
        from pythia.ui.native.main_window import LibraryInspector

        return LibraryInspector(backend)

    def test_versions_tree_shows_cross_system_rows_including_foreign(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        summary = _summary(
            detail_rows=[
                {
                    "system_uuid": "system-a",
                    "local": True,
                    "version": "1.0",
                    "variant_id": "v1",
                    "source_kind": "renpy",
                    "language": "en",
                    "mods": ["patch1"],
                    "path": "/games/test",
                },
                {
                    "system_uuid": "system-b",
                    "local": False,
                    "version": "1.0",
                    "variant_id": "v2",
                    "source_kind": "renpy",
                    "language": "",
                    "mods": [],
                    "path": "/other/machine/path",
                },
            ]
        )
        inspector.set_summary(summary)
        self.assertEqual(2, inspector.versions_tree.topLevelItemCount())
        own_row = inspector.versions_tree.topLevelItem(0)
        self.assertIn("this system", own_row.text(0))
        self.assertEqual("patch1", own_row.text(5))
        foreign_row = inspector.versions_tree.topLevelItem(1)
        self.assertNotIn("this system", foreign_row.text(0))
        self.assertEqual("/other/machine/path", foreign_row.text(6))

    def test_updates_list_and_dismiss_button_state(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        summary = _summary(
            has_updates=True,
            updates=[{"version": "2.0", "_reported_by": "system-a", "changelog": "Fixed things"}],
        )
        inspector.set_summary(summary)
        self.assertEqual(1, inspector.updates_list.count())
        self.assertIn("2.0", inspector.updates_list.item(0).text())
        self.assertTrue(inspector.dismiss_updates_button.isEnabled())

        inspector._on_dismiss_updates()
        self.assertIn(("dismiss_updates", "g1"), backend.calls)

    def test_dismiss_updates_clears_display_on_success(self):
        """2026-08-08, Codex review: a successful dismissal used to leave
        stale rows visible in the updates list until something else
        happened to trigger a refresh. Now self-contained."""
        backend = FakeBackend()
        backend.dismiss_updates_result = {"status": "ok", "dismissed": 1}
        inspector = self._inspector(backend)
        inspector.set_summary(_summary(has_updates=True, updates=[{"version": "2.0", "_reported_by": "system-a"}]))
        self.assertEqual(1, inspector.updates_list.count())

        inspector._on_dismiss_updates()
        self.assertEqual(0, inspector.updates_list.count())
        self.assertFalse(inspector.dismiss_updates_button.isEnabled())

    def test_dismiss_updates_failure_leaves_rows_and_shows_warning(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.dismiss_updates_result = {"status": "error", "message": "This game has no writable entry."}
        inspector = self._inspector(backend)
        inspector.set_summary(_summary(has_updates=True, updates=[{"version": "2.0", "_reported_by": "system-a"}]))

        with mock.patch.object(QMessageBox, "warning") as warn:
            inspector._on_dismiss_updates()
        warn.assert_called_once()
        self.assertEqual(1, inspector.updates_list.count())  # unchanged -- nothing to clear, it didn't succeed

    def test_no_updates_disables_dismiss_button(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        inspector.set_summary(_summary(has_updates=False, updates=[]))
        self.assertEqual(0, inspector.updates_list.count())
        self.assertFalse(inspector.dismiss_updates_button.isEnabled())

    def test_timeline_shows_real_events_most_recent_first(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        summary = _summary(
            timeline_events=[
                {"timestamp": "2026-08-08T10:00:00+00:00", "type": "game_renamed", "detail": "A -> B"},
                {"timestamp": "2026-08-08T09:00:00+00:00", "type": "onboarded", "detail": ""},
            ]
        )
        inspector.set_summary(summary)
        self.assertEqual(2, inspector.timeline_list.count())
        self.assertIn("game_renamed", inspector.timeline_list.item(0).text())

    def test_empty_timeline_shows_honest_placeholder(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        inspector.set_summary(_summary(timeline_events=[]))
        self.assertEqual(1, inspector.timeline_list.count())
        self.assertIn("No recorded history", inspector.timeline_list.item(0).text())


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class LibraryViewDepthTests(unittest.TestCase):
    """Bulk actions, saved-search round trips, and selection persistence --
    tested against the real LibraryView + GameListModel, not mocked out."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _game_row(self, game_id, name):
        return {"game_id": game_id, "entries": [{"system_uuid": "system-a", "canonical_name": name}]}

    def _library_view(self, backend):
        from pythia.ui.native.main_window import LibraryView

        return LibraryView(backend)

    def test_bulk_rescan_success_shows_information(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.games = [self._game_row("g1", "A"), self._game_row("g2", "B")]
        backend.bulk_action_result = {"status": "ok", "processed": 2, "errors": 0}
        view = self._library_view(backend)
        view.list_view.selectAll()

        with (
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
            mock.patch.object(QMessageBox, "information") as info,
            mock.patch.object(QMessageBox, "warning") as warn,
        ):
            view._on_bulk_rescan()
        self.assertTrue(any(c[0] == "bulk_action" and c[2] == "rescan" for c in backend.calls))
        info.assert_called_once()
        warn.assert_not_called()

    def test_bulk_rescan_partial_shows_warning(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.games = [self._game_row("g1", "A")]
        backend.bulk_action_result = {"status": "ok", "processed": 1, "errors": 1}
        view = self._library_view(backend)
        view.list_view.selectAll()

        with (
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
            mock.patch.object(QMessageBox, "information") as info,
            mock.patch.object(QMessageBox, "warning") as warn,
        ):
            view._on_bulk_rescan()
        info.assert_not_called()
        warn.assert_called_once()

    def test_bulk_rescan_error_status_shows_warning(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.games = [self._game_row("g1", "A")]
        backend.bulk_action_result = {"status": "unavailable", "message": "Bulk rescan is not implemented yet."}
        view = self._library_view(backend)
        view.list_view.selectAll()

        with (
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
            mock.patch.object(QMessageBox, "information") as info,
            mock.patch.object(QMessageBox, "warning") as warn,
        ):
            view._on_bulk_rescan()
        info.assert_not_called()
        warn.assert_called_once()

    def test_bulk_rescan_partial_status_shows_real_counts(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.games = [self._game_row("g1", "A")]
        backend.bulk_action_result = {"status": "partial", "processed": 1, "errors": 2}
        view = self._library_view(backend)
        view.list_view.selectAll()

        with (
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
            mock.patch.object(QMessageBox, "information") as info,
            mock.patch.object(QMessageBox, "warning") as warn,
        ):
            view._on_bulk_rescan()
        info.assert_not_called()
        warn.assert_called_once()
        self.assertIn("1 game(s), 2 with errors", warn.call_args[0][2])

    def test_bulk_rescan_declined_confirmation_does_not_call_backend(self):
        from pythia.ui.native.main_window import QMessageBox

        backend = FakeBackend()
        backend.games = [self._game_row("g1", "A")]
        view = self._library_view(backend)
        view.list_view.selectAll()

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            view._on_bulk_rescan()
        self.assertFalse(any(c[0] == "bulk_action" for c in backend.calls))

    def test_bulk_buttons_disabled_with_no_selection(self):
        backend = FakeBackend()
        backend.games = [self._game_row("g1", "A")]
        view = self._library_view(backend)
        self.assertFalse(view.bulk_rescan_button.isEnabled())
        self.assertFalse(view.bulk_export_button.isEnabled())

    def test_bulk_export_writes_real_file_via_backend(self):
        """2026-08-08, Codex review: this used to say "Exported N entrie(s)"
        after only retrieving the data, never writing it anywhere. Now a
        real save-file dialog followed by the real
        Backend.export_entries_to_file() call."""
        from pythia.ui.native.main_window import QFileDialog, QMessageBox

        backend = FakeBackend()
        backend.games = [self._game_row("g1", "A")]
        backend.export_result = {"status": "ok", "path": "/tmp/out.json", "count": 1}
        view = self._library_view(backend)
        view.list_view.selectAll()

        with (
            mock.patch.object(QFileDialog, "getSaveFileName", return_value=("/tmp/out.json", "")),
            mock.patch.object(QMessageBox, "information") as info,
        ):
            view._on_bulk_export()
        self.assertIn(("export_entries_to_file", ("g1",), "/tmp/out.json"), backend.calls)
        info.assert_called_once()
        self.assertIn("/tmp/out.json", info.call_args[0][2])

    def test_bulk_export_cancelled_dialog_does_not_call_backend(self):
        from pythia.ui.native.main_window import QFileDialog

        backend = FakeBackend()
        backend.games = [self._game_row("g1", "A")]
        view = self._library_view(backend)
        view.list_view.selectAll()

        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=("", "")):
            view._on_bulk_export()
        self.assertFalse(any(c[0] == "export_entries_to_file" for c in backend.calls))

    def test_bulk_export_write_failure_shows_warning(self):
        from pythia.ui.native.main_window import QFileDialog, QMessageBox

        backend = FakeBackend()
        backend.games = [self._game_row("g1", "A")]
        backend.export_result = {"status": "error", "message": "Disk full."}
        view = self._library_view(backend)
        view.list_view.selectAll()

        with (
            mock.patch.object(QFileDialog, "getSaveFileName", return_value=("/tmp/out.json", "")),
            mock.patch.object(QMessageBox, "warning") as warn,
            mock.patch.object(QMessageBox, "information") as info,
        ):
            view._on_bulk_export()
        warn.assert_called_once()
        info.assert_not_called()

    def test_saved_search_round_trip(self):
        backend = FakeBackend()
        view = self._library_view(backend)
        view.search_box.setText("some query")
        with mock.patch("pythia.ui.native.main_window.QInputDialog.getText", return_value=("My Search", True)):
            view._on_save_search()
        self.assertIn({"name": "My Search", "search": "some query"}, backend.saved_searches)
        self.assertEqual(2, view.saved_search_combo.count())  # placeholder + 1

        # Re-saving under the same name updates rather than duplicates.
        view.search_box.setText("updated query")
        with mock.patch("pythia.ui.native.main_window.QInputDialog.getText", return_value=("My Search", True)):
            view._on_save_search()
        self.assertEqual(1, len(backend.saved_searches))
        self.assertEqual("updated query", backend.saved_searches[0]["search"])

        view.saved_search_combo.setCurrentIndex(1)
        view._on_remove_saved_search()
        self.assertEqual([], backend.saved_searches)

    def test_selection_persists_across_refresh(self):
        backend = FakeBackend()
        backend.games = [self._game_row("g1", "A"), self._game_row("g2", "B")]
        view = self._library_view(backend)
        index = view.model.index_for_game_id("g2")
        view.list_view.setCurrentIndex(view.proxy.mapFromSource(index))
        self.assertEqual("g2", view._current_game_id)

        view.refresh()  # same games list -- selection should survive
        self.assertEqual("g2", view._current_game_id)
        self.assertEqual("g2", view.inspector._game_id)

    def test_selection_cleared_honestly_when_game_removed(self):
        backend = FakeBackend()
        backend.games = [self._game_row("g1", "A"), self._game_row("g2", "B")]
        view = self._library_view(backend)
        index = view.model.index_for_game_id("g2")
        view.list_view.setCurrentIndex(view.proxy.mapFromSource(index))
        self.assertEqual("g2", view._current_game_id)

        backend.games = [self._game_row("g1", "A")]  # g2 is gone
        view.refresh()
        self.assertIsNone(view._current_game_id)
        self.assertIsNone(view.inspector._game_id)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class ActivityHandlerTests(unittest.TestCase):
    """MainWindow's activity/status handlers, exercised the same
    SimpleNamespace way as DecisionNeededTests below -- avoids constructing
    a full MainWindow (which needs a much larger FakeBackend) just to test
    one handler's text/formatting logic. Confirms no fabricated percentages
    or successful-sounding text appears for a failure/cancel outcome."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _fake_self(self):
        import types

        from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStatusBar

        activity_list = QListWidget()
        fake_self = types.SimpleNamespace(
            activity_list=activity_list,
            status_bar=QStatusBar(),
            library_view=types.SimpleNamespace(refresh=lambda: None),
        )
        # Real MainWindow._append_activity() bound to this fake -- avoids
        # duplicating its (trivial) timestamp-prefix logic here just to make
        # the SimpleNamespace stand-in satisfy `self._append_activity(...)`.
        from pythia.ui.native.main_window import MainWindow

        fake_self._append_activity = lambda text: MainWindow._append_activity(fake_self, text)
        return fake_self

    def test_onboard_cancelled_does_not_say_failed_or_ok(self):
        from pythia.ui.native.main_window import MainWindow

        fake_self = self._fake_self()
        MainWindow._on_onboard_complete(fake_self, {"status": "cancelled", "message": "Onboarding was cancelled."})
        text = fake_self.activity_list.item(0).text()
        self.assertIn("Cancelled", text)
        self.assertNotIn("Failed", text)

    def test_onboard_failure_shows_real_message(self):
        from pythia.ui.native.main_window import MainWindow

        fake_self = self._fake_self()
        MainWindow._on_onboard_complete(fake_self, {"status": "error", "message": "Not a directory."})
        text = fake_self.activity_list.item(0).text()
        self.assertIn("Not a directory.", text)

    def test_merge_success_names_real_ids_not_fabricated_message(self):
        from pythia.ui.native.main_window import MainWindow

        fake_self = self._fake_self()
        MainWindow._on_merge_complete(fake_self, {"status": "ok", "game_id": "keep1", "retired_game_id": "drop1"})
        text = fake_self.activity_list.item(0).text()
        self.assertIn("keep1", text)
        self.assertIn("drop1", text)

    def test_update_check_reports_real_counts_no_percentage(self):
        from pythia.ui.native.main_window import MainWindow

        fake_self = self._fake_self()
        MainWindow._on_update_check_complete(fake_self, {"checked": 5, "updated": 2, "errors": [], "plugins": ["f95"]})
        text = fake_self.activity_list.item(0).text()
        self.assertIn("Checked 5", text)
        self.assertIn("2 with an update", text)
        self.assertNotIn("%", text)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class DecisionNeededTests(unittest.TestCase):
    """MainWindow._on_decision_needed(), exercised directly against a
    SimpleNamespace stand-in for `self` -- avoids constructing a full
    MainWindow (which needs a real Backend.games/get_all_plugins() etc.)
    just to test one handler's decision-building logic."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _run(self, backend, preview, get_item_return):
        import types

        from pythia.ui.native.main_window import MainWindow, QInputDialog

        fake_self = types.SimpleNamespace(backend=backend)
        with mock.patch.object(QInputDialog, "getItem", return_value=get_item_return):
            MainWindow._on_decision_needed(fake_self, preview)

    def test_cancel_does_not_silently_become_new(self):
        backend = FakeBackend()
        preview = {"path": "/p", "name": "Foo", "candidates": {"certain": None, "suggested": []}}
        self._run(backend, preview, ("Add as a new game", False))  # ok=False -> Cancel
        self.assertIn(("resolveDecision", "/p", {"action": "cancel"}), backend.calls)

    def test_explicit_new_choice_is_real_new(self):
        backend = FakeBackend()
        preview = {"path": "/p", "name": "Foo", "candidates": {"certain": None, "suggested": []}}
        self._run(backend, preview, ("Add as a new game", True))
        self.assertIn(("resolveDecision", "/p", {"action": "new"}), backend.calls)

    def test_link_to_suggested_candidate(self):
        backend = FakeBackend()
        preview = {
            "path": "/p",
            "name": "Foo",
            "candidates": {"certain": None, "suggested": [{"game_id": "g9", "canonical_name": "Existing"}]},
        }
        self._run(backend, preview, ("Link to existing: Existing", True))
        self.assertIn(("resolveDecision", "/p", {"action": "link", "game_id": "g9"}), backend.calls)

    def test_certain_candidate_is_offered_and_selectable(self):
        backend = FakeBackend()
        preview = {
            "path": "/p",
            "name": "Foo",
            "candidates": {
                "certain": {"game_id": "g0", "canonical_name": "Certain Match"},
                "suggested": [{"game_id": "g9", "canonical_name": "Existing"}],
            },
        }
        self._run(backend, preview, ("Confirmed match: Certain Match", True))
        self.assertIn(("resolveDecision", "/p", {"action": "link", "game_id": "g0"}), backend.calls)

    def test_new_game_still_reachable_when_certain_present(self):
        """Regression test for a real indexing bug caught before commit: an
        earlier version computed the "Add as a new game" option's position
        arithmetically and got it wrong whenever `certain` was present,
        silently mapping the New choice to the wrong candidate."""
        backend = FakeBackend()
        preview = {
            "path": "/p",
            "name": "Foo",
            "candidates": {"certain": {"game_id": "g0", "canonical_name": "Certain Match"}, "suggested": []},
        }
        self._run(backend, preview, ("Add as a new game", True))
        self.assertIn(("resolveDecision", "/p", {"action": "new"}), backend.calls)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class CascadeConfigFormTests(unittest.TestCase):
    """2026-08-08, "native global settings and launch configuration
    completion": the shared global/game/instance config editor. Uses
    FakeBackend's real in-memory cascade emulation (instance > game > global
    > default), not canned fixtures, so inheritance/override/reset are
    actually exercised."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _fields(self):
        return [
            {"key": "dxvk", "label": "DXVK", "type": "bool"},
            {"key": "wine_version", "label": "Wine version", "type": "text"},
            {"key": "environment", "label": "Env", "type": "keyvalue"},
            {"key": "monitoring", "label": "Monitoring", "type": "json"},
        ]

    def test_global_scope_rejects_empty_string_game_id(self):
        """Real safety check: passing "" instead of None for game_id would
        make datadir.find_entry_file()'s glob prefix match every entry for
        this system (see config_form.py's own docstring). CascadeConfigForm
        must pass real None through, never ""."""
        from pythia.ui.native.config_form import CascadeConfigForm

        backend = FakeBackend()
        CascadeConfigForm(backend, self._fields(), game_id=None, variant_id=None, edit_scope="global")
        call = next(c for c in backend.calls if c[0] == "get_effective_config")
        self.assertIsNone(call[2])  # game_id
        self.assertIsNone(call[3])  # variant_id

    def test_invalid_edit_scope_rejected(self):
        from pythia.ui.native.config_form import CascadeConfigForm

        backend = FakeBackend()
        with self.assertRaises(ValueError):
            CascadeConfigForm(backend, self._fields(), game_id=None, variant_id=None, edit_scope="nonsense")

    def test_save_writes_every_field_to_edit_scope(self):
        from pythia.ui.native.config_form import CascadeConfigForm

        backend = FakeBackend()
        form = CascadeConfigForm(backend, self._fields(), game_id="g1", variant_id=None, edit_scope="game")
        form._rows["dxvk"]["widget"].setChecked(True)
        form._rows["wine_version"]["widget"].setText("8.0")
        result = form.save()
        self.assertEqual("ok", result["status"])
        self.assertEqual(True, backend._game_config["g1"]["dxvk"])
        self.assertEqual("8.0", backend._game_config["g1"]["wine_version"])

    def test_invalid_json_field_does_not_block_other_fields(self):
        """The 'invalid values must be identified without discarding the
        user's other edits' requirement -- a bad JSON field must not stop
        the valid dxvk field from saving."""
        from pythia.ui.native.config_form import CascadeConfigForm

        backend = FakeBackend()
        form = CascadeConfigForm(backend, self._fields(), game_id="g1", variant_id=None, edit_scope="game")
        form._rows["dxvk"]["widget"].setChecked(True)
        form._rows["monitoring"]["widget"].setPlainText("{not valid json")
        result = form.save()
        self.assertEqual("error", result["status"])
        self.assertIn("monitoring", result["errors"])
        self.assertEqual(True, backend._game_config["g1"]["dxvk"])  # still saved

    def test_invalid_keyvalue_line_reported(self):
        from pythia.ui.native.config_form import CascadeConfigForm

        backend = FakeBackend()
        form = CascadeConfigForm(backend, self._fields(), game_id="g1", variant_id=None, edit_scope="game")
        form._rows["environment"]["widget"].setPlainText("NOTKEYVALUE")
        result = form.save()
        self.assertEqual("error", result["status"])
        self.assertIn("environment", result["errors"])

    def test_reset_clears_edit_scope_override_and_falls_back(self):
        from pythia.ui.native.config_form import CascadeConfigForm

        backend = FakeBackend()
        backend._global_config["wine_version"] = "7.0"
        backend._game_config["g1"] = {"wine_version": "8.0"}
        form = CascadeConfigForm(backend, self._fields(), game_id="g1", variant_id=None, edit_scope="game")
        self.assertTrue(form._rows["wine_version"]["reset_button"].isEnabled())

        form._on_reset("wine_version")
        self.assertNotIn("wine_version", backend._game_config.get("g1", {}))
        # Falls back to global after reset.
        self.assertEqual("7.0", form._rows["wine_version"]["widget"].text())
        self.assertFalse(form._rows["wine_version"]["reset_button"].isEnabled())

    def test_failed_reset_keeps_override_and_reports_error(self):
        from pythia.ui.native.config_form import CascadeConfigForm, QMessageBox

        backend = FakeBackend()
        backend._game_config["g1"] = {"wine_version": "8.0"}
        original = backend.set_config_value

        def fail_reset(scope, game_id, variant_id, option_key, value):
            if value is None:
                return {"status": "error", "message": "read-only store"}
            return original(scope, game_id, variant_id, option_key, value)

        backend.set_config_value = fail_reset
        form = CascadeConfigForm(backend, self._fields(), game_id="g1", variant_id=None, edit_scope="game")
        with mock.patch.object(QMessageBox, "warning") as warning:
            form._on_reset("wine_version")
        self.assertEqual("8.0", backend._game_config["g1"]["wine_version"])
        self.assertEqual("8.0", form._rows["wine_version"]["widget"].text())
        warning.assert_called_once()

    def test_source_scope_label_reflects_real_inheritance(self):
        from pythia.ui.native.config_form import CascadeConfigForm

        backend = FakeBackend()
        backend._global_config["wine_version"] = "7.0"
        form = CascadeConfigForm(backend, self._fields(), game_id="g1", variant_id=None, edit_scope="game")
        self.assertIn("global", form._rows["wine_version"]["source_label"].text())


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class SettingsViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _settings_view(self, backend, **kwargs):
        from pythia.ui.native.settings_view import SettingsView

        return SettingsView(backend, **kwargs)

    def test_launch_and_ui_mode_load_real_persisted_values(self):
        backend = FakeBackend()
        backend.launch_mode = "manager"
        backend.ui_mode = "bigpicture"
        view = self._settings_view(backend)
        self.assertEqual("manager", view.launch_mode_combo.currentData())
        self.assertEqual("bigpicture", view.ui_mode_combo.currentData())

    def test_changing_launch_mode_calls_backend(self):
        backend = FakeBackend()
        view = self._settings_view(backend)
        index = view.launch_mode_combo.findData("manager")
        view.launch_mode_combo.setCurrentIndex(index)
        self.assertIn(("set_launch_mode", "manager"), backend.calls)

    def test_reload_reproduces_saved_values(self):
        """"reload must reproduce values" -- change launch mode via the
        backend directly (simulating a real external change or a fresh
        process), then refresh() must reflect it, not stale cached state."""
        backend = FakeBackend()
        view = self._settings_view(backend)
        backend.launch_mode = "manager"
        view.refresh()
        self.assertEqual("manager", view.launch_mode_combo.currentData())

    def test_apply_defaults_success(self):
        from pythia.ui.native.settings_view import QMessageBox

        backend = FakeBackend()
        view = self._settings_view(backend)
        with mock.patch.object(QMessageBox, "information") as info:
            view._on_apply_defaults()
        info.assert_called_once()

    def test_apply_defaults_reports_real_errors(self):
        from pythia.ui.native.settings_view import QMessageBox

        backend = FakeBackend()
        view = self._settings_view(backend)
        view.defaults_form._rows["monitoring"]["widget"].setPlainText("{bad")
        with mock.patch.object(QMessageBox, "warning") as warn:
            view._on_apply_defaults()
        warn.assert_called_once()

    def test_refresh_sources_shows_real_counts(self):
        backend = FakeBackend()
        backend.refresh_sources_result = {"status": "ok", "checked": 4, "changed": 2, "errors": 0}
        view = self._settings_view(backend)
        view._on_refresh_sources()
        self.assertIn("4", view.maintenance_status.text())
        self.assertIn("2", view.maintenance_status.text())

    def test_check_updates_uses_all_known_game_ids(self):
        backend = FakeBackend()
        backend.games = [{"game_id": "g1", "entries": []}, {"game_id": "g2", "entries": []}]
        view = self._settings_view(backend)
        view._on_check_updates()
        self.assertIn(("start_update_check", ("g1", "g2")), backend.calls)

    def test_check_updates_with_no_games_does_not_call_backend(self):
        backend = FakeBackend()
        backend.games = []
        view = self._settings_view(backend)
        view._on_check_updates()
        self.assertFalse(any(c[0] == "start_update_check" for c in backend.calls))

    def test_rebuild_cache_success_and_failure(self):
        backend = FakeBackend()
        view = self._settings_view(backend)
        backend.rebuild_cache_result = {"status": "ok"}
        view._on_rebuild_cache()
        self.assertIn("rebuilt", view.maintenance_status.text().lower())

        backend.rebuild_cache_result = {"status": "error", "message": "Disk error."}
        view._on_rebuild_cache()
        self.assertIn("Disk error.", view.maintenance_status.text())

    def test_open_plugins_calls_provided_callback(self):
        backend = FakeBackend()
        called = []
        view = self._settings_view(backend, switch_to_plugins_tab=lambda: called.append(True))
        view._on_open_plugins()
        self.assertEqual([True], called)

    def test_system_info_rendered_read_only(self):
        backend = FakeBackend()
        backend.system_info_result = {
            "system_uuid": "sys-xyz",
            "sync_folder": "/data/sync",
            "app_data_folder": "/data/app",
            "other_systems": [{"uuid": "sys-2", "count": 3}],
        }
        view = self._settings_view(backend)
        self.assertIn("sys-xyz", view.system_info_label.text())
        self.assertIn("sys-2", view.system_info_label.text())

    def test_open_known_data_folder_uses_backend(self):
        backend = FakeBackend()
        view = self._settings_view(backend)
        view._on_open_data_folder("database")
        self.assertIn(("open_data_folder", "database"), backend.calls)

    def test_open_data_folder_failure_is_visible(self):
        from pythia.ui.native.settings_view import QMessageBox

        backend = FakeBackend()
        backend.open_data_folder = lambda _kind: {"status": "error", "message": "File manager refused."}
        view = self._settings_view(backend)
        with mock.patch.object(QMessageBox, "warning") as warning:
            view._on_open_data_folder("app")
        warning.assert_called_once()
        self.assertIn("File manager refused.", warning.call_args[0][2])

    def test_export_diagnostics_uses_real_backend_file_api(self):
        from pythia.ui.native.settings_view import QFileDialog, QMessageBox

        backend = FakeBackend()
        view = self._settings_view(backend)
        with (
            mock.patch.object(QFileDialog, "getSaveFileName", return_value=("/tmp/diagnostics.json", "")),
            mock.patch.object(QMessageBox, "information") as info,
        ):
            view._on_export_diagnostics()
        self.assertIn(("export_diagnostics_to_file", "/tmp/diagnostics.json"), backend.calls)
        info.assert_called_once()

    def test_library_roots_and_logging_are_not_present(self):
        """Confirms the deliberate omission documented in settings_view.py's
        own module docstring and aisync/WORKLOG.md's claim entry -- neither
        maps to any real, persisted backend concept, so nothing fakes them."""
        backend = FakeBackend()
        view = self._settings_view(backend)
        self.assertFalse(hasattr(view, "library_roots_list"))
        self.assertFalse(hasattr(view, "logging_level_combo"))


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class AdvancedTabTests(unittest.TestCase):
    """LibraryInspector's Advanced tab -- real per-game/instance config
    editing, and the effective-launch-plan preview sourced from the exact
    same backend path Launch itself uses."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _inspector(self, backend):
        from pythia.ui.native.main_window import LibraryInspector

        return LibraryInspector(backend)

    def test_non_editable_game_shows_guidance_not_a_form(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        inspector.set_summary(_summary(editable=False))
        self.assertIsNone(inspector.advanced_form)
        self.assertFalse(inspector.advanced_apply_button.isEnabled())

    def test_editable_game_gets_game_scope_form_by_default(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())
        self.assertIsNotNone(inspector.advanced_form)
        self.assertEqual("game", inspector.advanced_form.edit_scope)
        self.assertEqual("g1", inspector.advanced_form.game_id)
        self.assertIsNone(inspector.advanced_form.variant_id)

    def test_instance_scope_requires_a_selected_variant(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        inspector.set_summary(_summary(variants=[]))  # no local variant
        index = inspector.advanced_scope_combo.findData("instance")
        inspector.advanced_scope_combo.setCurrentIndex(index)
        self.assertIsNone(inspector.advanced_form)
        self.assertFalse(inspector.advanced_apply_button.isEnabled())

    def test_instance_scope_with_variant_builds_form(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())
        index = inspector.advanced_scope_combo.findData("instance")
        inspector.advanced_scope_combo.setCurrentIndex(index)
        self.assertIsNotNone(inspector.advanced_form)
        self.assertEqual("instance", inspector.advanced_form.edit_scope)
        self.assertEqual("v1", inspector.advanced_form.variant_id)

    def test_effective_plan_preview_uses_same_backend_path_as_launch(self):
        backend = FakeBackend()
        backend.effective_launch_plan_result = {
            "ready": True,
            "state": "ready",
            "executable": "/games/test/game.exe",
            "args": ["--windowed"],
            "working_dir": None,
            "backend": "native",
            "environment": {"FOO": "bar"},
            "wrappers": [],
            "monitoring": {},
        }
        inspector = self._inspector(backend)
        inspector.set_summary(_summary())
        self.assertIn(("get_effective_launch_plan", "g1", "v1"), backend.calls)
        self.assertIn("--windowed", inspector.plan_label.text())
        self.assertIn("FOO", inspector.plan_label.text())

    def test_no_local_variant_shows_honest_plan_placeholder(self):
        backend = FakeBackend()
        inspector = self._inspector(backend)
        inspector.set_summary(_summary(variants=[]))
        self.assertIn("No local", inspector.plan_label.text())
        self.assertFalse(any(c[0] == "get_effective_launch_plan" for c in backend.calls))


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class SyncViewTests(unittest.TestCase):
    """Native Devices & Sync page (2026-08-08, "optional core sync service
    and native device/transfer workflow")."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _view(self, backend):
        from pythia.ui.native.sync_view import SyncView

        return SyncView(backend)

    def test_opening_page_calls_ensure_started_which_is_a_real_no_op_while_disabled(self):
        """The task's own requirement: "Opening the page while disabled must
        not initialize sync." Backend.ensure_sync_started() is itself a real
        no-op while disabled (SyncService.start() checks config first) --
        this only confirms the view actually calls it (so a real enabled
        session DOES get started) without the view doing anything extra."""
        backend = FakeBackend()
        view = self._view(backend)
        self.assertIn(("ensure_sync_started",), backend.calls)
        self.assertFalse(view.enabled_checkbox.isChecked())
        self.assertIn("disabled", view.status_label.text().lower())

    def test_enabled_status_renders_device_id_and_system_uuid(self):
        backend = FakeBackend()
        backend.sync_status_result = {
            "running": True,
            "config": {"enabled": True, "lan_discovery": True, "global_discovery": False},
            "network_settings": {"listen_host": "0.0.0.0", "listen_port": 24030, "global_discovery_server": ""},
            "device_id": "DEVICE-1",
            "system_uuid": "system-a",
            "system_name": "My PC",
            "peers": {},
            "pending_offers": {},
        }
        view = self._view(backend)
        self.assertTrue(view.enabled_checkbox.isChecked())
        self.assertIn("DEVICE-1", view.status_label.text())
        self.assertIn("system-a", view.status_label.text())

    def test_toggling_enabled_calls_backend_set_sync_enabled(self):
        backend = FakeBackend()
        view = self._view(backend)
        view.enabled_checkbox.setChecked(True)
        self.assertIn(("set_sync_enabled", True), backend.calls)

    def test_relay_status_label_reflects_real_connection_state(self):
        backend = FakeBackend()
        backend.sync_status_result["relay_connected"] = True
        view = self._view(backend)
        self.assertIn("connected", view.relay_status_label.text().lower())
        self.assertNotIn("not connected", view.relay_status_label.text().lower())

    def test_relay_status_label_shows_not_connected_by_default(self):
        backend = FakeBackend()
        view = self._view(backend)
        self.assertIn("not connected", view.relay_status_label.text().lower())

    def test_nat_status_label_shows_no_active_mapping_by_default(self):
        backend = FakeBackend()
        view = self._view(backend)
        self.assertIn("no active mapping", view.nat_status_label.text().lower())

    def test_nat_status_label_renders_real_active_mapping(self):
        backend = FakeBackend()
        backend.sync_status_result["nat"] = {
            "active": True,
            "protocol": "pcp",
            "external_address": "8.8.8.8",
            "external_port": 51413,
            "lifetime_seconds": 3600,
            "obtained_at": 0.0,
            "last_error": None,
        }
        view = self._view(backend)
        text = view.nat_status_label.text()
        self.assertIn("pcp", text.lower())
        self.assertIn("8.8.8.8", text)
        self.assertIn("51413", text)

    def test_nat_status_label_shows_real_last_error_when_inactive(self):
        backend = FakeBackend()
        backend.sync_status_result["nat"] = {
            "active": False, "protocol": None, "external_address": None, "external_port": None,
            "lifetime_seconds": None, "obtained_at": None, "last_error": "no NAT traversal protocol succeeded",
        }
        view = self._view(backend)
        self.assertIn("no NAT traversal protocol succeeded", view.nat_status_label.text())

    def test_dual_stack_label_shows_listening_when_ipv6_bound(self):
        backend = FakeBackend()
        backend.sync_status_result["dual_stack"] = {
            "ipv6_supported": True,
            "listening_families": ["IPv4", "IPv6"],
            "own_ipv6_candidates": [],
        }
        view = self._view(backend)
        self.assertIn("ipv6: listening", view.dual_stack_label.text().lower())

    def test_dual_stack_label_shows_unavailable_when_ipv6_not_supported(self):
        backend = FakeBackend()
        backend.sync_status_result["dual_stack"] = {
            "ipv6_supported": False,
            "listening_families": ["IPv4"],
            "own_ipv6_candidates": [],
        }
        view = self._view(backend)
        self.assertIn("not available", view.dual_stack_label.text().lower())

    def test_dual_stack_label_shows_available_but_not_listening(self):
        backend = FakeBackend()
        backend.sync_status_result["dual_stack"] = {
            "ipv6_supported": True,
            "listening_families": ["IPv4"],
            "own_ipv6_candidates": [],
        }
        view = self._view(backend)
        text = view.dual_stack_label.text().lower()
        self.assertIn("available", text)
        self.assertIn("not currently listening", text)

    def test_dual_stack_label_lists_own_ipv6_candidates(self):
        backend = FakeBackend()
        backend.sync_status_result["dual_stack"] = {
            "ipv6_supported": True,
            "listening_families": ["IPv4", "IPv6"],
            "own_ipv6_candidates": ["[2606:4700:4700::1111]:24030"],
        }
        view = self._view(backend)
        self.assertIn("2606:4700:4700::1111", view.dual_stack_label.text())

    def test_retry_nat_mapping_button_calls_backend(self):
        backend = FakeBackend()
        view = self._view(backend)
        view._on_retry_nat_mapping()
        self.assertIn(("retry_sync_nat_mapping",), backend.calls)

    def test_apply_network_settings_includes_nat_traversal_flag(self):
        backend = FakeBackend()
        view = self._view(backend)
        view.nat_checkbox.setChecked(True)
        view._on_apply_network_settings()
        config_call = next(c for c in backend.calls if c[0] == "set_sync_config")
        config_payload = json.loads(config_call[1])
        self.assertTrue(config_payload["nat_traversal"])

    def test_apply_network_settings_includes_relay_fields(self):
        backend = FakeBackend()
        view = self._view(backend)
        view.relay_server_edit.setText("relay.example:24040")
        view.relay_checkbox.setChecked(True)
        view._on_apply_network_settings()
        network_call = next(c for c in backend.calls if c[0] == "set_sync_network_settings")
        payload = json.loads(network_call[1])
        self.assertEqual("relay.example:24040", payload["relay_server"])
        config_call = next(c for c in backend.calls if c[0] == "set_sync_config")
        config_payload = json.loads(config_call[1])
        self.assertTrue(config_payload["relay_discovery"])

    def test_route_diagnostics_renders_real_peer_state(self):
        backend = FakeBackend()
        backend.sync_status_result["peers"] = {
            "peer-1": {"route": "unreachable", "error": "connection refused", "last_seen": 0},
            "peer-2": {"route": "direct_outgoing", "error": None, "last_seen": 0},
        }
        view = self._view(backend)
        self.assertEqual(2, view.route_list.count())
        texts = [view.route_list.item(i).text() for i in range(view.route_list.count())]
        self.assertTrue(any("unreachable" in t and "connection refused" in t for t in texts))
        self.assertTrue(any("direct_outgoing" in t for t in texts))

    def test_route_diagnostics_shows_retry_state(self):
        backend = FakeBackend()
        backend.sync_status_result["peers"] = {"peer-1": {"route": "unreachable", "error": "refused", "last_seen": 0}}
        backend.sync_status_result["retry_state"] = {"peer-1": {"attempts": 2, "next_attempt": 9999999999.0}}
        view = self._view(backend)
        text = view.route_list.item(0).text()
        self.assertIn("retry attempt 2", text)

    def test_create_invitation_with_empty_result_shows_warning_not_success(self):
        backend = FakeBackend()
        backend.create_sync_pairing_invitation_result = ""  # sync not running
        view = self._view(backend)
        with mock.patch("pythia.ui.native.sync_view.QMessageBox.warning") as warn:
            view._on_create_invitation()
        warn.assert_called_once()
        self.assertEqual("", view.invitation_edit.text())

    def test_create_invitation_with_real_text_populates_field(self):
        backend = FakeBackend()
        backend.create_sync_pairing_invitation_result = "pythia-pair:abc123"
        view = self._view(backend)
        view._on_create_invitation()
        self.assertEqual("pythia-pair:abc123", view.invitation_edit.text())

    def test_trusted_devices_rendered_and_remove_calls_backend(self):
        backend = FakeBackend()
        backend.sync_trusted_devices = [{"system_uuid": "peer-1", "friendly_name": "Peer PC"}]
        view = self._view(backend)
        self.assertEqual(1, view.trusted_list.count())
        view.trusted_list.setCurrentRow(0)
        view._on_remove_trusted()
        self.assertIn(("remove_sync_trusted_device", "peer-1"), backend.calls)

    def test_incoming_offer_appears_and_reject_resolves_and_clears_row(self):
        backend = FakeBackend()
        view = self._view(backend)
        backend.syncTransferOfferReceived.emit(
            {"offer_id": "offer-1", "offer": {"game_id": "g1", "display_name": "Test Game"}, "peer_system_uuid": "peer-1"}
        )
        self.assertEqual(1, view.offers_list.count())
        view.offers_list.setCurrentRow(0)
        view._on_reject_offer()
        self.assertIn(("resolve_sync_transfer_offer", "offer-1", False, "", "", "Declined."), backend.calls)
        self.assertEqual(0, view.offers_list.count())

    def test_push_and_pull_metadata_buttons_require_a_selected_device(self):
        backend = FakeBackend()
        backend.sync_trusted_devices = [{"system_uuid": "peer-1", "friendly_name": "Peer PC"}]
        view = self._view(backend)
        view._on_push_metadata()  # nothing selected -- must not call backend
        view._on_pull_metadata()
        self.assertFalse(any(c[0] in ("push_sync_metadata_to_device", "pull_sync_metadata_from_device") for c in backend.calls))

        view.trusted_list.setCurrentRow(0)
        view._on_push_metadata()
        self.assertIn(("push_sync_metadata_to_device", "peer-1"), backend.calls)
        view._on_pull_metadata()
        self.assertIn(("pull_sync_metadata_from_device", "peer-1"), backend.calls)

    def test_cancel_transfer_requires_selection_and_shows_info_when_nothing_active(self):
        backend = FakeBackend()
        backend.sync_trusted_devices = [{"system_uuid": "peer-1", "friendly_name": "Peer PC"}]
        backend.cancel_sync_transfer_result = False
        view = self._view(backend)
        view._on_cancel_transfer()  # nothing selected -- must not call backend
        self.assertFalse(any(c[0] == "cancel_sync_transfer" for c in backend.calls))

        view.trusted_list.setCurrentRow(0)
        with mock.patch("pythia.ui.native.sync_view.QMessageBox.information") as info:
            view._on_cancel_transfer()
        self.assertIn(("cancel_sync_transfer", "peer-1"), backend.calls)
        info.assert_called_once()

    def test_staging_export_and_import_require_a_folder(self):
        backend = FakeBackend()
        view = self._view(backend)
        with mock.patch("pythia.ui.native.sync_view.QMessageBox.warning") as warn:
            view._on_export_staging()
            view._on_import_staging()
        self.assertEqual(2, warn.call_count)
        self.assertFalse(any(c[0] in ("export_sync_staging_snapshot", "import_sync_staging_snapshot") for c in backend.calls))

        view.staging_folder_edit.setText("/some/folder")
        view._on_export_staging()
        view._on_import_staging()
        self.assertIn(("export_sync_staging_snapshot", "/some/folder"), backend.calls)
        self.assertIn(("import_sync_staging_snapshot", "/some/folder"), backend.calls)

    def test_staging_import_conflicts_are_reported_not_silently_applied(self):
        backend = FakeBackend()
        view = self._view(backend)
        with mock.patch("pythia.ui.native.sync_view.QMessageBox.information") as info:
            backend.syncStagingComplete.emit(
                {"direction": "import", "result": {"status": "ok", "applied": 1, "conflicts": [{"reason": "x"}]}}
            )
        message = info.call_args[0][-1]
        self.assertIn("1 conflict", message)

    def test_transfer_received_after_accept_triggers_real_onboarding(self):
        """"onboarding after successful materialization" -- the destination
        chosen when accepting an offer must be onboarded once (and only
        once) a real transfer_received status event confirms the peer's
        transfer actually completed, never merely on accepting the offer."""
        backend = FakeBackend()
        view = self._view(backend)
        backend.syncTransferOfferReceived.emit(
            {"offer_id": "offer-1", "offer": {"game_id": "g1", "display_name": "Test Game"}, "peer_system_uuid": "peer-1"}
        )
        with mock.patch("pythia.ui.native.sync_view.QFileDialog.getExistingDirectory", side_effect=["/dest", "/cache"]):
            view.offers_list.setCurrentRow(0)
            view._on_accept_offer()
        self.assertFalse(any(c[0] == "start_onboard" for c in backend.calls))  # not yet -- only accepted so far

        backend.syncStatusChanged.emit({"event": "transfer_received", "system_uuid": "peer-1", "result": {"status": "ok"}})
        self.assertIn(("start_onboard", "/dest"), backend.calls)

        # A second, unrelated status event for the same peer must not
        # re-onboard -- the destination was already consumed.
        backend.calls.clear()
        backend.syncStatusChanged.emit({"event": "transfer_received", "system_uuid": "peer-1", "result": {"status": "ok"}})
        self.assertFalse(any(c[0] == "start_onboard" for c in backend.calls))


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class AccessibilityTests(unittest.TestCase):
    """Offscreen accessibility checks -- accessible names on the major
    interactive controls, and that keyboard focus can actually reach them
    (tab order isn't broken by e.g. a stray Qt.NoFocus policy)."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_settings_controls_have_accessible_names(self):
        from pythia.ui.native.settings_view import SettingsView

        backend = FakeBackend()
        view = SettingsView(backend)
        self.assertTrue(view.launch_mode_combo.accessibleName())
        self.assertTrue(view.ui_mode_combo.accessibleName())

    def test_sync_view_controls_have_accessible_names_and_focus_policy(self):
        from PySide6.QtCore import Qt
        from pythia.ui.native.sync_view import SyncView

        backend = FakeBackend()
        view = SyncView(backend)
        for control in (
            view.enabled_checkbox,
            view.system_name_edit,
            view.lan_checkbox,
            view.global_checkbox,
            view.listen_host_edit,
            view.listen_port_spin,
            view.trusted_list,
            view.offers_list,
            view.staging_folder_edit,
            view.relay_checkbox,
            view.relay_server_edit,
            view.route_list,
            view.nat_checkbox,
        ):
            self.assertTrue(control.accessibleName())
            self.assertNotEqual(Qt.FocusPolicy.NoFocus, control.focusPolicy())

    def test_advanced_scope_combo_has_accessible_name_and_focus_policy(self):
        from PySide6.QtCore import Qt
        from pythia.ui.native.main_window import LibraryInspector

        backend = FakeBackend()
        inspector = self._inspector(backend) if hasattr(self, "_inspector") else LibraryInspector(backend)
        self.assertTrue(inspector.advanced_scope_combo.accessibleName())
        self.assertNotEqual(Qt.FocusPolicy.NoFocus, inspector.advanced_scope_combo.focusPolicy())

    def test_variant_and_search_controls_have_accessible_names(self):
        from pythia.ui.native.main_window import LibraryInspector, LibraryView

        backend = FakeBackend()
        inspector = LibraryInspector(backend)
        self.assertTrue(inspector.variant_combo.accessibleName())

        backend2 = FakeBackend()
        view = LibraryView(backend2)
        self.assertTrue(view.search_box.accessibleName())
        self.assertTrue(view.list_view.accessibleName())


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class DownloadsViewTests(unittest.TestCase):
    """2026-08-08, "download automator end-to-end hardening and native
    workflow": the real Downloads tab -- optional-dependency status,
    starting/tracking/cancelling downloads, and the add-to-library handoff."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _view(self, backend):
        from pythia.ui.native.downloads_view import DownloadsView

        return DownloadsView(backend)

    def test_not_installed_state_shown_and_install_enabled(self):
        backend = FakeBackend()
        backend.optional_plugins_result = [
            {"id": "pythia.downloader.automator", "display_name": "Download Automator", "version": "1.0.0", "installed": False, "enabled": False}
        ]
        view = self._view(backend)
        self.assertIn("not installed", view.automator_status_label.text())
        self.assertTrue(view.install_automator_button.isEnabled())

    def test_enabled_state_shown_and_install_disabled(self):
        backend = FakeBackend()
        backend.optional_plugins_result = [
            {"id": "pythia.downloader.automator", "display_name": "Download Automator", "version": "1.0.0", "installed": True, "enabled": True}
        ]
        view = self._view(backend)
        self.assertIn("enabled", view.automator_status_label.text())
        self.assertFalse(view.install_automator_button.isEnabled())

    def test_installed_but_disabled_state_shown_honestly(self):
        backend = FakeBackend()
        backend.optional_plugins_result = [
            {"id": "pythia.downloader.automator", "display_name": "Download Automator", "version": "1.0.0", "installed": True, "enabled": False}
        ]
        view = self._view(backend)
        self.assertIn("disabled", view.automator_status_label.text())
        self.assertFalse(view.install_automator_button.isEnabled())

    def test_install_button_calls_backend_and_refreshes(self):
        backend = FakeBackend()
        backend.optional_plugins_result = [
            {"id": "pythia.downloader.automator", "display_name": "Download Automator", "version": "1.0.0", "installed": False, "enabled": False}
        ]
        view = self._view(backend)
        with mock.patch("pythia.ui.native.downloads_view.QMessageBox.information"):
            view._on_install_automator()
        self.assertIn(("install_optional_plugin", "pythia.downloader.automator"), backend.calls)

    def test_start_download_adds_a_tracked_row(self):
        backend = FakeBackend()
        view = self._view(backend)
        view.start_download("https://example.invalid/game.zip", "game.zip")
        self.assertEqual(1, view.downloads_list.count())
        self.assertIn(("start_automated_download", "https://example.invalid/game.zip", "game.zip"), backend.calls)

    def test_running_progress_shows_real_bytes_not_percentage(self):
        backend = FakeBackend()
        view = self._view(backend)
        view.start_download("https://example.invalid/game.zip", "game.zip")
        view.on_progress({"status": "running", "download_id": "dl-1", "filename": "game.zip", "received": 500, "total": 1000})
        text = view.downloads_list.item(0).text()
        self.assertIn("500/1000", text)
        self.assertNotIn("%", text)

    def test_unknown_size_progress_says_so_honestly(self):
        backend = FakeBackend()
        view = self._view(backend)
        view.start_download("https://example.invalid/game.zip", "game.zip")
        view.on_progress({"status": "running", "download_id": "dl-1", "filename": "game.zip", "received": 500, "total": 0})
        self.assertIn("size unknown", view.downloads_list.item(0).text())

    def test_cancel_calls_real_backend_with_download_id(self):
        backend = FakeBackend()
        view = self._view(backend)
        view.start_download("https://example.invalid/game.zip", "game.zip")
        view.downloads_list.setCurrentRow(0)
        view._on_cancel_selected()
        self.assertIn(("cancel_automated_download", "dl-1"), backend.calls)

    def test_add_to_library_disabled_until_completion(self):
        backend = FakeBackend()
        view = self._view(backend)
        view.start_download("https://example.invalid/game.zip", "game.zip")
        view.downloads_list.setCurrentRow(0)
        self.assertFalse(view.add_to_library_button.isEnabled())

        view.on_progress({"status": "ok", "download_id": "dl-1", "filename": "game.zip", "path": "/downloads/game.zip", "received": 10, "total": 10})
        self.assertTrue(view.add_to_library_button.isEnabled())

    def test_add_to_library_calls_start_onboard_with_containing_folder(self):
        backend = FakeBackend()
        view = self._view(backend)
        view.start_download("https://example.invalid/game.zip", "game.zip")
        view.on_progress({"status": "ok", "download_id": "dl-1", "filename": "game.zip", "path": "/downloads/game.zip", "received": 10, "total": 10})
        view.downloads_list.setCurrentRow(0)
        with mock.patch("pythia.ui.native.downloads_view.QMessageBox.information"):
            view._on_add_to_library()
        self.assertIn(str(Path("/downloads")), backend.start_onboard_calls)

    def test_extraction_success_enables_add_to_library_with_extracted_path(self):
        backend = FakeBackend()
        view = self._view(backend)
        view.start_download("https://example.invalid/game.zip", "game.zip")
        view.on_progress({"status": "ok", "download_id": "dl-1", "filename": "game.zip", "path": "/downloads/game.zip", "received": 10, "total": 10})
        view.on_progress({"status": "ok", "phase": "extract", "download_id": "dl-1", "path": "/downloads/game"})
        view.downloads_list.setCurrentRow(0)
        with mock.patch("pythia.ui.native.downloads_view.QMessageBox.information"):
            view._on_add_to_library()
        self.assertIn("/downloads/game", backend.start_onboard_calls)

    def test_checksum_mismatch_shown_and_does_not_enable_add_to_library(self):
        backend = FakeBackend()
        view = self._view(backend)
        view.start_download("https://example.invalid/game.zip", "game.zip")
        view.on_progress({"status": "checksum_mismatch", "download_id": "dl-1", "filename": "game.zip", "message": "bad hash"})
        view.downloads_list.setCurrentRow(0)
        self.assertIn("checksum mismatch", view.downloads_list.item(0).text())
        self.assertFalse(view.add_to_library_button.isEnabled())

    def test_sign_in_host_prompts_and_calls_backend(self):
        from pythia.ui.native.downloads_view import QInputDialog

        backend = FakeBackend()
        view = self._view(backend)
        with mock.patch.object(QInputDialog, "getText", return_value=("https://example.invalid/login", True)):
            view._on_sign_in_host()
        self.assertIn(("start_download_host_login", "https://example.invalid/login"), backend.calls)

    def test_host_login_persistence_failure_is_reported(self):
        from pythia.ui.native.downloads_view import QMessageBox

        backend = FakeBackend()
        view = self._view(backend)
        with mock.patch.object(QMessageBox, "warning") as warning:
            backend.downloadHostLoginComplete.emit({"status": "error", "message": "read-only credential store"})
        warning.assert_called_once()
        self.assertIn("read-only credential store", warning.call_args.args[2])

    def test_clear_host_session_prompts_calls_backend_and_reports_real_success(self):
        from pythia.ui.native.downloads_view import QInputDialog, QMessageBox

        backend = FakeBackend()
        view = self._view(backend)
        with mock.patch.object(QInputDialog, "getText", return_value=("https://example.invalid/file", True)), mock.patch.object(
            QMessageBox, "information"
        ) as information:
            view._on_clear_host_session()
        self.assertIn(("clear_download_host_session", "https://example.invalid/file"), backend.calls)
        information.assert_called_once()

    def test_f95_sign_in_calls_backend(self):
        backend = FakeBackend()
        view = self._view(backend)
        view._on_sign_in_f95()
        self.assertIn(("start_f95_login",), backend.calls)

    def test_clear_f95_session_requires_confirmation_and_reports_real_success(self):
        from pythia.ui.native.downloads_view import QMessageBox

        backend = FakeBackend()
        view = self._view(backend)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), mock.patch.object(
            QMessageBox, "information"
        ) as information:
            view._on_clear_f95_session()
        self.assertIn(("clear_f95_session",), backend.calls)
        information.assert_called_once()

    def test_f95_login_persistence_failure_is_shown_as_failure(self):
        from pythia.ui.native.downloads_view import QMessageBox

        backend = FakeBackend()
        view = self._view(backend)
        with mock.patch.object(QMessageBox, "warning") as warning:
            backend.f95LoginComplete.emit({"status": "error", "message": "read-only store"})
        warning.assert_called_once()
        self.assertIn("read-only store", warning.call_args.args[2])

    def test_f95_import_picker_dispatches_real_database_path(self):
        backend = FakeBackend()
        view = self._view(backend)
        view._on_import_f95()
        self.assertIn(("pick_f95_database_file",), backend.calls)
        self.assertIn(("import_f95", "/tmp/f95checker.db"), backend.calls)

    def test_f95_import_failure_is_shown(self):
        from pythia.ui.native.downloads_view import QMessageBox

        backend = FakeBackend()
        view = self._view(backend)
        with mock.patch.object(QMessageBox, "warning") as warning:
            backend.accountSyncComplete.emit("pythia.library.f95", {"status": "error", "message": "bad database"})
        warning.assert_called_once()
        self.assertIn("bad database", warning.call_args.args[2])


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class UpdateRowDownloadButtonTests(unittest.TestCase):
    """LibraryInspector's Systems && Updates tab -- a real Download button
    only appears when an update actually carries a download URL, and calls
    whatever real starter MainWindow wired in (DownloadsView.start_download
    in the real app)."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_update_with_download_url_gets_a_working_download_button(self):
        from PySide6.QtWidgets import QPushButton
        from pythia.ui.native.main_window import LibraryInspector

        backend = FakeBackend()
        started = []
        inspector = LibraryInspector(backend, start_download_callback=lambda url, name: started.append((url, name)) or {"status": "pending"})
        summary = _summary(
            has_updates=True,
            updates=[
                {
                    "version": "2.0",
                    "_reported_by": "system-a",
                    "downloads": (("Windows", (("Mirror", "https://example.invalid/v2.zip"),)),),
                }
            ],
        )
        inspector.set_summary(summary)

        buttons = [b for b in inspector.updates_list.findChildren(QPushButton) if b.text() == "Download"]
        self.assertEqual(1, len(buttons))
        buttons[0].click()
        self.assertEqual([("https://example.invalid/v2.zip", "Test Game")], started)

    def test_update_without_download_url_gets_no_button(self):
        from PySide6.QtWidgets import QPushButton
        from pythia.ui.native.main_window import LibraryInspector

        backend = FakeBackend()
        inspector = LibraryInspector(backend, start_download_callback=lambda url, name: {"status": "pending"})
        summary = _summary(has_updates=True, updates=[{"version": "2.0", "_reported_by": "system-a", "downloads": []}])
        inspector.set_summary(summary)

        buttons = [b for b in inspector.updates_list.findChildren(QPushButton) if b.text() == "Download"]
        self.assertEqual(0, len(buttons))

    def test_no_callback_wired_means_no_button_even_with_url(self):
        from PySide6.QtWidgets import QPushButton
        from pythia.ui.native.main_window import LibraryInspector

        backend = FakeBackend()
        inspector = LibraryInspector(backend)  # no start_download_callback
        summary = _summary(
            has_updates=True,
            updates=[{"version": "2.0", "_reported_by": "system-a", "downloads": (("Windows", (("Mirror", "https://example.invalid/v2.zip"),)),)}],
        )
        inspector.set_summary(summary)
        buttons = [b for b in inspector.updates_list.findChildren(QPushButton) if b.text() == "Download"]
        self.assertEqual(0, len(buttons))


class MergeErrorContractTests(unittest.TestCase):
    """pythia.reconciliation.merge()'s error-path id-echoing fix --
    2026-08-08, found via the ReconcileDialog tests: neither error return
    used to include game_id/retired_game_id, making an async caller unable
    to correlate a failure back to its request. Doesn't need Qt."""

    def test_self_merge_error_includes_both_ids(self):
        from pythia import reconciliation

        result = reconciliation.merge("same", "same")
        self.assertEqual("error", result["status"])
        self.assertEqual("same", result["game_id"])
        self.assertEqual("same", result["retired_game_id"])

    def test_untracked_ids_error_includes_both_ids(self):
        from pythia import reconciliation

        with mock.patch.object(reconciliation, "_own_entries", return_value=[]):
            result = reconciliation.merge("nonexistent-a", "nonexistent-b")
        self.assertEqual("error", result["status"])
        self.assertEqual("nonexistent-a", result["game_id"])
        self.assertEqual("nonexistent-b", result["retired_game_id"])


class OnboardingCancelActionTests(unittest.TestCase):
    """pythia.onboarding.onboard()'s new {"action": "cancel"} branch --
    doesn't need Qt at all."""

    def test_cancel_action_returns_cancelled_without_minting_anything(self):
        from pythia import onboarding

        fake_pv = {
            "status": "ok",
            "path": "/fake/path",
            "detection": {},
            "additional_sources": [],
            "name": "Fake Game",
            "version_label": "1.0",
            "mods": [],
            "language": None,
            "candidates": {"certain": None, "suggested": []},
        }
        with mock.patch.object(onboarding, "preview", return_value=fake_pv):
            result = onboarding.onboard("/fake/path", decision={"action": "cancel"})
        self.assertEqual("cancelled", result["status"])


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class ReconcileDialogTests(unittest.TestCase):
    """2026-08-08, native UI depth: real reconcile-review workflow. Uses the
    real Signal-based FakeBackend (mergeCandidatesReady/mergeComplete) since
    ReconcileDialog connects to these for real -- not just recording calls."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _candidate(self, score=0.9):
        return {
            "score": score,
            "a": {"game_id": "keep1", "name": "Alpha", "engine_kind": "renpy", "version_count": 1, "path": "/a"},
            "b": {"game_id": "drop1", "name": "Alpha 2", "engine_kind": "renpy", "version_count": 1, "path": "/b"},
        }

    def test_candidates_rendered_on_construction(self):
        from pythia.ui.native.main_window import ReconcileDialog

        backend = FakeBackend()
        dialog = ReconcileDialog(backend, None)
        self.assertIn(("refresh_merge_candidates",), backend.calls)

        backend.mergeCandidatesReady.emit([self._candidate()])
        self.assertEqual(1, dialog.candidates_list.count())
        self.assertIn("Alpha", dialog.candidates_list.item(0).text())

    def test_no_candidates_shows_honest_message(self):
        from pythia.ui.native.main_window import ReconcileDialog

        backend = FakeBackend()
        dialog = ReconcileDialog(backend, None)
        backend.mergeCandidatesReady.emit([])
        self.assertEqual(0, dialog.candidates_list.count())
        self.assertIn("No likely duplicates", dialog.status_label.text())

    def test_candidate_discovery_failure_is_not_shown_as_no_duplicates(self):
        from pythia.ui.native.main_window import ReconcileDialog

        backend = FakeBackend()
        dialog = ReconcileDialog(backend, None)
        backend.mergeCandidatesFailed.emit("Could not find duplicate games: disk error")
        self.assertEqual(0, dialog.candidates_list.count())
        self.assertIn("disk error", dialog.status_label.text())
        self.assertNotIn("No likely duplicates", dialog.status_label.text())

    def test_merge_requires_confirmation_and_shows_merging_not_success(self):
        from pythia.ui.native.main_window import QMessageBox, ReconcileDialog

        backend = FakeBackend()
        dialog = ReconcileDialog(backend, None)
        backend.mergeCandidatesReady.emit([self._candidate()])
        dialog.candidates_list.setCurrentRow(0)

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog._on_merge_clicked("a")
        self.assertIn(("keep1", "drop1"), backend.merge_calls)
        # Real in-progress state, not a claimed success, while the async
        # merge is outstanding.
        self.assertIn("Merging", dialog.candidates_list.item(0).text())
        self.assertNotIn("Merged", dialog.candidates_list.item(0).text())

    def test_declined_confirmation_does_not_call_merge(self):
        from pythia.ui.native.main_window import QMessageBox, ReconcileDialog

        backend = FakeBackend()
        dialog = ReconcileDialog(backend, None)
        backend.mergeCandidatesReady.emit([self._candidate()])
        dialog.candidates_list.setCurrentRow(0)

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            dialog._on_merge_clicked("a")
        self.assertEqual([], backend.merge_calls)

    def test_successful_merge_removes_row_only_after_real_signal(self):
        from pythia.ui.native.main_window import QMessageBox, ReconcileDialog

        backend = FakeBackend()
        dialog = ReconcileDialog(backend, None)
        backend.mergeCandidatesReady.emit([self._candidate()])
        dialog.candidates_list.setCurrentRow(0)

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog._on_merge_clicked("a")
        self.assertEqual(1, dialog.candidates_list.count())  # still there -- not yet confirmed

        backend.mergeComplete.emit({"status": "ok", "game_id": "keep1", "retired_game_id": "drop1"})
        self.assertEqual(0, dialog.candidates_list.count())  # removed only now, on the real signal

    def test_failed_merge_shows_real_failure_not_removed(self):
        """2026-08-08, real backend contract repair caught by this test:
        reconciliation.merge()'s error paths used to omit game_id/
        retired_game_id entirely, so a caller driving several concurrent
        merges (like this dialog, in principle) had no way to correlate a
        *failure* back to which request caused it -- this test failed
        against the original backend contract (a simulated error payload
        with no ids), which is exactly why merge()'s error returns were
        fixed to always echo both ids back, same as the success path
        already did."""
        from pythia.ui.native.main_window import QMessageBox, ReconcileDialog

        backend = FakeBackend()
        dialog = ReconcileDialog(backend, None)
        backend.mergeCandidatesReady.emit([self._candidate()])
        dialog.candidates_list.setCurrentRow(0)

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog._on_merge_clicked("a")
        backend.mergeComplete.emit(
            {
                "status": "error",
                "game_id": "keep1",
                "retired_game_id": "drop1",
                "message": "Both game IDs must be tracked by this system.",
            }
        )
        self.assertEqual(1, dialog.candidates_list.count())
        self.assertIn("Failed", dialog.candidates_list.item(0).text())

    def test_unrelated_merge_complete_is_ignored(self):
        from pythia.ui.native.main_window import QMessageBox, ReconcileDialog

        backend = FakeBackend()
        dialog = ReconcileDialog(backend, None)
        backend.mergeCandidatesReady.emit([self._candidate()])
        dialog.candidates_list.setCurrentRow(0)

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog._on_merge_clicked("a")
        # A merge some OTHER part of the app triggered, unrelated ids.
        backend.mergeComplete.emit({"status": "ok", "game_id": "other1", "retired_game_id": "other2"})
        self.assertIn("Merging", dialog.candidates_list.item(0).text())


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class PluginsViewInstallRemoveTests(unittest.TestCase):
    """2026-08-08, native UI depth: real install-from-file, protected-
    built-in removal guard, and remote-index update flow."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _plugins_view(self, backend):
        from pythia.ui.native.plugins_view import PluginsView

        return PluginsView(backend)

    def _plugin(self, **overrides):
        base = {
            "id": "pythia.engine.renpy",
            "display_name": "Ren'Py",
            "categories": ["engine"],
            "version": "1.0",
            "author": "Pythia",
            "enabled": True,
            "origin": "Built-in",
            "capability_enabled": {"engine": True},
            "configurable": False,
            "config_schema": [],
            "optional_dependencies": [],
        }
        base.update(overrides)
        return base

    def test_install_from_file_success_refreshes_list(self):
        from pythia.ui.native.plugins_view import QFileDialog

        backend = FakeBackend()
        backend.install_zip_result = {"status": "ok", "id": "third.party", "display_name": "Third Party"}
        view = self._plugins_view(backend)

        with (
            mock.patch.object(QFileDialog, "getOpenFileName", return_value=("/tmp/plugin.zip", "")),
            mock.patch("pythia.ui.native.plugins_view.QMessageBox.information") as info,
        ):
            view._on_install_from_file()
        self.assertIn(("install_plugin_zip", "/tmp/plugin.zip"), backend.calls)
        info.assert_called_once()

    def test_install_from_file_cancelled_does_not_call_backend(self):
        from pythia.ui.native.plugins_view import QFileDialog

        backend = FakeBackend()
        view = self._plugins_view(backend)
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=("", "")):
            view._on_install_from_file()
        self.assertFalse(any(c[0] == "install_plugin_zip" for c in backend.calls))

    def test_install_from_file_failure_shows_warning(self):
        from pythia.ui.native.plugins_view import QFileDialog, QMessageBox

        backend = FakeBackend()
        backend.install_zip_result = {"status": "error", "message": "Not a valid zip file."}
        view = self._plugins_view(backend)
        with (
            mock.patch.object(QFileDialog, "getOpenFileName", return_value=("/tmp/bad.zip", "")),
            mock.patch.object(QMessageBox, "warning") as warn,
        ):
            view._on_install_from_file()
        warn.assert_called_once()

    def test_builtin_plugin_has_no_remove_button(self):
        from PySide6.QtWidgets import QPushButton

        backend = FakeBackend()
        backend.all_plugins = [self._plugin(origin="Built-in")]
        view = self._plugins_view(backend)
        view._list.setCurrentRow(0)
        detail = view._detail_container.widget()
        remove_buttons = [b for b in detail.findChildren(QPushButton) if b.text() == "Remove plugin"]
        self.assertEqual(0, len(remove_buttons))

    def test_installed_plugin_has_remove_button_that_confirms_and_calls(self):
        from PySide6.QtWidgets import QMessageBox, QPushButton

        backend = FakeBackend()
        backend.all_plugins = [self._plugin(id="third.party", display_name="Third Party", origin="Installed package")]
        view = self._plugins_view(backend)
        view._list.setCurrentRow(0)
        detail = view._detail_container.widget()
        remove_buttons = [b for b in detail.findChildren(QPushButton) if b.text() == "Remove plugin"]
        self.assertEqual(1, len(remove_buttons))

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            remove_buttons[0].click()
        self.assertIn(("uninstall_plugin_zip", "third.party"), backend.calls)

    def test_remove_declined_confirmation_does_not_call_backend(self):
        from PySide6.QtWidgets import QMessageBox, QPushButton

        backend = FakeBackend()
        backend.all_plugins = [self._plugin(id="third.party", origin="Installed package")]
        view = self._plugins_view(backend)
        view._list.setCurrentRow(0)
        detail = view._detail_container.widget()
        remove_buttons = [b for b in detail.findChildren(QPushButton) if b.text() == "Remove plugin"]

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            remove_buttons[0].click()
        self.assertFalse(any(c[0] == "uninstall_plugin_zip" for c in backend.calls))


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed in this test environment")
class PluginUpdateDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_index_error_shown_honestly_not_as_no_updates(self):
        from pythia.ui.native.plugins_view import PluginUpdateDialog

        backend = FakeBackend()
        backend.check_plugin_index_result = {"status": "error", "message": "Index unreachable (repo is private)."}
        dialog = PluginUpdateDialog(backend, None)
        self.assertIn("Couldn't check", dialog.status_label.text())
        self.assertIn("Index unreachable", dialog.status_label.text())

    def test_current_plugins_are_not_actionable(self):
        from pythia.ui.native.plugins_view import PluginUpdateDialog

        backend = FakeBackend()
        backend.check_plugin_index_result = {
            "status": "ok",
            "plugins": [{"id": "p1", "display_name": "P1", "version": "1.0", "relation": "current"}],
        }
        dialog = PluginUpdateDialog(backend, None)
        self.assertEqual(0, dialog.entries_list.count())
        self.assertIn("already current", dialog.status_label.text())

    def test_new_and_update_entries_are_listed_and_installable(self):
        from pythia.ui.native.plugins_view import PluginUpdateDialog, QMessageBox

        backend = FakeBackend()
        backend.check_plugin_index_result = {
            "status": "ok",
            "plugins": [
                {"id": "p1", "display_name": "P1", "version": "1.0", "relation": "current"},
                {"id": "p2", "display_name": "P2", "version": "2.0", "relation": "new"},
                {"id": "p3", "display_name": "P3", "version": "3.0", "relation": "update"},
            ],
        }
        backend.install_from_index_result = {"status": "ok", "id": "p2", "display_name": "P2"}
        dialog = PluginUpdateDialog(backend, None)
        self.assertEqual(2, dialog.entries_list.count())

        dialog.entries_list.setCurrentRow(0)
        with mock.patch.object(QMessageBox, "information") as info:
            dialog._on_install_selected()
        self.assertIn(("install_plugin_from_index", "p2"), backend.calls)
        info.assert_called_once()
        self.assertEqual(1, dialog.entries_list.count())  # installed entry removed from the pending list

    def test_partial_remote_install_is_removed_but_warns_about_refresh(self):
        from pythia.ui.native.plugins_view import PluginUpdateDialog, QMessageBox

        backend = FakeBackend()
        backend.check_plugin_index_result = {
            "status": "ok",
            "plugins": [{"id": "p2", "display_name": "P2", "version": "2.0", "relation": "new"}],
        }
        backend.install_from_index_result = {
            "status": "partial",
            "id": "p2",
            "message": "Installed, but the plugin cache is locked.",
        }
        dialog = PluginUpdateDialog(backend, None)
        dialog.entries_list.setCurrentRow(0)
        with mock.patch.object(QMessageBox, "warning") as warning:
            dialog._on_install_selected()
        warning.assert_called_once()
        self.assertEqual(0, dialog.entries_list.count())


if __name__ == "__main__":
    unittest.main()
