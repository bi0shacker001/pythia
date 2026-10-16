"""pythia.droidtop: the library Pythia hands the droidtop agent, and the thin
modules over the compiled agent module, without that module."""

import os
import tempfile
import threading
from pathlib import Path
from unittest import TestCase, mock

from pythia.droidtop import launch, library, native, scan, service, store, takeover


class TitleKeyTest(TestCase):
    def test_matches_the_agents_title_key(self):
        # droidtop-agent core library::title_key's own example.
        self.assertEqual(library.title_key("The Game: Deluxe!"), "title:the game deluxe")
        self.assertEqual(library.title_key("the  game deluxe"), "title:the game deluxe")
        self.assertEqual(library.title_key("Café Stella"), "title:café stella")


class ScannedGamesTest(TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old = os.path.join(self.dir.name, "Eternum-0.9.4-pc")
        self.new = os.path.join(self.dir.name, "Eternum-0.9.5-pc")
        os.makedirs(self.old)
        os.makedirs(self.new)

    def tearDown(self):
        self.dir.cleanup()

    def entry(self, **extra):
        e = {
            "system_uuid": "me",
            "canonical_name": "Eternum",
            "platform_ids": [],
            "versions": [
                {"version": "0.9.4", "variants": [{"path": self.old, "detected_at": "2026-10-01T00:00:00"}]},
                {"version": "0.9.5", "variants": [{"path": self.new, "detected_at": "2026-10-02T00:00:00"}]},
            ],
        }
        e.update(extra)
        return e

    def test_the_newest_install_with_a_folder_is_the_game(self):
        (game,) = library.scanned_games([self.entry()], "me")
        self.assertEqual(game["key"], "title:eternum")
        self.assertEqual(game["install"]["version"], "0.9.5")
        self.assertEqual(game["base"], self.new)
        self.assertEqual(game["install"]["launcher"], "pythia")

    def test_store_ids_are_the_agents_keys(self):
        steam = self.entry(platform_ids=[{"platform": "f95", "id": "1"}, {"platform": "steam", "id": "440"}])
        egs = self.entry(platform_ids=[{"platform": "egs", "id": "Fortnite"}])
        self.assertEqual([g["key"] for g in library.scanned_games([steam, egs], "me")], ["steam:440", "epic:Fortnite"])

    def test_other_systems_and_missing_folders_are_left_out(self):
        gone = self.entry(versions=[{"version": "1", "variants": [{"path": os.path.join(self.dir.name, "gone")}]}])
        self.assertEqual(library.scanned_games([self.entry(system_uuid="other"), gone], "me"), [])

    def test_a_version_that_repeats_the_title_is_no_version(self):
        e = self.entry(versions=[{"version": "Eternum", "variants": [{"path": self.new}]}])
        (game,) = library.scanned_games([e], "me")
        self.assertNotIn("version", game["install"])


class NativeTest(TestCase):
    def test_refusals_raise_with_the_agents_words(self):
        fake = mock.Mock()
        fake.store.unpair.side_effect = native.AgentError("No paired handheld has that id.")
        with mock.patch.object(native, "_module", fake):
            with self.assertRaisesRegex(native.AgentError, "No paired handheld"):
                store.unpair("ab")
        fake.store.unpair.assert_called_once_with("ab")

    def test_pythias_folder_is_named_before_the_first_call(self):
        fake = mock.Mock()
        with mock.patch.object(native, "_module", fake), mock.patch.object(native, "_folder_named", False):
            store.identity()
            store.devices()
        fake.use_folder.assert_called_once_with(path=native.FOLDER)

    def test_without_the_module_it_says_why(self):
        with mock.patch.object(native, "_module", None), mock.patch.object(native, "_missing", "not installed"):
            self.assertFalse(native.available())
            with self.assertRaisesRegex(native.AgentError, "not installed"):
                store.identity()

    def test_the_library_is_handed_over_as_pythias_games(self):
        fake = mock.Mock()
        fake.scan.set_manager_games.return_value = {"games": 0, "changes": 0}
        with (
            mock.patch.object(native, "_module", fake),
            mock.patch.object(library.datadir, "list_all_entries", return_value=[]),
            mock.patch.object(library, "get_system_uuid", return_value="me"),
        ):
            self.assertEqual(library.export(rescan=False), {"games": 0, "changes": 0})
        fake.scan.set_manager_games.assert_called_once_with("Pythia", [], rescan=False)


class ExportSoonTest(TestCase):
    """library.export_soon(): what the UI calls after each change to the library."""

    def run_burst(self, serving_state):
        fake = mock.Mock()
        fake.service.serving.return_value = {"state": serving_state}
        first_call_started, release = threading.Event(), threading.Event()

        def slow_export(*_args, **_kwargs):
            first_call_started.set()
            release.wait(5)
            return {"games": 0, "changes": 0}

        fake.scan.set_manager_games.side_effect = slow_export
        with (
            mock.patch.object(native, "_module", fake),
            mock.patch.object(library.datadir, "list_all_entries", return_value=[]),
            mock.patch.object(library, "get_system_uuid", return_value="me"),
        ):
            library.export_soon()
            if serving_state == "serving":
                self.assertTrue(first_call_started.wait(5))
            for _ in range(5):  # changes while the first export runs
                library.export_soon()
            release.set()
            thread = library._export_thread
            if thread is not None:
                thread.join(5)
            self.assertIsNone(library._export_thread)
        return fake.scan.set_manager_games.call_count

    def test_a_burst_of_changes_costs_two_exports(self):
        self.assertEqual(self.run_burst("serving"), 2)

    def test_nothing_is_handed_over_while_another_program_serves(self):
        self.assertEqual(self.run_burst("stopped"), 0)


class RomFoldersTest(TestCase):
    """droidtop.scan.rom_folders(): the agent's scanner says which folders are ROM folders."""

    def found(self, root, *games):
        fake = mock.Mock()
        fake.scan.scan_with.return_value = {
            "games": [
                {"game": {"platform": platform, "install": {"path": os.path.join(root, path), "launcher": launcher}}}
                for path, platform, launcher in games
            ]
        }
        with mock.patch.object(native, "_module", fake):
            return {k.relative_to(root).as_posix(): v for k, v in scan.rom_folders(Path(root)).items()}

    def test_system_folders_and_folders_of_them_are_rom_folders(self):
        got = self.found("/g", ("psx/Crash.chd", "psx", "emulator"), ("Emu/snes/Zelda.sfc", "snes", "emulator"))
        self.assertEqual(got["psx"], {"systems": ["psx"], "roms": 1})
        self.assertEqual(set(got), {"psx", "Emu", "Emu/snes"})

    def test_a_folder_with_a_pc_game_in_it_is_not(self):
        got = self.found("/g", ("Mixed/psx/Crash.chd", "psx", "emulator"), ("Mixed/Game", "pc", "folder"))
        self.assertEqual(set(got), {"Mixed/psx"})

    def test_a_pc_game_folder_is_never_one(self):
        self.assertEqual(self.found("/g", ("Game", "pc", "folder")), {})


class GamesByDeviceTest(TestCase):
    """droidtop.store.games_by_device(): each handheld's games, from the shared library."""

    def test_installed_games_per_handheld_by_title(self):
        def install(installed, version=None):
            return {"device_name": "x", "install": {"installed": installed, "version": version}}

        library = {
            "title:eternum": {"title": "Eternum", "platform": "pc", "installs": {"rp5": install(True, "0.9.5")}},
            "rom:psx/crash": {
                "title": "Crash",
                "platform": "psx",
                "installs": {"rp5": install(True), "pc": install(True)},
            },
            "steam:440": {"title": "TF2", "platform": "pc", "installs": {"rp5": install(False), "pc": install(True)}},
        }
        devices = [{"id": "rp5", "name": "Retroid"}, {"id": "win4", "name": "Win 4"}]
        rp5, win4 = store.games_by_device(library, devices)
        self.assertEqual([g["title"] for g in rp5["games"]], ["Crash", "Eternum"])
        self.assertEqual(
            rp5["games"][1], {"key": "title:eternum", "title": "Eternum", "platform": "pc", "version": "0.9.5"}
        )
        self.assertEqual(win4, {"id": "win4", "name": "Win 4", "games": []})


class AgentEventsTest(TestCase):
    def test_events_since_the_last_seen(self):
        fake = mock.Mock()
        fake.service.events.return_value = [{"seq": 3, "kind": "launch", "message": "x"}]
        with mock.patch.object(native, "_module", fake):
            self.assertEqual(service.events(2)[0]["kind"], "launch")
        fake.service.events.assert_called_once_with(since=2, wait=0.0)

    def test_waiting_for_the_next_event(self):
        fake = mock.Mock()
        fake.service.events.return_value = []
        with mock.patch.object(native, "_module", fake):
            self.assertEqual(service.events(5, wait=30), [])
        fake.service.events.assert_called_once_with(since=5, wait=30)


class LauncherFoldersTest(TestCase):
    """scanning.scan_library leaves out a launcher's folder and anything found below it."""

    def test_a_launcher_folder_and_what_is_below_it_are_left_out(self):
        from pythia import scanning

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            launcher, portal, game = (
                root / "Epic Games" / "Launcher",
                root / "Epic Games" / "Launcher" / "Portal",
                root / "Game",
            )
            with (
                mock.patch.object(scanning, "_discover_roots", return_value=[portal, game]),
                mock.patch.object(scanning, "_rom_folders", return_value={}),
                mock.patch.object(scanning, "_launcher_rule", return_value=lambda f: f == launcher),
                mock.patch.object(scanning.onboarding, "onboard", return_value={"status": "ok", "name": "Game"}),
            ):
                results = scanning.scan_library(root)
        self.assertEqual([r["status"] for r in results], ["skipped", "ok"])
        self.assertIn("Launcher is a launcher's own folder", results[0]["message"])


class TakeoverTest(TestCase):
    """Pythia turning droidtop-agent off while it serves, and back on."""

    def agent(self, running=(), starts=False, still=()):
        fake = mock.Mock()
        fake.agent.status.return_value = {"running": list(running), "starts_at_sign_in": starts, "turned_off": None}
        fake.agent.turn_off.return_value = {"entries": ["desktop"] if starts else [], "still_running": list(still)}
        fake.agent.sync.return_value = {"imported": []}
        fake.agent.turn_on.return_value = {"entries": ["desktop"]}
        return fake

    def test_nothing_to_turn_off_still_syncs_and_writes_back(self):
        fake = self.agent()
        with mock.patch.object(native, "_module", fake):
            taken = takeover.take_over()
        fake.agent.turn_off.assert_not_called()
        fake.agent.sync.assert_called_once_with(write_back=True)
        self.assertEqual(taken["turned_off"], [])

    def test_a_running_agent_is_turned_off_before_the_sync(self):
        fake = self.agent(running=[{"pid": 7, "name": "droidtop-agent"}], starts=True)
        with mock.patch.object(native, "_module", fake):
            taken = takeover.take_over()
        self.assertEqual([c[0] for c in fake.agent.method_calls], ["status", "turn_off", "sync"])
        self.assertEqual(taken["turned_off"], ["desktop"])
        fake.agent.sync.assert_called_once_with(write_back=True)

    def test_an_agent_that_did_not_stop_gets_no_writes(self):
        still = [{"pid": 7, "name": "droidtop-agent"}]
        fake = self.agent(running=still, still=still)
        with mock.patch.object(native, "_module", fake):
            taken = takeover.take_over()
        fake.agent.sync.assert_called_once_with(write_back=False)
        self.assertEqual(taken["still_running"], still)

    def test_turning_the_agent_back_on_writes_back_first_and_stops_serving_from_pythia(self):
        fake = self.agent()
        with (
            mock.patch.object(native, "_module", fake),
            mock.patch.object(takeover.settings, "write_setting") as write,
        ):
            done = takeover.turn_agent_back_on()
        self.assertEqual([c[0] for c in fake.agent.method_calls], ["sync", "turn_on"])
        fake.agent.sync.assert_called_once_with(write_back=True)
        write.assert_called_once_with(takeover.SERVE_SETTING, False)
        self.assertEqual(done["entries"], ["desktop"])

    def test_pythia_serves_unless_told_not_to(self):
        with mock.patch.object(takeover.settings, "read_bool_setting", side_effect=lambda key, default: default):
            self.assertTrue(takeover.serves())


class HandheldLaunchTest(TestCase):
    """launch.launch_for_handheld: the agent's launcher for keys that are not Steam's."""

    def entry(self, runner=None):
        variant = {"path": "/games/Tiny", "runner": runner}
        return {
            "system_uuid": "me",
            "game_id": "g1",
            "canonical_name": "Tiny Game",
            "versions": [{"version": None, "variants": [variant]}],
        }

    def run_with(self, entries, key="title:tiny game"):
        with (
            mock.patch.object(launch.datadir, "list_all_entries", return_value=entries),
            mock.patch.object(launch, "get_system_uuid", return_value="me"),
        ):
            return launch.launch_for_handheld(key, "Tiny Game")

    def test_steam_keys_and_unknown_keys_are_not_pythias(self):
        self.assertIsNone(self.run_with([self.entry("linux")], key="steam:440"))
        self.assertIsNone(self.run_with([self.entry("linux")], key="title:something else"))
        self.assertIsNone(self.run_with([dict(self.entry("linux"), system_uuid="other")]))

    def test_a_game_with_a_runner_starts_in_a_pythia_process_of_its_own(self):
        with (
            mock.patch("pythia.game.Game") as game,
            mock.patch.object(launch.subprocess, "Popen") as popen,
        ):
            result = self.run_with([self.entry("linux")])
        self.assertEqual(result, {"result": "started", "stream": "windowcast"})
        game.assert_called_once_with("g1")
        command = popen.call_args.args[0]
        self.assertEqual(command[1:], ["-m", "pythia.launch", "pythia:rungameid/g1"])

    def test_lutris_refusing_it_is_told_to_the_handheld_and_nothing_runs(self):
        from pythia.exceptions import GameConfigError

        with (
            mock.patch("pythia.game.Game") as game,
            mock.patch.object(launch.subprocess, "Popen") as popen,
        ):
            game.return_value.check_launchable.side_effect = GameConfigError("Missing runner")
            result = self.run_with([self.entry("linux")])
        self.assertEqual(result["result"], "failed")
        self.assertIn("Missing runner", result["message"])
        popen.assert_not_called()

    def test_without_a_runner_or_a_store_launcher_it_says_so(self):
        with mock.patch("pythia.plugins.manager.find_for_game", return_value=[]):
            result = self.run_with([self.entry()])
        self.assertEqual(result["result"], "failed")
        self.assertIn("cannot start here yet", result["message"])

    def test_a_store_game_goes_to_its_launcher(self):
        plugin = mock.Mock(display_name="Epic Games Launcher")
        plugin.is_available.return_value = True
        plugin.open.return_value = True
        with mock.patch("pythia.plugins.manager.find_for_game", return_value=[(plugin, {"kind": "egs"})]):
            result = self.run_with([self.entry()])
        self.assertEqual(result, {"result": "started", "stream": "windowcast"})
        plugin.open.assert_called_once()

    def test_a_build_without_host_launches_registers_nothing(self):
        fake = mock.Mock()
        fake.service = mock.Mock(spec=["serve", "serving", "events"])
        with mock.patch.object(native, "_module", fake):
            self.assertFalse(launch.register())
        fake2 = mock.Mock()
        with mock.patch.object(native, "_module", fake2):
            self.assertTrue(launch.register())
        fake2.service.set_launcher.assert_called_once_with(launcher=launch.launch_for_handheld)
