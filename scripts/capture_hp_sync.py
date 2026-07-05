#!/usr/bin/env python
"""Capture visible HP crop and selected memory candidates at the same instant."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from PIL import ImageGrab

from metin2_research.window_capture import activate_window, find_window
from refine_value import close_handle, find_pid, open_process, read_as_type
from capture_hp_panel import enhance_for_digits, scaled_rect


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture HP crop and memory values together")
    ap.add_argument("--process", default="mt2portugalia")
    ap.add_argument("--query", default="MT2Portugalia")
    ap.add_argument("--addr", action="append", default=["0x00d17bfa"], help="candidate address, repeatable")
    ap.add_argument("--out-dir", default="reports/hp_sync")
    ap.add_argument("--rect", default="208,466,130,38")
    args = ap.parse_args()

    pid = find_pid(args.process)
    if pid is None:
        print(f"Process not found: {args.process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print("OpenProcess failed")
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        window = find_window(args.query)
        activate_window(window)
        values = []
        # Read memory immediately before and after capture to estimate race.
        before = time.time()
        for raw in args.addr:
            addr = int(raw, 0)
            values.append({
                "address": f"0x{addr:08x}",
                "before": {typ: read_as_type(handle, addr, typ) for typ in ["i16", "u16", "i32", "u32", "f32"]},
            })

        full = ImageGrab.grab(bbox=window.bbox).convert("RGB")
        x, y, w, h = [int(v.strip()) for v in args.rect.split(",")]
        crop_box = scaled_rect((x, y, w, h), full.size)
        crop = full.crop(crop_box)
        enhanced = enhance_for_digits(crop)

        after = time.time()
        for item in values:
            addr = int(item["address"], 0)
            item["after"] = {typ: read_as_type(handle, addr, typ) for typ in ["i16", "u16", "i32", "u32", "f32"]}

        ts = time.strftime("%Y%m%d_%H%M%S")
        crop_path = out_dir / f"sync_hp_crop_{ts}.png"
        enhanced_path = out_dir / f"sync_hp_crop_enhanced_{ts}.png"
        meta_path = out_dir / f"sync_{ts}.json"
        crop.save(crop_path)
        enhanced.save(enhanced_path)
        meta = {
            "pid": pid,
            "window": window.__dict__,
            "capture_size": full.size,
            "crop_box_xyxy": crop_box,
            "timestamp_before": before,
            "timestamp_after": after,
            "memory_values": values,
            "crop_path": str(crop_path),
            "enhanced_path": str(enhanced_path),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(json.dumps(meta, indent=2))
        return 0
    finally:
        close_handle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
