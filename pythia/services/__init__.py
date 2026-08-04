"""Service package"""

import os
from typing import TYPE_CHECKING

from pythia import settings
from pythia.services.amazon import AmazonService
from pythia.services.battlenet import BattleNetService
from pythia.services.dolphin import DolphinService
from pythia.services.ea_app import EAAppService
from pythia.services.egs import EpicGamesStoreService
from pythia.services.flathub import FlathubService
from pythia.services.gamejolt import GameJoltService
from pythia.services.gog import GOGService
from pythia.services.humblebundle import HumbleBundleService
from pythia.services.itchio import ItchIoService
from pythia.services.pythia import PythiaService
from pythia.services.mame import MAMEService
from pythia.services.scummvm import SCUMMVM_CONFIG_FILE, ScummvmService
from pythia.services.steam import SteamService
from pythia.services.steamfamily import SteamFamilyService
from pythia.services.steamwindows import SteamWindowsService
from pythia.services.ubisoft import UbisoftConnectService
from pythia.services.xdg import XDGService
from pythia.services.zoom import ZoomService
from pythia.util import system
from pythia.util.dolphin.cache_reader import DOLPHIN_GAME_CACHE_FILE
from pythia.util.linux import LINUX_SYSTEM

if TYPE_CHECKING:
    from pythia.services.base import BaseService

DEFAULT_SERVICES = ["gog", "egs", "ea_app", "ubisoft", "steam"]


def get_services() -> dict[str, "type[BaseService]"]:
    """Return a mapping of available services"""
    _services = {
        "gog": GOGService,
        "zoomplatform": ZoomService,
        "humblebundle": HumbleBundleService,
        "egs": EpicGamesStoreService,
        "gamejolt": GameJoltService,
        "itchio": ItchIoService,
        "ea_app": EAAppService,
        "ubisoft": UbisoftConnectService,
        "amazon": AmazonService,
        "flathub": FlathubService,
    }
    _services["battlenet"] = BattleNetService
    if not LINUX_SYSTEM.is_flatpak():
        _services["xdg"] = XDGService
    if LINUX_SYSTEM.has_steam():
        _services["steam"] = SteamService
    _services["steamwindows"] = SteamWindowsService
    _services["steamfamily"] = SteamFamilyService
    if system.path_exists(DOLPHIN_GAME_CACHE_FILE):
        _services["dolphin"] = DolphinService
    if system.path_exists(SCUMMVM_CONFIG_FILE):
        _services["scummvm"] = ScummvmService
    if os.environ.get("PYTHIA_SERVICE_ENABLED") == "1":
        _services["pythia"] = PythiaService
    return _services


SERVICES = get_services()


# Those services are not yet ready to be used
def get_wip_services() -> dict[str, "type[BaseService]"]:
    return {"mame": MAMEService}


WIP_SERVICES = get_wip_services()

if os.environ.get("PYTHIA_ENABLE_ALL_SERVICES"):
    SERVICES.update(WIP_SERVICES)


def get_enabled_services():
    return {
        key: _class
        for key, _class in SERVICES.items()
        if settings.read_setting(key, section="services").lower() == "true"
    }
