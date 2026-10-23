******
Pythia
******

Pythia is a personal, multi-system game library manager. It started as a
Ren'Py ARM/x86_64 library patcher and grew into a tool for organizing and
tracking a game library spread across multiple machines, drives, and
storefronts.

Based on Lutris
===============

**Pythia is largely based on** `Lutris <https://github.com/lutris/lutris>`_.
Pythia's ``main`` branch is Lutris's own source (imported at upstream commit
b33d7d8, 2026-07-30), renamed and reworked. Lutris's runner, service and
installer infrastructure (Wine/Proton management, Steam/GOG/Epic/itch.io/etc.
library sync, scripted installers) is reused as it is rather than reinvented.

What Pythia changes or adds on top of Lutris:

- a multi-system library model: one record per (system, game) in a synced
  ``datadir/`` folder, with versions and variants (mods, language, platform)
  per game, store identities and ownership separate from the game itself,
  and a review step for merging duplicate entries;
- a plugin architecture (library, engine, manager, downloader, metadata,
  source, mod and patcher plugins; built-in, pip-installed or drop-in);
- engine detection and remediation (the Ren'Py ARM/x86_64 library patcher
  Pythia started as, and detection for Unity, KiriKiri, RPG Maker, Godot and
  others);
- F95zone watchlist import, export and update checking;
- its own user interfaces in place of Lutris's GTK one (Qt desktop and
  big-picture modes, and a browser-based UI), and a Windows build.

**Pythia is not Lutris**, is not affiliated with or endorsed by the Lutris
project, and does not connect to lutris.net as an account/sync service the
way upstream Lutris does.

Pythia is licensed under the **GPL-3.0, the same license as Lutris** -- see
``LICENSE``. ``AUTHORS`` credits the Lutris contributors whose code this
project is built on top of. Thank you to the Lutris developers: without their
years of work on runners, services and installers this project would not
exist. If you find Pythia useful, consider supporting Lutris
(https://lutris.net/donate).

Status
======

This is an active rework in progress, not a finished product. The ``main``
branch is Lutris's real source with its GTK UI (``lutris/gui/``,
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

Handhelds running droidtop
==========================

While Pythia runs, it is this computer's `droidtop <https://github.com/Droidtop/droidtop>`_
agent. Paired handhelds see the computer's games, Pythia's own tracked games
among them, and keep their saves in step with it. Pythia does not talk to a
separate agent program. It is built from droidtop-agent's crates, loaded as
the ``droidtop_agent`` Python module (one submodule per crate: ``store``,
``pair``, ``scan``, ``saves``, ``service``), so the pairing, the encrypted
connection, the library and save sync are the same code droidtop-agent runs,
over the same files. Run either Pythia or droidtop-agent on a computer, not
both. If droidtop-agent is already running, it keeps serving and Pythia says so.
The games Pythia tracks stay on the handhelds when droidtop-agent serves after
Pythia closed.

In the window, Systems & sync lists the paired handhelds (which one's saves
win, which may start games here, Forget) and pairs new ones: "Pair a handheld"
takes the code the handheld shows, "Show a code" shows one for a handheld that
cannot reach this computer. Save backups lists the copies kept on this computer
when a sync would have overwritten saves that differ.

Install the ``droidtop_agent`` wheel for your system from
`droidtop-agent's releases <https://github.com/Droidtop/droidtop-agent/releases>`_
(``pip install droidtop_agent-*.whl``). Without it, Pythia works as before.

* ``pythia --droidtop-pair 123456``: pair with the handheld showing that code
  (droidtop: Settings > Computers > Pair a computer).
* ``pythia --droidtop-pair-show``: show a code on this computer for a handheld
  that cannot reach it.
* ``pythia --droidtop-devices`` and ``--droidtop-unpair <id>``: list or forget
  paired handhelds.
* ``pythia --droidtop-serve``: serve the paired handhelds from the command line.

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
