#!/usr/bin/env python
"""Dump/decode a memory cluster around an entity label text address."""
from __future__ import annotations
import argparse, json, re, struct
from pathlib import Path
from refine_value import close_handle, find_pid, open_process, read_memory

TEXT_RE = re.compile(r"[A-Za-zÀ-ÿ0-9_ '\-]{3,60}(?:\(\s*\d{1,4}\s*,\s*\d{1,4}\s*\))?")

def printable(raw: bytes) -> str:
    return ''.join(chr(b) if 32 <= b < 127 or 160 <= b <= 255 else ' ' for b in raw)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--process',default='pgclient.app')
    ap.add_argument('--address',required=True)
    ap.add_argument('--before',type=int,default=8192)
    ap.add_argument('--after',type=int,default=8192)
    ap.add_argument('--out',default='reports/entity_label_cluster_dump.json')
    args=ap.parse_args()
    addr=int(args.address,0)
    pid=find_pid(args.process)
    if pid is None:
        print('process not found'); return 1
    h=open_process(pid)
    base=max(0,addr-args.before); size=args.before+args.after
    raw=read_memory(h,base,size) or b''
    close_handle(h)
    text=printable(raw)
    strings=[]
    for m in TEXT_RE.finditer(text):
        s=' '.join(m.group(0).split())
        if len(s)>=3 and not s.isdigit():
            strings.append({'address':base+m.start(),'text':s})
    nums=[]
    for off in range(0,len(raw)-4,2):
        u16=struct.unpack_from('<H',raw,off)[0]
        if 0 <= u16 <= 1200:
            nums.append({'address':base+off,'type':'u16','value':u16})
        i16=struct.unpack_from('<h',raw,off)[0]
        if -1200 <= i16 <= 1200 and i16 != u16:
            nums.append({'address':base+off,'type':'i16','value':i16})
        if off%4==0:
            u32=struct.unpack_from('<I',raw,off)[0]
            if 0 <= u32 <= 1200:
                nums.append({'address':base+off,'type':'u32','value':u32})
    result={'pid':pid,'base':base,'target_address':addr,'size':len(raw),'strings':strings[:500],'small_numbers':nums[:1000],'printable':text}
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(f'base=0x{base:08x} size={len(raw)} strings={len(strings)} nums={len(nums)} out={out}')
    for s in strings[:120]: print(f"0x{s['address']:08x} {s['text']}")
    return 0
if __name__=='__main__': raise SystemExit(main())
