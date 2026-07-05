#!/usr/bin/env python
"""Read a small memory text window around a known address."""
from __future__ import annotations
import argparse, re
from refine_value import close_handle, find_pid, open_process, read_memory

COORD_RE = re.compile(r"\(?\s*(\d{2,4})\s*,\s*(\d{2,4})\s*\)?")

def printable(raw: bytes) -> str:
    return ''.join(chr(b) if 32 <= b < 127 or 160 <= b <= 255 else ' ' for b in raw)

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--process', default='pgclient.app')
    ap.add_argument('--address', action='append', required=True)
    ap.add_argument('--before', type=int, default=32)
    ap.add_argument('--size', type=int, default=192)
    args=ap.parse_args()
    pid=find_pid(args.process)
    h=open_process(pid)
    try:
        for a in args.address:
            addr=int(a,0); base=max(0, addr-args.before)
            raw=read_memory(h, base, args.size) or b''
            text=printable(raw)
            coords=COORD_RE.findall(text)
            print(f'base=0x{base:08x} addr=0x{addr:08x} coords={coords} text={text!r}')
    finally:
        close_handle(h)
    return 0
if __name__=='__main__':
    raise SystemExit(main())
