******
Pythia
******

Pythia is a personal, multi-system game library manager. It started as a
Ren'Py ARM/x86_64 library patcher and grew into a tool for organizing and
tracking a game library spread across multiple machines, drives, and
storefronts.

Pythia is based on `Lutris <https://github.com/lutris/lutris>`_ -- it reuses
Lutris's real runner, service, and installer infrastructure (Wine/Proton
management, Steam/GOG/Epic/itch.io/etc. library sync, scripted installers)
rather than reinventing that plumbing. **Pythia is not Lutris**, is not
affiliated with or endorsed by the Lutris project, and does not connect to
lutris.net as an account/sync service the way upstream Lutris does. The most
visible difference: Pythia replaces Lutris's GTK desktop UI with its own,
built for this project's specific workflow (library organization across
systems, not a single-machine game launcher front-end).

Pythia is licensed under the GPL-3.0, the same license as Lutris -- see
``LICENSE``. ``AUTHORS`` credits the real Lutris contributors whose code this
project is built on top of.

Status
======

This is an active rework in progress, not a finished product. The current
branch (``lutris-core``) is Lutris's real source with its GTK UI (``lutris/gui/``,
now ``pythia/gui/``) being decoupled from the rest of the codebase and
replaced with a new UI. Large parts of the original GTK interface still exist
in the tree but are being phased out; some Linux-desktop-specific
functionality (X11/Wayland display management, D-Bus screensaver inhibition,
XDG portals) is real and works on Linux, but isn't yet reimplemented for
Windows.

Running Pythia
==============

Pythia targets both Windows and Linux. On Linux, once dependencies are
installed you can run it from the source directory with ``./bin/pythia``.
Windows support is in progress alongside the UI rework.

Configuration files
====================

* ``~/.local/share/pythia`` (Linux) / the platform-appropriate app-data
  folder (Windows): client, runner, and game configuration files.
* ``pythia.conf``: preferences for the client.
* ``system.yml`` / ``runners/*.yml`` / ``games/*.yml``: default, per-runner,
  and per-game configuration, in increasing priority order.
* ``pga.db``: SQLite database tracking the game library, install status,
  file locations, and metadata.

Contributing
============

This is a personal project, not a community-run one -- there's no public
Discord, Patreon, or issue tracker to join. If you're looking for the
original, actively maintained community project this is based on, that's
`Lutris <https://lutris.net>`_.
