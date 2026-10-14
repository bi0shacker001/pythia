#!/usr/bin/env bash
# Build Pythia's Linux AppImage (2026-08-07). MUST run on Linux -- PyInstaller
# doesn't cross-compile, so this always builds for whatever architecture it's
# actually running on (x86_64 or aarch64 -- read from `uname -m`; see
# .github/workflows/build.yml's linux job matrix for how both get built in
# CI, one real machine per architecture). Requires `appimagetool` on PATH
# (download the arch-matching release from
# https://github.com/AppImage/appimagetool/releases if not already present).
#
# Usage: scripts/build_appimage.sh
#
# This REPLACES the old GTK/linuxdeploy-based utils/appimage/ -- a
# PyInstaller-frozen Qt app bundles its own Qt/WebEngine libs already and
# has no use for linuxdeploy's GTK plugin.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="$(python3 -c 'import sys; sys.path.insert(0, "pythia"); from pythia import __version__; print(__version__)')"
APP_ID="net.pythia.Pythia"
ARCH="$(uname -m)"

DIST_DIR="$REPO_ROOT/dist/linux"
BUILD_DIR="$REPO_ROOT/build/linux"
APPDIR="$BUILD_DIR/Pythia.AppDir"

echo "==> Building frozen app with PyInstaller ..."
python3 -m PyInstaller "$REPO_ROOT/packaging/linux/pythia.spec" \
    --distpath "$DIST_DIR" --workpath "$BUILD_DIR" --noconfirm

ONEDIR="$DIST_DIR/pythia"
if [ ! -x "$ONEDIR/pythia" ]; then
    echo "Expected PyInstaller output at $ONEDIR/pythia, not found." >&2
    exit 1
fi

echo "==> Assembling AppDir at $APPDIR ..."
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/metainfo"

# The whole PyInstaller onedir bundle (pythia executable + _internal/) lands
# directly under usr/bin/ -- AppRun execs usr/bin/pythia, which finds its own
# _internal/ next to it exactly like the unpacked onedir build does outside
# an AppImage.
cp -a "$ONEDIR/." "$APPDIR/usr/bin/"

cp "$REPO_ROOT/share/applications/${APP_ID}.desktop" "$APPDIR/usr/share/applications/"
cp "$REPO_ROOT/share/metainfo/${APP_ID}.metainfo.xml" "$APPDIR/usr/share/metainfo/" 2>/dev/null || true
cp -a "$REPO_ROOT/share/icons" "$APPDIR/usr/share/"

# AppImage requires the .desktop file, an icon, and AppRun at the AppDir root
# (not just under usr/share/) -- standard appimagetool convention.
cp "$APPDIR/usr/share/applications/${APP_ID}.desktop" "$APPDIR/${APP_ID}.desktop"
cp "$REPO_ROOT/share/icons/hicolor/256x256/apps/${APP_ID}.png" "$APPDIR/${APP_ID}.png"
ln -sf "${APP_ID}.png" "$APPDIR/.DirIcon"

cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "$HERE/usr/bin/pythia" "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo "==> Running appimagetool ..."
if ! command -v appimagetool >/dev/null 2>&1; then
    echo "appimagetool not found on PATH -- download it from " \
         "https://github.com/AppImage/appimagetool/releases and try again." >&2
    exit 1
fi

mkdir -p "$DIST_DIR"
OUTPUT="$DIST_DIR/Pythia-${VERSION}-${ARCH}.AppImage"
ARCH="$ARCH" appimagetool "$APPDIR" "$OUTPUT"

echo "==> Done: $OUTPUT"
