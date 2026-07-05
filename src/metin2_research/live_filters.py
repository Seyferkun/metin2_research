from __future__ import annotations

from typing import Any
from pathlib import Path

from PIL import Image


def _iter_pixels(image: Image.Image):
    """Return RGB pixels without using Pillow's deprecated getdata()."""
    get_flattened_data = getattr(image, "get_flattened_data", None)
    if get_flattened_data is not None:
        return get_flattened_data()
    return image.getdata()


def is_ui_or_overlay_box(box: dict[str, Any], image_width: int, image_height: int) -> bool:
    """Return True when a YOLO Metin candidate is in a known UI/overlay zone.

    Coordinates are crop-relative to the game window. This intentionally rejects
    false positives we observed live: minimap/top-right, task icon bottom-left,
    hotbar, character panel, titlebar/window edge, and large map overlay.
    """
    x = float(box["x_center"])
    y = float(box["y_center"])
    xmin = float(box.get("xmin", x))
    ymin = float(box.get("ymin", y))
    xmax = float(box.get("xmax", x))
    ymax = float(box.get("ymax", y))
    width = float(box.get("width", xmax - xmin))
    height = float(box.get("height", ymax - ymin))

    # Extreme left/right edge false positives and partial off-screen objects are unsafe.
    if x < image_width * 0.06 or x > image_width * 0.94:
        return True

    # Window/titlebar/top-edge false positives, including minimap frame clipped at y=0.
    if y < image_height * 0.06 or ymin <= 2:
        return True

    # Top-right minimap, event/UI buttons, map overlay close buttons.
    if x > image_width * 0.78 and y < image_height * 0.36:
        return True

    # Bottom hotbar/chat/quest/task icons.
    if y > image_height * 0.82:
        return True

    # Left character panel / task panel region. In live runs this produced false
    # Metin boxes on the task icon and character/equipment UI.
    if x < image_width * 0.20 and y > image_height * 0.20:
        return True

    # Big map overlay region around center-right; Metin-looking map icons are not world targets.
    if image_width * 0.62 < x < image_width * 0.80 and image_height * 0.12 < y < image_height * 0.52:
        return True

    # Tiny fragments are usually labels/UI/terrain; world Metins occupy a visible body.
    if width < 45 or height < 60:
        return True

    return False


def filter_world_metin_candidates(
    detections: list[dict[str, Any]],
    image_width: int,
    image_height: int,
    *,
    min_confidence: float = 0.20,
) -> list[dict[str, Any]]:
    kept = []
    for box in detections:
        if float(box.get("confidence", 0.0)) < min_confidence:
            continue
        if is_ui_or_overlay_box(box, image_width, image_height):
            continue
        kept.append(box)
    kept.sort(key=lambda b: (float(b.get("confidence", 0.0)), float(b.get("height", 0.0))), reverse=True)
    return kept


def has_mob_level_label_near_box(image: Image.Image, box: dict[str, Any]) -> bool:
    """Detect green 'Lv' style mob/player labels near a candidate.

    The current Metin detector can false-positive boars/wolves. Hostile mobs in
    this client usually have a green level label immediately above/inside the
    object label area. Metin labels are red names without the green level prefix,
    so this is a conservative veto for spawn-memory/controller target selection.
    """
    img = image.convert("RGB")
    w, h = img.size
    xmin = int(max(0, float(box.get("xmin", box["x_center"])) - 20))
    xmax = int(min(w, float(box.get("xmax", box["x_center"])) + 20))
    ymin = int(max(0, float(box.get("ymin", box["y_center"])) - 45))
    ymax = int(min(h, float(box.get("ymin", box["y_center"])) + max(35.0, float(box.get("height", 0.0)) * 0.35)))
    if xmax <= xmin or ymax <= ymin:
        return False
    crop = img.crop((xmin, ymin, xmax, ymax))
    green_pixels = 0
    for r, g, b in _iter_pixels(crop):
        if g > 150 and r < 110 and b < 110 and (g - r) > 55:
            green_pixels += 1
    return green_pixels >= 18


def filter_world_metin_candidates_with_image(
    image_path: str | Path,
    detections: list[dict[str, Any]],
    image_width: int,
    image_height: int,
    *,
    min_confidence: float = 0.20,
) -> list[dict[str, Any]]:
    base = filter_world_metin_candidates(detections, image_width, image_height, min_confidence=min_confidence)
    if not base:
        return base
    with Image.open(image_path) as image:
        kept = [box for box in base if not has_mob_level_label_near_box(image, box)]
    kept.sort(key=lambda b: (float(b.get("confidence", 0.0)), float(b.get("height", 0.0))), reverse=True)
    return kept


def screen_xy_for_metin_body(box: dict[str, Any], window_bbox: tuple[int, int, int, int]) -> list[int]:
    """Click slightly below center of the visible body, not labels/ground/UI."""
    left, top, _right, _bottom = window_bbox
    x = float(box["x_center"])
    y = float(box.get("ymin", box["y_center"])) + float(box.get("height", 0.0)) * 0.58
    return [int(round(left + x)), int(round(top + y))]
