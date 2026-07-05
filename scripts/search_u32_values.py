#!/usr/bin/env python
"""Search process memory for exact 32-bit values and dump contexts."""
from __future__ import annotations
import argparse, json, struct
from pathlib import Path
from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory

def printable(raw):
    return ''.join(chr(b) if 32<=b<127 or 160<=b<=255 else ' ' for b in raw)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--process',default='pgclient.app'); ap.add_argument('--value',action='append',required=True); ap.add_argument('--out',default='reports/value_refs.json'); ap.add_argument('--context',type=int,default=96); ap.add_argument('--max-region-mb',type=int,default=64); ap.add_argument('--show',type=int,default=80)
    args=ap.parse_args(); values=[int(v,0) for v in args.value]
    pats={struct.pack('<I',v):v for v in values}
    pid=find_pid(args.process); h=open_process(pid); hits=[]
    try:
        for r in enumerate_regions(h,max_region_mb=args.max_region_mb):
            if r.get('type')=='image': continue
            base=int(r['base']); size=min(int(r['size']), args.max_region_mb*1024*1024); step=1024*1024; overlap=args.context+4; prev=b''; prev_base=base
            for off in range(0,size,step):
                chunk=read_memory(h,base+off,min(step,size-off))
                if not chunk: continue
                data=prev+chunk; data_base=prev_base
                for pat,val in pats.items():
                    start=0
                    while True:
                        i=data.find(pat,start)
                        if i<0: break
                        lo=max(0,i-args.context); hi=min(len(data),i+4+args.context)
                        hits.append({'address':data_base+i,'value':f'0x{val:08x}','region_base':base,'raw':printable(data[lo:hi])})
                        start=i+1
                if len(chunk)>overlap:
                    prev=chunk[-overlap:]; prev_base=base+off+len(chunk)-overlap
                else:
                    prev=chunk; prev_base=base+off
    finally: close_handle(h)
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps({'pid':pid,'hits':hits},indent=2,ensure_ascii=False),encoding='utf-8')
    print(f'hits={len(hits)} out={out}')
    for hit in hits[:args.show]: print(f"0x{hit['address']:08x} {hit['value']} raw={hit['raw']!r}")
    return 0
if __name__=='__main__': raise SystemExit(main())
