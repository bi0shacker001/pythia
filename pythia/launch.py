"""
Real, direct game launching -- the "Lutris replacement" default mode from
PLAN.md's Step 1. This is the immediately-achievable first slice: native
execution needs no compatibility layer at all on either Windows or Linux, so
it doesn't wait on the deeper Wine/Proton-adapted-from-Lutris work. That work
(pythia/runners/, already an imported/adapted copy of Lutris's real runner
code) covers the Wine/Proton/emulator case for later; this module is the
sibling "just run the native executable" path PLAN.md calls out as not
existing in Lutris's own runner model at all (Lutris assumes Linux+Wine).

Cross-platform on purpose (2026-08-06, user: "remember we're cross-platform"):
Windows and Linux each get a real executable-resolution heuristic below, not
just one platform with a TODO for the other.
"""

import os
import platform
import shlex
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

# Files that are technically executable but are never the game itself --
# installer/uninstaller/updater/support binaries Ren'Py and other engines
# routinely ship alongside the real executable. Matched case-insensitively
# against the filename stem.
_EXCLUDED_STEMS = {
    "unins000",
    "uninstall",
    "uninstaller",
    "update",
    "updater",
    "crashpad_handler",
    "python",
    "pythonw",
}

_launch_lock = threading.RLock()
_launches: dict[int, dict] = {}


def _register_launch(process: subprocess.Popen, command: list[str], cwd: str) -> None:
    record = {
        "pid": process.pid,
        "command": list(command),
        "working_dir": cwd,
        "started_at": time.time(),
        "finished_at": None,
        "exit_code": None,
        "running": True,
        "process": process,
    }
    with _launch_lock:
        _launches[process.pid] = record

    def _observe() -> None:
        code = process.wait()
        with _launch_lock:
            current = _launches.get(process.pid)
            if current is not None:
                current.update(running=False, exit_code=code, finished_at=time.time())

    threading.Thread(target=_observe, name=f"pythia-process-{process.pid}", daemon=True).start()


def monitored_launches(*, include_finished: bool = True) -> list[dict]:
    """Snapshot of processes launched directly by this Pythia process.

    Process objects never cross the API boundary; callers get JSON-safe state.
    Finished records remain available for honest exit reporting until explicitly
    cleared, rather than disappearing at the moment a game crashes or exits.
    """
    with _launch_lock:
        rows = []
        for record in _launches.values():
            if include_finished or record["running"]:
                rows.append({key: value for key, value in record.items() if key != "process"})
        return sorted(rows, key=lambda row: row["started_at"], reverse=True)


def stop_monitored_launch(pid: int) -> dict:
    with _launch_lock:
        record = _launches.get(int(pid))
        if record is None:
            return {"status": "error", "message": f"No Pythia-launched process with PID {pid}."}
        if not record["running"]:
            return {"status": "not_running", "pid": int(pid), "exit_code": record["exit_code"]}
        process = record["process"]
    try:
        process.terminate()
    except OSError as exc:
        return {"status": "error", "message": f"Could not stop PID {pid}: {exc}"}
    return {"status": "ok", "pid": int(pid), "message": f"Stop requested for PID {pid}."}


def clear_finished_launches() -> int:
    with _launch_lock:
        finished = [pid for pid, record in _launches.items() if not record["running"]]
        for pid in finished:
            del _launches[pid]
        return len(finished)


def _is_excluded(path: Path) -> bool:
    stem = path.stem.lower()
    return stem in _EXCLUDED_STEMS or stem.startswith("unins")


def find_primary_executable(variant_path: str, windows_binary: bool = False) -> Path | None:
    """Best real candidate for "the game's own executable" inside a tracked
    variant's install folder -- top-level only (Ren'Py, RPG Maker, and every
    other engine this project has real samples for puts the launcher at the
    install root, not buried in a subfolder). Returns None rather than
    guessing wrong when nothing plausible is found; callers should surface
    that as a real, honest "couldn't find an executable" state, not launch
    the wrong file."""
    root = Path(variant_path)
    if not root.is_dir():
        return None

    if platform.system() == "Windows" or windows_binary:
        candidates = [p for p in root.glob("*.exe") if p.is_file() and not _is_excluded(p)]
    else:
        # Ren'Py's own Linux export convention is a top-level <name>.sh launcher
        # script; fall back to any top-level file with the executable bit set
        # for engines that ship a plain binary instead.
        candidates = [p for p in root.glob("*.sh") if p.is_file() and not _is_excluded(p)]
        if not candidates:
            candidates = [
                p
                for p in root.iterdir()
                if p.is_file() and not _is_excluded(p) and (os.stat(p).st_mode & stat.S_IXUSR)
            ]

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # More than one plausible candidate (common: a 32-bit and 64-bit .exe
    # side by side) -- prefer one whose name matches the containing folder,
    # Ren'Py's own naming convention when it isn't overridden; otherwise the
    # first alphabetically is at least deterministic rather than random.
    folder_name = root.name.lower()
    for c in sorted(candidates):
        if c.stem.lower() in folder_name or folder_name in c.stem.lower():
            return c
    return sorted(candidates)[0]


