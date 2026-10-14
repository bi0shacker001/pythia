"""
Real plugin packaging format (2026-08-06, user directives, in order):

- "Plugins should be zipped bundles containing a metadata json file and one
  or more code files."
- "We never unpack plugins. We run the code still inside the zip." -- so
  installing a plugin never extracts it; the .zip itself is the artifact,
  loaded at runtime via the stdlib `zipimport` module.
- "The hash of the plugin zip file is part of our validation (and allows for
  us to build a signing system later)" -- every installed zip's real SHA-256
  is computed and recorded in a local index (plugins/index.json) at install
  time.
- "the 'main' file will be plugin.py, and it'll have access to whatever
  other files are in the plugin" -- fixed entry-point filename, meant to
  become the general extension format later (custom runners, etc.), not
  just game-detection plugins.
- "we'll use type-(type).py to indicate plugin types ... those files contain
  the plugin data for that type of extension" + "You need BOTH [the class
  registered in plugin.py AND a correctly-configured type-<category>.py],
  ... for it to be detected properly. Otherwise, we throw up an error
  entry." -- a category a plugin's class(es) implement (real Python
  inheritance, checked in registry.py against CATEGORY_CLASSES) must have a
  matching type-<category>.py in the same zip declaring that category's own
  CONFIG_SCHEMA (a list, possibly empty -- "even if it's just that there's
  no configuration required"). A mismatch either direction is a load error
  for the whole package, not a partially-working plugin.

Zip layout:

    plugin.json          <- {"id", "display_name", "version", "author"}
    plugin.py             <- entry point; declares PLUGIN_CLASSES = [...]
    type-metadata.py       <- one per category a PLUGIN_CLASSES class implements;
    type-library.py         each declares CONFIG_SCHEMA = [...] (own facet's config)
    helper.py                <- plugin.py (or a type-*.py) can `import helper`

`zipimport.zipimporter` resolves module names against the zip's internal
root the same way a real sys.path entry would -- it has no concept of "this
lives one folder deep," so everything above sits directly at the zip's
root, never inside a wrapped folder.
"""

import hashlib
import ast
import json
import os
import re
import sys
import tempfile
import types
import zipfile
import zipimport
from pathlib import Path

ENTRY_POINT_MODULE = "plugin"  # always plugin.py at the zip's root
# "categories" (2026-08-07, user: "we just read the metadata files, and
# plugins are loaded when enabled") is what lets registry.py answer "does
# this plugin belong in category X" and build the Plugins-page listing
# without ever importing plugin.py -- required, same as "id".
REQUIRED_MANIFEST_FIELDS = ("id", "categories")
MAX_ARCHIVE_ENTRIES = 256
MAX_ARCHIVE_UNCOMPRESSED = 16 * 1024 * 1024
MAX_ARCHIVE_MEMBER = 8 * 1024 * 1024
PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class PluginPackageError(ValueError):
    pass


