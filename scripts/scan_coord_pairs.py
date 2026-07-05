#!/usr/bin/env python
"""Find/read numeric coordinate-pair candidates in pgclient.app memory.

This is a deeper fallback when UI coordinate text mirrors become stale. It scans for
small numeric pairs equal to a known coordinate, then can re-read the same addresses
after movement to identify live fields.
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory


@dataclass(frozen=True)
class PairHit:
    x_addr: int
    y_addr: int
    x_type: str
    y_type: str
    x_value: int
    y_value: int
    gap: int
    region_type: str | None


def find_values(raw: bytes, base: int, target: int) -> list[tuple[int, str, int]]:
    out: list[tuple[int, str, int]] = []
    patterns = [
        ("i16", struct.pack("<h", target) if -32768 <= target <= 32767 else None, 2),
        ("u16", struct.pack("<H", target) if 0 <= target <= 65535 else None, 2),
        ("i32", struct.pack("<i", target), 4),
        ("u32", struct.pack("<I", target), 4),
    ]
    for typ, pat, size in patterns:
        if pat is None:
            continue
        start = 0
        while True:
            idx = raw.find(pat, start)
            if idx < 0:
                break
            out.append((base + idx, typ, size))
            start = idx + 1
    return out


def read_typed(handle: int, addr: int, typ: str) -> int | None:
    size = 2 if typ in {"i16", "u16"} else 4
    raw = read_memory(handle, addr, size)
    if not raw or len(raw) < size:
        return None
    try:
        if typ == "i16":
            return struct.unpack("<h", raw)[0]
        if typ == "u16":
            return struct.unpack("<H", raw)[0]
        if typ == "i32":
            return struct.unpack("<i", raw)[0]
        if typ == "u32":
            return struct.unpack("<I", raw)[0]
    except struct.error:
        return None
    return None


def scan_pairs(handle: int, x: int, y: int, max_gap: int, max_region_mb: int, chunk_size: int) -> list[PairHit]:
    hits: list[PairHit] = []
    for region in enumerate_regions(handle, max_region_mb=max_region_mb):
        if region.get("type") == "image":
            continue
        base = int(region["base"])
        size = min(int(region["size"]), max_region_mb * 1024 * 1024)
        overlap = max_gap + 8
        prev = b""
        prev_base = base
        for off in range(0, size, chunk_size):
            cur_base = base + off
            raw0 = read_memory(handle, cur_base, min(chunk_size, size - off))
            if not raw0:
                continue
            raw = prev + raw0
            raw_base = prev_base if prev else cur_base
            xs = find_values(raw, raw_base, x)
            ys = find_values(raw, raw_base, y)
            for xa, xt, _xsiz in xs:
                for ya, yt, _ysiz in ys:
                    gap = abs(ya - xa)
                    if gap <= max_gap and xa != ya:
                        hits.append(PairHit(xa, ya, xt, yt, x, y, gap, region.get("type")))
            if len(raw0) > overlap:
                prev = raw0[-overlap:]
                prev_base = cur_base + len(raw0) - overlap
            else:
                prev = raw0
                prev_base = cur_base
    # dedupe
    seen = set()
    out = []
    for h in hits:
        key = (h.x_addr, h.y_addr, h.x_type, h.y_type)
        if key not in seen:
            seen.add(key)
            out.append(h)
    out.sort(key=lambda h: (h.region_type != "private", h.gap, h.x_addr, h.y_addr))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("scan")
    sp.add_argument("--process", default="pgclient.app")
    sp.add_argument("--x", type=int, required=True)
    sp.add_argument("--y", type=int, required=True)
    sp.add_argument("--max-gap", type=int, default=64)
    sp.add_argument("--out", required=True)
    sp.add_argument("--show", type=int, default=50)
    sp.add_argument("--max-region-mb", type=int, default=64)
    sp.add_argument("--chunk-size", type=int, default=1024 * 1024)

    rp = sub.add_parser("read")
    rp.add_argument("--process", default="pgclient.app")
    rp.add_argument("--pairs", required=True)
    rp.add_argument("--show", type=int, default=100)
    rp.add_argument("--out")

    args = ap.parse_args()
    pid = find_pid(args.process)
    if pid is None:
        print(f"Process not found: {args.process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print(f"OpenProcess failed: {pid}")
        return 1
    try:
        if args.cmd == "scan":
            hits = scan_pairs(handle, args.x, args.y, args.max_gap, args.max_region_mb, args.chunk_size)
            payload = {"process": args.process, "pid": pid, "x": args.x, "y": args.y, "hits": [asdict(h) for h in hits]}
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"found {len(hits)} pair candidates for ({args.x},{args.y}); saved {args.out}")
            for h in hits[: args.show]:
                print(f"0x{h.x_addr:08x}/{h.x_type} 0x{h.y_addr:08x}/{h.y_type} gap={h.gap} [{h.region_type}]")
            return 0 if hits else 2
        payload = json.loads(Path(args.pairs).read_text(encoding="utf-8"))
        rows = []
        for h in payload.get("hits", []):
            xv = read_typed(handle, int(h["x_addr"]), h["x_type"])
            yv = read_typed(handle, int(h["y_addr"]), h["y_type"])
            row = dict(h)
            row["current_x"] = xv
            row["current_y"] = yv
            row["dx"] = None if xv is None else xv - int(h["x_value"])
            row["dy"] = None if yv is None else yv - int(h["y_value"])
            rows.append(row)
        rows.sort(key=lambda r: (r["current_x"] == r["x_value"] and r["current_y"] == r["y_value"], r.get("gap", 999), r.get("x_addr", 0)))
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps({"process": args.process, "pid": pid, "rows": rows}, indent=2), encoding="utf-8")
        changed = [r for r in rows if r["current_x"] != r["x_value"] or r["current_y"] != r["y_value"]]
        print(f"read {len(rows)} pairs; changed={len(changed)}")
        for r in rows[: args.show]:
            print(f"0x{r['x_addr']:08x}/{r['x_type']}={r['current_x']} 0x{r['y_addr']:08x}/{r['y_type']}={r['current_y']} old=({r['x_value']},{r['y_value']}) delta=({r['dx']},{r['dy']}) gap={r['gap']} [{r['region_type']}]")
        return 0
    finally:
        close_handle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
