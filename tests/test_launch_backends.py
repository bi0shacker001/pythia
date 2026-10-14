import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from pythia import launch


class CompatibilityLaunchPlanTests(unittest.TestCase):
    def setUp(self):
        with launch._launch_lock:
            launch._launches.clear()
    def test_wine_builds_internal_command_and_environment(self):
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / "Game.exe"
            exe.write_bytes(b"MZ")
            config = {"backend": "wine", "wine_prefix": str(Path(root) / "prefix"),
                      "wine_arch": "win64", "esync": True, "fsync": False,
                      "dxvk": True, "vkd3d": False}
            with mock.patch("pythia.launch.shutil.which", return_value="/usr/bin/wine"):
                plan = launch.resolve_launch_plan({"path": root}, config)
            self.assertTrue(plan["ready"])
            self.assertEqual(["/usr/bin/wine"], plan["wrappers"])
            self.assertEqual("win64", plan["environment"]["WINEARCH"])
            self.assertEqual("1", plan["environment"]["WINEESYNC"])
            self.assertEqual("0", plan["environment"]["WINEFSYNC"])

    def test_proton_uses_run_subcommand(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "Game.exe").write_bytes(b"MZ")
            with mock.patch("pythia.launch.shutil.which", return_value="/opt/proton"):
                plan = launch.resolve_launch_plan({"path": root}, {"backend": "proton"})
            self.assertTrue(plan["ready"])
            self.assertEqual(["/opt/proton", "run"], plan["wrappers"])

    def test_missing_runner_is_honestly_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("pythia.launch.shutil.which", return_value=None):
                plan = launch.resolve_launch_plan({"path": root}, {"backend": "wine"})
            self.assertFalse(plan["ready"])
            self.assertEqual("backend_unavailable", plan["state"])

    def test_unset_toggles_preserve_runner_defaults(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "Game.exe").write_bytes(b"MZ")
            with mock.patch("pythia.launch.shutil.which", return_value="/usr/bin/wine"):
                plan = launch.resolve_launch_plan({"path": root}, {"backend": "wine"})
            self.assertNotIn("WINEESYNC", plan["environment"])
            self.assertNotIn("WINEFSYNC", plan["environment"])
            self.assertNotIn("DXVK_DISABLE", plan["environment"])

    def test_process_monitor_records_exit_and_can_clear_history(self):
        class FakeProcess:
            pid = 1234

            def __init__(self):
                self.released = threading.Event()

            def wait(self):
                self.released.wait(1)
                return 7

        process = FakeProcess()
        launch._register_launch(process, ["game.exe"], "/games/test")
        self.assertTrue(launch.monitored_launches()[0]["running"])
        process.released.set()
        for _ in range(100):
            if not launch.monitored_launches()[0]["running"]:
                break
            threading.Event().wait(0.005)
        row = launch.monitored_launches()[0]
        self.assertFalse(row["running"])
        self.assertEqual(7, row["exit_code"])
        self.assertEqual(1, launch.clear_finished_launches())
        self.assertEqual([], launch.monitored_launches())

    def test_stop_monitored_launch_terminates_running_process(self):
        class FakeProcess:
            pid = 5678

            def __init__(self):
                self.terminated = False
                self.released = threading.Event()

            def wait(self):
                self.released.wait(1)
                return -15

            def terminate(self):
                self.terminated = True
                self.released.set()

        process = FakeProcess()
        launch._register_launch(process, ["game.exe"], "/games/test")
        result = launch.stop_monitored_launch(process.pid)
        self.assertEqual("ok", result["status"])
        self.assertTrue(process.terminated)


if __name__ == "__main__":
    unittest.main()
