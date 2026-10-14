"""
Central plugin registry. Every plugin -- built-in or third-party -- is a
zip package (see packaging.py's own docstring for the full format: a fixed
plugin.py entry point self-registering via `PLUGIN_CLASSES`, plus one
type-<category>.py per category it implements, each declaring that facet's
own CONFIG_SCHEMA). Nothing is ever extracted to loose files; every load
goes through zipimport.

**Real, required cross-validation** (2026-08-06, user: "You need BOTH [the
class registered in plugin.py AND a correctly-configured
type-<category>.py], ... for it to be detected properly. Otherwise, we
throw up an error entry."): a category a plugin's PLUGIN_CLASSES actually
implement (real Python issubclass against CATEGORY_CLASSES below) must have
a matching type-<category>.py declaring a valid CONFIG_SCHEMA list in the
same zip, or the whole package fails to load -- recorded in
get_plugin_load_errors(), never silently dropped.

Built-ins are not a separate, eager-import-time-instantiation path (adapted
from Lutris's real runner loader -- classes, not instances, until something
actually needs one). "Disabled" means "not loaded, not just hidden"
(2026-08-06 correction): for_category()/all_plugins() only ever instantiate
a plugin that is both known and enabled.

2026-08-07, user, final correction after two intermediate (wrong) designs:
"Loose source plus a real build script. We wanna be running executables on
given operating systems... We never run from the source (and we can't, we
need to explicitly break the software's ability to read 'loose' plugins,
like it has been doing." A built-in's real, hand-authored source is a loose
directory at pythia/plugin_sources/<category>/<name>/ (never read by the
running app), built by scripts/build_plugins.py into a committed
pythia/plugins/builtin/<id>.zip -- the *same* zip format, and the *same*
zipimport-based discovery path (discover_builtin_zips(), a sibling of
discover_installed_zips() below), as any third-party package. There is no
runtime code path anywhere in this module that imports plugin_sources/ or
any other loose .py file -- if it isn't a real .zip, it does not load.
`is_protected()` -- computed from the built-in zip set, not a
separately-maintained id list -- is the *only* thing that still
distinguishes a built-in from anything else.

Third-party plugins reach the registry through either of two paths:
- A proper pip-installed package declaring a single `importlib.metadata`
  entry point under the "pythia.plugins" group (discover_external()).
- A .zip package installed via packaging.install_plugin_zip() into
  app_data_dir()/plugins/ (discover_installed_zips()).
"""

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
    PluginMeta,
    SourcePlugin,
    packaging,
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

_protected_ids_cache: set | None = None
_builtin_entries_cache: dict[str, dict] = {}  # built-in id -> catalog entry (see _load_and_validate_package)
_instance_cache: dict[str, list] = {}  # plugin id -> [instance, ...] (one package, maybe several classes), populated lazily
_external_class_cache = None
_installed_entries_cache = None
_load_errors: dict[str, str] = {}  # plugin id -> real reason its package failed to load


def categories_of_class(cls) -> list:
    """Every category this plugin CLASS implements -- issubclass, not
    isinstance, so this works without instantiating anything."""
    return [name for name, base in CATEGORY_CLASSES.items() if issubclass(cls, base)]


def categories_of(plugin) -> list:
    """Same as categories_of_class(), for an already-instantiated plugin."""
    return categories_of_class(type(plugin))


def get_plugin_load_errors() -> dict:
    """id (or a package's declared id, or its zip filename if it failed
    before even that was readable) -> real error message, for every package
    that failed validation this process. Never silently dropped -- see this
    module's own docstring for the cross-validation rule that most commonly
    produces one of these."""
    return dict(_load_errors)


