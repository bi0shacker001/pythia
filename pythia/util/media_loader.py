"""Loads game media in parallel -- moved out of gui/views/media_loader.py
(2026-08-04 GTK removal), where it lived despite having zero real GTK
dependency; every service (base.py, lutris.py, ...) calls this to fetch
cover art/icons, independent of whatever UI displays them.
"""

import concurrent.futures

from pythia.util import system
from pythia.util.log import logger
from pythia.util.notifier import NotificationSource

MEDIA_CACHE_INVALIDATED = NotificationSource()


def download_media(media_urls, service_media):
    """Download a list of media files concurrently.

    Limits the number of simultaneous downloads to avoid API throttling.
    """
    icons = {}
    num_workers = 5
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        future_downloads = {
            executor.submit(service_media.download, slug, url): slug for slug, url in media_urls.items() if url
        }
        for future in concurrent.futures.as_completed(future_downloads):
            slug = future_downloads[future]
            try:
                path = future.result()
            except Exception as ex:  # pylint: disable=broad-except
                logger.exception("%r failed: %s", slug, ex)
                path = None
            if system.path_exists(path):
                icons[slug] = path

    if icons:
        MEDIA_CACHE_INVALIDATED.fire()
    return icons
