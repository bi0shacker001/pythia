import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class EnginePluginIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_data = os.environ.get("PYTHIA_DATA_DIR")
        cls._data = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["PYTHIA_DATA_DIR"] = cls._data.name

    @classmethod
    def tearDownClass(cls):
        cls._data.cleanup()
        if cls._old_data is None:
            os.environ.pop("PYTHIA_DATA_DIR", None)
        else:
            os.environ["PYTHIA_DATA_DIR"] = cls._old_data

    def test_all_builtin_engine_packages_load_and_expose_complete_api(self):
        from pythia.plugins import EnginePlugin
        from pythia.plugins import engine
        from pythia.plugins.context import PluginContext
        from pythia.plugins.registry import get_plugin_load_errors

        plugins = engine.enabled_plugins()
        self.assertEqual(len(plugins), 14)
        self.assertEqual(len({plugin.id for plugin in plugins}), 14)
        self.assertFalse({key: value for key, value in get_plugin_load_errors().items() if "engine" in key})
        with tempfile.TemporaryDirectory() as empty:
            root = Path(empty)
            for plugin in plugins:
                with self.subTest(plugin=plugin.id):
                    self.assertIsInstance(plugin, EnginePlugin)
                    self.assertIsNone(plugin.detect(root))
                    capabilities = plugin.capabilities(PluginContext(log=lambda _message: None))
                    self.assertIn("installed", capabilities)
                    status = plugin.platform_status(root, PluginContext(log=lambda _message: None))
                    self.assertIsInstance(status, dict)
                    self.assertTrue(status)

    def test_ui_engine_choices_come_from_every_loaded_engine_class(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 is not installed in this test runtime")
        from pythia.ui.backend import Backend

        backend = Backend()
        choices = backend.get_engine_options()
        kinds = {choice["kind"] for choice in choices}
        self.assertEqual(
            kinds,
            {
                "august",
                "buriko",
                "catsystem2",
                "cmvs",
                "flash",
                "godot",
                "kirikiri",
                "renpy",
                "rpgmaker_mv",
                "rpgmaker_mz",
                "rpgmaker_vxace",
                "twine",
                "unity",
                "unreal",
            },
        )

    def test_unity_and_godot_report_real_native_linux_exports(self):
        from pythia.plugins import engine
        from pythia.plugins.context import PluginContext

        plugins = {plugin.id: plugin for plugin in engine.enabled_plugins()}
        context = PluginContext(log=lambda _message: None)
        for plugin_id, signature in (
            ("pythia.engine.unity", "UnityPlayer.so"),
            ("pythia.engine.godot", "game.pck"),
        ):
            with self.subTest(plugin=plugin_id), tempfile.TemporaryDirectory() as root:
                path = Path(root)
                (path / signature).write_bytes(b"signature")
                launcher = path / "game.sh"
                launcher.write_text("#!/bin/sh\n")
                launcher.chmod(0o755)
                self.assertIsNotNone(plugins[plugin_id].detect(path))
                linux = plugins[plugin_id].platform_status(path, context)["linux"]
                self.assertTrue(linux["supported"])
                self.assertTrue(linux["native"])
                self.assertEqual([], linux["requires"])

    def test_windows_only_detect_plugins_report_internal_wine_support(self):
        from pythia.plugins import engine
        from pythia.plugins.context import PluginContext

        plugins = {plugin.id: plugin for plugin in engine.enabled_plugins()}
        context = PluginContext(log=lambda _message: None)
        for plugin_id in (
            "pythia.engine.august", "pythia.engine.buriko", "pythia.engine.catsystem2",
            "pythia.engine.cmvs", "pythia.engine.flash_air", "pythia.engine.kirikiri",
            "pythia.engine.unreal", "pythia.engine.rpgmaker_vxace",
        ):
            with self.subTest(plugin=plugin_id), tempfile.TemporaryDirectory() as root:
                (Path(root) / "Game.exe").write_bytes(b"MZ")
                linux = plugins[plugin_id].platform_status(Path(root), context)["linux"]
                self.assertTrue(linux["supported"])
                self.assertFalse(linux["native"])
                self.assertEqual(["Wine or Proton"], linux["requires"])

    def test_twine_html_is_native_on_both_platforms(self):
        from pythia.plugins import engine
        from pythia.plugins.context import PluginContext

        plugin = next(p for p in engine.enabled_plugins() if p.id == "pythia.engine.twine")
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / "index.html").write_text("<tw-storydata name='Game'></tw-storydata>")
            status = plugin.platform_status(path, PluginContext(log=lambda _message: None))
            self.assertTrue(status["windows"]["native"])
            self.assertTrue(status["linux"]["native"])

    def test_renpy_patch_engine_honors_selected_platform(self):
        import importlib.util

        source = Path(__file__).resolve().parents[1] / "pythia" / "plugin_sources" / "engine" / "renpy" / "renpy_patch_engine.py"
        spec = importlib.util.spec_from_file_location("test_renpy_patch_engine", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache:
            root_path = Path(root)
            with mock.patch.object(module, "get_lib_naming_info", return_value=("py3-", "3", True)), \
                    mock.patch.object(module, "get_renpy_engine_version", return_value="8.3.0"), \
                    mock.patch.object(module, "resolve_sdk_version", return_value="8.3.0"), \
                    mock.patch.object(module, "ensure_cached_platform_folder") as ensure:
                source_lib = Path(cache) / "source"
                source_lib.mkdir()
                (source_lib / "runtime.so").write_bytes(b"runtime")
                ensure.return_value = source_lib
                module.process_game_root(
                    root_path, "Game", Path(cache), False, object(), lambda _message: None,
                    target_platforms=["linux-aarch64"],
                )
            ensure.assert_called_once()
            self.assertTrue((root_path / "lib" / "py3-linux-aarch64").is_dir())
            self.assertFalse((root_path / "lib" / "py3-linux-x86_64").exists())

    def test_renpy_sdk_download_rejects_oversized_response(self):
        import importlib.util
        import io

        source = Path(__file__).resolve().parents[1] / "pythia" / "plugin_sources" / "engine" / "renpy" / "renpy_patch_engine.py"
        spec = importlib.util.spec_from_file_location("test_renpy_patch_download", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class Response(io.BytesIO):
            headers = {"Content-Length": str(module.MAX_SDK_DOWNLOAD + 1)}

            def geturl(self):
                return "https://www.renpy.org/dl/sdk.tar.bz2"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        with tempfile.TemporaryDirectory() as root, mock.patch.object(module.urllib.request, "urlopen", return_value=Response(b"")):
            destination = Path(root) / "sdk.tar.bz2"
            with self.assertRaisesRegex(OSError, "2 GiB"):
                module._download_bounded("https://www.renpy.org/dl/sdk.tar.bz2", destination)


if __name__ == "__main__":
    unittest.main()
