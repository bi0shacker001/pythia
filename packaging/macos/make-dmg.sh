#!/bin/sh
# Pythia's macOS disk image, over the standalone build packaging/pyinstaller/pythia.spec makes
# (dist/standalone/Pythia.app): the app beside a link to Applications, to drag across.
# Built by .github/workflows/build.yml from the repository root:
#
#     packaging/macos/make-dmg.sh <version> <arch>
set -eu
version=${1:?version}
arch=${2:?arch}
app=dist/standalone/Pythia.app
[ -d "$app" ] || { echo "no $app: build the standalone app first" >&2; exit 1; }
stage=$(mktemp -d)
cp -R "$app" "$stage/"
ln -s /Applications "$stage/Applications"
mkdir -p dist
hdiutil create -volname "Pythia $version" -srcfolder "$stage" -ov -format UDZO "dist/Pythia-$version-macos-$arch.dmg"
