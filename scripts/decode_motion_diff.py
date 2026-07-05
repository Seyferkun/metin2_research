#!/usr/bin/env python
"""Decode numeric fields from changed memory chunks in a memory_state_diff report.

Input is reports produced by scripts/memory_state_diff.py compare. It inspects
before/after bytes for each changed span/chunk and lists numeric addresses whose
values changed plausibly for player movement.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import struct
from pathlib import Path
from typing import Any


def get_bytes(obj: dict[str, Any], *names: str) -> bytes | None:
    for name in names:
        v = obj.get(name)
        if isinstance(v, str):
            # Try base64, then hex.
            try:
                return base64.b64decode(v, validate=True)
            except Exception:
                pass
            try:
                return bytes.fromhex(v)
            except Exception:
                pass
        if isinstance(v, list):
            try:
                return bytes(int(x) & 0xFF for x in v)
            except Exception:
                pass
    return None


def decode(raw: bytes, off: int, typ: str) -> float | None:
    try:
        if typ == "i16":
            return float(struct.unpack_from("<h", raw, off)[0])
        if typ == "u16":
            return float(struct.unpack_from("<H", raw, off)[0])
        if typ == "i32":
            return float(struct.unpack_from("<i", raw, off)[0])
        if typ == "u32":
            return float(struct.unpack_from("<I", raw, off)[0])
        if typ == "f32":
            v = struct.unpack_from("<f", raw, off)[0]
            return float(v) if math.isfinite(v) else None
    except Exception:
        return None
    return None


def plausible(before: float, after: float, typ: str, min_delta: float, max_delta: float) -> bool:
    d = after - before
    if not (min_delta <= abs(d) <= max_delta):
        return False
    if typ == "f32":
        return abs(before) < 10_000_000 and abs(after) < 10_000_000
    return abs(before) < 100_000_000 and abs(after) < 100_000_000


def iter_change_entries(data: Any):
    if isinstance(data, dict):
        for key in ("changes", "changed_chunks", "chunks", "diffs", "interesting"):
            v = data.get(key)
            if isinstance(v, list):
                yield from v
        # Some reports may have nested regions.
        for v in data.values():
            if isinstance(v, (dict, list)):
                yield from iter_change_entries(v)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--show", type=int, default=100)
    ap.add_argument("--min-delta", type=float, default=0.01)
    ap.add_argument("--max-delta", type=float, default=10000)
    args = ap.parse_args()

    data = json.loads(Path(args.diff).read_text(encoding="utf-8"))
    rows = []
    for entry in iter_change_entries(data):
        # memory_state_diff.py format: each changed chunk has changed_spans with exact old/new hex.
        spans = entry.get("changed_spans")
        if isinstance(spans, list):
            for span in spans:
                addr = span.get("address")
                old_hex = span.get("old_hex")
                new_hex = span.get("new_hex")
                if addr is None or not isinstance(old_hex, str) or not isinstance(new_hex, str):
                    continue
                try:
                    base_i = int(addr, 0) if isinstance(addr, str) else int(addr)
                    before = bytes.fromhex(old_hex)
                    after = bytes.fromhex(new_hex)
                except Exception:
                    continue
                n = min(len(before), len(after))
                for off in range(0, max(0, n - 1), 1):
                    for typ in ("i16", "u16", "i32", "u32", "f32"):
                        size = 2 if typ in {"i16", "u16"} else 4
                        if off + size > n:
                            continue
                        b = decode(before, off, typ)
                        a = decode(after, off, typ)
                        if b is None or a is None or not plausible(b, a, typ, args.min_delta, args.max_delta):
                            continue
                        rows.append({
                            "address": base_i + off,
                            "typ": typ,
                            "before": b,
                            "after": a,
                            "delta": a - b,
                            "region_type": entry.get("region_type", entry.get("type")),
                        })
            continue

        base = entry.get("base", entry.get("address", entry.get("addr", entry.get("start"))))
        if base is None:
            continue
        try:
            base_i = int(base, 0) if isinstance(base, str) else int(base)
        except Exception:
            continue
        before = get_bytes(entry, "before", "old", "old_bytes", "before_bytes")
        after = get_bytes(entry, "after", "new", "new_bytes", "after_bytes")
        if not before or not after:
            continue
        n = min(len(before), len(after))
        for off in range(0, max(0, n - 4), 1):
            if before[off : off + 4] == after[off : off + 4]:
                continue
            for typ in ("i16", "u16", "i32", "u32", "f32"):
                size = 2 if typ in {"i16", "u16"} else 4
                if off + size > n:
                    continue
                b = decode(before, off, typ)
                a = decode(after, off, typ)
                if b is None or a is None or not plausible(b, a, typ, args.min_delta, args.max_delta):
                    continue
                rows.append({
                    "address": base_i + off,
                    "typ": typ,
                    "before": b,
                    "after": a,
                    "delta": a - b,
                    "region_type": entry.get("region_type", entry.get("type")),
                })
    # Prefer private, sane-size deltas, and floats/32-bit fields over noisy byte-shifted i16s.
    def rank(r):
        type_rank = {"f32": 0, "i32": 1, "u32": 2, "i16": 3, "u16": 4}.get(r["typ"], 9)
        return (r.get("region_type") != "private", type_rank, abs(abs(r["delta"]) - 10), r["address"])
    rows.sort(key=rank)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
    print(f"decoded changed numeric rows={len(rows)} saved {args.out}")
    for r in rows[: args.show]:
        print(f"0x{r['address']:08x}/{r['typ']} [{r['region_type']}] {r['before']:.6g}->{r['after']:.6g} d={r['delta']:.6g}")
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
