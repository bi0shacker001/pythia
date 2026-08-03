"""
renpy_patch -- adds native Linux x86_64 and ARM64 (aarch64) engine libraries to
existing Ren'Py game installs, so they can run on Linux (including ARM64, without
FEX / x86 emulation) as well as x86_64 Linux, which isn't always shipped either.

A "game root" is any folder that directly contains both a "renpy" and a "game"
subfolder (this matches how Ren'Py distributes games, and how some titles ship
multiple independent installs -- e.g. Part1/, Part2/ -- inside one wrapper folder).

For each game root:
  1. Works out which Ren'Py engine version built it (renpy/vc_version.py, falling
     back to renpy/__init__.py for older engines without a semver field).
  2. Works out the lib/ naming convention the game already uses (e.g. "py3-",
     "py2-", or no prefix at all -- this varies a lot across Ren'Py's history).
  3. Checks whether lib/<prefix>linux-x86_64 and lib/<prefix>linux-aarch64 already
     exist. If not, downloads the matching version's Ren'Py SDK (regular SDK for
     x86_64, ARM SDK for aarch64) from renpy.org and copies the platform folder in.

This is purely additive -- it only ever adds new lib/<platform> folders. It never
deletes or overwrites existing files (including any Windows platform folders
already in a game), so it's safe to re-run and safe to undo (delete the added
folder). Both x86_64 and aarch64 support are always added; there's no "just one
architecture" mode.

Downloaded SDKs are cached (minus their Windows platform folders, which this
Linux-support tool never needs) under a fixed per-OS cache directory, and reused
across every game that needs the same engine version.

Usable standalone (`python -m pythia.renpy_patch.patch --library <path>`) or as
the `renpy-patch` subcommand of the unified `pythia` CLI/GUI.

Stdlib only, no exceptions -- this module keeps working with a stock python3 on
either Windows or Linux even if the rest of Pythia grows optional dependencies.
"""

import argparse
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

RENPY_DL_BASE = "https://www.renpy.org/dl"
MAX_VERSION_FALLBACK = 5

# (arch key, lib platform suffix, renpy.org SDK "kind")
ARCH_TARGETS = [
    ("x86_64", "linux-x86_64", "sdk"),
    ("aarch64", "linux-aarch64", "sdkarm"),
]


def default_cache_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "renpatchpy"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "renpatchpy"


# ---------------------------------------------------------------------------
# 1. Find game roots: any folder that directly has both "renpy" and "game" subfolders.
# ---------------------------------------------------------------------------
def find_game_roots(path: Path):
    if (path / "renpy").is_dir() and (path / "game").is_dir():
        yield path
        return
    try:
        children = sorted(p for p in path.iterdir() if p.is_dir())
    except OSError:
        return
    for child in children:
        yield from find_game_roots(child)


# ---------------------------------------------------------------------------
# 2. Work out the lib/ naming convention (prefix) and python major version.
#    Returns (prefix, pyver, found). `found` is False only when neither a
#    platform folder nor a python stdlib folder could be located at all.
# ---------------------------------------------------------------------------
def get_lib_naming_info(game_root: Path):
    lib_dir = game_root / "lib"
    entries = []
    if lib_dir.is_dir():
        try:
            entries = [p.name for p in lib_dir.iterdir() if p.is_dir()]
        except OSError:
            entries = []

    pyver = None
    if any(re.match(r"python.*3", name) for name in entries):
        pyver = "py3"
    elif any(re.match(r"python.*2", name) for name in entries):
        pyver = "py2"

    prefix = None
    found = False
    for name in entries:
        m = re.match(r"^(py\d+-)?(windows|linux|mac)", name)
        if m:
            found = True
            prefix = m.group(1) or ""
            if prefix:
                break  # prefer an explicit py-prefixed match if one exists

    if not found and not pyver:
        return None, None, False

    if not found:
        # Found a python stdlib folder but no platform folder to copy the naming style from.
        prefix = "py3-" if pyver == "py3" else ""  # py2 -> legacy no-prefix convention

    return prefix, pyver, True


