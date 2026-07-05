#!/usr/bin/env python
"""Read selected motion-coordinate candidate addresses from process memory."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from refine_value import close_handle, find_pid, open_process, read_memory


def read_typed(handle: int, address: int, typ: str):
    size = 2 if typ in {"i16", "u16"} else 4
    raw = read_memory(handle, address, size)
    if not raw or len(raw) < size:
        return None
    if typ == "i16":
        return struct.unpack("<h", raw)[0]
    if typ == "u16":
        return struct.unpack("<H", raw)[0]
    if typ == "i32":
        return struct.unpack("<i", raw)[0]
    if typ == "u32":
        return struct.unpack("<I", raw)[0]
    if typ == "f32":
        return struct.unpack("<f", raw)[0]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--process", default="pgclient.app")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--out")
    args = ap.parse_args()
    data = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    candidates = data.get("rows", [])[: args.limit]
    pid = find_pid(args.process)
    if pid is None:
        print(f"Process not found: {args.process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print(f"OpenProcess failed: {pid}")
        return 1
    rows = []
    try:
        for c in candidates:
            addr = int(c["address"])
            typ = c["typ"]
            val = read_typed(handle, addr, typ)
            row = dict(c)
            row["current"] = val
            rows.append(row)
    finally:
        close_handle(handle)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
    for r in rows:
        print(f"0x{r['address']:08x}/{r['typ']} current={r['current']} p0={r.get('p0')} s1={r.get('s1')} w1={r.get('w1')} ds={r.get('delta_s')} dw={r.get('delta_w')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
