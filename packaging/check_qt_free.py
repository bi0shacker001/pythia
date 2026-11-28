"""CI only: the frozen Python host serves the bundled native window with no Qt."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import time
from multiprocessing.connection import Client
from pathlib import Path


def main() -> None:
    root = Path(sys.executable).parent
    executable = root / "native-ui" / ("pythia-ui.exe" if sys.platform == "win32" else "pythia-ui")
    assert executable.is_file(), executable
    assert importlib.util.find_spec("PySide6") is None, "Qt is importable in the frozen host"
    forbidden = [
        path
        for path in root.rglob("*")
        if path.is_file() and ("pyside" in path.name.lower() or path.name.lower().startswith(("qt6", "libqt6")))
    ]
    assert not forbidden, forbidden
    with tempfile.TemporaryDirectory(prefix="pythia-native-bundle-") as data:
        os.environ["PYTHIA_DATA_DIR"] = data
        os.environ["PYTHIA_DATADIR_OVERRIDE"] = str(Path(data) / "datadir")
        from pythia.ipc import host
        from pythia.ui.backend import Backend
        from pythia.ui.native_host import _frontend_environment

        backend = Backend()
        server = host.start(backend)
        assert server is not None, "frozen backend could not start"
        stop = threading.Event()

        def emit() -> None:
            while not stop.wait(0.2):
                backend.gamesChanged.emit()

        worker = threading.Thread(target=emit, daemon=True)
        worker.start()
        try:
            completed = subprocess.run(
                [str(executable), "--smoke"],
                cwd=executable.parent,
                env=_frontend_environment(),
                capture_output=True,
                text=True,
                timeout=150,
                check=False,
            )
            print(completed.stdout, flush=True)
            print(completed.stderr, file=sys.stderr, flush=True)
            assert completed.returncode == 0, completed.returncode
            assert "ui: rendered library games=" in completed.stdout
            assert "smoke: ok" in completed.stdout
        finally:
            stop.set()
            worker.join(timeout=5)
            server.stop()
        # Exercise the installed entry point too, not only a host assembled by this check.
        from pythia import instance

        with tempfile.TemporaryFile(mode="w+") as log:
            application = subprocess.Popen([sys.executable, "--ui", "native"], stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    assert application.poll() is None, "installed native launcher exited during startup"
                    probe = subprocess.run(
                        [str(executable), "--probe"],
                        cwd=executable.parent,
                        env=_frontend_environment(),
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False,
                    )
                    if probe.returncode == 0:
                        assert '"server":"pythia"' in probe.stdout, probe.stdout
                        break
                    time.sleep(0.5)
                else:
                    raise AssertionError("installed native launcher did not serve authenticated IPC")
                key = instance.key_path()
                assert key.is_file(), "installed launcher did not create its command-channel key"
                assert application.poll() is None, "installed native launcher exited before quit"
                # Keep connection and authentication exceptions visible in CI. The ordinary
                # CLI treats both as "no running instance" and attempts to start one.
                with Client(instance.address(), authkey=bytes.fromhex(key.read_text("ascii").strip())) as channel:
                    channel.send({"op": "quit", "args": {}})
                    assert channel.poll(15), "installed command channel did not reply to quit"
                    assert channel.recv() == {"done": 0}, "installed host rejected quit"
                assert application.wait(timeout=15) == 0, "installed native launcher failed"
            finally:
                if application.poll() is None:
                    application.terminate()
                    application.wait(timeout=15)
                log.seek(0)
                print(log.read(), flush=True)
    print("qt-free installed bundle: ok", flush=True)


if __name__ == "__main__":
    main()
