"""
VDF (Valve Data Format) parser -- ported near-verbatim from Lutris's
lutris/util/steam/vdfutils.py (2026-08-03). Only change: `lutris.util.log.logger`
replaced with stdlib `logging`. This replaces pythia/plugins/library/steam.py's own
flat regex scan for real VDF structure -- our regex only ever handled flat
key-value manifests (appmanifest_*.acf, which happens to be flat), but a real nested
file like libraryfolders.vdf (multiple Steam library folders, one dict per folder)
needs actual recursive parsing, which this provides.
"""

import logging
from typing import IO, Any

logger = logging.getLogger(__name__)


def vdf_parse(steam_config_file: IO[str], config: dict[str, Any]) -> dict[str, Any]:
    """Parse a Steam config file and return the contents as a dict."""
    line = " "
    while line:
        try:
            line = steam_config_file.readline()
        except UnicodeDecodeError:
            logger.error("Error while reading Steam VDF file %s. Returning %s", steam_config_file, config)
            return config
        if not line or line.strip() == "}":
            return config
        while not line.strip().endswith('"'):
            nextline = steam_config_file.readline()
            if not nextline:
                break
            line = line[:-1] + nextline
        # Handle escaped quotes in values by temporarily replacing them so
        # split('"') works correctly, then restore them.
        line = line.replace('\\"', "\x00")
        line_elements = line.strip().split('"')
        line_elements = [elem.replace("\x00", '"') for elem in line_elements]
        if len(line_elements) == 3:
            key = line_elements[1]
            steam_config_file.readline()  # skip '{'
            config[key] = vdf_parse(steam_config_file, {})
        else:
            try:
                config[line_elements[1]] = line_elements[3]
            except IndexError:
                logger.error("Malformed config file: %s", line)
    return config


def parse_vdf_file(path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return vdf_parse(f, {})
