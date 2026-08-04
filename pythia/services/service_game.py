"""Service game module"""

from pythia import settings
from pythia.database import sql
from pythia.database.services import ServiceGameCollection
from pythia.services.service_media import ServiceMedia


class ServiceGame:
    """Representation of a game from a 3rd party service"""

    service = NotImplemented
    installer_slug = NotImplemented
    medias = (ServiceMedia,)

    def __init__(self):
        self.appid = None  # External ID of the game on the 3rd party service
        self.game_id = None  # Internal Pythia ID
        self.runner = None  # Name of the runner
        self.name = None  # Name
        self.slug = None  # Game slug
        self.pythia_slug = None  # Slug used by the pythia website
        self.logo = None  # Game logo
        self.icon = None  # Game icon
        self.details = None  # Additional details for the game

    def save(self):
        """Save this game to database"""
        game_data = {
            "service": self.service,
            "appid": self.appid,
            "name": self.name,
            "slug": self.slug,
            "pythia_slug": self.pythia_slug,
            "icon": self.icon,
            "logo": self.logo,
            "details": str(self.details),
        }
        existing_game = ServiceGameCollection.get_game(self.service, self.appid)
        if existing_game:
            sql.db_update(settings.DB_PATH, "service_games", game_data, {"id": existing_game["id"]})
        else:
            sql.db_insert(settings.DB_PATH, "service_games", game_data)
