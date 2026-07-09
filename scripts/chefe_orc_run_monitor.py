#!/usr/bin/env python
"""Passive monitor for Chefe Orc spawn test runs.

Observation-only: reads recorder events/state files and writes progress artifacts. Sends no keys/clicks.
"""
from __future__ import annotations

import argparse, json, time
from pathlib import Path
from collections import Counter


def load_jsonl(path: Path):
    if not path.exists():
        return []
    rows=[]
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        if line.strip():
            try: rows.append(json.loads(line))
            except Exception: pass
    return rows


def cofre_count(state: dict):
    for item in state.get('inventory') or []:
        if not isinstance(item, dict):
            continue
        if str(item.get('vnum')) == '50070' or str(item.get('name')) == 'Cofre do Chefe Orc':
            try: return int(item.get('count') or 0)
            except Exception: return None
    return None


def summarize(run_dir: Path) -> dict:
    rows=load_jsonl(run_dir/'events.jsonl')
    samples=[r for r in rows if r.get('type')=='sample']
    targets=[]; names=Counter(); sources=Counter(); stale=0; screenshots=0
    loot=[]; prev=None
    for r in samples:
        st=r.get('state') or {}
        age=None
        try:
            m=st.get('_file_mtime')
            # recorder snapshots keep _file_mtime but not current wall time; stale handled by age in state if present
            age=st.get('state_age_seconds')
        except Exception: pass
        if st.get('available') is False: stale += 1
        if r.get('screenshot'): screenshots += 1
        t=st.get('target')
        if isinstance(t, dict) and (t.get('vid') or t.get('name')):
            rec={'t':r.get('t'), 'vid':t.get('vid'), 'name':t.get('name'), 'source':t.get('source') or t.get('target_source'), 'hp_pct':t.get('hp_pct'), 'hp':t.get('hp'), 'max_hp':t.get('max_hp'), 'alive':t.get('alive')}
            targets.append(rec); names[str(rec['name'])]+=1; sources[str(rec['source'])]+=1
        c=cofre_count(st)
        if c is not None:
            if prev is None or c != prev:
                loot.append({'t':r.get('t'), 'count':c, 'delta':None if prev is None else c-prev})
            prev=c
    chefe=[t for t in targets if 'chefe orc' in str(t.get('name') or '').casefold()]
    kills=[]
    cur=None
    for t in targets:
        is_boss='chefe orc' in str(t.get('name') or '').casefold()
        if is_boss and (cur is None or cur.get('vid')!=t.get('vid')):
            if cur: kills.append(cur)
            cur={'vid':t.get('vid'),'start':t.get('t'),'end':t.get('t'),'first_hp_pct':t.get('hp_pct'),'last_hp_pct':t.get('hp_pct'),'source':t.get('source')}
        elif is_boss and cur:
            cur['end']=t.get('t'); cur['last_hp_pct']=t.get('hp_pct')
        elif cur and not is_boss:
            kills.append(cur); cur=None
    if cur: kills.append(cur)
    confirmed_hp_zero=sum(1 for k in kills if k.get('last_hp_pct') in (0,0.0))
    loot_delta=sum(x['delta'] for x in loot if x.get('delta') and x['delta']>0)
    return {'run_dir':str(run_dir),'samples':len(samples),'screenshots':screenshots,'target_samples':len(targets),'target_names':dict(names),'target_sources':dict(sources),'chefe_orc_samples':len(chefe),'chefe_orc_segments':kills,'chefe_orc_hp_zero_segments':confirmed_hp_zero,'loot_count_changes':loot,'loot_positive_delta':loot_delta,'estimated_boss_kills':max(confirmed_hp_zero, loot_delta)}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--interval', type=float, default=5.0)
    ap.add_argument('--duration', type=float, default=2100.0)
    args=ap.parse_args()
    run_dir=Path(args.run_dir)
    out=run_dir/'chefe_orc_monitor_summary.json'
    start=time.time()
    while time.time()-start < args.duration:
        s=summarize(run_dir)
        s['updated_at']=time.strftime('%Y-%m-%d %H:%M:%S')
        out.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({'updated_at':s['updated_at'],'samples':s['samples'],'chefe_orc_samples':s['chefe_orc_samples'],'estimated_boss_kills':s['estimated_boss_kills'],'target_sources':s['target_sources']}, ensure_ascii=False), flush=True)
        time.sleep(max(1.0,args.interval))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
