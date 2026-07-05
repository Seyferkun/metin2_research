#!/usr/bin/env python
"""Decode the visible label render queue around a known label address.

This extracts repeated records that look like:
  [metadata words][inline text]\0[padding]
from the currently observed label cluster. It is a stepping stone from UI labels
toward entity structs: the records expose visible labels, possible VID-like values,
and possible screen/depth metadata.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path
from refine_value import close_handle, find_pid, open_process, read_memory

TEXT_START_RE = re.compile(rb"[A-Za-z\xc0-\xff][A-Za-z\xc0-\xff0-9_ '(),\-]{3,80}\x00")


def printable(raw: bytes) -> str:
    return ''.join(chr(b) if 32 <= b < 127 or 160 <= b <= 255 else ' ' for b in raw)


def f32(raw: bytes, off: int) -> float | None:
    if off < 0 or off + 4 > len(raw):
        return None
    try:
        return struct.unpack_from('<f', raw, off)[0]
    except Exception:
        return None


def u32(raw: bytes, off: int) -> int | None:
    if off < 0 or off + 4 > len(raw):
        return None
    return struct.unpack_from('<I', raw, off)[0]


def decode_records(raw: bytes, base: int) -> list[dict]:
    records=[]
    seen=set()
    for m in TEXT_START_RE.finditer(raw):
        text=printable(m.group(0).rstrip(b'\x00')).strip()
        if len(text) < 4:
            continue
        # skip long unrelated strings; visible labels in this cluster are compact.
        if len(text) > 64:
            continue
        text_addr=base+m.start()
        if text_addr in seen:
            continue
        seen.add(text_addr)
        header_start=max(0,m.start()-32)
        header=raw[header_start:m.start()]
        fields=[]
        for off in range(header_start,m.start(),4):
            fields.append({
                'address': base+off,
                'u32': u32(raw, off),
                'f32': f32(raw, off),
            })
        records.append({
            'text_address': text_addr,
            'text': text,
            'header_start': base+header_start,
            'fields': fields,
            'raw_header_hex': header.hex(' '),
        })
    return records


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--process',default='pgclient.app')
    ap.add_argument('--base',default='0x2d93f880')
    ap.add_argument('--size',type=lambda s:int(s,0),default=0x400)
    ap.add_argument('--out',default='reports/entity_label_records.json')
    args=ap.parse_args()
    pid=find_pid(args.process)
    h=open_process(pid); base=int(args.base,0); raw=read_memory(h,base,args.size) or b''; close_handle(h)
    records=decode_records(raw,base)
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({'pid':pid,'base':base,'size':len(raw),'records':records},indent=2,ensure_ascii=False),encoding='utf-8')
    print(f'records={len(records)} out={out}')
    for r in records:
        print(f"TEXT 0x{r['text_address']:08x} {r['text']!r} header=0x{r['header_start']:08x}")
        vals=[]
        for f in r['fields']:
            fv=f['f32']
            vals.append(f"0x{f['u32']:08x}/{fv:.2f}" if fv is not None else f"0x{f['u32']:08x}")
        print('  ' + ' | '.join(vals))
    return 0

if __name__=='__main__': raise SystemExit(main())