def resolve_launch_plan(variant: dict, effective_config: dict | None = None) -> dict:
    """The single source of truth for "can this variant be launched, and
    with what" -- used identically by both the real launch call and the UI's
    readiness display, so the two can never silently disagree (2026-08-07,
    external UX review, Phase 1: "Derive readiness from a validated launch
    plan," not a blanket claim; "'Ready' is therefore often an unsupported
    claim" was the exact violation this closes).

    A variant's own `executable`/`args`/`working_dir` fields (see
    datadir.VARIANT_LAUNCH_FIELDS), when explicitly set via Configure, always
    win; find_primary_executable() is only consulted for whatever wasn't
    explicitly configured. Returns a dict with at least `ready` (bool) and
    `state` (a specific reason, never a generic catch-all) -- see the state
    values inline below; `executable`/`args`/`working_dir` are only
    meaningful when `ready` is True."""
    config = effective_config or {}
    backend = config.get("backend") or "native"
    if backend not in {"native", "wine", "proton"}:
        return {"ready": False, "state": "backend_unavailable",
                "reason": f"The {backend} execution backend is not implemented.",
                "executable": None, "args": [], "working_dir": None, "detected": False,
                "backend": backend, "environment": config.get("environment") or {},
                "wrappers": [], "monitoring": config.get("monitoring") or {}}
    backend_runner = None
    if backend in {"wine", "proton"}:
        configured = config.get("wine_version")
        candidate = str(configured).strip() if configured else ("wine" if backend == "wine" else "proton")
        backend_runner = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if backend_runner is None:
            return {"ready": False, "state": "backend_unavailable",
                    "reason": f"{backend.title()} was not found. Set its executable path in Advanced configuration.",
                    "executable": None, "args": [], "working_dir": None, "detected": False,
                    "backend": backend, "environment": config.get("environment") or {},
                    "wrappers": [], "monitoring": config.get("monitoring") or {}}
    path = variant.get("path")
    if not path:
        return {
            "ready": False,
            "state": "not_configured",
            "reason": "This variant has no install path recorded.",
            "executable": None,
            "args": [],
            "working_dir": None,
            "detected": False,
        }

    root = Path(path)
    if not root.is_dir():
        return {
            "ready": False,
            "state": "folder_missing",
            "reason": f"The folder '{path}' no longer exists.",
            "executable": None,
            "args": [],
            "working_dir": None,
            "detected": False,
        }

    explicit_exe = variant.get("executable")
    detected = False
    if explicit_exe:
        exe_path = Path(explicit_exe)
        if not exe_path.is_absolute():
            exe_path = root / exe_path
        if not exe_path.is_file():
            return {
                "ready": False,
                "state": "executable_missing",
                "reason": f"The configured executable '{explicit_exe}' no longer exists.",
                "executable": None,
                "args": [],
                "working_dir": None,
                "detected": False,
            }
    else:
        exe_path = find_primary_executable(path, windows_binary=backend in {"wine", "proton"})
        detected = True
        if exe_path is None:
            return {
                "ready": False,
                "state": "needs_executable_confirmation",
                "reason": "Pythia couldn't confidently detect this variant's executable -- confirm it in Configure.",
                "executable": None,
                "args": [],
                "working_dir": None,
                "detected": False,
            }

    working_dir_raw = variant.get("working_dir")
    working_dir = Path(working_dir_raw) if working_dir_raw else exe_path.parent

    prefix = config.get("command_prefix") or ""
    try:
        wrappers = shlex.split(prefix, posix=platform.system() != "Windows") if prefix else []
    except ValueError as exc:
        return {"ready": False, "state": "invalid_command_prefix", "reason": str(exc),
                "executable": None, "args": [], "working_dir": None, "detected": detected,
                "backend": backend, "environment": config.get("environment") or {},
                "wrappers": [], "monitoring": config.get("monitoring") or {}}
    environment = dict(config.get("environment") or {})
    if backend in {"wine", "proton"}:
        prefix_path = config.get("wine_prefix")
        if prefix_path:
            environment["WINEPREFIX" if backend == "wine" else "STEAM_COMPAT_DATA_PATH"] = str(prefix_path)
        if config.get("wine_arch"):
            environment["WINEARCH"] = str(config["wine_arch"])
        if config.get("esync") is not None:
            environment["WINEESYNC"] = "1" if config["esync"] else "0"
        if config.get("fsync") is not None:
            environment["WINEFSYNC"] = "1" if config["fsync"] else "0"
        if config.get("dxvk") is not None:
            environment["DXVK_DISABLE"] = "0" if config["dxvk"] else "1"
        environment["WINEDLLOVERRIDES"] = "d3d12=n" if config.get("vkd3d") is False else environment.get("WINEDLLOVERRIDES", "")
        wrappers.extend([backend_runner] + (["run"] if backend == "proton" else []))
    return {
        "ready": True,
        "state": "ready",
        "reason": None,
        "executable": exe_path,
        "args": list(variant.get("args") or []),
        "working_dir": working_dir,
        "detected": detected,
        "backend": backend,
        "environment": environment,
        "wrappers": wrappers,
        "pre_launch_script": config.get("pre_launch_script") or "",
        "wait_pre_launch": bool(config.get("wait_pre_launch", True)),
        "post_exit_script": config.get("post_exit_script") or "",
        "monitoring": config.get("monitoring") or {},
    }