def _load_and_validate_package(zip_path, plugin_id: str) -> dict | None:
    """Structural validation reading ONLY metadata -- plugin.json's own
    declared `categories` list, and each declared category's own
    type-<category>.py CONFIG_SCHEMA -- never plugin.py itself (2026-08-07,
    two corrections in the same breath: "we just read the metadata files,
    and plugins are loaded when enabled" -- discovery/listing must not run
    a plugin's real behavior code just to appear in a list -- and "the
    type-*.py file contains the config schema for that type. It's where it
    HAS to be" -- so config_schema is read from there, not guessed from a
    class attribute). Verifies (a) every category plugin.json declares has
    a matching, validly-configured type-<category>.py in the same zip, and
    (b) the package declares real OS support via os-generic.py or at least
    one of os-windows.py/os-linux.py. Returns a catalog entry dict on
    success; None on failure (and records the real reason in _load_errors)
    -- "You need BOTH [class + type-file] ... for it to be detected
    properly. Otherwise, we throw up an error entry," extended the same way
    to the OS-support declaration. plugin.py is only ever read later, by
    get_plugin_instance(), and only for a plugin actually being loaded."""
    try:
        manifest = packaging.read_manifest(zip_path)
        categories = manifest.get("categories") or []
        if not categories:
            raise packaging.PluginPackageError("plugin.json declares no 'categories'.")
        unknown = [c for c in categories if c not in CATEGORY_CLASSES]
        if unknown:
            raise packaging.PluginPackageError(f"plugin.json declares unknown categor(ies): {unknown}.")

        config_schema: list = []
        for category in categories:
            schema = packaging.load_type_config_schema(zip_path, plugin_id, category)
            if schema is None:
                raise packaging.PluginPackageError(
                    f"implements '{category}' but has no valid type-{category}.py "
                    "(must exist at the zip's root and declare CONFIG_SCHEMA as a list)."
                )
            config_schema.extend(schema)

        if not packaging.declared_os_support(zip_path):
            raise packaging.PluginPackageError(
                "declares no OS support -- must have os-generic.py (works on every OS) or "
                "at least one of os-windows.py/os-linux.py at the zip's root."
            )
    except Exception as e:
        _load_errors[plugin_id] = str(e)
        print(f"plugin discovery: {plugin_id!r} failed validation: {e}")
        return None

    _load_errors.pop(plugin_id, None)
    return {
        "id": plugin_id,
        "zip_path": zip_path,
        "manifest": manifest,
        "categories": categories,
        "display_name": manifest.get("display_name", plugin_id),
        "version": manifest.get("version", "0.0.0"),
        "author": manifest.get("author", "unknown"),
        "config_schema": config_schema,
        "optional_dependencies": manifest.get("optional_dependencies", []),
    }


def optional_dependency_status(plugin_id: str) -> list[dict]:
    """Resolve non-blocking plugin relationships without loading either plugin."""
    entries = all_plugin_entries()
    entry = entries.get(plugin_id) or {}
    enabled_ids = get_enabled_plugin_ids()
    result = []
    for declaration in entry.get("optional_dependencies", []):
        dep = {"id": declaration} if isinstance(declaration, str) else dict(declaration)
        target = entries.get(dep["id"])
        installed = target is not None
        enabled = installed and (enabled_ids is None or dep["id"] in enabled_ids)
        min_version = dep.get("min_version")
        compatible = installed and (not min_version or packaging._version_tuple(target["version"]) >= packaging._version_tuple(min_version))
        result.append({**dep, "installed": installed, "enabled": enabled, "compatible": compatible})
    return result


def _builtin_zip_dir() -> Path:
    """Where scripts/build_plugins.py writes its committed output --
    shipped with the app, part of the pythia.plugins package tree. Never
    written to at runtime; only read."""
    import pythia.plugins as plugins_pkg

    return Path(plugins_pkg.__file__).parent / "builtin"


