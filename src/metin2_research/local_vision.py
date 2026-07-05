from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image


_COORD_RE = re.compile(r"(?P<name>[A-Za-z0-9_]+)\s*(?:[:(])?\s*(?P<x>\d{1,5})\s*[,;:]\s*(?P<y>\d{1,5})\s*\)?")


def parse_player_coordinate_text(text: str) -> dict[str, Any] | None:
    cleaned = " ".join(str(text).strip().split())
    match = _COORD_RE.search(cleaned)
    if not match:
        return None
    return {
        "player_name": match.group("name"),
        "coord": [int(match.group("x")), int(match.group("y"))],
        "raw_text": cleaned,
    }


def minimap_area_for_size(width: int, height: int) -> list[int]:
    """Return the top-right minimap + location/coord text crop region.

    The region intentionally includes the circular minimap and the black text box
    below it where Metin2 shows map/channel/time and, when hovered manually,
    `Yoshypt(x, y)` coordinate text.
    """
    return [
        int(round(width * 0.800)),
        int(round(height * 0.029)),
        int(width),
        int(round(height * 0.329)),
    ]


def crop_minimap_area(image_path: str | Path) -> Image.Image:
    image = Image.open(image_path).convert("RGB")
    box = minimap_area_for_size(image.width, image.height)
    return image.crop(tuple(box))


def available_ocr_backends() -> dict[str, Any]:
    pytesseract_available = False
    try:
        import pytesseract  # noqa: F401

        pytesseract_available = True
    except Exception:
        pytesseract_available = False
    return {
        "tesseract_cmd": shutil.which("tesseract"),
        "pytesseract": pytesseract_available,
    }


def read_coordinate_with_ocr(image_path: str | Path) -> dict[str, Any]:
    """Try local OCR on the minimap text crop.

    This is intentionally optional. The current machine may not have Tesseract or
    pytesseract installed; callers still get a structured unavailable result so
    the mapping loop can fall back to manual hover text or visual-only nodes.
    """
    backends = available_ocr_backends()
    if not backends["pytesseract"] or not backends["tesseract_cmd"]:
        return {
            "available": False,
            "backend": "pytesseract+tesseract",
            "reason": "pytesseract or tesseract executable is not installed",
            "text": None,
            "parsed": None,
        }

    import pytesseract

    crop = crop_minimap_area(image_path)
    text = pytesseract.image_to_string(crop, config="--psm 6")
    return {
        "available": True,
        "backend": "pytesseract+tesseract",
        "reason": None,
        "text": text,
        "parsed": parse_player_coordinate_text(text),
    }


def compare_coordinate_feedback(before_state: dict[str, Any], after_state: dict[str, Any], *, expected_action: str) -> dict[str, Any]:
    before = before_state.get("player_coord")
    after = after_state.get("player_coord")
    base = {"expected_action": expected_action, "before_coord": before, "after_coord": after}
    if before is None:
        return {**base, "status": "unknown_no_before_coordinate", "delta": None, "needs_feedback": "manual hover or OCR coordinate read"}
    if after is None:
        return {**base, "status": "unknown_no_after_coordinate", "delta": None, "needs_feedback": "manual hover or OCR coordinate read"}
    delta = [int(after[0]) - int(before[0]), int(after[1]) - int(before[1])]
    if delta == [0, 0]:
        return {**base, "status": "no_coordinate_change", "delta": delta, "needs_feedback": "check stuck state, menu focus, or ignored key input"}
    return {**base, "status": "moved", "delta": delta, "needs_feedback": None}


def build_local_state(image_path: str | Path, *, coordinate_text: str | None = None) -> dict[str, Any]:
    image_path = Path(image_path)
    image = Image.open(image_path).convert("RGB")
    minimap_area = minimap_area_for_size(image.width, image.height)

    parsed = parse_player_coordinate_text(coordinate_text or "") if coordinate_text else None
    coordinate_source = "provided_text" if parsed else None
    ocr_result: dict[str, Any] | None = None
    if parsed is None:
        ocr_result = read_coordinate_with_ocr(image_path)
        parsed = ocr_result.get("parsed")
        coordinate_source = "local_ocr" if parsed else None

    return {
        "image": {
            "path": str(image_path),
            "width": image.width,
            "height": image.height,
        },
        "regions": {
            "minimap_area": minimap_area,
        },
        "player_name": parsed["player_name"] if parsed else None,
        "player_coord": parsed["coord"] if parsed else None,
        "ocr": {
            "coordinate_source": coordinate_source,
            "provided_text": coordinate_text,
            "ocr_result": ocr_result,
            "available_backends": available_ocr_backends(),
        },
        "feedback": {
            "has_world_coordinate": parsed is not None,
            "needs_manual_coordinate_hover": parsed is None,
        },
    }
