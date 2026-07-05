#!/usr/bin/env python
"""Fast exact memory search for local coordinate byte patterns."""
from __future__ import annotations

import argparse
from pathlib import Path
import json
from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory


def printable(raw: bytes) -> str:
    return ''.join(chr(b) if 32 <= b < 127 or 160 <= b <= 255 else ' ' for b in raw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--process', default='pgclient.app')
    ap.add_argument('--pattern', action='append', required=True, help='ASCII pattern to find, repeatable')
    ap.add_argument('--out')
    ap.add_argument('--context', type=int, default=96)
    ap.add_argument('--max-region-mb', type=int, default=64)
    args = ap.parse_args()
    pats = [p.encode('latin1') for p in args.pattern]
    pid = find_pid(args.process)
    if pid is None:
        print('process not found')
        return 1
    h = open_process(pid)
    hits=[]
    try:
        for r in enumerate_regions(h, max_region_mb=args.max_region_mb):
            if r.get('type') == 'image':
                continue
            base=int(r['base']); size=min(int(r['size']), args.max_region_mb*1024*1024)
            step=1024*1024
            overlap=max(len(p) for p in pats)+args.context
            prev=b''; prev_base=base
            for off in range(0,size,step):
                chunk=read_memory(h, base+off, min(step,size-off))
                if not chunk: continue
                data=prev+chunk; data_base=prev_base
                for pat in pats:
                    start=0
                    while True:
                        i=data.find(pat,start)
                        if i<0: break
                        addr=data_base+i
                        lo=max(0,i-args.context); hi=min(len(data),i+len(pat)+args.context)
                        hits.append({'address':addr,'pattern':pat.decode('latin1'),'region_type':r.get('type'),'raw':printable(data[lo:hi])})
                        start=i+1
                if len(chunk)>overlap:
                    prev=chunk[-overlap:]; prev_base=base+off+len(chunk)-overlap
                else:
                    prev=chunk; prev_base=base+off
    finally:
        close_handle(h)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({'hits':hits},indent=2),encoding='utf-8')
    print(f'hits={len(hits)}')
    for hit in hits[:100]:
        print(f"0x{hit['address']:08x} {hit['pattern']} [{hit['region_type']}] raw={hit['raw']!r}")
    return 0 if hits else 2

if __name__ == '__main__':
    raise SystemExit(main())
