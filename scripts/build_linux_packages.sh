#!/usr/bin/env bash
# Build Pythia's .rpm and .deb packages via fpm (2026-08-07). MUST run on
# Linux. Requires `fpm` (gem install fpm) and reuses the SAME PyInstaller
# onedir output scripts/build_appimage.sh produces -- run that first, or
# this will build it itself if dist/linux/pythia/ doesn't exist yet.
#
# This REPLACES the old pythia.spec (RPM, GTK/meson-oriented, assumed a
# native system-Python install) -- a PyInstaller-frozen bundle installs the
# same way regardless of distro, so one fpm invocation per format replaces
# hand-written spec/control files entirely.
#
# Usage: scripts/build_linux_packages.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="$(python3 -c 'import sys; sys.path.insert(0, "pythia"); from pythia import __version__; print(__version__)')"
APP_ID="net.pythia.Pythia"

# deb and rpm use different arch-naming conventions for the same real
# hardware -- fpm figures out the right one for each package's own metadata
# on its own (native-arch autodetect), this is only for the OUTPUT filename.
UNAME_ARCH="$(uname -m)"
case "$UNAME_ARCH" in
    x86_64) DEB_ARCH=amd64; RPM_ARCH=x86_64 ;;
    aarch64|arm64) DEB_ARCH=arm64; RPM_ARCH=aarch64 ;;
    *) DEB_ARCH="$UNAME_ARCH"; RPM_ARCH="$UNAME_ARCH" ;;
esac

DIST_DIR="$REPO_ROOT/dist/linux"
BUILD_DIR="$REPO_ROOT/build/linux"
ONEDIR="$DIST_DIR/pythia"
STAGE_DIR="$BUILD_DIR/fpm-stage"

if [ ! -x "$ONEDIR/pythia" ]; then
    echo "==> No PyInstaller output at $ONEDIR yet -- building it first ..."
    python3 -m PyInstaller "$REPO_ROOT/packaging/linux/pythia.spec" \
        --distpath "$DIST_DIR" --workpath "$BUILD_DIR" --noconfirm
fi

if ! command -v fpm >/dev/null 2>&1; then
    echo "fpm not found on PATH -- install it with 'gem install fpm' and try again." >&2
    exit 1
fi

echo "==> Staging package contents at $STAGE_DIR ..."
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR/opt/pythia" "$STAGE_DIR/usr/bin" "$STAGE_DIR/usr/share/applications" \
    "$STAGE_DIR/usr/share/metainfo" "$STAGE_DIR/usr/share/mime/packages"

cp -a "$ONEDIR/." "$STAGE_DIR/opt/pythia/"
cp -a "$REPO_ROOT/share/icons" "$STAGE_DIR/usr/share/"
cp "$REPO_ROOT/share/applications/${APP_ID}.desktop" "$STAGE_DIR/usr/share/applications/"
cp "$REPO_ROOT/share/metainfo/${APP_ID}.metainfo.xml" "$STAGE_DIR/usr/share/metainfo/" 2>/dev/null || true
cp "$REPO_ROOT/share/mime/packages/application-x-pythia.xml" "$STAGE_DIR/usr/share/mime/packages/"

cat > "$STAGE_DIR/usr/bin/pythia" <<'EOF'
#!/usr/bin/env bash
exec /opt/pythia/pythia "$@"
EOF
chmod +x "$STAGE_DIR/usr/bin/pythia"

FPM_COMMON_ARGS=(
    -s dir -C "$STAGE_DIR"
    --name pythia
    --version "$VERSION"
    --license "GPL-3.0-or-later"
    --description "Video game preservation platform"
    --url "https://pythia.net/"
    --maintainer "Pythia Team <bioshacker001@gmail.com>"
    --after-install <(echo 'update-desktop-database /usr/share/applications >/dev/null 2>&1 || true; update-mime-database /usr/share/mime >/dev/null 2>&1 || true')
    opt usr
)

mkdir -p "$DIST_DIR"

echo "==> Building .deb ..."
fpm -t deb -p "$DIST_DIR/pythia_${VERSION}_${DEB_ARCH}.deb" "${FPM_COMMON_ARGS[@]}"

echo "==> Building .rpm ..."
fpm -t rpm -p "$DIST_DIR/pythia-${VERSION}-1.${RPM_ARCH}.rpm" "${FPM_COMMON_ARGS[@]}"

echo "==> Done: $DIST_DIR/pythia_${VERSION}_${DEB_ARCH}.deb, $DIST_DIR/pythia-${VERSION}-1.${RPM_ARCH}.rpm"
