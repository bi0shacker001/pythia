"""A game's cover, from what is already on this computer: Lutris's cover art
(``coverart/<slug>.jpg``, the same files Lutris shows), else Steam's own
library art for a game with a Steam id (``appcache/librarycache``, 2:3, the
art Steam's library shows). Nothing is downloaded. The web page shows a cover
as a file URL; a game without one gets the page's drawn art."""

from functools import lru_cache
from pathlib import Path

from pythia import settings
from pythia.util.strings import slugify

_EXTENSIONS = (".jpg", ".png", ".webp")


@lru_cache(maxsize=1)
def _steam_cache() -> Path | None:
    try:
        from pythia.util.steam.config import get_steam_dir

        steam = get_steam_dir()
    except Exception:  # Steam's config is not ours: no Steam art, not no page.
        return None
    cache = Path(steam) / "appcache" / "librarycache" if steam else None
    return cache if cache and cache.is_dir() else None


def _steam_art(appid: str) -> Path | None:
    cache = _steam_cache()
    if not cache or not appid.isdigit():
        return None
    # Older Steam keeps it flat; newer Steam keeps a folder per game.
    for path in (cache / f"{appid}_library_600x900.jpg", cache / appid / "library_600x900.jpg"):
        if path.is_file():
            return path
    return None


def cover_for(name: str, platform_ids: list[dict]) -> Path | None:
    """The cover for a game named ``name`` with those store ids, or None."""
    slug = slugify(name or "")
    if slug:
        for ext in _EXTENSIONS:
            path = Path(settings.COVERART_PATH) / f"{slug}{ext}"
            if path.is_file():
                return path
    for link in platform_ids or []:
        if link.get("platform") == "steam":
            art = _steam_art(str(link.get("id") or ""))
            if art:
                return art
    return None


def cover_url(entries: list[dict]) -> str | None:
    """The file URL of the cover of a game recorded as ``entries``."""
    for entry in entries:
        path = cover_for(entry.get("canonical_name") or "", entry.get("platform_ids") or [])
        if path:
            return path.as_uri()
    return None
