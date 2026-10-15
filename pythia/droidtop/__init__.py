"""Pythia as this computer's droidtop agent.

Owner, 2026-10-14: "pythia will NEVER call droidtop-agent. If it's running,
it will BE our agent", and "We will modularize the desktop agent, and use its
modules as part of pythia". Pythia is built from droidtop-agent's crates:
its ``droidtop_agent`` extension module (droidtop-agent's crates/python,
DESIGN sections 13 and 16), the same Rust code ``droidtop-agent run`` is, in
Pythia's own process and over the same files. So the identity and pairings,
the library's change log, save sync, the scanner, WireGuard and the
rendezvous are one implementation. Either program can run on a computer,
and either can take over from the other. Only one serves at a time: the
first to listen on the agent's port.

One thin module here per submodule of ``droidtop_agent`` that Pythia uses;
with ``native`` they are the only place that touches the extension module:
- ``store``: this computer's identity, the paired handhelds, the library;
- ``pair``: pairing a handheld (typing its code, or showing one);
- ``service``: serving paired handhelds while Pythia runs;
- ``saves``: the copies of saves kept on this computer;
- ``library``: the games Pythia tracks, handed to the agent (``scan``).
- ``scan``: the agent's scanner rules over a folder Pythia scans (its ROM folders).

Without the module every call raises ``AgentError`` saying why, and Pythia
works as before.
"""

from pythia.droidtop.native import AgentError, available, why_unavailable

__all__ = ["AgentError", "available", "why_unavailable"]
