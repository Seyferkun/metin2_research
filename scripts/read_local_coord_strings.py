#!/usr/bin/env python
"""Scan pgclient.app memory for local map coordinate strings near a target area.

This is intended for memory-only navigation debugging: it prints coordinate-like
ASCII/UTF-16 strings around a specified box, including unnamed fragments like
"(313, 217)" and named entities like "Metin da Batalha(284, 218)".
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory

COORD_RE = re.compile(r"(?P<name>[A-Za-zÀ-ÿ0-9_ .:'`-]{0,48})\(\s*(?P<x>\d{2,4})\s*,\s*(?P<y>\d{2,4})\s*\)")

@dataclass
class Hit:
    address: int
    encoding: str
    name: str
    x: int
    y: int
    dist_to_start: float
    dist_to_target: float
    raw: str


def clean(s: str) -> str:
    return ''.join(ch if 32 <= ord(ch) < 127 or 160 <= ord(ch) <= 255 else ' ' for ch in s)


def scan_text(raw: bytes, base: int, encoding: str, sx: int, sy: int, tx: int, ty: int, radius: int) -> list[Hit]:
    if encoding == 'ascii':
        text = clean(raw.decode('latin1', errors='ignore'))
        scale = 1
    else:
        text = clean(raw.decode('utf-16le', errors='ignore'))
        scale = 2
    hits = []
    for m in COORD_RE.finditer(text):
        x = int(m.group('x')); y = int(m.group('y'))
        if not (min(sx, tx) - radius <= x <= max(sx, tx) + radius and min(sy, ty) - radius <= y <= max(sy, ty) + radius):
            continue
        start = max(0, m.start() - 60); end = min(len(text), m.end() + 60)
        name = m.group('name').strip()
        hits.append(Hit(
            address=base + m.start() * scale,
            encoding=encoding,
            name=name,
            x=x,
            y=y,
            dist_to_start=((x-sx)**2 + (y-sy)**2) ** 0.5,
            dist_to_target=((x-tx)**2 + (y-ty)**2) ** 0.5,
            raw=text[start:end].strip(),
        ))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--process', default='pgclient.app')
    ap.add_argument('--start-x', type=int, required=True)
    ap.add_argument('--start-y', type=int, required=True)
    ap.add_argument('--target-x', type=int, required=True)
    ap.add_argument('--target-y', type=int, required=True)
    ap.add_argument('--radius', type=int, default=80)
    ap.add_argument('--out')
    ap.add_argument('--show', type=int, default=80)
    ap.add_argument('--ascii-only', action='store_true')
    ap.add_argument('--max-region-mb', type=int, default=16)
    args = ap.parse_args()
    pid = find_pid(args.process)
    if pid is None:
        print(f'process not found: {args.process}')
        return 1
    h = open_process(pid)
    if not h:
        print(f'OpenProcess failed: {pid}')
        return 1
    hits: list[Hit] = []
    try:
        for r in enumerate_regions(h, max_region_mb=args.max_region_mb):
            if r.get('type') == 'image':
                continue
            base = int(r['base']); size = min(int(r['size']), args.max_region_mb*1024*1024)
            step = 1024 * 1024
            for off in range(0, size, step):
                raw = read_memory(h, base + off, min(step, size - off))
                if not raw:
                    continue
                hits.extend(scan_text(raw, base + off, 'ascii', args.start_x, args.start_y, args.target_x, args.target_y, args.radius))
                if not args.ascii_only:
                    hits.extend(scan_text(raw, base + off, 'utf16le', args.start_x, args.start_y, args.target_x, args.target_y, args.radius))
    finally:
        close_handle(h)
    # rank: closest to start first, then target, prefer player-ish unnamed/Yoshypt but keep named target visible
    def rank(hit: Hit):
        lname = hit.name.lower()
        player_bonus = 0 if ('yoshy' in lname or hit.dist_to_start <= 3) else 1
        return (player_bonus, hit.dist_to_start, hit.dist_to_target, hit.address)
    hits.sort(key=rank)
    # dedupe by address/name/xy
    dedup=[]; seen=set()
    for hit in hits:
        key=(hit.address, hit.encoding, hit.name, hit.x, hit.y)
        if key in seen: continue
        seen.add(key); dedup.append(hit)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({'hits':[asdict(h) for h in dedup]}, indent=2), encoding='utf-8')
    print(f'hits={len(dedup)} pid={pid}')
    for hit in dedup[:args.show]:
        nm = hit.name if hit.name else '<unnamed>'
        print(f"0x{hit.address:08x} {hit.encoding} {nm}({hit.x},{hit.y}) d_start={hit.dist_to_start:.1f} d_target={hit.dist_to_target:.1f} raw={hit.raw!r}")
    return 0 if dedup else 2

if __name__ == '__main__':
    raise SystemExit(main())
