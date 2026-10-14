"""Build metadata -- overwritten by CI immediately before packaging a build
for any channel (2026-08-07, real release-channel system: latest built on
every commit, nightly once a day, unstable for pre-release testing, stable
for real tagged releases -- "we build every commit... We also must never
delete old builds. We keep the whole history."). Stays at these defaults
for a normal source checkout or dev run -- pythia.util.update_check only
uses BUILD_COMMIT for the latest/nightly channels (where "is there a newer
commit than mine" is the only comparison that makes sense; stable/unstable
compare real version strings instead, see update_check.py)."""

BUILD_CHANNEL = "dev"
BUILD_COMMIT: str | None = None
