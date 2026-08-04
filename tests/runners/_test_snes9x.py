import logging
from unittest import TestCase

from pythia.runners.snes9x import snes9x
from pythia.util.test_config import setup_test_environment

setup_test_environment()

LOGGER = logging.getLogger(__name__)


class TestSnes9x(TestCase):
    def test_set_option(self):
        snes9x_runner = snes9x()
        if snes9x_runner.is_installed():
            snes9x_runner.set_option("full_screen_on_open", "1")
        else:
            LOGGER.info("snes9x not installed, can't run test")
