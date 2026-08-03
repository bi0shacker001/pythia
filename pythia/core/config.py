"""
Cascading plugin configuration -- adapted from Lutris's lutris/config.py
(`LutrisConfig`, 2026-08-03).

What's kept: the real, valuable pattern Lutris's own Milestone 4 research already
flagged (see the plan) -- three cascading levels, each only overriding what it
explicitly sets, reads walking most-specific-first. Lutris's triad is
(system, runner, game); Pythia's is renamed to (system, plugin, game) since we have
no "runner" concept identically -- a plugin (any category: engine, downloader,
manager, ...) is Pythia's closest analog, and its declared `config_schema`
(PluginMeta.config_schema, already existing) supplies the option list this class
used to get from a runner's `*_options` attributes or Lutris's `sysoptions` module.

What's dropped: Lutris's `sysoptions.py` (574 lines of X11/Wine-launch-time options --
resolution overrides, compositor toggles, GPU selection) isn't ported here at all --
it's about configuring an environment to *launch* a game in, which is exactly the
"launchy bits" the user asked to keep gated behind an experimental flag rather than
build out now (see the plan's Round on this). `system_options_default()` below
starts empty; something can populate it later if the experimental launch path ever
gets built out.

Storage is JSON, not YAML (see core/settings.py's docstring for why -- avoids adding
a PyYAML dependency for something JSON already does).
"""

import json
import os
import time
from typing import Any

from pythia.core import settings


def _read_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(config: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.replace(temp_path, path)


def make_game_config_id(game_slug: str) -> str:
    return f"{game_slug}-{int(time.time())}"


def system_options_default() -> list[dict[str, Any]]:
    """Empty by default -- see module docstring. Not the same thing as "no system
    config is possible," just "nothing populates it yet."""
    return []


class PluginConfig:
    """Cascading (system -> plugin -> game) option storage. See module docstring for
    what changed from Lutris's LutrisConfig. `plugin_id` plays the role Lutris's
    `runner_slug` did; `plugin_schema` is that plugin's `config_schema` (a list of
    {"option", "default", ...} dicts, PluginMeta's existing declarative shape) --
    passed in explicitly rather than looked up via an `import_runner`-style registry
    call, so this class has no import-time dependency on the plugin registry."""

    def __init__(
        self,
        plugin_id: str | None = None,
        plugin_schema: list[dict[str, Any]] | None = None,
        game_config_id: str | None = None,
        level: str | None = None,
    ):
        self.plugin_id = plugin_id
        self.plugin_schema = plugin_schema or []
        self.game_config_id = game_config_id

        self.game_config: dict[str, Any] = {}
        self.plugin_config: dict[str, Any] = {}
        self.system_config: dict[str, Any] = {}

        self.raw_game_config: dict[str, Any] = {}
        self.raw_plugin_config: dict[str, Any] = {}
        self.raw_system_config: dict[str, Any] = {}
        self.raw_config: dict[str, Any] = {}

        self.level = level or ("game" if game_config_id else "plugin" if plugin_id else "system")
        self._initialize()

    @property
    def system_config_path(self) -> str:
        return os.path.join(settings.CONFIG_DIR, "system.json")

    @property
    def plugin_config_path(self) -> str | None:
        if not self.plugin_id:
            return None
        return os.path.join(settings.RUNNERS_CONFIG_DIR, f"{self.plugin_id}.json")

    @property
    def game_config_path(self) -> str | None:
        if not self.game_config_id:
            return None
        return os.path.join(settings.GAME_CONFIG_DIR, f"{self.game_config_id}.json")

    def _initialize(self) -> None:
        self.game_level: dict[str, Any] = {"system": {}, "plugin": {}, "game": {}}
        self.plugin_level: dict[str, Any] = {"system": {}, "plugin": {}}
        self.system_level: dict[str, Any] = {"system": {}}

        if self.game_config_path:
            self.game_level.update(_read_json(self.game_config_path))
        if self.plugin_config_path:
            self.plugin_level.update(_read_json(self.plugin_config_path))
        self.system_level.update(_read_json(self.system_config_path))

        self._update_cascaded_config()
        self._update_raw_config()

    def _get_defaults(self) -> dict[str, Any]:
        defaults = {}
        for opt in self.plugin_schema:
            if "default" in opt:
                defaults[opt["option"]] = opt["default"]
        return defaults

    def _update_cascaded_config(self) -> None:
        self.system_config.clear()
        self.system_config.update(self.system_level.get("system", {}))

        if self.level in ("plugin", "game") and self.plugin_id:
            self.plugin_config.clear()
            self.plugin_config.update(self._get_defaults())
            self.plugin_config.update(self.plugin_level.get("plugin", {}))
            self.system_config.update(self.plugin_level.get("system", {}))

        if self.level == "game":
            self.game_config.clear()
            self.game_config.update(self.game_level.get("game", {}))
            self.plugin_config.update(self.game_level.get("plugin", {}))
            self.system_config.update(self.game_level.get("system", {}))

    def _update_raw_config(self) -> None:
        raw = {"game": self.game_level, "plugin": self.plugin_level, "system": self.system_level}[self.level]
        self.raw_system_config = raw["system"]
        if self.level in ("plugin", "game"):
            self.raw_plugin_config = raw["plugin"]
        if self.level == "game":
            self.raw_game_config = raw["game"]
        self.raw_config = raw

    def save(self) -> None:
        if self.level == "system":
            config, path = self.system_level, self.system_config_path
        elif self.level == "plugin" and self.plugin_config_path:
            config, path = self.plugin_level, self.plugin_config_path
        elif self.level == "game" and self.game_config_path:
            config, path = self.game_level, self.game_config_path
        else:
            raise ValueError(f"Invalid config level '{self.level}'")
        config = {k: v for k, v in config.items() if v}
        _write_json(config, path)
        self._initialize()
