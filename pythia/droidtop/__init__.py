"""Pythia as this computer's droidtop agent.

Owner, 2026-10-14: "pythia will NEVER call droidtop-agent. If it's running,
it will BE our agent", and "We will modularize the desktop agent, and use its
modules as part of pythia"; 2026-10-16: "Pythia reimplements the droidtop
agent functions". Pythia builds its own extension module, ``pythia._native``
(``native/``), on droidtop-agent's crates (pinned by commit), so the protocol,
the pairing, the library's change log, save sync, the scanner, WireGuard and
the rendezvous are the agent's code, in Pythia's process. Pythia keeps its
own state, in ``DATA_DIR/droidtop``; the computer's identity is windowcast's
host key, which droidtop-agent uses too. Only one of the two serves a
computer: Pythia turns the agent off when it serves.

One thin module here per submodule of ``pythia._native`` that Pythia uses;
with ``native`` they are the only place that touches the extension module:
- ``store``: this computer's identity, the paired handhelds, the library;
- ``pair``: pairing a handheld (typing its code, or showing one);
- ``service``: serving paired handhelds while Pythia runs;
- ``saves``: the copies of saves kept on this computer;
- ``library``: the games Pythia tracks, handed to the agent (``scan``).
- ``scan``: the agent's scanner rules over a folder Pythia scans (its ROM folders).
- ``launch``: starting Pythia's own games for a handheld (the agent's
  launcher), through the manager plugins' hand-off;
- ``takeover``: droidtop-agent, the separate program: turned off while Pythia
  serves, back on when asked, and its state synced both ways.

In a source tree that was never built there is no module: every call raises
``AgentError`` saying how to build it, and the rest of Pythia works.
"""

from pythia.droidtop.native import AgentError, available, why_unavailable

__all__ = ["AgentError", "available", "why_unavailable"]
