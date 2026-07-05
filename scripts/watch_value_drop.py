#!/usr/bin/env python
"""Watch a refine_value snapshot for a visible numeric value dropping.

Example:
    python scripts/watch_value_drop.py --snapshot reports/hp_1492_snapshot.json --old-value 1492 --seconds 90

Useful for HP: take damage while it watches. It reports addresses whose former
value now became a plausible lower HP value.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from refine_value import find_pid, open_process, close_handle, read_as_type


def is_plausible_drop(value, old_value: int) -> bool:
    if isinstance(value, int):
        return 1 <= value < old_value
    if isinstance(value, float):
        return 1.0 <= value < float(old_value)
    if isinstance(value, str) and value.isdigit():
        return 1 <= int(value) < old_value
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Watch snapshot hits for a numeric drop")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--process", default="mt2portugalia")
    ap.add_argument("--old-value", type=int, required=True)
    ap.add_argument("--seconds", type=float, default=90)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--out", default="reports/hp_drop_candidates.json")
    args = ap.parse_args()

    snap_path = Path(args.snapshot)
    if not snap_path.exists():
        print(f"Missing snapshot: {snap_path}")
        return 1

    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    hits = snap.get("hits", [])
    pid = find_pid(args.process)
    if pid is None:
        print(f"Process not found: {args.process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print("OpenProcess failed. Try running as Administrator.")
        return 1

    print(f"WATCHING {len(hits)} candidates from {snap_path}")
    print(f"Old visible value: {args.old_value}. Let a mob hit you now.", flush=True)

    deadline = time.monotonic() + args.seconds
    tick = 0
    try:
        while time.monotonic() < deadline:
            changed = []
            for hit in hits:
                typ = hit["type"]
                addr = int(hit["address"])
                val = read_as_type(handle, addr, typ)
                old = str(args.old_value) if typ in ("ascii", "utf16le") else args.old_value
                if val == old:
                    continue
                if is_plausible_drop(val, args.old_value):
                    changed.append({**hit, "new_value": val})

            if changed:
                out = {
                    "snapshot": str(snap_path),
                    "old_value": args.old_value,
                    "pid": pid,
                    "matches": changed,
                    "time": time.time(),
                }
                out_path = Path(args.out)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
                print(f"FOUND {len(changed)} plausible current-HP drops. Saved {out_path}")
                for hit in changed[:40]:
                    print(
                        f"  0x{int(hit['address']):08x} {hit['type']} "
                        f"{args.old_value} -> {hit['new_value']} [{hit.get('region_type')}]"
                    )
                return 0

            if tick % max(1, int(10 / args.interval)) == 0:
                elapsed = int(args.seconds - (deadline - time.monotonic()))
                print(f"  still watching... {elapsed}s elapsed", flush=True)
            tick += 1
            time.sleep(args.interval)

        print("Timed out: no candidate dropped. Either no damage happened, or current HP is stored in a different representation/region.")
        return 2
    finally:
        close_handle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