def _builtin_entries() -> dict[str, dict]:
    """id -> catalog entry for every built-in, discovered purely by scanning
    pythia/plugins/builtin/*.zip (scripts/build_plugins.py's committed
    output) -- the exact same metadata-only path discover_installed_zips()
    uses for a user-installed package. No import, no read, of
    pythia/plugin_sources/ (or anything else outside a .zip, or plugin.py
    inside one) happens here or anywhere else in this module."""
    global _protected_ids_cache
    if _builtin_entries_cache:
        return _builtin_entries_cache
    protected = set()
    zip_dir = _builtin_zip_dir()
    for zip_path in sorted(zip_dir.glob("*.zip")) if zip_dir.is_dir() else []:
        try:
            manifest = packaging.read_manifest(zip_path)
            plugin_id = manifest["id"]
        except Exception as e:
            print(f"plugin discovery: failed to read built-in package {zip_path.name!r}: {e}")
            _load_errors[zip_path.name] = str(e)
            continue
        entry = _load_and_validate_package(zip_path, plugin_id)
        if entry is None:
            continue
        entry["builtin"] = True
        _builtin_entries_cache[plugin_id] = entry
        protected.add(plugin_id)
    _protected_ids_cache = protected
    return _builtin_entries_cache


def is_protected(plugin_id: str) -> bool:
    """True if this plugin is built-in and therefore can't be uninstalled (it
    can still be disabled). Computed from the packaged built-in manifest set,
    not a separately-maintained id list."""
    if _protected_ids_cache is None:
        _builtin_entries()
    return plugin_id in (_protected_ids_cache or set())


def discover_installed_zips(log=print) -> dict:
    """id -> catalog entry for every user-installed .zip under
    app_data_dir()/plugins/ (packaging.install_plugin_zip()'s own target
    directory, excluding index.json bookkeeping). A single broken package is
    logged (both to stderr and get_plugin_load_errors()) and skipped, never
    allowed to take the whole registry down."""
    global _installed_entries_cache
    if _installed_entries_cache is not None:
        return _installed_entries_cache

    from pythia.identity import app_data_dir

    found = {}
    index = packaging._read_index()
    plugins_dir = app_data_dir() / "plugins"
    if plugins_dir.is_dir():
        for zip_path in sorted(plugins_dir.glob("*.zip")):
            try:
                manifest = packaging.read_manifest(zip_path)
                plugin_id = manifest["id"]
            except Exception as e:
                log(f"plugin discovery: failed to read package {zip_path.name!r}: {e}")
                _load_errors[zip_path.name] = str(e)
                continue

            # Integrity check (2026-08-06, plugin-system research pass): the
            # hash recorded at install time was write-only until now --
            # nothing ever read it back. Recomputing and comparing on every
            # load catches a zip that's changed on disk since install
            # (corruption, or someone editing it outside Pythia) -- real,
            # if minimal, use of the hash the user specifically asked to be
            # recorded "for validation," short of a full signing system.
            recorded = index.get(plugin_id, {}).get("sha256")
            if not recorded:
                msg = "package is not registered in the installation index; install it through Pythia before loading."
                log(f"plugin discovery: {plugin_id!r} rejected: {msg}")
                _load_errors[plugin_id] = msg
                continue
            if packaging.compute_hash(zip_path) != recorded:
                msg = "file has changed since installation (hash mismatch) -- possible corruption or tampering."
                log(f"plugin discovery: {plugin_id!r} failed integrity check: {msg}")
                _load_errors[plugin_id] = msg
                continue

            entry = _load_and_validate_package(zip_path, plugin_id)
            if entry is None:
                continue
            entry["builtin"] = False
            found[plugin_id] = entry

    _installed_entries_cache = found
    return found


def invalidate_installed_zip_cache(plugin_id: str | None = None) -> None:
    """Call after installing/uninstalling a user zip so the next
    all_plugin_entries() call re-scans app_data_dir()/plugins/ instead of
    serving a stale cache (2026-08-06, plugin-system research pass: an
    update used to silently keep serving the OLD cached class/instance
    until restart, with no error -- worse than a crash because it was
    invisible). `plugin_id`, when known (install/uninstall both know it),
    is evicted from the instance cache unconditionally -- an *updated*
    plugin keeps the same id, so "no longer present" isn't the right test
    for it; the caller telling us which id just changed is."""
    global _installed_entries_cache
    _installed_entries_cache = None
    if plugin_id is not None:
        _instance_cache.pop(plugin_id, None)
    known_ids = set(_builtin_entries_cache) | set(_external_classes()) | set(discover_installed_zips())
    for stale_id in set(_instance_cache) - known_ids:
        _instance_cache.pop(stale_id, None)


