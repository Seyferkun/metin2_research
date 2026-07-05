#!/usr/bin/env python
"""Find memory addresses that fluctuate inside a visible numeric range.

For HP discovery when visible HP is actively changing, e.g. 1485/1492:

    python scripts/watch_value_range.py --process mt2portugalia --min-value 1480 --max-value 1492 --seconds 45

It scans readable memory for int16/u16/int32/u32/float32 values in range, then
polls those addresses and reports candidates whose values change while staying
inside the range.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import time
from collections import defaultdict
from pathlib import Path

from refine_value import (
    close_handle,
    enumerate_regions,
    find_pid,
    open_process,
    read_as_type,
    read_memory,
)

NUMERIC_TYPES = ("i16", "u16", "i32", "u32", "f32")


def decode_at(data: bytes, off: int, typ: str):
    try:
        if typ == "i16" and off + 2 <= len(data):
            return int.from_bytes(data[off:off+2], "little", signed=True)
        if typ == "u16" and off + 2 <= len(data):
            return int.from_bytes(data[off:off+2], "little", signed=False)
        if typ == "i32" and off + 4 <= len(data):
            return int.from_bytes(data[off:off+4], "little", signed=True)
        if typ == "u32" and off + 4 <= len(data):
            return int.from_bytes(data[off:off+4], "little", signed=False)
        if typ == "f32" and off + 4 <= len(data):
            v = struct.unpack("<f", data[off:off+4])[0]
            return v if math.isfinite(v) else None
    except Exception:
        return None
    return None


def in_range(value, lo: int, hi: int) -> bool:
    if isinstance(value, float):
        return float(lo) <= value <= float(hi)
    if isinstance(value, int):
        return lo <= value <= hi
    return False


def scan_range(
    handle: int,
    regions: list[dict],
    lo: int,
    hi: int,
    *,
    chunk_size: int = 1024 * 1024,
    stride: int = 4,
) -> list[dict]:
    hits = []
    for region in regions:
        base = int(region["base"])
        size = int(region["size"])
        for chunk_start in range(0, size, chunk_size):
            rs = min(chunk_size, size - chunk_start)
            data = read_memory(handle, base + chunk_start, rs)
            if not data:
                continue
            for off in range(0, max(0, rs - 4), stride):
                for typ in NUMERIC_TYPES:
                    val = decode_at(data, off, typ)
                    if in_range(val, lo, hi):
                        hits.append({
                            "address": base + chunk_start + off,
                            "type": typ,
                            "initial": val,
                            "region_type": region.get("type", "unknown"),
                        })
    # De-duplicate exact addr/type and cap pathological duplicates
    seen = set()
    deduped = []
    for h in hits:
        key = (h["address"], h["type"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
    return deduped


def main() -> int:
    ap = argparse.ArgumentParser(description="Watch memory values that fluctuate inside a numeric range")
    ap.add_argument("--process", default="mt2portugalia")
    ap.add_argument("--min-value", type=int, required=True)
    ap.add_argument("--max-value", type=int, required=True)
    ap.add_argument("--seconds", type=float, default=45)
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--out", default="reports/hp_range_fluctuating_candidates.json")
    ap.add_argument("--max-candidates", type=int, default=200000)
    ap.add_argument("--stride", type=int, default=4, help="Initial scan byte stride: 4=aligned fast, 1=slow unaligned")
    args = ap.parse_args()

    pid = find_pid(args.process)
    if pid is None:
        print(f"Process not found: {args.process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print("OpenProcess failed. Try running as Administrator.")
        return 1

    try:
        print(f"Scanning for values in [{args.min_value}, {args.max_value}]...", flush=True)
        regions = enumerate_regions(handle)
        t0 = time.monotonic()
        hits = scan_range(handle, regions, args.min_value, args.max_value, stride=args.stride)
        scan_elapsed = time.monotonic() - t0
        print(f"Initial scan found {len(hits)} addr/type candidates in {scan_elapsed:.1f}s")
        by_type = defaultdict(int)
        for h in hits:
            by_type[h["type"]] += 1
        print("By type:", ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
        if len(hits) > args.max_candidates:
            print(f"Too many candidates ({len(hits)}). Narrow the range or raise --max-candidates.")
            return 2

        history = { (h["address"], h["type"]): [h["initial"]] for h in hits }
        hit_map = { (h["address"], h["type"]): h for h in hits }
        deadline = time.monotonic() + args.seconds
        tick = 0
        print("Polling candidates. Keep the dogs hitting you now.", flush=True)
        while time.monotonic() < deadline:
            for key in list(history.keys()):
                addr, typ = key
                val = read_as_type(handle, addr, typ)
                if in_range(val, args.min_value, args.max_value):
                    if val != history[key][-1]:
                        history[key].append(val)
            if tick % max(1, int(5 / args.interval)) == 0:
                moving = sum(1 for vals in history.values() if len(set(vals)) >= 2)
                print(f"  {int(args.seconds - (deadline - time.monotonic()))}s elapsed; moving candidates={moving}", flush=True)
            tick += 1
            time.sleep(args.interval)

        movers = []
        for key, vals in history.items():
            uniq = []
            for v in vals:
                if v not in uniq:
                    uniq.append(v)
            if len(uniq) >= 2:
                h = hit_map[key]
                movers.append({**h, "values": uniq, "changes": len(vals) - 1, "unique_count": len(uniq)})
        movers.sort(key=lambda h: (-h["unique_count"], -h["changes"], h["address"]))

        out = {
            "pid": pid,
            "range": [args.min_value, args.max_value],
            "initial_candidates": len(hits),
            "moving_candidates": movers,
            "created_at": time.time(),
        }
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

        print(f"Found {len(movers)} candidates that changed inside range. Saved {out_path}")
        for h in movers[:50]:
            vals = ",".join(str(v) for v in h["values"][:12])
            print(f"  0x{int(h['address']):08x} {h['type']} [{h['region_type']}] values={vals}")
        return 0 if movers else 3
    finally:
        close_handle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
