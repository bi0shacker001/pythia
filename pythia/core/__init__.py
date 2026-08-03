"""
pythia.core -- foundation layer ported/adapted from Lutris (github.com/lutris/lutris,
GPLv3; the reference clone lives in the sibling `lutris/` repo for comparison, never
imported from directly). Started 2026-08-03 after repeated real problems building
Pythia's own scaffolding from scratch (a hollow Settings dialog, hand-duplicated
service logic that Lutris already had working and better) -- Lutris's actual
`services/`/`runners/`/`config.py` subsystems become Pythia's base layer, repurposed
for a different mission (never launches a game itself; a library manager, not a
launcher) rather than reimplemented.

Every module here that started from a Lutris file says so in its own docstring,
including what was changed and why (GTK stripped, `lutris`->`pythia` renamed for
internal paths/branding, external URLs like lutris.net's API left alone since we
still want to hit them as a real metadata/cross-store-ID source).
"""
