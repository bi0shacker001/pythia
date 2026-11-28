# Packaging

Every Pythia release is a standalone installer on every system (docs/SPEC.md, decision 11). Each one
carries its own Python, Qt and droidtop agent, so nothing else needs installing first.

| System | What a release offers | Made by |
|---|---|---|
| Windows (x86-64) | `pythia-windows-setup.exe` | `windows/pythia.iss` (Inno Setup) |
| macOS (Apple silicon) | `pythia-macos-arm64.dmg` | `macos/make-dmg.sh` |
| macOS (Intel) | `pythia-macos-x86_64.dmg` | `macos/make-dmg.sh` |
| Linux (x86-64, glibc 2.35+) | `pythia-linux-amd64.deb`, `pythia-linux-x86_64.rpm` | `linux/nfpm.yaml` (nfpm) |

All of them wrap one standalone build, `pyinstaller/pythia.spec`, made from Pythia installed straight into
the build's environment (`pip install .`, which compiles `pythia._native` with cargo) and
`freeze_requirements.py`'s list (the dependencies less PyGObject and dbus-python). `.github/workflows/build.yml`
has one job per system (macOS twice, one per chip), each building, installing and starting what it makes,
on every push to main and by hand on a `ci/` branch. No wheels are built; one can still be built from the
repository by hand (`python -m build`).

The `ci/native-preview` Windows installer bundles the shared Compose front end
in the `native-ui` directory. Launch Pythia from Start as usual. The Python
process starts the local backend and tray, then opens the bundled front end.
Closing that window returns to the tray when one is available. Use **Open
Pythia** in the tray to reopen it. To compare with the HTML UI, quit Pythia
from the tray first, then launch `Pythia.exe --ui web`.

Download `pythia-windows-installer` from the preview branch's **Build** workflow
artifacts. Its Windows installer job packages the native front end built from
the same commit. The preview uses the regular Pythia installation location and
user data, so it replaces the installed Windows build when installed there.

**Release lanes** (docs/SPEC.md, decision 13), as droidtop has them:

- **Unstable**: every green build of main publishes itself as a pre-release tagged `v<version>-dev.<build>`
  (`release_channel.py publish-build`), where the build number is the count of commits up to it. Those
  releases are the build history and are never rewritten.
- **Testing** and **Stable**: fixed tags `testing` and `stable`, moved by hand (Actions > Promote a build,
  `release-promote.yml`) to an Unstable build. Promoting builds nothing: it publishes that build's own files
  after checking each against its `release-info.json`.

Every release carries `release-info.json`: the build, its commit, and each installer's name, size and
SHA-256. It is the updater's one probe (`pythia/updater.py`), so its fields and the asset names above are a
contract with every installed Pythia. `release_channel.py stamp` writes the build number into
`pythia/_build.py` before anything is built; `version.py --display` and `--package` give the version with
it.

What each installs:

- **Windows**: Pythia in `%LOCALAPPDATA%\Programs\Pythia` (or Program Files, when installed for everyone),
  a Start menu entry, an optional desktop icon, the `pythia:` link handler, and an uninstaller in
  Settings > Apps. `Pythia.exe` opens the window; `pythia-cli.exe` is the command line.
- **macOS**: `Pythia.app`, dragged to Applications (the Apple silicon or the Intel image). It is not signed by Apple, so the first start is
  Control-click > Open.
- **Linux**: `/opt/pythia`, `pythia` on the path, the desktop entry, icons, metainfo and man page. The
  package is called `pythia`, as the Launchpad-built one is; installing one replaces the other.

Uninstalling never removes the person's library or settings.
