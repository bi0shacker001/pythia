"""
Path/URL constants -- ported from Lutris's lutris/settings.py (2026-08-03).

Changes from the original:
- GLib.get_user_{config,data,cache}_dir() calls replaced with
  pythia.identity.app_data_dir(), which already does the same job (per-OS standard
  location, override-able) without a GTK/GLib dependency -- Lutris needed GLib
  because it's a GTK app; Pythia doesn't link GTK at all.
- Lutris's own local settings-file system (SettingsIO/lutris.conf, an INI-style file
  for a handful of user prefs like runner_dir/cache_dir) is dropped entirely --
  Pythia already has this exact concept as pythia.identity.get_setting/set_setting,
  backed by local.db. No need for two parallel settings stores.
- CONFIG_DIR/RUNNERS_CONFIG_DIR/GAME_CONFIG_DIR here are for *this* module's own
  cascading option-schema config files (see config.py), stored as JSON, not YAML --
  substituted to avoid adding a PyYAML dependency for something JSON already does
  fine; nothing here needs YAML specifically, that was just Lutris's own choice.
- STEAM_API_KEY (Lutris's own hardcoded default developer key) is deliberately NOT
  copied over -- using Lutris's own API key for Pythia's requests would misattribute
  traffic/quota to their app. Any Steam Web API integration needs the user's own key
  (per this project's existing "authenticated Steam import needs the user's own API
  key" note in the plan).
- SITE_URL and the URLs derived from it (installer scripts, runtime metadata) are
  kept pointing at lutris.net on purpose -- this is Lutris's real public games
  database, independently researched this session as a genuine cross-store-ID source
  Pythia wants to use (steamid/gogslug/humblestoreid fields, installer scripts with
  embedded appids). Using it for that is a legitimate, different reason than Lutris's
  own use of it as its update-check/installer-fetch backend.
"""

import os

from pythia.identity import app_data_dir

PROJECT = "Pythia"

DATA_DIR = str(app_data_dir())
CONFIG_DIR = DATA_DIR
CACHE_DIR = os.path.join(DATA_DIR, "cache")
TMP_DIR = os.path.join(CACHE_DIR, "tmp")

GAME_CONFIG_DIR = os.path.join(CONFIG_DIR, "games")
RUNNERS_CONFIG_DIR = os.path.join(CONFIG_DIR, "runners_config")
RUNNER_DIR = os.path.join(DATA_DIR, "runners")
RUNTIME_DIR = os.path.join(DATA_DIR, "runtime")

SITE_URL = "https://lutris.net"
INSTALLER_URL = SITE_URL + "/api/installers/%s"
INSTALLER_REVISION_URL = SITE_URL + "/api/installers/game/%s/revisions/%s"
GAME_URL = SITE_URL + "/games/%s/"
RUNTIME_URL = SITE_URL + "/api/runtimes"

DEFAULT_USER_AGENT = "Pythia (https://github.com/bi0shacker001/pythia)"