# ---------------------------------------------------------------------------
# 3. Work out the Ren'Py engine version.
# ---------------------------------------------------------------------------
def get_renpy_engine_version(game_root: Path, pyver):
    vc_file = game_root / "renpy" / "vc_version.py"
    if vc_file.is_file():
        try:
            content = vc_file.read_text(errors="replace")
        except OSError:
            content = ""
        m = re.search(r"version\s*=\s*u?['\"](\d+\.\d+\.\d+)", content)
        if m:
            return m.group(1)

    # Fallback: older engines hardcode version_tuple directly in renpy/__init__.py,
    # sometimes with separate PY2/PY3 branches. Handles "version_tuple = (7, 4, 10, ...)",
    # "version_tuple = VersionTuple(7, 6, 0, ...)", and minified "version_tuple=7,5,3,..."
    # (no parens).
    init_file = game_root / "renpy" / "__init__.py"
    if init_file.is_file():
        try:
            content = init_file.read_text(errors="replace")
        except OSError:
            content = ""
        matches = re.findall(
            r"version_tuple\s*=\s*[A-Za-z_]*\(?\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", content
        )
        if matches:
            idx = 0
            if len(matches) > 1 and pyver == "py3":
                idx = -1
            major, minor, patch = matches[idx]
            return f"{major}.{minor}.{patch}"

    return None


# ---------------------------------------------------------------------------
# 4. Resolve the SDK version actually available for download (with fallback),
#    memoized to a small JSON file so repeat runs don't re-check the network
#    for versions already known to be (un)available.
# ---------------------------------------------------------------------------
class ResolveCache:
    _MISSING = object()

    def __init__(self, cache_root: Path):
        self.path = cache_root / "resolved-versions.json"
        self.data = {}
        if self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError):
                self.data = {}

    def get(self, key):
        return self.data.get(key, self._MISSING)

    def set(self, key, value):
        self.data[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data))
        except OSError:
            pass


def url_exists(url, timeout=20):
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def resolve_sdk_version(version, kind, resolve_cache, max_fallback=MAX_VERSION_FALLBACK):
    cache_key = f"{kind}:{version}"
    cached = resolve_cache.get(cache_key)
    if cached is not ResolveCache._MISSING:
        return cached

    major, minor, patch = (int(x) for x in version.split("."))
    result = None
    for step in range(max_fallback + 1):
        try_patch = patch - step
        if try_patch < 0:
            break
        try_version = f"{major}.{minor}.{try_patch}"
        url = f"{RENPY_DL_BASE}/{try_version}/renpy-{try_version}-{kind}.tar.bz2"
        if url_exists(url):
            result = try_version
            break

    resolve_cache.set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# 5. Ensure the *entire* lib/ tree for a given version+kind is downloaded,
#    extracted, and cached (minus Windows platform folders, which this Linux-
#    support script never needs). Downloaded once per version+kind and reused
#    for every platform folder and every game that needs that version after
#    that -- SDK archives are 100-170MB each, and a library commonly has many
#    games clustered on a handful of versions.
# ---------------------------------------------------------------------------
def _make_executable(path: Path):
    for p in path.rglob("*"):
        if p.is_file():
            try:
                st = p.stat()
                p.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except OSError:
                pass


def ensure_version_lib_cache(sdk_version, kind, cache_root: Path, log):
    lib_cache_dir = cache_root / sdk_version / kind / "lib"
    marker = lib_cache_dir / ".complete"
    if marker.is_file():
        return True
    if lib_cache_dir.exists():
        shutil.rmtree(lib_cache_dir, ignore_errors=True)

    url = f"{RENPY_DL_BASE}/{sdk_version}/renpy-{sdk_version}-{kind}.tar.bz2"
    log(f"  Downloading {url} ...")

    fd, tmp_archive_str = tempfile.mkstemp(suffix=".tar.bz2")
    os.close(fd)
    tmp_archive = Path(tmp_archive_str)
    try:
        try:
            urllib.request.urlretrieve(url, tmp_archive)
        except (urllib.error.URLError, OSError) as e:
            log(f"  Download failed: {e}")
            return False

        log("  Extracting...")
        with tempfile.TemporaryDirectory() as extract_root_str:
            extract_root = Path(extract_root_str)
            try:
                with tarfile.open(tmp_archive, mode="r:bz2") as tf:
                    tf.extractall(extract_root, filter="data")
            except tarfile.TarError as e:
                log(f"  Failed to extract archive: {e}")
                return False

            top_dirs = [p for p in extract_root.iterdir() if p.is_dir()]
            if not top_dirs:
                log(f"  Could not read archive contents for {url}")
                return False
            src_lib = top_dirs[0] / "lib"
            if not src_lib.is_dir():
                log(f"  No lib/ folder found in {url}")
                return False

            lib_cache_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_lib), str(lib_cache_dir))

        for entry in lib_cache_dir.iterdir():
            if entry.is_dir() and "windows" in entry.name:
                shutil.rmtree(entry, ignore_errors=True)

        _make_executable(lib_cache_dir)
        marker.touch()
        return True
    finally:
        try:
            tmp_archive.unlink()
        except OSError:
            pass


