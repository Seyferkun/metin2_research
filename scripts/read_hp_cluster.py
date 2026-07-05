#!/usr/bin/env python
"""Read the currently suspected pgclient HP cluster."""

from __future__ import annotations

import argparse

from refine_value import close_handle, find_pid, open_process, read_memory

DEFAULT_BASE = 0x06C37B8E
DEFAULT_SIZE = 160
WATCH_ADDRS = {0x06C37BCE, 0x06C37BD2, 0x06C37BD6}
INTERESTING = {1491, 1492, 1493, 1731, 1732, 1733, 240}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="pgclient.app")
    ap.add_argument("--base", default=hex(DEFAULT_BASE))
    ap.add_argument("--size", type=int, default=DEFAULT_SIZE)
    args = ap.parse_args()

    base = int(str(args.base), 0)
    pid = find_pid(args.process)
    if pid is None:
        print(f"Process not found: {args.process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print(f"OpenProcess failed for pid={pid}")
        return 1
    try:
        raw = read_memory(handle, base, args.size)
    finally:
        close_handle(handle)

    print(f"process={args.process} pid={pid} base=0x{base:08x} len={len(raw)}")
    for off in range(0, max(0, len(raw) - 3), 2):
        addr = base + off
        u16 = int.from_bytes(raw[off : off + 2], "little")
        i16 = int.from_bytes(raw[off : off + 2], "little", signed=True)
        u32 = int.from_bytes(raw[off : off + 4], "little")
        mark = "<-- cluster" if addr in WATCH_ADDRS else ""
        if u16 in INTERESTING or u32 in INTERESTING or mark:
            print(f"0x{addr:08x} +{off:03d} u16={u16:5d} i16={i16:6d} u32={u32:10d} {mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
