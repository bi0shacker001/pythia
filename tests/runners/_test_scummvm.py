from unittest import TestCase
from unittest.mock import patch

from pythia.config import PythiaConfig
from pythia.runners.runner import Runner
from pythia.runners.scummvm import scummvm
from pythia.util.test_config import setup_test_environment

setup_test_environment()


class TestScummvm(TestCase):
    def test_custom_data_dir(self):
        scummvm_runner = scummvm()
        scummvm_runner.config = PythiaConfig()
        scummvm_runner.config.runner_config["datadir"] = "~/custom/scummvm"

        with patch.object(Runner, "get_command", return_value=["scummvm"]):
            self.assertEqual(scummvm_runner.get_scummvm_data_dir(), "~/custom/scummvm")
            self.assertEqual(scummvm_runner.get_command()[1], "--extrapath=~/custom/scummvm")

    def test_no_executable(self):
        with patch.object(Runner, "get_command", return_value=[]):
            self.assertEqual(scummvm().get_command(), [])