def ensure_cached_platform_folder(sdk_version, kind, folder_name, cache_root, log):
    if not ensure_version_lib_cache(sdk_version, kind, cache_root, log):
        return None
    dest_dir = cache_root / sdk_version / kind / "lib" / folder_name
    if not dest_dir.is_dir():
        log(f"  Could not find lib/{folder_name} in Ren'Py {sdk_version} {kind}")
        return None
    return dest_dir


# ---------------------------------------------------------------------------
# Process a single game root. Always ensures both x86_64 and aarch64. Returns
# one summary line.
# ---------------------------------------------------------------------------
def process_game_root(root: Path, label: str, cache_root: Path, dry_run: bool, resolve_cache, log):
    log(f"== {label} ==")

    prefix, pyver, found = get_lib_naming_info(root)
    if not found:
        log("  Could not determine lib naming convention (no platform folder or python stdlib found) -- skipping.")
        log("")
        return f"{label}: skipped (unknown lib layout)"

    version = get_renpy_engine_version(root, pyver)
    if not version:
        log("  Could not determine Ren'Py engine version -- skipping.")
        log("")
        return f"{label}: skipped (unknown version)"
    log(f"  Engine version: {version}   Lib prefix: '{prefix}'")

    added = []
    failed = []

    for arch_key, platform, kind in ARCH_TARGETS:
        folder_name = f"{prefix}{platform}"
        target_dir = root / "lib" / folder_name

        if target_dir.is_dir():
            log(f"  [ok] lib/{folder_name} already present")
            continue

        sdk_version = resolve_sdk_version(version, kind, resolve_cache)
        if not sdk_version:
            log(f"  [--] no {kind} available for {version} (or nearby versions) -- {arch_key} not added")
            failed.append(arch_key)
            continue
        if sdk_version != version:
            log(f"  (using nearby version {sdk_version} for {arch_key} -- exact {version} not published)")

        if dry_run:
            log(f"  [dry-run] would add lib/{folder_name} from Ren'Py {sdk_version} {kind}")
            added.append(arch_key)
            continue

        cached_dir = ensure_cached_platform_folder(sdk_version, kind, folder_name, cache_root, log)
        if not cached_dir:
            failed.append(arch_key)
            continue

        shutil.copytree(cached_dir, target_dir)
        _make_executable(target_dir)
        log(f"  [+] added lib/{folder_name}")
        added.append(arch_key)

    if not added and not failed:
        status = "already complete"
    elif failed and not added:
        status = f"unsupported ({', '.join(failed)})"
    else:
        status = f"patched ({', '.join(added)})"
        if failed:
            status += f" / missing ({', '.join(failed)})"

    log("")
    return f"{label}: {status}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def run_cli(argv):
    parser = argparse.ArgumentParser(
        prog="pythia renpy-patch",
        description="Add native Linux x86_64 + ARM64 Ren'Py engine libraries to game installs.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--library", metavar="PATH", help="Recursively scan this folder for Ren'Py game installs.")
    group.add_argument("--game", metavar="PATH", help="Treat this folder as a single Ren'Py game install.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be done without changing anything.")
    args = parser.parse_args(argv)

    cache_root = default_cache_dir()
    cache_root.mkdir(parents=True, exist_ok=True)
    resolve_cache = ResolveCache(cache_root)

    summary = []
    if args.game:
        root = Path(args.game).resolve()
        if not root.is_dir():
            print(f"Game path '{root}' does not exist.", file=sys.stderr)
            return 1
        if not (root / "renpy").is_dir() or not (root / "game").is_dir():
            print(
                f"'{root}' does not look like a Ren'Py game install (expected a 'renpy' and a 'game' subfolder).",
                file=sys.stderr,
            )
            return 1
        summary.append(process_game_root(root, root.name, cache_root, args.dry_run, resolve_cache, print))
    else:
        library = Path(args.library).resolve()
        if not library.is_dir():
            print(f"Library path '{library}' does not exist.", file=sys.stderr)
            return 1
        print(f"Scanning '{library}' for Ren'Py game installs...")
        roots = list(find_game_roots(library))
        print(f"Found {len(roots)} game install(s).\n")
        for root in roots:
            label = str(root.relative_to(library))
            summary.append(process_game_root(root, label, cache_root, args.dry_run, resolve_cache, print))

    print("=================== Summary ===================")
    for line in summary:
        print(line)
    return 0


