"""
Central plugin registry. Every plugin -- built-in or third-party, single-category or
multi-category -- is discovered and instantiated exactly *once* here; each category's
own module (pythia.plugins.library, .engine, .downloader, .source, .mod) is a thin
filtered view over this single list, split by isinstance(). This is what makes a
multi-category plugin (2026-08-02: "a single plugin that can do downloads, manage
library sources, handle mods") work correctly: `class MyPlugin(DownloaderPlugin,
ModPlugin)` gets built once and appears in both the downloader and mod registries
from that one instance, not two independently-constructed ones -- important if the
plugin holds shared state (an authenticated session, a connection to one service).

Built-in plugins self-register via register_builtin() at import time, right next to
their own class definition, instead of a big central list that drifts out of sync as
plugins are added. Third-party plugins reach the registry through either of two
paths, matching how they're actually distributed:

- A proper pip-installed package declaring a single `importlib.metadata` entry point
  under the "pythia.plugins" group -- one entry point per plugin however many
  categories it spans, not one per category (discover_external(), unchanged).
- A drop-in folder under app_data_dir()/plugins/, each with its own plugin.json
  manifest (discover_folder_plugins(), added 2026-08-03 -- Playnite's extension.yaml
  + Extensions-folder model, the piece Lutris's own hardcoded services/runners lists
  have no equivalent of at all). This is the "download a .zip, drop it in a folder"
  path pip-install doesn't cover -- list_folder_plugin_manifests() reads the manifest
  fields alone, with no import of the plugin's actual code, so a future "available
  extensions" UI can list/version-check what's on disk without running anything.
"""

import importlib.util
import json
from pathlib import Path

from pythia.plugins import (
    DownloaderPlugin,
    EnginePlugin,
    LibraryPlugin,
    ManagerPlugin,
    MetadataPlugin,
    ModPlugin,
    PatcherPlugin,
    SourcePlugin,
)

ENTRY_POINT_GROUP = "pythia.plugins"

CATEGORY_CLASSES = {
    "library": LibraryPlugin,
    "manager": ManagerPlugin,
    "engine": EnginePlugin,
    "downloader": DownloaderPlugin,
    "metadata": MetadataPlugin,
    "source": SourcePlugin,
    "mod": ModPlugin,
    "patcher": PatcherPlugin,
}

_builtin_instances = []
_external_cache = None
_folder_cache = None


def _plugins_dir() -> Path:
    from pythia.identity import app_data_dir

    return app_data_dir() / "plugins"


def list_folder_plugin_manifests() -> list[dict]:
    """Read every app_data_dir()/plugins/*/plugin.json manifest -- id, display_name,
    version, author, categories (the *declared* categories; a plugin's real
    categories are only known for certain once it's actually loaded and isinstance-
    checked, same as every other plugin) -- without importing a single line of the
    plugin's own code. Malformed manifests are skipped, not raised, matching
    discover_external()'s "one broken plugin never blocks the rest" rule."""
    manifests = []
    plugins_dir = _plugins_dir()
    if not plugins_dir.is_dir():
        return manifests
    for folder in sorted(p for p in plugins_dir.iterdir() if p.is_dir()):
        manifest_path = folder / "plugin.json"
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not data.get("id") or not data.get("entry_module") or not data.get("entry_class"):
            continue
        data["_folder"] = str(folder)
        manifests.append(data)
    return manifests


