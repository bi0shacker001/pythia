"""Pythia's backend over a local socket (docs/SPEC.md, decision 15; Droidtop/tracker#958).

A second front end (the shared Compose UI) is another process, so it reaches the same `Backend` slots the HTML
UI calls, over a local channel: a Unix socket on Linux and macOS, a named pipe on Windows, never TCP.

- `framing`: one message is a 4-byte big-endian length and that many bytes of UTF-8 JSON.
- `transport`: where the channel is, its permissions, and a stream either way.
- `schema`: the method and event list, read from the Backend itself and checked in as schema/backend-ipc.json.
- `server`: authenticates a connection, dispatches calls, pushes the signals and settles pending answers.
- `qt`: the Qt side of it, started with the running Pythia (pythia.ui.mode).
- `client`: a small blocking client, for tests and for `python -m pythia.ipc`.

Every answer is the backend contract's (ui_contract: ok, unavailable, error, pending). `ui_contract.py` is
mytop-project/mytop-ui contract/python/ui_contract.py, copied unchanged at CONTRACT_PIN; its fixtures are
tests/fixtures/ui_contract_answers.json, checked by tests/_test_ipc_contract.py.
"""

CONTRACT_PIN = "mytop-project/mytop-ui@8a88ee97ca7b12cb2319f0d303eb547c943cc4a3 contract/python/ui_contract.py"
PROTOCOL = 1
