"""Threading module, used to launch games while monitoring them."""

import contextlib
import io
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable
from copy import copy
from typing import TYPE_CHECKING

from pythia import settings
from pythia.util import system
from pythia.util.log import logger
from pythia.util.shell import get_terminal_script

if TYPE_CHECKING:
    from pythia.runners.runner import Runner
    from pythia.util.log_buffer import SimpleLogBuffer


def get_wrapper_script_location() -> str:
    """Return absolute path of pythia-wrapper script"""
    wrapper_relpath = "share/pythia/bin/pythia-wrapper"
    candidates = [
        os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "..")),
        os.path.dirname(os.path.dirname(settings.__file__)),
        sys.prefix,  # an installed package (pip, a venv) puts its data files under the prefix
        "/usr",
        "/usr/local",
    ]
    for candidate in candidates:
        wrapper_abspath = os.path.join(candidate, wrapper_relpath)
        if os.path.isfile(wrapper_abspath):
            return wrapper_abspath
    raise FileNotFoundError("Couldn't find pythia-wrapper script in any of the expected locations")


WRAPPER_SCRIPT = get_wrapper_script_location()
RUNNING_COMMANDS = set()


class MonitoredCommand:
    """Exexcutes a commmand while keeping track of its state"""

    fallback_cwd = tempfile.gettempdir()

    def __init__(
        self,
        command: list[str],
        runner: "Runner | None" = None,
        env: dict[str, str] | None = None,
        term: str | None = None,
        cwd: str | None = None,
        include_processes: list[str] | None = None,
        exclude_processes: list[str] | None = None,
        log_buffer: "SimpleLogBuffer | None" = None,
        title: str | None = None,
    ):  # pylint: disable=too-many-arguments
        self.ready_state = True
        self.env = self.get_environment(env)

        self.accepted_return_code = "0"

        self.command = command
        self.runner = runner
        self.stop_func: Callable[[], bool] = lambda: True
        self.game_process: subprocess.Popen[bytes] | None = None
        self.prevent_on_stop = False
        self.return_code = None
        self.terminal = term
        self.is_running = True
        self.error = None
        self.log_handlers = [
            self.log_handler_stdout,
            self.log_handler_console_output,
        ]
        self.set_log_buffer(log_buffer)
        self.stdout_monitor = None
        self.include_processes = include_processes or []
        self.exclude_processes = exclude_processes or []

        self.cwd = self.get_cwd(cwd)

        self._stdout = io.StringIO()

        self._title = title if title else command[0]

    @property
    def stdout(self) -> str:
        return self._stdout.getvalue()

    def get_wrapper_command(self) -> list[str]:
        """Return launch arguments for the wrapper script"""
        # Invoke the wrapper via the current Python explicitly rather than
        # relying on its `#!/usr/bin/env python3` shebang. The shebang
        # resolves through PATH at exec time, which silently picks up a
        # different interpreter in distribution scenarios where the host
        # Python doesn't match the one running Pythia (AppImage, venv
        # launched from outside, etc.) — and a wrong-version Python
        # crashes hard on Pythia's own stdlib via the inherited
        # PYTHONPATH.
        wrapper_command = (
            [
                sys.executable,
                WRAPPER_SCRIPT,
                self._title,
                str(len(self.include_processes)),
                str(len(self.exclude_processes)),
            ]
            + self.include_processes
            + self.exclude_processes
        )
        if not self.terminal:
            return wrapper_command + list(self.command)

        terminal_path = system.find_required_executable(self.terminal)
        script_path = get_terminal_script(self.command, self.cwd, self.env)
        return wrapper_command + [terminal_path, "-e", script_path]

    def set_log_buffer(self, log_buffer: "SimpleLogBuffer | None") -> None:
        """Attach a TextBuffer to this command enables the buffer handler"""
        if not log_buffer:
            return None
        self.log_buffer = log_buffer
        if self.log_handler_buffer not in self.log_handlers:
            self.log_handlers.append(self.log_handler_buffer)

    def get_cwd(self, cwd: str | None) -> str:
        """Return the current working dir of the game"""
        if not cwd:
            cwd = self.runner.working_dir if self.runner else None
        return os.path.expanduser(cwd or "~")

    @staticmethod
    def get_environment(user_env: dict[str, str] | None) -> dict[str, str]:
        """Process the user provided environment variables for use as self.env"""
        env = copy(user_env) if user_env else {}

        # Defense-in-depth for pythia-wrapper subprocesses: the wrapper script
        # bootstraps its own sys.path when run from a git checkout, but
        # publishing PYTHONPATH here means any unusual layout the wrapper's
        # own bootstrap doesn't recognise still resolves `from pythia...`
        # imports. Safe because:
        #   * The wrapper now runs on the same Python as Pythia (sys.executable
        #     in get_wrapper_command), so an inherited PYTHONPATH pointing at
        #     our stdlib is ABI-compatible.
        #   * pythia-wrapper deletes PYTHONPATH from os.environ before
        #     spawning the game, so the leak doesn't propagate to Wine,
        #     umu-launcher, or any other runner subprocess.
        env["PYTHONPATH"] = ":".join(sys.path)
        # Drop bad values of environment keys, those will confuse the Python
        # interpreter.
        game_uuid = str(uuid.uuid4())
        env["PYTHIA_GAME_UUID"] = game_uuid
        env["PYTHIA_RETURN_CODE_FILE"] = os.path.join(settings.TMP_DIR, "return-code-%s" % game_uuid)

        cleaned = {}
        for key, value in env.items():
            if "=" in key:
                logger.warning("Environment variable name '%s' contains '=' so it can't be used; skipping.", key)
            elif value is None:
                logger.warning("Environment variable '%s' has None for its value; skipping.", key)
            elif not isinstance(value, str):
                logger.warning("Environment variable '%s' value '%s' is not a string; converting.", key, value)
                cleaned[key] = str(value)
            else:
                cleaned[key] = value
        return cleaned

    def get_child_environment(self) -> dict[str, str]:
        """Returns the calculated environment for the child process."""
        env = system.get_environment()
        env.update(self.env)
        return env

    def start(self) -> None:
        """Run the thread."""
        if os.environ.get("PYTHIA_DEBUG_ENV") == "1":
            for key, value in self.env.items():
                logger.debug('%s="%s"', key, value)
        wrapper_command = self.get_wrapper_command()
        env = self.get_child_environment()

        self.game_process = self.execute_process(wrapper_command, env)
        RUNNING_COMMANDS.add(self)

        if not self.game_process:
            logger.error("No game process available")
            return None

        # GTK removal (2026-08-04): this used to be GLib.child_watch_add() (wait
        # for exit) plus GLib.io_add_watch() over a non-blocking stdout fd
        # (fcntl O_NONBLOCK), both driven by a running GLib main loop. Neither
        # exists anymore -- replaced with the standard portable pattern: two
        # dedicated threads, each doing a plain *blocking* read/wait (blocking
        # is fine off the main thread), reporting back via schedule_at_idle so
        # callbacks still land off the calling thread the same way idle_add's
        # callbacks did.
        threading.Thread(target=self._wait_thread, daemon=True).start()

        self._stdout_stop = threading.Event()
        if self.game_process.stdout:
            stdout_monitor = threading.Thread(target=self._stdout_thread, daemon=True)
            stdout_monitor.start()
            self.stdout_monitor = stdout_monitor

    def log_filter(self, line: str) -> bool:
        """Filter out some message we don't want to show to the user."""
        if "GStreamer-WARNING **" in line:
            return False
        if "Bad file descriptor" in line:
            return False
        if "'libgamemodeauto.so.0' from LD_PRELOAD" in line:
            return False
        if "Unable to read VR Path Registry" in line:
            return False
        return True

    def log_handler_stdout(self, line: str) -> None:
        """Add the line to this command's stdout attribute"""
        if not self.log_filter(line):
            return None
        self._stdout.write(line)

    def log_handler_buffer(self, line: str) -> None:
        """Add the line to the associated LogBuffer object"""
        self.log_buffer.insert(self.log_buffer.get_end_iter(), line, -1)

    def log_handler_console_output(self, line: str) -> None:
        """Print the line to stdout"""
        if not self.log_filter(line):
            return None
        with contextlib.suppress(BlockingIOError):
            sys.stdout.write(line)
            sys.stdout.flush()

    def get_return_code(self) -> str:
        """Get the return code from the file written by the wrapper"""
        return_code_path = self.env["PYTHIA_RETURN_CODE_FILE"]
        if os.path.exists(return_code_path):
            with open(return_code_path, encoding="utf-8") as return_code_file:
                return_code = return_code_file.read()
            os.unlink(return_code_path)
        else:
            return_code = ""
            logger.warning("No file %s", return_code_path)
        return return_code

    def _wait_thread(self) -> None:
        """Blocks until the game process exits, then handles the same
        cleanup on_stop() used to (see start()'s docstring note)."""
        game_process = self.game_process
        assert game_process is not None  # started by start() before this thread
        pid = game_process.pid
        game_process.wait()
        if self.prevent_on_stop:  # stop() already in progress
            return
        self.return_code = self.get_return_code()
        self.is_running = False
        logger.debug("Process %s has terminated with code %s", pid, self.return_code)
        if not self.stop():
            logger.info("Full shutdown prevented")

    def _stdout_thread(self) -> None:
        """Blocking-read loop over the child's stdout, dispatching each chunk
        to the log handlers -- runs on its own thread so the blocking read
        never stalls anything else (see start()'s docstring note)."""
        assert self.game_process is not None and self.game_process.stdout is not None
        stdout = self.game_process.stdout
        while not self._stdout_stop.is_set():
            try:
                chunk = stdout.read(262144)
            except (ValueError, OSError):
                break  # file_desc might be closed
            if not chunk:
                break  # EOF -- process closed stdout (equivalent of the old IO_HUP)
            line = chunk.decode("utf-8", errors="ignore")
            if "winemenubuilder.exe" in line:
                continue
            if not self.is_running:
                break
            for log_handler in self.log_handlers:
                log_handler(line)

    def execute_process(self, command: list[str], env: dict[str, str] | None = None) -> subprocess.Popen[bytes] | None:
        """Execute and return a subprocess"""

        env = env or {}

        # If a None gets into execute_process, we get annoying errors
        # that are hard to race. We'll try to repair the bad command or environment
        # instead, while emitting warnings.abs

        for i, item in enumerate(command):
            if not isinstance(item, str):
                logger.warning("Wrapper command contains a non-string: %s", command)
                command[i] = str(item) if item else ""

        if "" in env:
            del env[""]

        for key, value in env.items():
            if not isinstance(key, str) or key.isspace():
                logger.warning("Environment contains a non-string as a key %s=%s: %s", key, value, env)
                env = copy(env)  # can't del while iterating
                del env[key]
                continue

            if not isinstance(value, str):
                logger.warning("Environment contains a non-string as the value for the key: %s=%s: %s", key, value, env)
                env[key] = str(value) if value else ""

        if self.cwd and not system.path_exists(self.cwd):
            try:
                os.makedirs(self.cwd)
            except OSError:
                logger.error("Failed to create working directory, falling back to %s", self.fallback_cwd)
                self.cwd = self.fallback_cwd
        try:
            return subprocess.Popen(  # pylint: disable=consider-using-with
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.cwd,
                env=env,
            )
        except OSError as ex:
            logger.exception("Failed to execute %s: %s", " ".join(command), ex)
            self.error = ex.strerror
            return None

    def stop(self) -> bool:
        """Stops the current game process and cleans up the instance"""
        # Prevent stop() being called again by the process exiting
        self.prevent_on_stop = True

        if self.game_process:
            try:
                self.game_process.terminate()
            except ProcessLookupError:
                # process already dead.
                pass

        resume_stop = self.stop_func()
        if not resume_stop:
            logger.warning("Stop execution halted by demand of stop_func")
            return False

        if self.stdout_monitor:
            self._stdout_stop.set()
            self.stdout_monitor = None

        self.is_running = False
        self.ready_state = False
        RUNNING_COMMANDS.discard(self)
        return True


def exec_command(command: str) -> MonitoredCommand:
    """Execute arbitrary command in a MonitoredCommand

    Used by the --exec command line flag.
    """
    command = MonitoredCommand(shlex.split(command), env={})  # runtime.get_env())
    command.start()
    return command
