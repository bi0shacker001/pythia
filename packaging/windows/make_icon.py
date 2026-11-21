"""Writes the Windows installer's icon (.ico, every size Windows asks for) from Pythia's PNG icon.

Usage: python packaging/windows/make_icon.py <png> <ico>
"""

import sys

from PIL import Image

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

if __name__ == "__main__":
    Image.open(sys.argv[1]).save(sys.argv[2], sizes=SIZES)
