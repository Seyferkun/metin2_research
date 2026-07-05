#!/usr/bin/env python
"""Track addresses that alternate between two visible values across manual toggles.

Workflow:
  1. Set game to state A (e.g. weapon OFF, HP 1492)
  2. Run: python scripts/toggle_value_tracker.py sample --label off --value 1492
  3. Toggle weapon ON (HP 1732)
  4. Run: python scripts/toggle_value_tracker.py sample --label on --value 1732
  5. Toggle OFF again and sample again, repeat a few cycles
  6. Run: python scripts/toggle_value_tracker.py analyze

The analyzer finds addr/type pairs whose sampled value follows labels exactly.
"""

from __future__ import annotations

import argparse
import json
import struct
import time
from collections import defaultdict
from pathlib import Path

from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_as_type, read_memory

TYPES = ("i16", "u16", "i32", "u32", "f32")


def value_patterns(value: int):
    return [
        ("i16", value, int(value).to_bytes(2, "little", signed=True)),
        ("u16", value, int(value).to_bytes(2, "little", signed=False)),
        ("i32", value, int(value).to_bytes(4, "little", signed=True)),
        ("u32", value, int(value).to_bytes(4, "little", signed=False)),
        ("f32", value, struct.pack("<f", float(value))),
    ]


def scan_value(handle: int, value: int, max_region_mb: int = 64) -> list[dict]:
    regs = enumerate_regions(handle, max_region_mb=max_region_mb)
    pats = value_patterns(value)
    hits = []
    for r in regs:
        base = int(r["base"])
        size = min(int(r["size"]), max_region_mb * 1024 * 1024)
        for start in range(0, size, 1024 * 1024):
            raw = read_memory(handle, base + start, min(1024 * 1024, size - start)) or b""
            if not raw:
                continue
            for typ, _val, pat in pats:
                pos = 0
                while True:
                    idx = raw.find(pat, pos)
                    if idx < 0:
                        break
                    hits.append({"address": base + start + idx, "type": typ, "value": value, "region_type": r.get("type")})
                    pos = idx + 1
    # dedupe
    seen = set()
    out = []
    for h in hits:
        key = (h["address"], h["type"])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def load_samples(path: Path) -> list[dict]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def save_samples(path: Path, samples: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(samples, indent=2), encoding="utf-8")


def cmd_sample(args) -> int:
    pid = find_pid(args.process)
    if pid is None:
        print(f"Process not found: {args.process}")
        return 1
    h = open_process(pid)
    if not h:
        print("OpenProcess failed")
        return 1
    try:
        print(f"Scanning sample label={args.label!r} value={args.value}...")
        hits = scan_value(h, args.value, max_region_mb=args.max_region_mb)
        sample = {"label": args.label, "value": args.value, "hits": hits, "pid": pid, "time": time.time()}
        path = Path(args.samples)
        samples = load_samples(path)
        samples.append(sample)
        save_samples(path, samples)
        print(f"Saved sample #{len(samples)} to {path}: hits={len(hits)}")
        by_type = defaultdict(int)
        for hit in hits:
            by_type[hit["type"]] += 1
        print("By type:", ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
        return 0
    finally:
        close_handle(h)


def cmd_analyze(args) -> int:
    path = Path(args.samples)
    samples = load_samples(path)
    if len(samples) < 2:
        print("Need at least two samples")
        return 1

    # Candidate keys that appear in at least one sample. Read current values not needed;
    # analyze exact hit membership for each expected value sample.
    all_keys = set()
    sample_sets = []
    for s in samples:
        keys = {(int(h["address"]), h["type"]) for h in s["hits"]}
        sample_sets.append(keys)
        all_keys |= keys

    matches = []
    for key in all_keys:
        present = [key in keys for keys in sample_sets]
        # Good key appears in every sample where its value equals sampled value? Actually
        # if the same addr alternates, it will appear in all samples, because each sample
        # scans for that sample's value. So require presence in all samples.
        if not all(present):
            continue
        addr, typ = key
        matches.append({"address": addr, "type": typ, "labels": [s["label"] for s in samples], "values": [s["value"] for s in samples]})

    matches.sort(key=lambda m: (m["address"], m["type"]))
    out = {"samples": [{"label": s["label"], "value": s["value"], "hit_count": len(s["hits"])} for s in samples], "matches": matches}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Samples: {[(s['label'], s['value'], len(s['hits'])) for s in samples]}")
    print(f"Addresses present for every toggled value: {len(matches)}. Saved {out_path}")
    for m in matches[:80]:
        print(f"  0x{m['address']:08x} {m['type']} values={m['values']}")
    return 0 if matches else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Track value addresses across manual toggles")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sample")
    sp.add_argument("--process", default="mt2portugalia")
    sp.add_argument("--label", required=True)
    sp.add_argument("--value", type=int, required=True)
    sp.add_argument("--samples", default="reports/hp_weapon_toggle_samples.json")
    sp.add_argument("--max-region-mb", type=int, default=64)
    sp.set_defaults(func=cmd_sample)

    an = sub.add_parser("analyze")
    an.add_argument("--samples", default="reports/hp_weapon_toggle_samples.json")
    an.add_argument("--out", default="reports/hp_weapon_toggle_analysis.json")
    an.set_defaults(func=cmd_analyze)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
