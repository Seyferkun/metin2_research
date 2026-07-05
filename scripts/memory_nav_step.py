#!/usr/bin/env python
"""Memory-feedback coordinate navigation for the private Metin2 sandbox.

Reads coordinate text mirrors from pgclient.app memory, chooses the candidate that
looks most like the live player coordinate, sends scan-code movement, and repeats.
This does not use mouse steering or visual OCR for the control loop.
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

COORD_LINE_RE = re.compile(r"^0x(?P<addr>[0-9a-fA-F]+)\s+(?P<enc>\w+)\s+(?P<name>\w+)\((?P<x>\d+),\s*(?P<y>\d+)\)")
BEST_RE = re.compile(r"^coord=(?P<name>\w+)\((?P<x>\d+),\s*(?P<y>\d+)\)")


@dataclass(frozen=True)
class Coord:
    x: int
    y: int
    addr: int | None = None
    line: str = ""

    def dist(self, tx: int, ty: int) -> float:
        return math.hypot(self.x - tx, self.y - ty)


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=timeout)


def read_coords() -> tuple[Coord | None, list[Coord], str]:
    proc = run([sys.executable, "scripts/read_coords_memory_text.py", "--scan"], timeout=120)
    text = (proc.stdout or "") + (proc.stderr or "")
    coords: list[Coord] = []
    best: Coord | None = None
    for line in text.splitlines():
        m = BEST_RE.match(line.strip())
        if m:
            best = Coord(int(m.group("x")), int(m.group("y")), None, line)
            continue
        m = COORD_LINE_RE.match(line.strip())
        if m:
            coords.append(Coord(int(m.group("x")), int(m.group("y")), int(m.group("addr"), 16), line))
    return best, coords, text


def choose_live(coords: list[Coord], target_x: int, target_y: int, previous: Coord | None) -> Coord | None:
    if not coords:
        return previous
    # Prefer the dynamic UI/nameplate mirror observed in this session when present.
    dynamic = [c for c in coords if c.addr == 0x2D93F960]
    if dynamic:
        return dynamic[0]
    # If we have a previous live coordinate, choose the candidate closest to it, but not a known old stale line if alternatives exist.
    if previous:
        return min(coords, key=lambda c: (c.dist(previous.x, previous.y), c.dist(target_x, target_y)))
    # Otherwise choose the candidate closest to the target; stale mirrors far behind are less useful for steering.
    return min(coords, key=lambda c: c.dist(target_x, target_y))


def send_key(key: str, seconds: float) -> str:
    proc = run([sys.executable, "scripts/send_game_key.py", key, "--seconds", str(seconds)], timeout=max(20, int(seconds + 10)))
    return (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-x", type=int, required=True)
    ap.add_argument("--target-y", type=int, required=True)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--tolerance", type=float, default=5.0)
    ap.add_argument("--key", default="s", help="Movement key to use; for current camera S decreases both X/Y")
    ap.add_argument("--seconds", type=float, default=1.5)
    args = ap.parse_args()

    previous: Coord | None = None
    for i in range(args.steps + 1):
        _best, coords, raw = read_coords()
        current = choose_live(coords, args.target_x, args.target_y, previous)
        if current is None:
            print("No memory coordinate candidates found")
            print(raw)
            return 2
        dist = current.dist(args.target_x, args.target_y)
        print(f"step={i} memory_coord=({current.x},{current.y}) target=({args.target_x},{args.target_y}) dist={dist:.1f} addr={hex(current.addr) if current.addr else 'n/a'}")
        print(f"  source={current.line}")
        if dist <= args.tolerance:
            print("within tolerance; stopping")
            return 0
        if i == args.steps:
            break
        # With current camera, S has been observed to reduce both X and Y. If current is already below target on either axis, stop rather than overshoot blindly.
        if current.x <= args.target_x or current.y <= args.target_y:
            print("current coordinate is at/beyond target on one axis; stopping to avoid overshoot")
            return 0
        # Scale final approach down as we get close.
        seconds = min(args.seconds, max(0.35, dist / 8.0))
        print(f"  move key={args.key} seconds={seconds:.2f}")
        out = send_key(args.key, seconds)
        print("  input=" + out.strip().replace("\n", " | "))
        previous = current
        time.sleep(0.4)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