def compute_hash(zip_path) -> str:
    """Real SHA-256 of the zip file's exact bytes -- the validation record
    (and future signing-check anchor) the user asked for."""
    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _version_tuple(v: str) -> tuple:
    """Simple dotted-version comparison (no semver dependency -- matches this
    project's pure-stdlib policy): "0.5.23" -> (0, 5, 23). Non-numeric
    trailing segments (e.g. "1.0.0-beta") are dropped rather than raising,
    so a plugin author's slightly-off version string doesn't hard-crash
    every load."""
    parts = []
    for seg in v.split("."):
        digits = "".join(c for c in seg if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_pythia_version_compatible(manifest: dict) -> None:
    """A plugin's own base classes (CATEGORY_CLASSES) have already changed
    shape once (LibraryPlugin split into LibraryPlugin/ManagerPlugin -- see
    pythia/plugins/__init__.py's own docstring); a zip built against an
    older shape has no way today to say so, and nothing catches the
    mismatch (found by the plugin-system research pass, 2026-08-06).
    Optional manifest fields `min_pythia_version`/`max_pythia_version`
    (plain dotted strings, inclusive) are checked here, the same way VS
    Code's `engines.vscode` range or a WebExtension's
    `strict_min_version`/`strict_max_version` work -- absent means
    "no constraint declared," not a failure."""
    from pythia.settings import VERSION

    current = _version_tuple(VERSION)
    min_v = manifest.get("min_pythia_version")
    if min_v and current < _version_tuple(min_v):
        raise PluginPackageError(f"requires Pythia >= {min_v}, this is {VERSION}.")
    max_v = manifest.get("max_pythia_version")
    if max_v and current > _version_tuple(max_v):
        raise PluginPackageError(f"requires Pythia <= {max_v}, this is {VERSION}.")


def read_manifest(zip_path) -> dict:
    """Read and validate plugin.json straight out of the zip -- zipfile's
    read-only API, no extraction of anything to disk."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise PluginPackageError(f"Plugin archive has too many entries (maximum {MAX_ARCHIVE_ENTRIES}).")
            names = [info.filename for info in infos]
            normalized = []
            total_size = 0
            for info in infos:
                name = info.filename.replace("\\", "/")
                parts = [part for part in name.split("/") if part]
                if name.startswith("/") or (parts and ":" in parts[0]) or any(part == ".." for part in parts):
                    raise PluginPackageError(f"Unsafe path in plugin archive: {info.filename!r}.")
                normalized.append(name.casefold())
                if info.file_size > MAX_ARCHIVE_MEMBER:
                    raise PluginPackageError(f"Plugin archive member is too large: {info.filename!r}.")
                total_size += info.file_size
            if len(normalized) != len(set(normalized)):
                raise PluginPackageError("Plugin archive contains duplicate paths.")
            if total_size > MAX_ARCHIVE_UNCOMPRESSED:
                raise PluginPackageError("Plugin archive expands beyond the allowed size limit.")
            if "plugin.json" not in names:
                raise PluginPackageError("No plugin.json at the zip's root.")
            if f"{ENTRY_POINT_MODULE}.py" not in names:
                raise PluginPackageError(
                    f"No {ENTRY_POINT_MODULE}.py at the zip's root -- every package's entry "
                    f"point must be named {ENTRY_POINT_MODULE}.py, sitting directly at the "
                    "top level of the zip, not inside a wrapped folder."
                )
            manifest = json.loads(zf.read("plugin.json"))
    except zipfile.BadZipFile as e:
        raise PluginPackageError(f"Not a valid zip file: {e}") from e
    except json.JSONDecodeError as e:
        raise PluginPackageError(f"plugin.json is not valid JSON: {e}") from e

    if not isinstance(manifest, dict):
        raise PluginPackageError("plugin.json is not a JSON object.")
    missing = [f for f in REQUIRED_MANIFEST_FIELDS if not manifest.get(f)]
    if missing:
        raise PluginPackageError(f"plugin.json is missing required field(s): {', '.join(missing)}.")
    plugin_id = manifest["id"]
    if not isinstance(plugin_id, str) or not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
        raise PluginPackageError(
            "plugin.json id must be 1-128 lowercase ASCII letters, digits, dots, underscores, or hyphens."
        )
    categories = manifest["categories"]
    if (
        not isinstance(categories, list)
        or not categories
        or any(not isinstance(category, str) or not category.strip() for category in categories)
        or len(categories) != len(set(categories))
    ):
        raise PluginPackageError("plugin.json categories must be a non-empty list of unique, non-empty strings.")
    optional = manifest.get("optional_dependencies", [])
    if not isinstance(optional, list):
        raise PluginPackageError("plugin.json optional_dependencies must be a list.")
    for dependency in optional:
        if isinstance(dependency, str):
            if not dependency.strip():
                raise PluginPackageError("optional dependency ids cannot be empty.")
        elif isinstance(dependency, dict):
            if not isinstance(dependency.get("id"), str) or not dependency["id"].strip():
                raise PluginPackageError("each optional dependency object must contain a non-empty id.")
        else:
            raise PluginPackageError("optional dependencies must be plugin ids or objects containing an id.")
    check_pythia_version_compatible(manifest)
    return manifest


def _unique_module_name(plugin_id: str, suffix: str = "") -> str:
    base = "".join(c if c.isalnum() else "_" for c in plugin_id)
    return f"_pythia_plugin_{base}{suffix}"


def _exec_zip_module(zip_path, source_name: str, register_name: str) -> types.ModuleType:
    """Compile+exec `<source_name>.py` from inside the zip under a module
    name unique to this package (`register_name`), never the literal source
    filename -- every package can use the exact same entry filenames
    (plugin.py, type-metadata.py, ...) by convention, and Python's
    sys.modules is a single global cache keyed by name, so two packages
    both loading "plugin" would collide and the second would silently get
    the first's cached module back. importer.get_code() compiles without
    touching sys.modules at all, so the registration name is entirely under
    our control."""
    importer = zipimport.zipimporter(str(zip_path))
    code = importer.get_code(source_name)
    module = types.ModuleType(register_name)
    module.__file__ = f"{zip_path}/{source_name}.py"
    module.__loader__ = importer
    sys.modules[register_name] = module

    zip_str = str(zip_path)
    path_was_added = zip_str not in sys.path
    if path_was_added:
        sys.path.insert(0, zip_str)
    try:
        exec(code, module.__dict__)
    finally:
        if path_was_added:
            sys.path.remove(zip_str)
    return module


def load_plugin_module(zip_path, plugin_id: str) -> types.ModuleType:
    """Import plugin.py DIRECTLY from inside the zip via zipimport -- runs
    from the zip's own bytes, never extracted to a loose file. The returned
    module is expected to declare `PLUGIN_CLASSES = [...]` (self-
    registration, the same convention built-ins already used before this
    zip format existed)."""
    return _exec_zip_module(zip_path, ENTRY_POINT_MODULE, _unique_module_name(plugin_id))


def type_file_name(category: str) -> str:
    return f"type-{category}.py"


def zip_namelist(zip_path) -> list:
    with zipfile.ZipFile(zip_path) as zf:
        return zf.namelist()


def _literal_assignment_from_zip(zip_path, source_name: str, variable: str):
    """Read a metadata module's literal assignment without executing Python."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            source = zf.read(f"{source_name}.py").decode("utf-8")
        tree = ast.parse(source, filename=f"{zip_path}/{source_name}.py")
        assignments = [
            node.value
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in ([*node.targets] if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name) and target.id == variable
        ]
        if len(assignments) != 1:
            return None
        return ast.literal_eval(assignments[0])
    except (OSError, KeyError, UnicodeDecodeError, SyntaxError, ValueError, zipfile.BadZipFile):
        return None


def load_type_config_schema(zip_path, plugin_id: str, category: str) -> list | None:
    """CONFIG_SCHEMA from type-<category>.py inside the zip, or None if the
    file is missing or doesn't declare a valid (list) CONFIG_SCHEMA --
    distinguished from an intentionally-empty `CONFIG_SCHEMA = []` (a real,
    valid "this facet needs no configuration" declaration), which returns
    `[]`, not None."""
    source_name = f"type-{category}"
    if f"{source_name}.py" not in zip_namelist(zip_path):
        return None
    schema = _literal_assignment_from_zip(zip_path, source_name, "CONFIG_SCHEMA")
    return schema if isinstance(schema, list) else None


# OS-specific data files (2026-08-06, user: "we also need a linux.py and
# windows.py file to load os-specific data for each plugin", then corrected:
# "Those files are to be required, and denote which OSes the plugin
# supports. ... very few plugins that don't require os-specific data. If
# something's just a metadata provider or something (works on all OSes),
# we'll have os-generic.py to indicate it."). Required, same as
# type-<category>.py: a package must declare os-generic.py (works
# identically everywhere) OR at least one of os-windows.py/os-linux.py (the
# specific platform(s) it supports) -- which file(s) exist at the zip's
# root IS the platform-support declaration, checked by registry.py's
# cross-validation the same way category/type-file presence is.
OS_GENERIC = "os-generic"
OS_SPECIFIC = ("os-windows", "os-linux")
OS_DATA_MODULES = (OS_GENERIC, *OS_SPECIFIC)


def declared_os_support(zip_path) -> set:
    """Which of os-generic/os-windows/os-linux this package's zip actually
    declares, straight from file presence."""
    names = zip_namelist(zip_path)
    return {m for m in OS_DATA_MODULES if f"{m}.py" in names}


def load_os_data(zip_path, plugin_id: str, os_module: str) -> dict | None:
    """OS_DATA from os-generic.py/os-windows.py/os-linux.py inside the zip,
    or None if the file is absent or doesn't declare a valid (dict) OS_DATA."""
    if os_module not in OS_DATA_MODULES:
        raise ValueError(f"unknown os_module: {os_module!r} (expected one of {OS_DATA_MODULES})")
    if f"{os_module}.py" not in zip_namelist(zip_path):
        return None
    data = _literal_assignment_from_zip(zip_path, os_module, "OS_DATA")
    return data if isinstance(data, dict) else None


def load_current_os_data(zip_path, plugin_id: str) -> dict | None:
    """load_os_data() for whichever OS this process is actually running on --
    os-generic.py wins if present (applies regardless of host OS), otherwise
    the specific os-windows.py/os-linux.py for the current platform."""
    import platform

    generic = load_os_data(zip_path, plugin_id, OS_GENERIC)
    if generic is not None:
        return generic
    current = "os-windows" if platform.system() == "Windows" else "os-linux"
    return load_os_data(zip_path, plugin_id, current)


def _index_path() -> Path:
    from pythia.identity import app_data_dir

    return app_data_dir() / "plugins" / "index.json"


def _read_index() -> dict:
    path = _index_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_index(index: dict) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".index-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(index, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def list_installed_packages() -> dict:
    """id -> {"path", "sha256", "display_name", ...} for every installed
    zip -- the real validation index, not just whatever's sitting in the
    plugins/ folder."""
    return _read_index()


def install_plugin_zip(zip_path: str) -> dict:
    """Validate a plugin package (manifest + a loadable plugin.py -- full
    category/type-file cross-validation happens at registry discovery time,
    not here, since that needs CATEGORY_CLASSES knowledge) and register it:
    the zip is copied byte-for-byte into app_data_dir()/plugins/<id>.zip
    (still a zip, never unpacked) and its hash + manifest fields are
    recorded in index.json. Returns {"status": "ok", "id", "display_name"}
    or {"status": "error", "message"}."""
    import shutil

    from pythia.identity import app_data_dir

    try:
        manifest = read_manifest(zip_path)
        plugin_id = manifest["id"]
        # Never execute third-party plugin code during installation. Structural
        # metadata validation is sufficient here; code is loaded only when the
        # user enables/uses it through registry.get_plugin_instances().
        from pythia.plugins import registry

        if registry.is_protected(plugin_id):
            raise PluginPackageError(f"{plugin_id!r} is a protected built-in plugin id and cannot be replaced.")
        if registry._load_and_validate_package(Path(zip_path), plugin_id) is None:
            reason = registry.get_plugin_load_errors().get(plugin_id, "package metadata validation failed")
            raise PluginPackageError(reason)

        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in plugin_id)
        target_path = app_data_dir() / "plugins" / f"{safe_name}.zip"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target_path.parent, prefix=f".{safe_name}-", suffix=".tmp", delete=False
            ) as handle:
                temporary = Path(handle.name)
            shutil.copyfile(zip_path, temporary)
            # Re-check the copied bytes before promotion. This catches a source
            # file changing during installation and guarantees the index hash
            # describes the exact artifact that becomes visible to discovery.
            copied_hash = compute_hash(temporary)
            temporary.replace(target_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        index = _read_index()
        index[plugin_id] = {
            "path": str(target_path),
            "sha256": copied_hash,
            "display_name": manifest.get("display_name", plugin_id),
            "version": manifest.get("version", "0.0.0"),
        }
        _write_index(index)
    except PluginPackageError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:  # a genuinely broken/malicious zip must never crash the app
        return {"status": "error", "message": f"Couldn't install that package: {e}"}

    return {"status": "ok", "id": plugin_id, "display_name": manifest.get("display_name", plugin_id)}


def uninstall_plugin(plugin_id: str) -> dict:
    """Real removal (2026-08-06, plugin-system research pass -- the "Remove
    plugin" button was previously an honest "not built yet" stub since
    nothing existed to wire it to). Scoped deliberately to v1: delete the
    zip and its index entry. Does NOT touch this plugin's stored credentials
    under datadir/credentials/ (pythia.credentials) -- an uninstalled
    plugin's leftover OAuth token etc. staying present is a real, separately
    scoped gap, not silently "handled" by this function pretending to do
    more than it does. Built-ins can't reach this at all -- the caller
    (registry.is_protected()) must check first; this function doesn't
    re-check it, since app_data_dir()/plugins/_builtin/ zips are regenerated
    every process start and deleting one here would just reappear."""
    from pythia.plugins import registry

    if registry.is_protected(plugin_id):
        return {"status": "error", "message": "Built-in plugins can't be uninstalled."}
    index = _read_index()
    entry = index.get(plugin_id)
    if entry is None:
        return {"status": "error", "message": f"No installed package with id {plugin_id!r}."}
    if entry.get("builtin"):
        return {"status": "error", "message": "Built-in plugins can't be uninstalled."}

    path = Path(entry["path"])
    try:
        if path.is_file():
            path.unlink()
    except OSError as e:
        return {"status": "error", "message": f"Couldn't delete the package file: {e}"}

    del index[plugin_id]
    _write_index(index)
    return {"status": "ok", "id": plugin_id}
