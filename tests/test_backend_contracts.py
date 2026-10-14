import unittest
from unittest import mock
from pathlib import Path
import json
import tempfile


class DownloadHostSessionContractTests(unittest.TestCase):
    class _Signal:
        def connect(self, callback):
            self.callback = callback

        def emit(self, *args):
            self.callback(*args)

    class _Dialog:
        last = None

        def __init__(self, url, cookies):
            self.sessionSaved = DownloadHostSessionContractTests._Signal()
            self.rejected = DownloadHostSessionContractTests._Signal()
            DownloadHostSessionContractTests._Dialog.last = self

        def show(self):
            pass

    def test_clear_session_persists_empty_cookie_tombstone(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        plugin = mock.Mock()
        plugin.identify_host.return_value = {"supported": True, "host": "files.example"}
        backend = Backend.__new__(Backend)
        with mock.patch.object(Backend, "_download_automator", return_value=plugin):
            result = Backend.clear_download_host_session(backend, "https://files.example/download")
        self.assertEqual("ok", result["status"])
        plugin.set_host_cookies.assert_called_once_with("files.example", {})

    def test_clear_session_never_claims_success_when_persistence_fails(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        plugin = mock.Mock()
        plugin.identify_host.return_value = {"supported": True, "host": "files.example"}
        plugin.set_host_cookies.side_effect = OSError("read-only store")
        backend = Backend.__new__(Backend)
        with mock.patch.object(Backend, "_download_automator", return_value=plugin):
            result = Backend.clear_download_host_session(backend, "https://files.example/download")
        self.assertEqual("error", result["status"])
        self.assertIn("read-only store", result["message"])

    def test_clear_session_contains_registry_and_identity_failures(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        with mock.patch.object(Backend, "_download_automator", side_effect=RuntimeError("registry broken")):
            registry_result = Backend.clear_download_host_session(backend, "https://files.example/download")
        self.assertEqual("error", registry_result["status"])
        self.assertIn("registry broken", registry_result["message"])

        plugin = mock.Mock()
        plugin.identify_host.return_value = ["invalid"]
        with mock.patch.object(Backend, "_download_automator", return_value=plugin):
            identity_result = Backend.clear_download_host_session(backend, "https://files.example/download")
        self.assertEqual("error", identity_result["status"])
        self.assertIn("invalid host identity", identity_result["message"])

    def test_sign_in_normalizes_cookie_iterable_before_persist_and_count(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        plugin = mock.Mock()
        plugin.identify_host.return_value = {"supported": True, "host": "files.example"}
        plugin.get_host_cookies.return_value = {}
        backend = Backend.__new__(Backend)
        backend._download_host_login_dialogs = []
        backend.downloadHostLoginComplete = mock.Mock()
        with (
            mock.patch.object(Backend, "_download_automator", return_value=plugin),
            mock.patch("pythia.ui.f95_mask_resolver.DownloadHostLoginDialog", self._Dialog),
        ):
            Backend.start_download_host_login(backend, "https://files.example/login")
            self._Dialog.last.sessionSaved.emit("files.example", iter((("session", "cookie"),)))
        plugin.set_host_cookies.assert_called_once_with("files.example", {"session": "cookie"})
        result = backend.downloadHostLoginComplete.emit.call_args.args[0]
        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["cookies"])

    def test_sign_in_save_failure_emits_terminal_error(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        plugin = mock.Mock()
        plugin.identify_host.return_value = {"supported": True, "host": "files.example"}
        plugin.get_host_cookies.return_value = {}
        plugin.set_host_cookies.side_effect = OSError("read-only credential store")
        backend = Backend.__new__(Backend)
        backend._download_host_login_dialogs = []
        backend.downloadHostLoginComplete = mock.Mock()
        with (
            mock.patch.object(Backend, "_download_automator", return_value=plugin),
            mock.patch("pythia.ui.f95_mask_resolver.DownloadHostLoginDialog", self._Dialog),
        ):
            pending = Backend.start_download_host_login(backend, "https://files.example/login")
            self._Dialog.last.sessionSaved.emit("files.example", {"session": "cookie"})
        self.assertEqual("pending", pending["status"])
        result = backend.downloadHostLoginComplete.emit.call_args.args[0]
        self.assertEqual("error", result["status"])
        self.assertIn("read-only credential store", result["message"])
        self.assertEqual([], backend._download_host_login_dialogs)

    def test_sign_in_rejects_malformed_host_identity(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        plugin = mock.Mock()
        plugin.identify_host.return_value = ["invalid"]
        backend = Backend.__new__(Backend)
        with mock.patch.object(Backend, "_download_automator", return_value=plugin):
            result = Backend.start_download_host_login(backend, "https://files.example/login")
        self.assertEqual("error", result["status"])
        self.assertIn("invalid host identity", result["message"])


class AutomatedDownloadBoundaryContractTests(unittest.TestCase):
    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        backend._download_cancel_events = {}
        backend._run_worker = mock.Mock()
        return Backend, backend

    def test_registry_failure_is_a_terminal_result(self):
        Backend, backend = self._backend()
        with mock.patch.object(Backend, "_download_automator", side_effect=RuntimeError("registry broken")):
            result = Backend.start_automated_download(backend, "https://files.example/game.zip", "game.zip")
        self.assertEqual("error", result["status"])
        self.assertIn("registry broken", result["message"])

    def test_malformed_plan_is_rejected_before_worker_start(self):
        Backend, backend = self._backend()
        plugin = mock.Mock()
        plugin.prepare_download.return_value = ["not", "a", "plan"]
        with mock.patch.object(Backend, "_download_automator", return_value=plugin):
            result = Backend.start_automated_download(backend, "https://files.example/game.zip", "game.zip")
        self.assertEqual("error", result["status"])
        self.assertIn("invalid download plan", result["message"])
        backend._run_worker.assert_not_called()

    def test_success_plan_requires_url_and_filename(self):
        Backend, backend = self._backend()
        plugin = mock.Mock()
        with mock.patch.object(Backend, "_download_automator", return_value=plugin):
            for plan, missing in (({"status": "ok", "filename": "game.zip"}, "URL"), ({"status": "ok", "url": "https://files.example/game.zip"}, "filename")):
                with self.subTest(missing=missing):
                    plugin.prepare_download.return_value = plan
                    result = Backend.start_automated_download(
                        backend, "https://files.example/game.zip", "game.zip"
                    )
                    self.assertEqual("error", result["status"])
                    self.assertIn(missing, result["message"])
        backend._run_worker.assert_not_called()

    def test_config_failure_does_not_create_a_running_download(self):
        Backend, backend = self._backend()
        plugin = mock.Mock()
        plugin.prepare_download.return_value = {
            "status": "ok",
            "url": "https://files.example/game.zip",
            "filename": "game.zip",
        }
        plugin.get_config.side_effect = OSError("settings unavailable")
        with mock.patch.object(Backend, "_download_automator", return_value=plugin):
            result = Backend.start_automated_download(backend, "https://files.example/game.zip", "game.zip")
        self.assertEqual("error", result["status"])
        self.assertIn("settings unavailable", result["message"])
        self.assertEqual({}, backend._download_cancel_events)
        backend._run_worker.assert_not_called()

    def test_worker_scheduling_failure_rolls_back_cancellation_record(self):
        Backend, backend = self._backend()
        plugin = mock.Mock()
        plugin.prepare_download.return_value = {
            "status": "ok",
            "url": "https://files.example/game.zip",
            "filename": "game.zip",
        }
        plugin.get_config.return_value = "C:/Downloads"
        backend._run_worker.side_effect = RuntimeError("thread pool stopped")
        with mock.patch.object(Backend, "_download_automator", return_value=plugin):
            result = Backend.start_automated_download(backend, "https://files.example/game.zip", "game.zip")
        self.assertEqual("error", result["status"])
        self.assertIn("thread pool stopped", result["message"])
        self.assertEqual({}, backend._download_cancel_events)

    def test_malformed_worker_result_is_terminal_once_and_clears_cancellation(self):
        Backend, backend = self._backend()
        backend.automatedDownloadProgress = mock.Mock()
        backend._download_cancel_events = {"download-1": mock.Mock()}
        plugin = mock.Mock()
        plugin.download.return_value = ["invalid"]
        Backend._automated_download_worker(
            backend,
            plugin,
            {"filename": "game.zip", "url": "https://files.example/game.zip"},
            "C:/Downloads",
            "download-1",
            mock.Mock(),
        )
        self.assertEqual({}, backend._download_cancel_events)
        backend.automatedDownloadProgress.emit.assert_called_once()
        result = backend.automatedDownloadProgress.emit.call_args.args[0]
        self.assertEqual("error", result["status"])
        self.assertEqual("download-1", result["download_id"])

    def test_successful_worker_emits_download_completion_only_once(self):
        Backend, backend = self._backend()
        backend.automatedDownloadProgress = mock.Mock()
        backend._download_cancel_events = {"download-1": mock.Mock()}
        plugin = mock.Mock()
        plugin.download.return_value = {"status": "ok", "path": "C:/Downloads/game.zip"}
        plugin.get_config.return_value = False
        Backend._automated_download_worker(
            backend,
            plugin,
            {"filename": "game.zip", "url": "https://files.example/game.zip"},
            "C:/Downloads",
            "download-1",
            mock.Mock(),
        )
        backend.automatedDownloadProgress.emit.assert_called_once()


class F95SessionContractTests(unittest.TestCase):
    class _Signal:
        def connect(self, callback):
            self.callback = callback

        def emit(self, *args):
            self.callback(*args)

    class _Dialog:
        last = None

        def __init__(self, cookies):
            self.cookies = cookies
            self.sessionSaved = F95SessionContractTests._Signal()
            self.rejected = F95SessionContractTests._Signal()
            F95SessionContractTests._Dialog.last = self

        def show(self):
            pass

    def test_clear_session_persists_empty_credential_tombstone(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        with mock.patch("pythia.credentials.set_credential") as set_credential:
            result = Backend.clear_f95_session(backend)
        self.assertEqual("ok", result["status"])
        set_credential.assert_called_once_with("pythia.library.f95", "session_cookies", "")

    def test_clear_session_never_claims_success_when_persistence_fails(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        with mock.patch("pythia.credentials.set_credential", side_effect=OSError("read-only store")):
            result = Backend.clear_f95_session(backend)
        self.assertEqual("error", result["status"])
        self.assertIn("read-only store", result["message"])

    def test_login_contains_credential_read_failure(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        with mock.patch("pythia.credentials.get_credential", side_effect=OSError("credential store locked")):
            result = Backend.start_f95_login(backend)
        self.assertEqual("error", result["status"])
        self.assertIn("credential store locked", result["message"])

    def test_login_save_normalizes_iterable_and_cleans_dialog(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        backend._f95_login_dialogs = []
        backend.f95LoginComplete = mock.Mock()
        with (
            mock.patch("pythia.credentials.get_credential", return_value=""),
            mock.patch("pythia.credentials.set_credential") as set_credential,
            mock.patch("pythia.ui.f95_mask_resolver.F95LoginDialog", self._Dialog),
        ):
            pending = Backend.start_f95_login(backend)
            self._Dialog.last.sessionSaved.emit(iter((("session", "cookie"),)))
        self.assertEqual("pending", pending["status"])
        set_credential.assert_called_once_with(
            "pythia.library.f95", "session_cookies", '{"session": "cookie"}'
        )
        result = backend.f95LoginComplete.emit.call_args.args[0]
        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["cookies"])
        self.assertEqual([], backend._f95_login_dialogs)

    def test_login_save_failure_still_cleans_dialog(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        backend._f95_login_dialogs = []
        backend.f95LoginComplete = mock.Mock()
        with (
            mock.patch("pythia.credentials.get_credential", return_value=""),
            mock.patch("pythia.credentials.set_credential", side_effect=OSError("read-only store")),
            mock.patch("pythia.ui.f95_mask_resolver.F95LoginDialog", self._Dialog),
        ):
            Backend.start_f95_login(backend)
            self._Dialog.last.sessionSaved.emit({"session": "cookie"})
        result = backend.f95LoginComplete.emit.call_args.args[0]
        self.assertEqual("error", result["status"])
        self.assertEqual([], backend._f95_login_dialogs)


class F95MaskedLinkContractTests(unittest.TestCase):
    class _Signal:
        def connect(self, callback):
            self.callback = callback

        def emit(self, value):
            self.callback(value)

    class _Resolver:
        last = None

        def __init__(self, url, cookies):
            self.url = url
            self.cookies = cookies
            self.resolved = F95MaskedLinkContractTests._Signal()
            self.failed = F95MaskedLinkContractTests._Signal()
            F95MaskedLinkContractTests._Resolver.last = self

        def show(self):
            pass

    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        backend._mask_resolvers = []
        backend.maskedLinkResolved = mock.Mock()
        backend.start_automated_download = mock.Mock(
            return_value={"status": "unavailable", "message": "automator unavailable"}
        )
        return Backend, backend

    def test_browser_failure_is_emitted_as_terminal_error(self):
        Backend, backend = self._backend()
        backend.open_external_url = mock.Mock(
            return_value={"status": "error", "message": "browser rejected link"}
        )
        with (
            mock.patch("pythia.credentials.get_credential", return_value='{"session": "cookie"}'),
            mock.patch("pythia.ui.f95_mask_resolver.F95MaskResolver", self._Resolver),
        ):
            pending = Backend.resolve_f95_masked_link(backend, "https://f95zone.to/masked/example")
            self._Resolver.last.resolved.emit("https://files.example/game.zip")
        self.assertEqual("pending", pending["status"])
        result = backend.maskedLinkResolved.emit.call_args.args[0]
        self.assertEqual("error", result["status"])
        self.assertEqual("browser rejected link", result["message"])
        self.assertNotIn("opened_in_browser", result)

    def test_credential_read_failure_is_a_terminal_result(self):
        Backend, backend = self._backend()
        with mock.patch("pythia.credentials.get_credential", side_effect=OSError("credential store locked")):
            result = Backend.resolve_f95_masked_link(backend, "https://f95zone.to/masked/example")
        self.assertEqual("error", result["status"])
        self.assertIn("credential store locked", result["message"])
        self.assertEqual([], backend._mask_resolvers)

    def test_resolver_show_failure_rolls_back_retained_dialog(self):
        Backend, backend = self._backend()

        class BrokenResolver(self._Resolver):
            def show(self):
                raise RuntimeError("window system unavailable")

        with (
            mock.patch("pythia.credentials.get_credential", return_value='{"session": "cookie"}'),
            mock.patch("pythia.ui.f95_mask_resolver.F95MaskResolver", BrokenResolver),
        ):
            result = Backend.resolve_f95_masked_link(backend, "https://f95zone.to/masked/example")
        self.assertEqual("error", result["status"])
        self.assertIn("window system unavailable", result["message"])
        self.assertEqual([], backend._mask_resolvers)

    def test_non_object_cookie_payload_requires_sign_in(self):
        Backend, backend = self._backend()
        with mock.patch("pythia.credentials.get_credential", return_value='["not", "cookies"]'):
            result = Backend.resolve_f95_masked_link(backend, "https://f95zone.to/masked/example")
        self.assertEqual("error", result["status"])
        self.assertIn("Sign in", result["message"])

    def test_browser_success_is_reported_only_after_verified_open(self):
        Backend, backend = self._backend()
        backend.open_external_url = mock.Mock(return_value={"status": "ok"})
        with (
            mock.patch("pythia.credentials.get_credential", return_value='{"session": "cookie"}'),
            mock.patch("pythia.ui.f95_mask_resolver.F95MaskResolver", self._Resolver),
        ):
            Backend.resolve_f95_masked_link(backend, "https://f95zone.to/masked/example")
            self._Resolver.last.resolved.emit("https://files.example/game.zip")
        result = backend.maskedLinkResolved.emit.call_args.args[0]
        self.assertEqual("ok", result["status"])
        self.assertTrue(result["opened_in_browser"])


class ManagerOpenContractTests(unittest.TestCase):
    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        backend.logLine = mock.Mock()
        backend.managerOpenComplete = mock.Mock()
        return Backend, backend

    def test_worker_emits_real_plugin_result(self):
        Backend, backend = self._backend()
        plugin = mock.Mock(id="manager.test")
        plugin.open.return_value = False
        with (
            mock.patch("pythia.datadir.list_known_games", return_value={"game": []}),
            mock.patch("pythia.plugins.manager.find_for_game", return_value=[(plugin, {"kind": "test"})]),
        ):
            Backend._open_in_manager_worker(backend, "game", "manager.test")
        result = backend.managerOpenComplete.emit.call_args.args[0]
        self.assertEqual("error", result["status"])

    def test_worker_emits_failure_when_plugin_raises(self):
        Backend, backend = self._backend()
        plugin = mock.Mock(id="manager.test")
        plugin.open.side_effect = RuntimeError("manager crashed")
        with (
            mock.patch("pythia.datadir.list_known_games", return_value={"game": []}),
            mock.patch("pythia.plugins.manager.find_for_game", return_value=[(plugin, {"kind": "test"})]),
        ):
            Backend._open_in_manager_worker(backend, "game", "manager.test")
        result = backend.managerOpenComplete.emit.call_args.args[0]
        self.assertEqual("error", result["status"])
        self.assertIn("manager crashed", result["message"])


class ModePersistenceContractTests(unittest.TestCase):
    def test_launch_mode_write_failure_is_returned(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        with mock.patch("pythia.launch.set_launch_mode", side_effect=OSError("read-only store")):
            result = Backend.set_launch_mode(Backend.__new__(Backend), "direct")
        self.assertEqual("error", result["status"])
        self.assertIn("read-only store", result["message"])


class SavedSearchContractTests(unittest.TestCase):
    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        return Backend, Backend.__new__(Backend)

    def test_malformed_store_is_not_overwritten(self):
        Backend, backend = self._backend()
        with (
            mock.patch("pythia.identity.get_setting", return_value="not-json"),
            mock.patch("pythia.identity.set_setting") as set_setting,
        ):
            result = Backend.save_search(backend, "name", "query")
        self.assertEqual("error", result["status"])
        set_setting.assert_not_called()

    def test_write_failure_is_returned(self):
        Backend, backend = self._backend()
        with (
            mock.patch("pythia.identity.get_setting", return_value="[]"),
            mock.patch("pythia.identity.set_setting", side_effect=OSError("read-only store")),
        ):
            result = Backend.save_search(backend, "name", "query")
        self.assertEqual("error", result["status"])
        self.assertIn("read-only store", result["message"])


class SourceRefreshContractTests(unittest.TestCase):
    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        return Backend, Backend.__new__(Backend)

    def test_entry_persistence_failure_is_reported_as_partial(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        backend.logLine = mock.Mock()
        backend.refresh_games = mock.Mock()
        entry = {
            "game_id": "game-1",
            "system_uuid": "system-1",
            "versions": [{"variants": [{"path": "/games/one", "source_metadata": {"version": "1"}}]}],
        }
        plugin = mock.Mock()
        plugin.refresh.return_value = {"version": "2"}
        with (
            mock.patch("pythia.plugins.registry.for_category", return_value=[plugin]),
            mock.patch("pythia.ui.backend.get_system_uuid", return_value="system-1"),
            mock.patch("pythia.datadir.list_all_entries", return_value=[entry]),
            mock.patch("pythia.database.games.save_entry_and_sync_cache", side_effect=OSError("disk full")),
        ):
            result = Backend.refresh_sources(backend)
        self.assertEqual("partial", result["status"])
        self.assertEqual(1, result["checked"])
        self.assertEqual(1, result["changed"])
        self.assertEqual(1, result["errors"])
        backend.refresh_games.assert_called_once()

    def test_invalid_entries_are_filtered_on_read(self):
        Backend, backend = self._backend()
        raw = json.dumps([{"name": "valid", "search": "q"}, None, {"name": 1, "search": "bad"}])
        with mock.patch("pythia.identity.get_setting", return_value=raw):
            result = Backend.get_saved_searches(backend)
        self.assertEqual([{"name": "valid", "search": "q"}], result)

    def test_ui_mode_rejects_unknown_values_without_writing(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        with mock.patch("pythia.identity.set_setting") as set_setting:
            result = Backend.set_ui_mode(Backend.__new__(Backend), "web")
        self.assertEqual("error", result["status"])
        set_setting.assert_not_called()

    def test_ui_mode_write_failure_is_returned(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        with mock.patch("pythia.identity.set_setting", side_effect=OSError("read-only store")):
            result = Backend.set_ui_mode(Backend.__new__(Backend), "desktop")
        self.assertEqual("error", result["status"])
        self.assertIn("read-only store", result["message"])


class UpdateCheckWorkerContractTests(unittest.TestCase):
    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        backend.updateCheckComplete = mock.Mock()
        backend.logLine = mock.Mock()
        backend.refresh_games = mock.Mock()
        return Backend, backend

    def test_registry_failure_emits_terminal_error(self):
        Backend, backend = self._backend()
        with mock.patch("pythia.plugins.registry.for_category", side_effect=RuntimeError("registry failed")):
            Backend._update_check_worker(backend, [])
        result = backend.updateCheckComplete.emit.call_args.args[0]
        self.assertEqual("error", result["status"])
        self.assertIn("registry failed", result["message"])

    def test_malformed_provider_result_becomes_partial_error(self):
        Backend, backend = self._backend()
        plugin = mock.Mock(id="provider.test")
        plugin.check_updates.return_value = {"checked": 1, "updated": 2, "errors": []}
        with mock.patch("pythia.plugins.registry.for_category", return_value=[plugin]):
            Backend._update_check_worker(backend, ["game"])
        result = backend.updateCheckComplete.emit.call_args.args[0]
        self.assertEqual("partial", result["status"])
        self.assertEqual(0, result["checked"])
        self.assertIn("invalid update counts", result["errors"][0])

    def test_refresh_failure_still_emits_partial_result(self):
        Backend, backend = self._backend()
        plugin = mock.Mock(id="provider.test")
        plugin.check_updates.return_value = {"checked": 1, "updated": 0, "errors": []}
        backend.refresh_games.side_effect = OSError("cache locked")
        with mock.patch("pythia.plugins.registry.for_category", return_value=[plugin]):
            Backend._update_check_worker(backend, ["game"])
        result = backend.updateCheckComplete.emit.call_args.args[0]
        self.assertEqual("partial", result["status"])
        self.assertIn("cache locked", result["errors"][-1])


class PluginConfigContractTests(unittest.TestCase):
    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        return Backend, Backend.__new__(Backend)

    def test_unknown_fields_are_rejected_without_writing(self):
        Backend, backend = self._backend()
        entries = {"plugin.test": {"config_schema": [{"key": "enabled", "type": "bool"}]}}
        with (
            mock.patch("pythia.plugins.registry.all_plugin_entries", return_value=entries),
            mock.patch("pythia.identity.set_setting") as set_setting,
        ):
            result = Backend.save_plugin_config(backend, "plugin.test", '{"unexpected":true}')
        self.assertEqual("error", result["status"])
        set_setting.assert_not_called()

    def test_schema_type_mismatch_is_rejected(self):
        Backend, backend = self._backend()
        entries = {"plugin.test": {"config_schema": [{"key": "enabled", "type": "bool"}]}}
        with mock.patch("pythia.plugins.registry.all_plugin_entries", return_value=entries):
            result = Backend.save_plugin_config(backend, "plugin.test", '{"enabled":"yes"}')
        self.assertEqual("error", result["status"])

    def test_write_failure_is_returned(self):
        Backend, backend = self._backend()
        entries = {"plugin.test": {"config_schema": [{"key": "enabled", "type": "bool"}]}}
        with (
            mock.patch("pythia.plugins.registry.all_plugin_entries", return_value=entries),
            mock.patch("pythia.identity.set_setting", side_effect=OSError("read-only store")),
        ):
            result = Backend.save_plugin_config(backend, "plugin.test", '{"enabled":true}')
        self.assertEqual("error", result["status"])
        self.assertIn("read-only store", result["message"])


class CascadeWriteContractTests(unittest.TestCase):
    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        backend.refresh_games = mock.Mock()
        backend.logLine = mock.Mock()
        return Backend, backend

    def test_invalid_key_is_rejected_before_storage(self):
        Backend, backend = self._backend()
        with mock.patch("pythia.config_cascade.set_global") as set_global:
            result = Backend.set_config_value(backend, "global", "", "", "../secret", True)
        self.assertEqual("error", result["status"])
        set_global.assert_not_called()

    def test_instance_scope_requires_game_and_variant(self):
        Backend, backend = self._backend()
        result = Backend.set_config_value(backend, "instance", "game", "", "dxvk", True)
        self.assertEqual("error", result["status"])

    def test_storage_failure_is_returned(self):
        Backend, backend = self._backend()
        with mock.patch("pythia.config_cascade.set_game", side_effect=OSError("disk full")):
            result = Backend.set_config_value(backend, "game", "game", "", "dxvk", True)
        self.assertEqual("error", result["status"])
        self.assertIn("disk full", result["message"])


class BulkActionContractTests(unittest.TestCase):
    def test_single_game_rescan_isolates_bad_root_and_keeps_good_results(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        backend.refresh_games = mock.Mock()
        entry = {"versions": [{"variants": [{"path": "/bad"}, {"path": "/good"}]}]}

        def scan(root):
            if str(root) == "/bad":
                raise OSError("unreadable")
            return [{"status": "ok", "path": str(root)}]

        with (
            mock.patch.object(Backend, "_writable_entry", return_value=entry),
            mock.patch("pythia.scanning.scan_library", side_effect=scan),
        ):
            result = Backend.rescan_game(backend, "game")
        self.assertEqual({"status": "partial", "processed": 1, "errors": 1}, result)
        backend.refresh_games.assert_called_once()

    def test_single_game_rescan_reports_missing_paths(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        with mock.patch.object(Backend, "_writable_entry", return_value={"versions": []}):
            result = Backend.rescan_game(backend, "game")
        self.assertEqual("error", result["status"])
        self.assertEqual(1, result["errors"])

    def test_single_game_rescan_counts_malformed_scanner_rows(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        backend.refresh_games = mock.Mock()
        entry = {"versions": [{"variants": [{"path": "/game"}]}]}
        with (
            mock.patch.object(Backend, "_writable_entry", return_value=entry),
            mock.patch("pythia.scanning.scan_library", return_value=[{"status": "ok"}, None]),
        ):
            result = Backend.rescan_game(backend, "game")
        self.assertEqual({"status": "partial", "processed": 1, "errors": 1}, result)

    def test_rescan_preserves_partial_failures_in_aggregate_result(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        rows = {
            "ok": {"status": "ok", "processed": 1, "errors": 0},
            "partial": {"status": "partial", "processed": 3, "errors": 2},
            "missing": {"status": "error", "message": "Not installed."},
        }
        with mock.patch.object(Backend, "rescan_game", side_effect=lambda game_id: rows[game_id]):
            result = Backend.bulk_action(backend, ["ok", "partial", "missing"], "rescan")

        self.assertEqual(result, {"status": "partial", "processed": 3, "errors": 3})

    def test_rescan_reports_ok_only_when_every_game_succeeds(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        with mock.patch.object(
            Backend, "rescan_game", return_value={"status": "ok", "processed": 1, "errors": 0}
        ):
            result = Backend.bulk_action(backend, ["a", "a", "b"], "rescan")

        self.assertEqual(result, {"status": "ok", "processed": 2, "errors": 0})

    def test_rescan_isolates_one_game_exception_and_continues(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        outcomes = iter((RuntimeError("scanner crashed"), {"status": "ok", "processed": 1, "errors": 0}))

        def rescan(_game_id):
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with mock.patch.object(Backend, "rescan_game", side_effect=rescan):
            result = Backend.bulk_action(backend, ["broken", "healthy"], "rescan")

        self.assertEqual(result, {"status": "partial", "processed": 2, "errors": 1})

    def test_reconcile_filters_real_a_b_candidate_shape(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        candidates = [
            {"score": 0.9, "a": {"game_id": "a"}, "b": {"game_id": "b"}},
            {"score": 0.8, "a": {"game_id": "c"}, "b": {"game_id": "d"}},
        ]
        with mock.patch("pythia.ui.backend.reconciliation.find_merge_candidates", return_value=candidates):
            result = Backend.bulk_action(object(), ["b"], "reconcile")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["candidates"], candidates[:1])


class LibraryMutationContractTests(unittest.TestCase):
    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        backend.refresh_games = mock.Mock()
        backend.logLine = mock.Mock()
        return Backend, backend

    def test_notes_write_failure_is_returned(self):
        Backend, backend = self._backend()
        entry = {"system_uuid": "system", "game_id": "game"}
        with (
            mock.patch("pythia.ui.backend.get_system_uuid", return_value="system"),
            mock.patch("pythia.datadir.list_all_entries", return_value=[entry]),
            mock.patch("pythia.database.games.save_entry_and_sync_cache", side_effect=OSError("disk full")),
        ):
            result = Backend.save_notes(backend, "game", "note")
        self.assertEqual("error", result["status"])
        self.assertIn("disk full", result["message"])

    def test_rename_write_failure_is_returned(self):
        Backend, backend = self._backend()
        entry = {"game_id": "game", "canonical_name": "Old"}
        with (
            mock.patch.object(Backend, "_writable_entry", return_value=entry),
            mock.patch("pythia.database.games.save_entry_and_sync_cache", side_effect=OSError("disk full")),
        ):
            result = Backend.rename_game(backend, "game", "New")
        self.assertEqual("error", result["status"])
        self.assertIn("disk full", result["message"])

    def test_remove_failure_is_returned(self):
        Backend, backend = self._backend()
        with mock.patch("pythia.datadir.remove_entry", side_effect=OSError("locked")):
            result = Backend.remove_game(backend, "game")
        self.assertEqual("error", result["status"])
        self.assertIn("locked", result["message"])


class ReconciliationWorkerContractTests(unittest.TestCase):
    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        backend.mergeCandidatesReady = mock.Mock()
        backend.mergeCandidatesFailed = mock.Mock()
        backend.mergeComplete = mock.Mock()
        backend.refresh_games = mock.Mock()
        backend.logLine = mock.Mock()
        return Backend, backend

    def test_candidate_exception_emits_failure_not_empty_success(self):
        Backend, backend = self._backend()
        with mock.patch("pythia.reconciliation.find_merge_candidates", side_effect=RuntimeError("index failed")):
            Backend._merge_candidates_worker(backend)
        backend.mergeCandidatesReady.emit.assert_not_called()
        self.assertIn("index failed", backend.mergeCandidatesFailed.emit.call_args.args[0])

    def test_merge_exception_emits_id_correlated_terminal_error(self):
        Backend, backend = self._backend()
        with mock.patch("pythia.reconciliation.merge", side_effect=RuntimeError("write failed")):
            Backend._merge_worker(backend, "keep", "drop")
        result = backend.mergeComplete.emit.call_args.args[0]
        self.assertEqual("error", result["status"])
        self.assertEqual("keep", result["game_id"])
        self.assertEqual("drop", result["retired_game_id"])
        self.assertIn("write failed", result["message"])


class AccountPluginContractTests(unittest.TestCase):
    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        backend = Backend.__new__(Backend)
        backend.accountSyncComplete = mock.Mock()
        backend.refresh_games = mock.Mock()
        backend.logLine = mock.Mock()
        return Backend, backend

    def test_undeclared_credential_field_is_rejected(self):
        Backend, backend = self._backend()
        plugin = mock.Mock(id="library.test", credential_fields=[{"key": "token"}])
        with mock.patch("pythia.plugins.library.all_plugins", return_value=[plugin]):
            result = Backend.set_account_credential(backend, "library.test", "password", "secret")
        self.assertEqual("error", result["status"])
        plugin.set_credential.assert_not_called()

    def test_credential_write_failure_is_returned(self):
        Backend, backend = self._backend()
        plugin = mock.Mock(id="library.test", credential_fields=[{"key": "token"}])
        plugin.set_credential.side_effect = OSError("read-only store")
        with mock.patch("pythia.plugins.library.all_plugins", return_value=[plugin]):
            result = Backend.set_account_credential(backend, "library.test", "token", "secret")
        self.assertEqual("error", result["status"])
        self.assertIn("read-only store", result["message"])

    def test_missing_plugin_still_emits_sync_completion_error(self):
        Backend, backend = self._backend()
        with mock.patch("pythia.plugins.library.all_plugins", return_value=[]):
            Backend._sync_account_worker(backend, "missing")
        result = backend.accountSyncComplete.emit.call_args.args[1]
        self.assertEqual("error", result["status"])

    def test_sync_exception_is_emitted_as_failure(self):
        Backend, backend = self._backend()
        plugin = mock.Mock(id="library.test")
        with (
            mock.patch("pythia.plugins.library.all_plugins", return_value=[plugin]),
            mock.patch("pythia.onboarding.sync_owned", side_effect=RuntimeError("provider failed")),
        ):
            Backend._sync_account_worker(backend, "library.test")
        result = backend.accountSyncComplete.emit.call_args.args[1]
        self.assertEqual("error", result["status"])
        self.assertIn("provider failed", result["message"])

    def test_f95_import_rejects_non_sqlite_file(self):
        Backend, backend = self._backend()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "fake.db"
            target.write_bytes(b"not sqlite")
            Backend._import_f95_worker(backend, str(target))
        result = backend.accountSyncComplete.emit.call_args.args[1]
        self.assertEqual("error", result["status"])
        self.assertIn("not a SQLite", result["message"])

    def test_f95_import_missing_plugin_emits_terminal_error(self):
        Backend, backend = self._backend()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "valid.db"
            target.write_bytes(b"SQLite format 3\x00")
            with mock.patch("pythia.plugins.registry.get_plugin_instance", return_value=None):
                Backend._import_f95_worker(backend, str(target))
        result = backend.accountSyncComplete.emit.call_args.args[1]
        self.assertEqual("error", result["status"])
        self.assertIn("disabled or unavailable", result["message"])


class EntryExportContractTests(unittest.TestCase):
    def test_empty_export_path_is_rejected(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        with mock.patch("pythia.datadir.list_all_entries") as list_entries:
            result = Backend.export_entries_to_file(Backend.__new__(Backend), [], "")
        self.assertEqual("error", result["status"])
        list_entries.assert_not_called()

    def test_failed_replace_preserves_target_and_removes_temp(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "games.json"
            target.write_text("old", encoding="utf-8")
            with (
                mock.patch("pythia.datadir.list_all_entries", return_value=[]),
                mock.patch.object(Path, "replace", side_effect=PermissionError("locked")),
            ):
                result = Backend.export_entries_to_file(Backend.__new__(Backend), [], str(target))
            self.assertEqual("old", target.read_text(encoding="utf-8"))
            self.assertFalse(target.with_name(target.name + ".tmp").exists())
        self.assertEqual("error", result["status"])


class ProcessMonitoringUiContractTests(unittest.TestCase):
    def test_backend_exposes_real_process_registry_operations(self):
        source = (Path(__file__).parents[1] / "pythia" / "ui" / "backend.py").read_text(encoding="utf-8")
        self.assertIn("def get_monitored_launches", source)
        self.assertIn("launch.monitored_launches", source)
        self.assertIn("def stop_monitored_launch", source)
        self.assertIn("launch.stop_monitored_launch", source)
        self.assertIn("def clear_finished_launches", source)
        self.assertIn("launch.clear_finished_launches", source)

    def test_native_activity_surface_has_real_process_controls(self):
        source = (Path(__file__).parents[1] / "pythia" / "ui" / "native" / "main_window.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('QGroupBox("Launched game processes")', source)
        self.assertIn("self.backend.get_monitored_launches(True)", source)
        self.assertIn("self.backend.stop_monitored_launch", source)
        self.assertIn("self.backend.clear_finished_launches", source)
        self.assertIn("self._process_timer.setInterval(2000)", source)


class DesktopServiceContractTests(unittest.TestCase):
    def test_external_url_contains_platform_service_exception(self):
        try:
            from pythia.ui.backend import Backend
            from PySide6.QtGui import QDesktopServices
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        with mock.patch.object(QDesktopServices, "openUrl", side_effect=RuntimeError("desktop service crashed")):
            result = Backend.open_external_url(Backend.__new__(Backend), "https://example.invalid/help")
        self.assertEqual("error", result["status"])
        self.assertIn("desktop service crashed", result["message"])


class DataFolderContractTests(unittest.TestCase):
    def test_folder_api_accepts_only_known_folder_keys(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        with mock.patch("pythia.identity.app_data_dir", side_effect=AssertionError("must stay lazy")):
            result = Backend.open_data_folder(Backend.__new__(Backend), "../../arbitrary")
        self.assertEqual(result["status"], "error")
        self.assertIn("Unknown", result["message"])

    def test_known_folder_uses_local_file_url_and_reports_real_success(self):
        try:
            from pythia.ui.backend import Backend
            from PySide6.QtGui import QDesktopServices
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        with mock.patch.object(QDesktopServices, "openUrl", return_value=True) as opened:
            result = Backend.open_data_folder(Backend.__new__(Backend), "app")

        self.assertEqual(result["status"], "ok")
        self.assertTrue(Path(result["path"]).is_dir())
        self.assertTrue(opened.call_args[0][0].isLocalFile())

    def test_known_folder_reports_system_file_manager_failure(self):
        try:
            from pythia.ui.backend import Backend
            from PySide6.QtGui import QDesktopServices
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        with mock.patch.object(QDesktopServices, "openUrl", return_value=False):
            result = Backend.open_data_folder(Backend.__new__(Backend), "sync")

        self.assertEqual(result["status"], "error")
        self.assertIn("file manager", result["message"])

    def test_known_folder_contains_platform_service_exception(self):
        try:
            from pythia.ui.backend import Backend
            from PySide6.QtGui import QDesktopServices
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        with mock.patch.object(QDesktopServices, "openUrl", side_effect=RuntimeError("desktop service crashed")):
            result = Backend.open_data_folder(Backend.__new__(Backend), "sync")
        self.assertEqual("error", result["status"])
        self.assertIn("desktop service crashed", result["message"])


class DiagnosticsExportContractTests(unittest.TestCase):
    def test_export_redacts_common_log_credentials(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        secrets = "Authorization: Bearer bearer-secret\nCookie: sid=cookie-secret\nurl?access_token=query-secret\n{\"password\": \"json-secret\"}"
        backend = Backend.__new__(Backend)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "diagnostics.json"
            with (
                mock.patch("pythia.util.log.get_log_tail", return_value=(secrets, False)),
                mock.patch.object(Backend, "get_system_info", return_value={}),
                mock.patch.object(Backend, "get_launch_mode", return_value="direct"),
                mock.patch.object(Backend, "get_ui_mode", return_value="native"),
            ):
                result = Backend.export_diagnostics_to_file(backend, str(target))
            exported = target.read_text(encoding="utf-8")

        self.assertEqual(result["status"], "ok")
        for secret in ("bearer-secret", "cookie-secret", "query-secret", "json-secret"):
            self.assertNotIn(secret, exported)
        self.assertIn("[REDACTED]", exported)

    def test_export_is_bounded_and_writes_real_json(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "diagnostics.json"
            with (
                mock.patch("pythia.util.log.get_log_tail", return_value=("x" * 262_144, True)),
                mock.patch.object(Backend, "get_system_info", return_value={"system_uuid": "test"}),
                mock.patch.object(Backend, "get_launch_mode", return_value="direct"),
                mock.patch.object(Backend, "get_ui_mode", return_value="native"),
                mock.patch.object(Backend, "get_all_plugins", return_value=[{"id": "engine.test", "enabled": True}]),
                mock.patch.object(Backend, "get_sync_status", return_value={"enabled": False, "running": False}),
            ):
                result = Backend.export_diagnostics_to_file(backend, str(target))
            payload = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "ok")
        self.assertTrue(payload["log_truncated"])
        self.assertEqual(len(payload["recent_log"]), 262_144)
        self.assertEqual(payload["system"]["system_uuid"], "test")
        self.assertEqual(payload["plugins"][0]["id"], "engine.test")
        self.assertFalse(payload["sync"]["enabled"])

    def test_optional_diagnostic_subsystem_failure_does_not_abort_export(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "diagnostics.json"
            with (
                mock.patch("pythia.util.log.get_log_tail", return_value=("", False)),
                mock.patch.object(Backend, "get_system_info", return_value={}),
                mock.patch.object(Backend, "get_launch_mode", return_value="direct"),
                mock.patch.object(Backend, "get_ui_mode", return_value="native"),
                mock.patch.object(Backend, "get_all_plugins", side_effect=RuntimeError("plugin registry failed")),
                mock.patch.object(Backend, "get_sync_status", side_effect=RuntimeError("sync status failed")),
            ):
                result = Backend.export_diagnostics_to_file(backend, str(target))
            payload = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(payload["plugins"]["status"], "error")
        self.assertEqual(payload["sync"]["status"], "error")

    def test_failed_atomic_replace_preserves_old_report_and_removes_temp(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "diagnostics.json"
            target.write_text("old-report", encoding="utf-8")
            with (
                mock.patch("pythia.util.log.get_log_tail", return_value=("log", False)),
                mock.patch.object(Backend, "get_system_info", return_value={}),
                mock.patch.object(Backend, "get_launch_mode", return_value="direct"),
                mock.patch.object(Backend, "get_ui_mode", return_value="native"),
                mock.patch.object(Path, "replace", side_effect=PermissionError("locked")),
            ):
                result = Backend.export_diagnostics_to_file(backend, str(target))

            self.assertEqual(target.read_text(encoding="utf-8"), "old-report")
            self.assertFalse(target.with_name(target.name + ".tmp").exists())
        self.assertEqual(result["status"], "error")


class PluginMutationTerminalContractTests(unittest.TestCase):
    def _backend(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")
        return Backend, Backend.__new__(Backend)

    def test_zip_installer_exception_is_returned(self):
        Backend, backend = self._backend()
        manifest = {"id": "plugin.test"}
        with (
            mock.patch("pythia.plugins.packaging.read_manifest", return_value=manifest),
            mock.patch("pythia.plugins.registry.is_protected", return_value=False),
            mock.patch("pythia.plugins.packaging.install_plugin_zip", side_effect=OSError("disk full")),
        ):
            result = Backend.install_plugin_zip(backend, "plugin.zip")
        self.assertEqual("error", result["status"])
        self.assertIn("disk full", result["message"])

    def test_uninstaller_invalid_result_is_rejected(self):
        Backend, backend = self._backend()
        with mock.patch("pythia.plugins.packaging.uninstall_plugin", return_value=None):
            result = Backend.uninstall_plugin_zip(backend, "plugin.test")
        self.assertEqual("error", result["status"])
        self.assertIn("invalid result", result["message"])

    def test_local_install_and_uninstall_cache_failures_are_partial(self):
        Backend, backend = self._backend()
        manifest = {"id": "plugin.test"}
        with (
            mock.patch("pythia.plugins.packaging.read_manifest", return_value=manifest),
            mock.patch("pythia.plugins.registry.is_protected", return_value=False),
            mock.patch("pythia.plugins.packaging.install_plugin_zip", return_value={"status": "ok", "id": "plugin.test"}),
            mock.patch("pythia.plugins.registry.invalidate_installed_zip_cache", side_effect=RuntimeError("cache locked")),
        ):
            installed = Backend.install_plugin_zip(backend, "plugin.zip")
        self.assertEqual("partial", installed["status"])
        self.assertIn("was installed", installed["message"])

        with (
            mock.patch("pythia.plugins.packaging.uninstall_plugin", return_value={"status": "ok", "id": "plugin.test"}),
            mock.patch("pythia.plugins.registry.invalidate_installed_zip_cache", side_effect=RuntimeError("cache locked")),
        ):
            removed = Backend.uninstall_plugin_zip(backend, "plugin.test")
        self.assertEqual("partial", removed["status"])
        self.assertIn("was removed", removed["message"])

    def test_remote_installer_exception_is_returned(self):
        Backend, backend = self._backend()
        remote = [{"id": "plugin.test"}]
        with (
            mock.patch("pythia.plugins.remote_index.fetch_index", return_value=remote),
            mock.patch("pythia.plugins.remote_index.install_from_index", side_effect=RuntimeError("download failed")),
        ):
            result = Backend.install_plugin_from_index(backend, "plugin.test")
        self.assertEqual("error", result["status"])
        self.assertIn("download failed", result["message"])

    def test_remote_installer_rejects_malformed_catalog(self):
        Backend, backend = self._backend()
        with mock.patch("pythia.plugins.remote_index.fetch_index", return_value=[{"version": "1.0.0"}]):
            result = Backend.install_plugin_from_index(backend, "plugin.test")
        self.assertEqual("error", result["status"])
        self.assertIn("invalid entry", result["message"])

    def test_remote_install_cache_failure_reports_installed_partial(self):
        Backend, backend = self._backend()
        remote = [{"id": "plugin.test"}]
        installed = {"status": "ok", "id": "plugin.test", "display_name": "Test"}
        with (
            mock.patch("pythia.plugins.remote_index.fetch_index", return_value=remote),
            mock.patch("pythia.plugins.remote_index.install_from_index", return_value=installed),
            mock.patch(
                "pythia.plugins.registry.invalidate_installed_zip_cache",
                side_effect=RuntimeError("cache locked"),
            ),
        ):
            result = Backend.install_plugin_from_index(backend, "plugin.test")
        self.assertEqual("partial", result["status"])
        self.assertIn("was installed", result["message"])
        self.assertIn("cache locked", result["message"])

    def test_remote_catalog_contains_installed_registry_failure(self):
        Backend, backend = self._backend()
        remote = [{"id": "plugin.test", "version": "1.0.0"}]
        with (
            mock.patch("pythia.plugins.remote_index.fetch_index", return_value=remote),
            mock.patch("pythia.plugins.registry.all_plugin_entries", side_effect=RuntimeError("registry broken")),
        ):
            result = Backend.check_plugin_index(backend)
        self.assertEqual("error", result["status"])
        self.assertIn("registry broken", result["message"])

    def test_remote_catalog_rejects_malformed_entries(self):
        Backend, backend = self._backend()
        with (
            mock.patch("pythia.plugins.remote_index.fetch_index", return_value=[{"version": "1.0.0"}]),
            mock.patch("pythia.plugins.registry.all_plugin_entries", return_value={}),
        ):
            result = Backend.check_plugin_index(backend)
        self.assertEqual("error", result["status"])
        self.assertIn("invalid entry", result["message"])


class PluginToggleContractTests(unittest.TestCase):
    def test_native_plugin_toggles_require_backend_success_and_revert_failures(self):
        source = (Path(__file__).parents[1] / "pythia" / "ui" / "native" / "plugins_view.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('result.get("status") == "ok"', source)
        self.assertIn("enabled_box.blockSignals(True)", source)
        self.assertIn("checkbox.blockSignals(True)", source)
        self.assertIn('result.get("message", "The plugin state was not saved.")', source)

    def test_unknown_plugin_and_capability_are_rejected(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        with mock.patch("pythia.plugins.registry.all_plugin_entries", return_value={}):
            plugin = Backend.set_plugin_enabled(backend, "unknown", True)
            capability = Backend.set_plugin_capability_enabled(backend, "unknown", "engine", True)
        self.assertEqual(plugin["status"], "error")
        self.assertEqual(capability["status"], "error")

    def test_persisted_plugin_and_capability_changes_return_real_success(self):
        try:
            from pythia.ui.backend import Backend
        except ImportError as exc:
            self.skipTest(f"UI backend dependencies are unavailable: {exc}")

        backend = Backend.__new__(Backend)
        entries = {"plugin.test": {"categories": ["engine"]}}
        with (
            mock.patch("pythia.plugins.registry.all_plugin_entries", return_value=entries),
            mock.patch("pythia.plugins.registry.get_enabled_plugin_ids", return_value=[]),
            mock.patch("pythia.plugins.registry.set_enabled_plugin_ids") as set_plugins,
            mock.patch("pythia.plugins.registry.set_capability_enabled") as set_capability,
        ):
            plugin = Backend.set_plugin_enabled(backend, "plugin.test", True)
            capability = Backend.set_plugin_capability_enabled(backend, "plugin.test", "engine", False)
        self.assertEqual(plugin["status"], "ok")
        self.assertEqual(capability["status"], "ok")
        set_plugins.assert_called_once_with(["plugin.test"])
        set_capability.assert_called_once_with("plugin.test", "engine", False)


if __name__ == "__main__":
    unittest.main()
