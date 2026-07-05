#!/usr/bin/env python
"""Streaming range scanner for rapidly changing visible values.

Unlike watch_value_range.py, this does NOT do one slow scan and then poll fixed
addresses. It repeatedly rescans memory for every numeric representation in a
range and tracks addresses that appear with multiple values over time.

Use for HP fluctuating 1485/1492:
    python scripts/stream_range_scan.py --process mt2portugalia --min-value 1470 --max-value 1492 --seconds 30

Read-only: VirtualQueryEx + ReadProcessMemory only.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import time
from collections import Counter, defaultdict
from pathlib import Path

from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory


def patterns(lo: int, hi: int, include_text: bool = False) -> list[tuple[str, int, bytes]]:
    pats: list[tuple[str, int, bytes]] = []
    for v in range(lo, hi + 1):
        pats.append(("i16", v, int(v).to_bytes(2, "little", signed=True)))
        pats.append(("u16", v, int(v).to_bytes(2, "little", signed=False)))
        pats.append(("i32", v, int(v).to_bytes(4, "little", signed=True)))
        pats.append(("u32", v, int(v).to_bytes(4, "little", signed=False)))
        pats.append(("f32", v, struct.pack("<f", float(v))))
        if include_text:
            txt = str(v)
            pats.append(("ascii", v, txt.encode("ascii")))
            pats.append(("utf16le", v, txt.encode("utf-16le")))
    return pats


def find_all(data: bytes, pat: bytes):
    start = 0
    while True:
        idx = data.find(pat, start)
        if idx == -1:
            return
        yield idx
        start = idx + 1


def scan_once(handle: int, regions: list[dict], pats: list[tuple[str, int, bytes]], *, chunk_size: int, max_region_mb: int) -> dict[tuple[int, str], int]:
    seen: dict[tuple[int, str], int] = {}
    max_region = max_region_mb * 1024 * 1024
    max_pat = max(len(p) for _typ, _v, p in pats)
    for region in regions:
        base = int(region["base"])
        size = min(int(region["size"]), max_region)
        prev_tail = b""
        for chunk_start in range(0, size, chunk_size):
            rs = min(chunk_size, size - chunk_start)
            raw = read_memory(handle, base + chunk_start, rs)
            if not raw:
                prev_tail = b""
                continue
            data = prev_tail + raw
            data_base = base + chunk_start - len(prev_tail)
            for typ, val, pat in pats:
                for off in find_all(data, pat):
                    addr = data_base + off
                    if addr >= base + chunk_start:  # ignore overlap duplicate
                        seen[(addr, typ)] = val
            prev_tail = raw[-(max_pat - 1):]
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description="Streaming ReadProcessMemory range scanner")
    ap.add_argument("--process", default="mt2portugalia")
    ap.add_argument("--min-value", type=int, required=True)
    ap.add_argument("--max-value", type=int, required=True)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--include-text", action="store_true")
    ap.add_argument("--chunk-size", type=int, default=1024 * 1024)
    ap.add_argument("--max-region-mb", type=int, default=64)
    ap.add_argument("--out", default="reports/stream_range_scan.json")
    ap.add_argument("--min-unique", type=int, default=2)
    args = ap.parse_args()

    pid = find_pid(args.process)
    if pid is None:
        print(f"Process not found: {args.process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print("OpenProcess failed. Try Administrator.")
        return 1

    try:
        regs = enumerate_regions(handle, max_region_mb=args.max_region_mb)
        pats = patterns(args.min_value, args.max_value, include_text=args.include_text)
        print(f"PID={pid}, regions={len(regs)}, patterns={len(pats)}, range=[{args.min_value},{args.max_value}]", flush=True)
        history: dict[tuple[int, str], list[int]] = defaultdict(list)
        counts: Counter[tuple[int, str]] = Counter()
        deadline = time.monotonic() + args.seconds
        iteration = 0
        while time.monotonic() < deadline:
            t0 = time.monotonic()
            seen = scan_once(handle, regs, pats, chunk_size=args.chunk_size, max_region_mb=args.max_region_mb)
            elapsed = time.monotonic() - t0
            for key, val in seen.items():
                counts[key] += 1
                if not history[key] or history[key][-1] != val:
                    history[key].append(val)
            movers = sum(1 for vals in history.values() if len(set(vals)) >= args.min_unique)
            print(f"iter={iteration} seen={len(seen)} elapsed={elapsed:.2f}s movers={movers}", flush=True)
            iteration += 1

        results = []
        for (addr, typ), vals in history.items():
            uniq = []
            for v in vals:
                if v not in uniq:
                    uniq.append(v)
            if len(uniq) >= args.min_unique:
                results.append({"address": addr, "type": typ, "values": uniq, "seen_count": counts[(addr, typ)]})
        results.sort(key=lambda r: (-len(r["values"]), -r["seen_count"], r["address"]))
        out = {
            "pid": pid,
            "range": [args.min_value, args.max_value],
            "iterations": iteration,
            "moving_candidates": results,
            "created_at": time.time(),
        }
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"Saved {out_path}; moving candidates={len(results)}")
        for r in results[:80]:
            vals = ",".join(str(v) for v in r["values"][:20])
            print(f"  0x{r['address']:08x} {r['type']} seen={r['seen_count']} values={vals}")
        return 0 if results else 2
    finally:
        close_handle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
