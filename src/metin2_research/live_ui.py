from __future__ import annotations

from pathlib import Path

from PIL import Image


def _iter_pixels(image: Image.Image):
    """Return RGB pixels without using Pillow's deprecated getdata()."""
    get_flattened_data = getattr(image, "get_flattened_data", None)
    if get_flattened_data is not None:
        return get_flattened_data()
    return image.getdata()


def looks_like_escape_menu(image_path: str | Path) -> bool:
    """Detect the central Metin2 ESC menu/button stack.

    The menu is a vertical stack of dark gray buttons around the center of the
    screen. This intentionally avoids OCR and only detects the visible panel so
    callers can use ESC to close menus without blind-tapping ESC and opening the
    menu when the screen is already clear.
    """
    with Image.open(image_path).convert("RGB") as img:
        w, h = img.size
        crop = img.crop((int(w * 0.43), int(h * 0.32), int(w * 0.57), int(h * 0.78)))
        total = crop.width * crop.height
        if total <= 0:
            return False
        gray_dark = 0
        very_dark = 0
        for r, g, b in _iter_pixels(crop):
            spread = max(r, g, b) - min(r, g, b)
            if 35 <= r <= 110 and 35 <= g <= 110 and 35 <= b <= 110 and spread < 35:
                gray_dark += 1
            if r < 35 and g < 35 and b < 35:
                very_dark += 1
        gray_ratio = gray_dark / total
        dark_ratio = very_dark / total
        return gray_ratio > 0.16 and dark_ratio > 0.05
