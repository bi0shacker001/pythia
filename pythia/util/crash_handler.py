"""Minimal, dependency-light crash handler (2026-08-07, external review
against a clean install: "The crash hook also needs to be available
earlier... importing [pythia.ui.app] pulls in modules that can fail before
the hook is installed... The early handler must avoid importing the full
settings/logging stack.").

Deliberately stdlib-only (traceback, sys, os, datetime, pathlib) plus
pythia.identity (itself pure stdlib -- no logging/settings/migrations
import chain) -- meant to be installed as the FIRST thing a frozen entry
point does, before `pythia.ui.app`/`pythia.util.log` (which opens a log
file at import time -- see log.py's own docstring) or anything else with
real failure modes gets imported. Every operation here is wrapped so this
handler itself can never be the thing that crashes."""

import sys
import traceback


def install_minimal_crash_handler() -> None:
    def _hook(exc_type, exc_value, exc_tb):
        try:
            from datetime import datetime, timezone

            from pythia.identity import app_data_dir

            crash_dir = app_data_dir() / "logs"
            crash_dir.mkdir(parents=True, exist_ok=True)
            crash_path = crash_dir / "crash.log"
            timestamp = datetime.now(timezone.utc).isoformat()
            with open(crash_path, "a", encoding="utf-8") as f:
                f.write(f"\n--- Uncaught exception at {timestamp} ---\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass  # this handler must never itself be the reason startup fails
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
