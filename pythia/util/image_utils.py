"""Pillow-based image helpers -- moved out of gui/widgets/utils.py (2026-08-04
GTK removal), where they lived despite having zero real GTK/cairo/Gdk
dependency (only Pillow). Services (ea_app.py, egs.py) use these to process
downloaded cover art, independent of whatever UI toolkit eventually displays
the result.
"""

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]

ICON_SIZE = (32, 32)
BANNER_SIZE = (184, 69)


def thumbnail_image(base_image: "Image.Image", target_size: tuple[int, int]) -> "Image.Image":
    base_width, base_height = base_image.size
    base_ratio = base_width / base_height
    target_width, target_height = target_size
    target_ratio = target_width / target_height

    # Resize and crop coverart
    if base_ratio >= target_ratio:
        width = int(base_width * (target_height / base_height))
        height = target_height
    else:
        width = target_width
        height = int(base_height * (target_width / base_width))
    x_offset = int((width - target_width) / 2)
    y_offset = int((height - target_height) / 2)
    base_image = base_image.resize((width, height), resample=Image.Resampling.BICUBIC)
    base_image = base_image.crop((x_offset, y_offset, width - x_offset, height - y_offset))
    return base_image


def paste_overlay(base_image: "Image.Image", overlay_image: "Image.Image", position: float = 0.7) -> "Image.Image":
    base_width, base_height = base_image.size
    overlay_width, overlay_height = overlay_image.size
    offset_x = int((base_width - overlay_width) / 2)
    offset_y = int((base_height - overlay_height) / 2)
    base_image.paste(
        overlay_image, (offset_x, offset_y, overlay_width + offset_x, overlay_height + offset_y), mask=overlay_image
    )
    return base_image
