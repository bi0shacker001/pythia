"""Which build this is: written by CI before it builds (packaging/release_channel.py stamp), so an installed
Pythia knows its own build and the updater (pythia.updater) can tell whether a release is newer. A copy run from
the repository keeps these values: build 0, which never updates itself."""

CODE = 0
COMMIT = ""