def _external_classes(log=print) -> dict[str, type]:
    """id -> class for every third-party plugin registered under the single
    "pythia.plugins" entry-point group. `ep.load()` imports but does not
    instantiate. A single broken/incompatible plugin is logged and skipped,
    never allowed to take the whole registry down. Cached after the first
    successful call in a process (entry points don't change without a restart)."""
    global _external_class_cache
    if _external_class_cache is not None:
        return _external_class_cache

    from importlib.metadata import entry_points

    found = {}
    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except Exception as e:
        log(f"plugin discovery: could not query entry points for {ENTRY_POINT_GROUP!r}: {e}")
        _external_class_cache = found
        return found

    for ep in eps:
        try:
            cls = ep.load()
            found[cls.id] = cls
        except Exception as e:
            log(f"plugin discovery: failed to load plugin {ep.name!r}: {e}")

    _external_class_cache = found
    return found


def _external_entries() -> dict[str, dict]:
    """Catalog-entry view of _external_classes() -- the one distribution path
    that's unavoidably eager (a pip entry point has no separate manifest;
    `ep.load()` importing the module *is* how Python discovers it exists at
    all), so its categories/config_schema still come straight off the
    already-loaded class rather than a zip's metadata files."""
    result = {}
    for pid, cls in _external_classes().items():
        result[pid] = {
            "id": pid,
            "zip_path": None,
            "cls": cls,
            "categories": categories_of_class(cls),
            "display_name": cls.display_name,
            "version": cls.version,
            "author": cls.author,
            "config_schema": cls.config_schema,
            "builtin": False,
        }
    return result


def all_plugin_entries() -> dict[str, dict]:
    """Every known plugin's CATALOG ENTRY -- built-in, external entry-point,
    and user-installed zip -- keyed by id, regardless of enabled state.
    Metadata only (id, display_name, version, author, categories,
    config_schema): for a zip-based plugin this never executes plugin.py,
    only plugin.json + type-<category>.py (see _load_and_validate_package).
    Category filtering (for_category()) and the Plugins-page listing both
    run entirely off this -- a disabled plugin's real code is never touched
    just to appear in a list or be checked against a category."""
    result = dict(_builtin_entries())
    result.update(_external_entries())
    result.update(discover_installed_zips())
    return result


def get_plugin_instances(plugin_id: str) -> list:
    """Lazily LOAD (plugin.py exec, for a zip-based entry) and instantiate
    EVERY class a plugin package declares in PLUGIN_CLASSES, cached per-
    process -- the one place a zip-based plugin's real behavior code ever
    runs. Only ever called for a plugin that's actually enabled and needed
    (all_plugins()/for_category() below), which is what "disabled means not
    loaded" actually means in this design.

    A package is one enable/disable unit but can legitimately declare more
    than one class (2026-08-07 correction, e.g. the rpgmaker package's MV/
    MZ/VX Ace engine classes all live under one `pythia.engine.rpgmaker`
    manifest id): "we don't identify plugins by classname... we just run
    through all the classes and track which ones hit." No id-matching or
    name-based lookup happens here -- every declared class is instantiated,
    unconditionally; for_category() below decides which of those instances
    are actually relevant to a given category."""
    if plugin_id in _instance_cache:
        return _instance_cache[plugin_id]
    entry = all_plugin_entries().get(plugin_id)
    if entry is None:
        return []

    cls = entry.get("cls")
    if cls is not None:
        instances = [cls()]
    else:
        try:
            module = packaging.load_plugin_module(entry["zip_path"], plugin_id)
            classes = getattr(module, "PLUGIN_CLASSES", None) or []
            if not classes:
                raise packaging.PluginPackageError("plugin.py declares no PLUGIN_CLASSES.")
            if not all(isinstance(c, type) and issubclass(c, PluginMeta) for c in classes):
                raise packaging.PluginPackageError("PLUGIN_CLASSES must contain only Pythia plugin classes.")
            actual_categories = {category for c in classes for category in categories_of_class(c)}
            declared_categories = set(entry["categories"])
            if actual_categories != declared_categories:
                raise packaging.PluginPackageError(
                    "plugin.py categories do not match plugin.json: "
                    f"declared {sorted(declared_categories)}, implemented {sorted(actual_categories)}."
                )
            instances = [c() for c in classes]
        except Exception as e:
            _load_errors[plugin_id] = str(e)
            print(f"plugin load: {plugin_id!r} failed to load: {e}")
            instances = []

    _instance_cache[plugin_id] = instances
    return instances


