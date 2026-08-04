from pythia import settings
from pythia.api import get_api_games
from pythia.database import sql
from pythia.database.games import get_games_where
from pythia.util.log import logger


def migrate():
    """
    Update Games that do not have a Discord ID
    """
    logger.info("Updating Games Discord APP ID's")
    # Make a *de-duplicated* list of slugs; we need a list for the API.
    slugs_to_update = list({game["slug"] for game in get_games_where(discord_id__isnull=True)})
    if not slugs_to_update:
        return
    games = get_api_games(slugs_to_update)
    for game in games:
        if not game.get("discord_id"):
            continue

        sql.db_update(settings.DB_PATH, "games", {"discord_id": game["discord_id"]}, {"slug": game["slug"]})
        logger.info("Updated %s", game["name"])
