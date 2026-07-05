#!/usr/bin/env python
"""Scan pgclient.app for coordinate-bearing labels of any name, not just Metins."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory

COORD_RE = re.compile(r"(?P<name>[A-Za-zÀ-ÿ0-9_ '\-]{2,48})\(\s*(?P<x>\d{1,4})\s*,\s*(?P<y>\d{1,4})\s*\)")

@dataclass
class CoordLabel:
    address: int
    name: str
    x: int
    y: int
    raw: str
    region_type: str


def printable(raw: bytes) -> str:
    return ''.join(chr(b) if 32 <= b < 127 or 160 <= b <= 255 else ' ' for b in raw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--process', default='pgclient.app')
    ap.add_argument('--out', default='reports/all_coord_labels.json')
    ap.add_argument('--max-region-mb', type=int, default=64)
    ap.add_argument('--context', type=int, default=96)
    ap.add_argument('--show', type=int, default=100)
    args = ap.parse_args()
    pid = find_pid(args.process)
    if pid is None:
        print('process not found')
        return 1
    h = open_process(pid)
    rows: list[CoordLabel] = []
    seen=set()
    try:
        for r in enumerate_regions(h, max_region_mb=args.max_region_mb):
            if r.get('type') == 'image':
                continue
            base=int(r['base']); size=min(int(r['size']), args.max_region_mb*1024*1024)
            step=1024*1024; overlap=args.context+128
            prev=b''; prev_base=base
            for off in range(0, size, step):
                chunk=read_memory(h, base+off, min(step, size-off))
                if not chunk: continue
                data=prev+chunk; data_base=prev_base
                if b'(' not in data or b')' not in data:
                    if len(chunk)>overlap:
                        prev=chunk[-overlap:]; prev_base=base+off+len(chunk)-overlap
                    else:
                        prev=chunk; prev_base=base+off
                    continue
                text=printable(data)
                for m in COORD_RE.finditer(text):
                    name=' '.join(m.group('name').split()).strip(' ,.;:')
                    if len(name) < 2 or name.isdigit():
                        continue
                    x=int(m.group('x')); y=int(m.group('y'))
                    # Keep plausible in-map coordinates only.
                    if not (0 <= x <= 1200 and 0 <= y <= 1200):
                        continue
                    addr=data_base+m.start()
                    key=(name.lower(),x,y,addr)
                    if key in seen: continue
                    seen.add(key)
                    lo=max(0,m.start()-args.context); hi=min(len(data),m.end()+args.context)
                    rows.append(CoordLabel(addr,name,x,y,printable(data[lo:hi]),r.get('type','')))
                if len(chunk)>overlap:
                    prev=chunk[-overlap:]; prev_base=base+off+len(chunk)-overlap
                else:
                    prev=chunk; prev_base=base+off
    finally:
        close_handle(h)
    rows.sort(key=lambda row: (row.name.lower(), row.x, row.y, row.address))
    out=Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'pid':pid,'labels':[asdict(r) for r in rows]},indent=2,ensure_ascii=False),encoding='utf-8')
    print(f'labels={len(rows)} out={out}')
    for row in rows[:args.show]:
        print(f"0x{row.address:08x} {row.name}({row.x},{row.y}) raw={row.raw[:180]!r}")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