def get_plugin_instance(plugin_id: str):
    """Convenience wrapper over get_plugin_instances() for the common
    single-class-package case -- the first instance, or None."""
    instances = get_plugin_instances(plugin_id)
    return instances[0] if instances else None


def all_plugins() -> list:
    """Every instance from every ENABLED plugin package. A disabled
    package's metadata is known (it shows up in all_plugin_entries() for
    the Plugins-page listing) but its code is never loaded here --
    "disabled" means "not loaded," not just "hidden"."""
    enabled_ids = get_enabled_plugin_ids()
    result = []
    for pid in all_plugin_entries():
        if enabled_ids is not None and pid not in enabled_ids:
            continue
        result.extend(get_plugin_instances(pid))
    return result


def for_category(category: str) -> list:
    """Every ENABLED plugin instance implementing `category`'s interface.
    Package membership is checked against the manifest's declared
    `categories` list first (metadata, no code loaded) -- a disabled or
    non-matching package's code is never loaded just to find out it doesn't
    belong here. Once a matching, enabled package is actually loaded, EVERY
    class it declares is instantiated (get_plugin_instances()) and each
    instance is checked individually against `category` via real
    issubclass -- a multi-class package (e.g. rpgmaker's MV/MZ/VX Ace) can
    have some classes match a category and others not."""
    if category not in CATEGORY_CLASSES:
        raise ValueError(f"unknown category: {category!r}")
    enabled_ids = get_enabled_plugin_ids()
    result = []
    for pid, entry in all_plugin_entries().items():
        if category not in entry["categories"]:
            continue
        if enabled_ids is not None and pid not in enabled_ids:
            continue
        for instance in get_plugin_instances(pid):
            if category in categories_of(instance):
                result.append(instance)
    return result


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
    allowlist set -> every plugin passed in is enabled. Kept for the category
    packages' own enabled_only() wrappers; for_category() already excludes
    disabled plugins before instantiating them, so this is normally a no-op."""
    allowed = get_enabled_plugin_ids()
    if allowed is None:
        return list(plugins)
    allowed = set(allowed)
    return [p for p in plugins if p.id in allowed]


def _get_disabled_capabilities() -> set:
    """The (plugin_id, category) pairs disabled at the capability level --
    distinct from `enabled_plugins` above, which is whole-plugin."""
    from pythia.identity import get_setting

    raw = get_setting("disabled_plugin_capabilities", None)
    if not raw:
        return set()
    try:
        pairs = json.loads(raw)
    except (ValueError, TypeError):
        return set()
    return {tuple(p) for p in pairs if isinstance(p, list) and len(p) == 2}


def is_capability_enabled(plugin_id: str, category: str) -> bool:
    return (plugin_id, category) not in _get_disabled_capabilities()


def set_capability_enabled(plugin_id: str, category: str, enabled: bool) -> None:
    from pythia.identity import set_setting

    disabled = _get_disabled_capabilities()
    key = (plugin_id, category)
    if enabled:
        disabled.discard(key)
    else:
        disabled.add(key)
    set_setting("disabled_plugin_capabilities", json.dumps([list(p) for p in disabled]))
