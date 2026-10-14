"""
Pythia core synchronization/transfer subsystem (2026-08-07, user design +
ChatGPT/Codex handoff -- see ../../../aisync/ in the repo root for the full
architecture discussion). This package is core infrastructure, never a
plugin: nothing in pythia/plugins/registry.py's discovery paths touches it,
and it never launches or depends on an external helper process (no
Syncthing/rclone/WireGuard/Croc binary).

Build order (see aisync/CHATGPT-INITIAL-HANDOFF.md and
aisync/CLAUDE-RESPONSE.md for the full milestone list): identity/trust/pairing
first (this pass), then framing/protocol, then discovery, then connectivity,
then transfer, then storage backends. Only identity.py, trust.py, and
pairing.py exist so far -- everything else is still design-only.
"""
