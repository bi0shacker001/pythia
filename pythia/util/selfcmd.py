"""The command that starts another copy of Pythia's own Python, for one module or script.

Installed as a package (pip, the Debian package), that is the interpreter: ``python -m pythia.launch``.
In a standalone build (PyInstaller: the Windows installer, the macOS app, the Linux deb and rpm) there
is no interpreter, only Pythia's own program, so the same request goes to it as ``--pythia-module`` or
``--pythia-script``, and ``pythia.launch.main`` runs what it names. Every place Pythia starts a copy of
itself asks here, so neither layout is spelled out twice.
"""

import sys

MODULE_FLAG = "--pythia-module"
SCRIPT_FLAG = "--pythia-script"


def frozen() -> bool:
    """Whether this is a standalone build rather than Python running the package."""
    return bool(getattr(sys, "frozen", False))


def module_command(module: str, *args: str, isolated: bool = False) -> list[str]:
    """``python [-I] -m <module> <args>``, or the standalone program's equivalent. ``isolated`` asks for
    Python's isolated mode where there is an interpreter (an elevated copy must not read the person's
    environment); a standalone build is isolated already."""
    if frozen():
        return [sys.executable, MODULE_FLAG, module, *args]
    return [sys.executable, *(["-I"] if isolated else []), "-m", module, *args]


def script_command(path: str, *args: str) -> list[str]:
    """``python <script> <args>``, or the standalone program's equivalent."""
    if frozen():
        return [sys.executable, SCRIPT_FLAG, path, *args]
    return [sys.executable, path, *args]


def run_requested(argv: list[str]) -> bool:
    """Runs the module or script ``argv`` names when it is one of the requests above, with ``sys.argv``
    as that program expects it; returns whether it was one."""
    import runpy  # noqa: PLC0415

    if len(argv) < 3 or argv[1] not in (MODULE_FLAG, SCRIPT_FLAG):
        return False
    target, rest = argv[2], argv[3:]
    sys.argv = [target, *rest]
    if argv[1] == MODULE_FLAG:
        runpy.run_module(target, run_name="__main__", alter_sys=True)
    else:
        runpy.run_path(target, run_name="__main__")
    return True
