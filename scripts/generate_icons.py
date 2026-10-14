"""One-time generator for Pythia's placeholder app icon + desktop metadata
(2026-08-07: "Use the generic 'missing texture' asset for any assets we need
to replace. We haven't created icons and stuff yet, so that'll be a good
reminder.") Deliberately NOT share/pythia/media/logo.png (a real, decent-
looking game-cover asset) -- the classic magenta/black checkerboard is meant
to be obviously wrong so it gets replaced once real branding exists, not
mistaken for a finished icon.

Run once; output is committed (icons/desktop files don't need regenerating
on every build). Re-run after editing this script to regenerate.
"""

import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARE = REPO_ROOT / "share"
PACKAGING = REPO_ROOT / "packaging"

sys.path.insert(0, str(REPO_ROOT))
from pythia import __version__ as PYTHIA_VERSION  # noqa: E402

APP_ID = "net.pythia.Pythia"
CHECKER_SIZE = 512
TILE = 64
MAGENTA = (255, 0, 255, 255)
BLACK = (10, 10, 10, 255)

HICOLOR_SIZES = [16, 22, 24, 32, 48, 64, 128, 256]
ICO_SIZES = [16, 32, 48, 256]


def make_checkerboard(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            tile_x = x // (size // (CHECKER_SIZE // TILE))
            tile_y = y // (size // (CHECKER_SIZE // TILE))
            px[x, y] = MAGENTA if (tile_x + tile_y) % 2 == 0 else BLACK
    return img


def write_hicolor(master: Image.Image) -> None:
    for size in HICOLOR_SIZES:
        out_dir = SHARE / "icons" / "hicolor" / f"{size}x{size}" / "apps"
        out_dir.mkdir(parents=True, exist_ok=True)
        master.resize((size, size), Image.NEAREST).save(out_dir / f"{APP_ID}.png")
    print(f"Wrote {len(HICOLOR_SIZES)} hicolor sizes under {SHARE / 'icons' / 'hicolor'}")


def write_ico(master: Image.Image) -> None:
    for platform_dir in ("windows", "linux"):
        out_dir = PACKAGING / platform_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        master.save(out_dir / "icon.ico", sizes=[(s, s) for s in ICO_SIZES])
    print(f"Wrote icon.ico to packaging/windows/ and packaging/linux/")


def write_svg() -> None:
    out_dir = SHARE / "icons" / "hicolor" / "scalable" / "apps"
    out_dir.mkdir(parents=True, exist_ok=True)
    tiles = 512 // TILE
    rects = []
    for ty in range(tiles):
        for tx in range(tiles):
            if (tx + ty) % 2 == 0:
                rects.append(f'<rect x="{tx * TILE}" y="{ty * TILE}" width="{TILE}" height="{TILE}" fill="#ff00ff"/>')
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        f'<rect width="512" height="512" fill="#0a0a0a"/>'
        + "".join(rects)
        + "</svg>"
    )
    (out_dir / f"{APP_ID}.svg").write_text(svg, encoding="utf-8")
    print(f"Wrote {out_dir / f'{APP_ID}.svg'}")


DESKTOP_ENTRY = f"""[Desktop Entry]
Name=Pythia
StartupWMClass={APP_ID}
Comment=Video Game Preservation Platform
Categories=Game;PackageManager;
Keywords=gaming;emulator;renpy;
Exec=pythia %U
Icon={APP_ID}
Terminal=false
Type=Application
StartupNotify=true
MimeType=x-scheme-handler/pythia;application/x-pythia;
"""

METAINFO_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <id>{APP_ID}</id>
  <project_license>GPL-3.0-or-later</project_license>
  <metadata_license>CC0-1.0</metadata_license>
  <translation type="gettext">pythia</translation>
  <developer_name translatable="no">Pythia Team</developer_name>
  <update_contact>bioshacker001@gmail.com</update_contact>
  <name translatable="no">Pythia</name>
  <summary>Video game preservation platform</summary>
  <description><p>Pythia helps you install and play video games from all eras and from most gaming systems. By leveraging and combining existing emulators, engine re-implementations and compatibility layers, it gives you a central interface to launch all your games.</p></description>
  <url type="homepage">https://pythia.net/</url>
  <url type="bugtracker">https://github.com/pythia/pythia/issues</url>
  <url type="vcs-browser">https://github.com/pythia/pythia</url>
  <launchable type="desktop-id">{APP_ID}.desktop</launchable>
  <releases>
    <release version="{PYTHIA_VERSION}" date="2026-08-07" urgency="low">
      <description><p>Real zip-based plugin packaging, cross-platform build system.</p></description>
    </release>
  </releases>
  <content_rating type="oars-1.1" />
</component>
"""

MIME_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
    <mime-type type="application/x-pythia">
        <comment>YAML installation script for Pythia</comment>
        <glob pattern="*.pythia" />
        <icon name="application-x-pythia"/>
    </mime-type>
</mime-info>
"""


def write_desktop_metadata() -> None:
    (SHARE / "applications" / f"{APP_ID}.desktop").write_text(DESKTOP_ENTRY, encoding="utf-8")
    (SHARE / "metainfo" / f"{APP_ID}.metainfo.xml").write_text(METAINFO_XML, encoding="utf-8")
    (SHARE / "mime" / "packages" / "application-x-pythia.xml").write_text(MIME_XML, encoding="utf-8")
    print("Wrote .desktop, .metainfo.xml, mime/application-x-pythia.xml")


def main() -> None:
    master = make_checkerboard(512)
    write_hicolor(master)
    write_ico(master)
    write_svg()
    write_desktop_metadata()


if __name__ == "__main__":
    main()