def launch_native(exe_path: Path, args: list | None = None, working_dir: Path | None = None,
                  environment: dict | None = None, wrappers: list | None = None,
                  pre_launch_script: str = "", wait_pre_launch: bool = True,
                  post_exit_script: str = "") -> subprocess.Popen:
    """Actually run the executable -- Pythia's own process, not handed off to
    another launcher (the "Lutris replacement" mode's whole point). Detached
    from Pythia's own process group/session so closing Pythia doesn't take
    the game down with it, matching how every real game launcher behaves.
    `args`/`working_dir` are real, persisted per-variant overrides (see
    resolve_launch_plan()) -- previously always empty/exe-parent-dir, no
    matter what a user configured, because nothing configured them."""
    args = args or []
    cwd = str(working_dir) if working_dir else str(exe_path.parent)
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in (environment or {}).items()})
    if pre_launch_script:
        pre = subprocess.Popen(pre_launch_script, cwd=cwd, env=env, shell=True)
        if wait_pre_launch and pre.wait() != 0:
            raise OSError(f"Pre-launch script exited with status {pre.returncode}")
    command = [*(wrappers or []), str(exe_path), *args]
    if platform.system() == "Windows":
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )
    else:
        if exe_path.suffix == ".sh" and not wrappers:
            command = ["sh", str(exe_path), *args]
        process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
    _register_launch(process, command, cwd)
    if post_exit_script:
        def _post_exit():
            process.wait()
            subprocess.Popen(post_exit_script, cwd=cwd, env=env, shell=True).wait()
        threading.Thread(target=_post_exit, daemon=True).start()
    return process


LAUNCH_MODE_SETTING_KEY = "launch_mode"
LAUNCH_MODE_DIRECT = "direct"  # "Lutris replacement" -- Pythia launches the game itself
LAUNCH_MODE_MANAGER = "manager"  # "update and patch manager" -- hand off to a ManagerPlugin, never launch


def get_launch_mode() -> str:
    from pythia.identity import get_setting

    mode = get_setting(LAUNCH_MODE_SETTING_KEY, LAUNCH_MODE_DIRECT)
    return mode if mode in (LAUNCH_MODE_DIRECT, LAUNCH_MODE_MANAGER) else LAUNCH_MODE_DIRECT


def set_launch_mode(mode: str) -> None:
    from pythia.identity import set_setting

    if mode not in (LAUNCH_MODE_DIRECT, LAUNCH_MODE_MANAGER):
        raise ValueError(f"unknown launch mode: {mode!r}")
    set_setting(LAUNCH_MODE_SETTING_KEY, mode)