def discover_folder_plugins(log=print) -> list:
    """Import and instantiate every manifest from list_folder_plugin_manifests() --
    each manifest's "entry_module" is loaded from <folder>/<entry_module>.py (a
    plugin can be a single file; a package layout works too since
    importlib.util.spec_from_file_location follows a package's own __init__.py same
    as a normal import would once the module is registered in sys.modules), then
    "entry_class" is instantiated off it. A single broken/malformed plugin is logged
    and skipped, same isolation guarantee as discover_external(). Cached after the
    first successful call in a process, same reasoning as discover_external() --
    dropped-in plugin folders don't change without a restart either."""
    global _folder_cache
    if _folder_cache is not None:
        return _folder_cache

    import sys

    found = []
    for manifest in list_folder_plugin_manifests():
        folder = Path(manifest["_folder"])
        module_name = f"pythia_plugin_{manifest['id'].replace('.', '_').replace('-', '_')}"
        module_file = folder / f"{manifest['entry_module']}.py"
        try:
            if not module_file.is_file():
                # Package layout: <entry_module>/__init__.py instead of a single file.
                module_file = folder / manifest["entry_module"] / "__init__.py"
            spec = importlib.util.spec_from_file_location(module_name, module_file)
            if spec is None or spec.loader is None:
                raise ImportError(f"could not build an import spec for {module_file}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            cls = getattr(module, manifest["entry_class"])
            found.append(cls())
        except Exception as e:
            log(f"plugin discovery: failed to load folder plugin {manifest.get('id')!r}: {e}")

    _folder_cache = found
    return found


def register_builtin(instance):
    """Called once per built-in plugin instance, at the bottom of the module that
    defines it (see e.g. plugins/library/renpy.py). Returns the instance unchanged
    so it can be assigned to a module-level name in the same line."""
    _builtin_instances.append(instance)
    return instance


def categories_of(plugin) -> list:
    """Every category this plugin instance actually implements -- a multi-category
    plugin appears in more than one."""
    return [name for name, cls in CATEGORY_CLASSES.items() if isinstance(plugin, cls)]


def discover_external(log=print) -> list:
    """Instantiate every third-party plugin registered under the single
    "pythia.plugins" entry-point group. A single broken/incompatible plugin is
    logged and skipped, never allowed to take the whole registry down -- this is the
    actual point of dynamic discovery: the app must keep working when someone's
    third-party plugin doesn't. Cached after the first successful call in a process
    (entry points don't change without a restart)."""
    global _external_cache
    if _external_cache is not None:
        return _external_cache

    from importlib.metadata import entry_points

    found = []
    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except Exception as e:
        log(f"plugin discovery: could not query entry points for {ENTRY_POINT_GROUP!r}: {e}")
        return found

    for ep in eps:
        try:
            cls = ep.load()
            found.append(cls())
        except Exception as e:
            log(f"plugin discovery: failed to load plugin {ep.name!r}: {e}")

    _external_cache = found
    return found


def all_plugins() -> list:
    """Every discovered plugin instance, built-in + external (pip entry-point) +
    folder (drop-in manifest), instantiated exactly once regardless of how many
    categories it spans."""
    return list(_builtin_instances) + discover_external() + discover_folder_plugins()


def for_category(category: str) -> list:
    """Every discovered plugin (any enabled state) that implements `category`'s
    interface -- one instance can appear under several categories."""
    cls = CATEGORY_CLASSES[category]
    return [p for p in all_plugins() if isinstance(p, cls)]


def get_enabled_plugin_ids() -> list | None:
    """The `enabled_plugins` allowlist from settings, or None if unset/empty
    (meaning "no restriction -- everything is enabled"). Enablement is per plugin id,
    not per category -- a multi-category plugin is on or off as a whole."""
    from pythia.identity import get_setting

    raw = get_setting("enabled_plugins", None)
    if not raw:
        return None
    try:
        ids = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return ids or None


def set_enabled_plugin_ids(ids: list) -> None:
    """Set the allowlist. Pass an empty list to clear it back to "everything
    enabled" -- the debug-mode reset path."""
    from pythia.identity import set_setting

    set_setting("enabled_plugins", json.dumps(list(ids)))


def filter_enabled(plugins: list) -> list:
    """Apply the enabled_plugins allowlist to a list of plugin instances. No
    allowlist set -> every plugin passed in is enabled."""
    allowed = get_enabled_plugin_ids()
    if allowed is None:
        return list(plugins)
    allowed = set(allowed)
    return [p for p in plugins if p.id in allowed]
