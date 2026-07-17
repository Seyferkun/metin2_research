#!/usr/bin/env python
"""Screenshot-based target HP bar estimator for MT2Portugalia.

This is a read-only visual fallback for clients where Python target-board HP
callbacks do not expose HP in hermes_state.json. It detects the red filled part
of the top-center target HP bar and estimates percent from the calibrated target
bar width. No OCR dependency required.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def estimate_target_hp_from_image(image_path: str | Path) -> dict[str, Any]:
    path = Path(image_path)
    img = cv2.imread(str(path))
    if img is None:
        return {"available": False, "reason": f"image_not_readable:{path}"}
    h, w = img.shape[:2]
    # Target board sits top-center. Keep this broad enough for 16:9/windowed
    # captures while avoiding buff icons/minimap/UI red noise.
    x0, x1 = int(w * 0.30), int(w * 0.70)
    y0, y1 = int(h * 0.04), int(h * 0.13)
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return {"available": False, "reason": "empty_roi"}
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    # Red bar is vivid red; include wraparound hue ranges.
    mask = cv2.inRange(hsv, (0, 70, 70), (12, 255, 255)) | cv2.inRange(hsv, (170, 70, 70), (180, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 5), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        area = float(cv2.contourArea(c))
        global_x = x0 + x
        # The top target HP bar appears in the top-center/right target board;
        # red monster labels in the world appear lower/left and must be ignored.
        if global_x >= int(w * 0.48) and bw >= max(20, int(w * 0.015)) and 2 <= bh <= max(24, int(h * 0.03)):
            candidates.append((x, y, bw, bh, area))
    if not candidates:
        return {"available": False, "reason": "red_target_bar_not_found", "roi": [x0, y0, x1, y1]}
    x, y, bw, bh, area = max(candidates, key=lambda row: row[4])
    # Use red columns around the best component; anti-aliased red text can make
    # contours fragmented, but columns over the bar row are stable.
    full_guess = int(max(1.0, w * 0.11236))
    cx0 = max(0, x - 10)
    cx1 = min(mask.shape[1], x + full_guess + 35)
    row = mask[max(0, y - 3) : min(mask.shape[0], y + bh + 3), cx0:cx1]
    cols = np.where(row.max(axis=0) > 0)[0]
    if len(cols) == 0:
        red_width = bw
        red_min = x
        red_max = x + bw - 1
    else:
        red_min = int(cols.min()) + cx0
        red_max = int(cols.max()) + cx0
        red_width = red_max - red_min + 1
    # Calibrated from MT2Portugalia 1602x1031 screenshot: full target HP bar
    # is about 180 px wide => 0.11236 of window width. Scale by capture width.
    estimated_full_width = max(1.0, w * 0.11236)
    hp_pct = max(0.0, min(100.0, (float(red_width) / estimated_full_width) * 100.0))
    return {
        "available": True,
        "source": "target_bar_red_width",
        "hp_pct": round(hp_pct, 3),
        "red_width": int(red_width),
        "estimated_full_width": round(estimated_full_width, 3),
        "component_box_xywh": [int(x0 + red_min), int(y0 + y), int(red_width), int(bh)],
        "roi_xyxy": [x0, y0, x1, y1],
        "image_size": [int(w), int(h)],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    args = ap.parse_args(argv)
    print(json.dumps(estimate_target_hp_from_image(args.image), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