# ---------------------------------------------------------------------------
# GUI (stdlib tkinter only). Standalone Ren'Py-patcher GUI, separate from the
# unified Pythia GUI in pythia/gui.py.
# ---------------------------------------------------------------------------
def run_gui():
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Pythia -- Ren'Py Linux x86_64/ARM64 patcher")
    root.geometry("760x520")
    root.minsize(560, 360)

    path_var = tk.StringVar()
    is_single_game = tk.BooleanVar(value=False)
    dry_run_var = tk.BooleanVar(value=False)

    top = ttk.Frame(root, padding=10)
    top.pack(fill="x")
    top.columnconfigure(1, weight=1)

    ttk.Label(top, text="Folder:").grid(row=0, column=0, sticky="w")
    entry = ttk.Entry(top, textvariable=path_var)
    entry.grid(row=0, column=1, sticky="ew", padx=5)

    def browse():
        d = filedialog.askdirectory(title="Choose a folder")
        if d:
            path_var.set(d)

    ttk.Button(top, text="Browse...", command=browse).grid(row=0, column=2)

    ttk.Checkbutton(
        top, text="This is a single game folder (not a library)", variable=is_single_game
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
    ttk.Checkbutton(
        top, text="Dry run (preview only, no downloads or changes)", variable=dry_run_var
    ).grid(row=2, column=0, columnspan=3, sticky="w")

    start_btn = ttk.Button(top, text="Start")
    start_btn.grid(row=3, column=0, columnspan=3, pady=(10, 0))

    log_frame = ttk.Frame(root, padding=(10, 0, 10, 10))
    log_frame.pack(fill="both", expand=True)
    log_text = tk.Text(log_frame, wrap="word", state="disabled")
    scrollbar = ttk.Scrollbar(log_frame, command=log_text.yview)
    log_text.configure(yscrollcommand=scrollbar.set)
    log_text.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    log_queue = queue.Queue()
    DONE = object()

    def gui_log(msg):
        log_queue.put(msg)

    def drain_queue():
        while True:
            try:
                msg = log_queue.get_nowait()
            except queue.Empty:
                break
            if msg is DONE:
                start_btn.configure(state="normal")
                continue
            log_text.configure(state="normal")
            log_text.insert("end", msg + "\n")
            log_text.see("end")
            log_text.configure(state="disabled")
        root.after(100, drain_queue)

    def worker(path_str, single, dry_run):
        cache_root = default_cache_dir()
        cache_root.mkdir(parents=True, exist_ok=True)
        resolve_cache = ResolveCache(cache_root)
        summary = []
        p = Path(path_str)

        if single:
            if not (p / "renpy").is_dir() or not (p / "game").is_dir():
                gui_log(
                    f"'{p}' does not look like a Ren'Py game install "
                    "(expected a 'renpy' and a 'game' subfolder)."
                )
            else:
                summary.append(process_game_root(p, p.name, cache_root, dry_run, resolve_cache, gui_log))
        else:
            gui_log(f"Scanning '{p}' for Ren'Py game installs...")
            roots = list(find_game_roots(p))
            gui_log(f"Found {len(roots)} game install(s).\n")
            for game_root in roots:
                label = str(game_root.relative_to(p))
                summary.append(process_game_root(game_root, label, cache_root, dry_run, resolve_cache, gui_log))

        gui_log("=================== Summary ===================")
        for line in summary:
            gui_log(line)
        log_queue.put(DONE)

    def start():
        path_str = path_var.get().strip()
        if not path_str or not Path(path_str).is_dir():
            messagebox.showerror("Pythia", "Please choose a valid folder.")
            return
        start_btn.configure(state="disabled")
        log_text.configure(state="normal")
        log_text.delete("1.0", "end")
        log_text.configure(state="disabled")
        threading.Thread(
            target=worker, args=(path_str, is_single_game.get(), dry_run_var.get()), daemon=True
        ).start()

    start_btn.configure(command=start)

    root.after(100, drain_queue)
    root.mainloop()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        run_gui()
        return 0
    return run_cli(argv)


if __name__ == "__main__":
    sys.exit(main())
