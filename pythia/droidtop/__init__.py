"""Pythia as this computer's droidtop agent.

Owner, 2026-10-14: "pythia will NEVER call droidtop-agent. If it's running,
it will BE our agent". Pythia loads the agent itself: droidtop-agent's
`droidtop_agent` extension module (its crates/python, docs/DESIGN.md section
16), the same Rust code `droidtop-agent run` is, in Pythia's own process and
over the same files. So the identity and pairings, the library's change log,
save sync, the scanner, WireGuard and the rendezvous are one implementation.
Either program can run on a computer, and either can take over from the
other. Only one serves at a time: the first to listen on the agent's port.

This package only orchestrates:
- `agent`: loading the module, its calls, and serving while Pythia runs;
- `library`: the games Pythia tracks, handed to the agent so they show up
  on paired handhelds as this computer's games.
"""
