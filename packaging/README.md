# Packaging

Every Pythia release is a standalone installer on every system (docs/SPEC.md, decision 11). Each one
carries its own Python, Qt and droidtop agent, so nothing else needs installing first.

| System | What a release offers | Made by |
|---|---|---|
| Windows (x86-64) | `Pythia-<version>-windows-x86_64-setup.exe` | `windows/pythia.iss` (Inno Setup) |
| macOS (Apple silicon) | `Pythia-<version>-macos-arm64.dmg` | `macos/make-dmg.sh` |
| Linux (x86-64, glibc 2.35+) | `pythia_<version>_amd64.deb`, `pythia-<version>.x86_64.rpm` | `linux/nfpm.yaml` (nfpm) |

All of them wrap one standalone build, `pyinstaller/pythia.spec`, made from Pythia's wheel and
`freeze_requirements.py`'s list (the dependencies less PyGObject and dbus-python). `.github/workflows/build.yml`
builds, installs and starts every one on every push; a tag `v<version>`, where the version is
`pyproject.toml`'s (`version.py` prints it), publishes them as a GitHub release.

What each installs:

- **Windows**: Pythia in `%LOCALAPPDATA%\Programs\Pythia` (or Program Files, when installed for everyone),
  a Start menu entry, an optional desktop icon, the `pythia:` link handler, and an uninstaller in
  Settings > Apps. `Pythia.exe` opens the window; `pythia-cli.exe` is the command line.
- **macOS**: `Pythia.app`, dragged to Applications. It is not signed by Apple, so the first start is
  Control-click > Open.
- **Linux**: `/opt/pythia`, `pythia` on the path, the desktop entry, icons, metainfo and man page. The
  package is called `pythia`, as the Launchpad-built one is; installing one replaces the other.

Uninstalling never removes the person's library or settings.
