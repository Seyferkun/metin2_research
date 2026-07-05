#!/usr/bin/env python
"""Capture the visible HP text from the open Metin2 character window.

This is a visual fallback for HP while memory offsets are unconfirmed.
It uses desktop ImageGrab instead of PrintWindow because DirectX surfaces often
capture black with PrintWindow.

Current calibrated crop for 1602x1031 MT2Portugalia capture with character
window open:
    x=208, y=466, w=130, h=38

The script saves both the full capture and HP crop for OCR/vision inspection.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from PIL import ImageGrab, ImageOps, ImageEnhance

from metin2_research.window_capture import activate_window, find_window


def scaled_rect(base_rect: tuple[int, int, int, int], size: tuple[int, int], base_size: tuple[int, int] = (1602, 1031)) -> tuple[int, int, int, int]:
    x, y, w, h = base_rect
    sx = size[0] / base_size[0]
    sy = size[1] / base_size[1]
    return (round(x * sx), round(y * sy), round((x + w) * sx), round((y + h) * sy))


def enhance_for_digits(img):
    gray = ImageOps.grayscale(img)
    gray = ImageEnhance.Contrast(gray).enhance(3.0)
    gray = gray.resize((gray.width * 4, gray.height * 4))
    return gray


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture Metin2 character-window HP crop")
    ap.add_argument("--query", default="MT2Portugalia")
    ap.add_argument("--out-dir", default="reports/hp_visual")
    ap.add_argument("--rect", default="208,466,130,38", help="x,y,w,h relative to 1602x1031 capture")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    window = find_window(args.query)
    activate_window(window)
    full = ImageGrab.grab(bbox=window.bbox).convert("RGB")
    x, y, w, h = [int(v.strip()) for v in args.rect.split(",")]
    crop_box = scaled_rect((x, y, w, h), full.size)
    crop = full.crop(crop_box)
    enhanced = enhance_for_digits(crop)

    ts = time.strftime("%Y%m%d_%H%M%S")
    full_path = out_dir / f"full_{ts}.png"
    crop_path = out_dir / f"hp_crop_{ts}.png"
    enhanced_path = out_dir / f"hp_crop_enhanced_{ts}.png"
    meta_path = out_dir / f"hp_capture_{ts}.json"
    latest_full = out_dir / "latest_full.png"
    latest_crop = out_dir / "latest_hp_crop.png"
    latest_enhanced = out_dir / "latest_hp_crop_enhanced.png"

    full.save(full_path)
    crop.save(crop_path)
    enhanced.save(enhanced_path)
    full.save(latest_full)
    crop.save(latest_crop)
    enhanced.save(latest_enhanced)

    meta = {
        "window": window.__dict__,
        "capture_size": full.size,
        "base_rect_xywh": [x, y, w, h],
        "crop_box_xyxy": crop_box,
        "full_path": str(full_path),
        "crop_path": str(crop_path),
        "enhanced_path": str(enhanced_path),
        "latest_crop": str(latest_crop),
        "note": "Open this crop with vision/OCR. Calibrated from visible HP text 1486/1492.",
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
