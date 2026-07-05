#!/usr/bin/env python
"""Keyboard-only attack loop using memory-visible Metin label as alive signal.

Private sandbox only. Reads pgclient.app memory for dynamic visible-label text,
uses W to stay in contact and Space/1 to attack. No mouse input.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory

METIN_RE = re.compile(rb"Metin da Batalha")
COORD_RE = re.compile(rb"Metin da Batalha\(848,\s*662\)")


def send_key(key: str, seconds: float) -> str:
    proc = subprocess.run(
        [sys.executable, "scripts/send_game_key.py", key, "--seconds", str(seconds)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=max(20, int(seconds + 10)),
    )
    return (proc.stdout or "") + (proc.stderr or "")


def scan_alive(process: str, max_region_mb: int = 16) -> dict:
    pid = find_pid(process)
    if pid is None:
        return {"alive": False, "reason": "process_not_found", "hits": []}
    h = open_process(pid)
    if not h:
        return {"alive": False, "reason": "open_process_failed", "pid": pid, "hits": []}
    hits = []
    coord_hits = []
    try:
        for r in enumerate_regions(h, max_region_mb=max_region_mb):
            # Skip image/code; visible labels are in private/mapped heap buffers.
            if r.get("type") == "image":
                continue
            base = int(r["base"])
            size = min(int(r["size"]), max_region_mb * 1024 * 1024)
            step = 1024 * 1024
            for off in range(0, size, step):
                raw = read_memory(h, base + off, min(step, size - off))
                if not raw:
                    continue
                # Dynamic/visible-ish regions observed in this session are around 0x2e/0x31.
                for m in METIN_RE.finditer(raw):
                    addr = base + off + m.start()
                    if 0x2D000000 <= addr <= 0x32000000:
                        ctx = raw[max(0, m.start()-80):m.end()+80].decode("latin1", errors="replace")
                        hits.append({"address": f"0x{addr:08x}", "raw": ctx})
                for m in COORD_RE.finditer(raw):
                    addr = base + off + m.start()
                    ctx = raw[max(0, m.start()-80):m.end()+80].decode("latin1", errors="replace")
                    coord_hits.append({"address": f"0x{addr:08x}", "raw": ctx})
    finally:
        close_handle(h)
    # Alive if dynamic visible label exists; coordinate text alone can be stale/reused.
    return {"alive": bool(hits), "pid": pid, "hits": hits[:20], "coord_hits": coord_hits[:20]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="pgclient.app")
    ap.add_argument("--cycles", type=int, default=12)
    ap.add_argument("--out", default="reports/attack_until_metin_label_gone.jsonl")
    args = ap.parse_args()
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for cycle in range(1, args.cycles + 1):
            before = scan_alive(args.process)
            event = {"cycle": cycle, "before": before, "actions": []}
            print(json.dumps({"cycle": cycle, "alive_before": before.get("alive"), "hits": before.get("hits", [])[:3]}, ensure_ascii=False), flush=True)
            if not before.get("alive"):
                event["stop"] = "live_metin_label_absent_before_attack"
                f.write(json.dumps(event, ensure_ascii=False) + "\n"); f.flush()
                return 0
            # Move into/maintain contact, then attack. No target cycling and no mouse.
            event["actions"].append({"key": "w", "seconds": 1.0, "output": send_key("w", 1.0)})
            event["actions"].append({"key": "1", "seconds": 0.08, "output": send_key("1", 0.08)})
            event["actions"].append({"key": "space", "seconds": 12.0, "output": send_key("space", 12.0)})
            time.sleep(0.3)
            after = scan_alive(args.process)
            event["after"] = after
            print(json.dumps({"cycle": cycle, "alive_after": after.get("alive"), "hits": after.get("hits", [])[:3]}, ensure_ascii=False), flush=True)
            f.write(json.dumps(event, ensure_ascii=False) + "\n"); f.flush()
            if not after.get("alive"):
                return 0
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
