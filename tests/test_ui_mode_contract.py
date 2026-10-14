import unittest
from pathlib import Path


class DefaultDesktopContractTests(unittest.TestCase):
    def test_default_desktop_entry_uses_implemented_native_window(self):
        root = Path(__file__).parents[1]
        desktop = (root / "pythia" / "ui" / "desktop" / "app.py").read_text(encoding="utf-8")
        mode = (root / "pythia" / "ui" / "mode.py").read_text(encoding="utf-8")

        self.assertIn("from pythia.ui.native.main_window import MainWindow", desktop)
        self.assertNotIn("hasn't been built yet", desktop)
        self.assertNotIn("still-placeholder MainWindow", mode)

    def test_default_window_installer_hooks_are_not_silent_noops(self):
        root = Path(__file__).parents[1]
        native = (root / "pythia" / "ui" / "native" / "main_window.py").read_text(encoding="utf-8")
        self.assertNotIn("def show_installer_window(self, installers, service=None, appid=None, installation_kind=None) -> None:\n        pass", native)
        self.assertNotIn("def show_pythia_installer_window(self, game_slug: str) -> None:\n        pass", native)
        self.assertIn("Nothing was installed.", native)
        self.assertNotIn("early vertical slice", native)
        self.assertIn("installerUnavailableRequested = Signal(str, str)", native)
        self.assertIn("self.installerUnavailableRequested.emit(", native)

    def test_settings_do_not_offer_duplicate_desktop_and_native_choices(self):
        root = Path(__file__).parents[1]
        settings = (root / "pythia" / "ui" / "native" / "settings_view.py").read_text(encoding="utf-8")
        self.assertNotIn('(\"native\", \"Native (this UI)\")', settings)
        self.assertIn('if current in {"native", "web"}:', settings)
        self.assertNotIn('(\"web\", \"Web (development)\")', settings)

    def test_engine_option_failures_are_not_silently_swallowed(self):
        root = Path(__file__).parents[1]
        native = (root / "pythia" / "ui" / "native" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn("self.engine_combo.setEnabled(False)", native)
        self.assertIn("Couldn't load engine choices", native)
        self.assertNotIn("for option in self.backend.get_engine_options():\n                self.engine_combo.addItem(option[\"name\"], option[\"kind\"])\n        except Exception:\n            pass", native)

    def test_bigpicture_accounts_show_credential_and_sync_results(self):
        root = Path(__file__).parents[1]
        qml = (root / "pythia" / "ui" / "bigpicture" / "qml" / "SettingsScreen.qml").read_text(encoding="utf-8")
        self.assertIn("property string accountMessage", qml)
        self.assertIn("var result = backend.set_account_credential", qml)
        self.assertIn('result && result.status === "ok"', qml)
        self.assertIn("Account sync failed.", qml)

    def test_bigpicture_plugin_toggle_reloads_after_failed_persistence(self):
        root = Path(__file__).parents[1]
        qml = (root / "pythia" / "ui" / "bigpicture" / "qml" / "SettingsScreen.qml").read_text(encoding="utf-8")
        self.assertIn("var result = backend.set_plugin_enabled", qml)
        self.assertIn('result && result.status === "ok"', qml)
        self.assertIn("Plugin state was not saved.", qml)
        self.assertIn("root.reload()", qml)


if __name__ == "__main__":
    unittest.main()
