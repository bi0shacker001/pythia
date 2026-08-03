"""
GOG metadata plugin -- pulls a real display name (and platform support) from GOG's
public, unauthenticated product API. Closes a real, stated fidelity gap: GogLibraryPlugin's
list_owned() only ever returns bare product IDs (its OAuth-authenticated ownership
endpoint, `embed.gog.com/user/data/games`, has no name field at all -- see
plugins/library/gog.py's own docstring), unlike Steam's/Epic's equivalents. Same
category-separation reasoning as SteamMetadataPlugin: metadata pulling is independent
of local detection and independent of ownership, so it's its own plugin, not bundled
into plugins/library/gog.py.

Verified live before shipping (2026-08-03): `GET https://api.gog.com/products/{id}`
against a real product ID returned a genuine `title` field (confirmed structure: id,
title, slug, content_system_compatibility, downloads, ...).
"""

import json
import urllib.error
import urllib.request

from pythia.plugins import MetadataPlugin
from pythia.plugins.registry import register_builtin

_PRODUCT_URL = "https://api.gog.com/products/{id}"


class GogMetadataPlugin(MetadataPlugin):
    id = "pythia.metadata.gog"
    display_name = "GOG"
    version = "1.0.0"
    author = "pythia"

    def _product(self, product_id) -> dict | None:
        url = _PRODUCT_URL.format(id=product_id)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def resolve_name(self, platform_id: dict) -> str | None:
        product_id = platform_id.get("id")
        if not product_id:
            return None
        data = self._product(product_id)
        if data is None:
            return None
        return data.get("title")

    def platform_support(self, platform_id: dict) -> list | None:
        """GOG's own compatibility field is a dict keyed by OS name -- present-as-key
        means supported, same shape convention as Steam's "platforms" dict but GOG
        nests it one level deeper under content_system_compatibility."""
        product_id = platform_id.get("id")
        if not product_id:
            return None
        data = self._product(product_id)
        if data is None:
            return None
        compat = data.get("content_system_compatibility") or {}
        return [os_name for os_name, supported in compat.items() if supported]


plugin = register_builtin(GogMetadataPlugin())
