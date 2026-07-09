#!/usr/bin/env python
"""Passive screenshot monitor for Chefe Orc runs when JSON target state is stale.

Observation-only: scans recorder screenshots for a visible top-center target HP bar.
It does not send input and does not claim boss identity without JSON/text evidence.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from PIL import Image


def read_target_hp_percent(image_path: Path) -> float | None:
    """Estimate selected target HP from the top-center red target bar.

    This detects a selected-target bar, not the target name. It is useful fallback
    evidence when hermes_state.json is stale, but it cannot by itself prove the
    target is Chefe Orc.
    """
    try:
        with Image.open(image_path).convert("RGB") as img:
            w, _h = img.size
            crop = img.crop((int(w * 0.36), 48, int(w * 0.64), 105))
            total = crop.width * crop.height
            dark = sum(1 for r, g, b in crop.getdata() if r < 85 and g < 85 and b < 85)
            if dark / max(1, total) < 0.18:
                return None
            red_cols: list[int] = []
            for x in range(crop.width):
                count = 0
                for y in range(crop.height):
                    r, g, b = crop.getpixel((x, y))
                    if r > 115 and g < 80 and b < 80:
                        count += 1
                if count >= 2:
                    red_cols.append(x)
            if len(red_cols) < 8:
                return None
            span = max(red_cols) - min(red_cols) + 1
            return round(max(0.0, min(100.0, 100.0 * span / 285.0)), 2)
    except Exception:
        return None


def summarize(run_dir: Path, *, last_n: int | None = None) -> dict[str, Any]:
    shots = sorted((run_dir / "screenshots").glob("*.jpg"))
    if last_n:
        shots = shots[-last_n:]
    hits = []
    for p in shots:
        hp = read_target_hp_percent(p)
        if hp is not None:
            hits.append({"frame": p.name, "path": str(p), "hp_pct_estimate": hp})
    drops_to_zero = 0
    prev = None
    for h in hits:
        hp = h["hp_pct_estimate"]
        if prev is not None and prev > 5 and hp <= 2:
            drops_to_zero += 1
        prev = hp
    return {
        "run_dir": str(run_dir),
        "screenshots_scanned": len(shots),
        "visual_target_bar_samples": len(hits),
        "first_visual_target_bars": hits[:5],
        "last_visual_target_bars": hits[-5:],
        "visual_hp_drop_to_zero_candidates": drops_to_zero,
        "note": "Visual target-bar detector cannot prove target name; combine with JSON target or visible OCR/manual review.",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--duration", type=float, default=2100.0)
    ap.add_argument("--last-n", type=int, default=400)
    args = ap.parse_args()
    run_dir = Path(args.run_dir)
    out = run_dir / "chefe_orc_visual_monitor_summary.json"
    start = time.time()
    while time.time() - start < args.duration:
        s = summarize(run_dir, last_n=max(1, args.last_n))
        s["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        out.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"updated_at": s["updated_at"], "screenshots_scanned": s["screenshots_scanned"], "visual_target_bar_samples": s["visual_target_bar_samples"], "drops_to_zero": s["visual_hp_drop_to_zero_candidates"]}), flush=True)
        time.sleep(max(1.0, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
