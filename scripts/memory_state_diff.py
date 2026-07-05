#!/usr/bin/env python
"""Read-only memory chunk differ for weapon ON/OFF state changes.

This is for cases where visible HP changes (1492 <-> 1732), but no stable
literal address changes between those numbers. It snapshots readable memory
chunks with hashes and selected bytes, then compares a later state.

It does NOT write process memory. It only uses VirtualQueryEx + ReadProcessMemory.

Workflow:
  # with weapon ON
  python scripts/memory_state_diff.py snapshot --label on --out reports/weapon_on_memdiff.json

  # toggle weapon OFF
  python scripts/memory_state_diff.py compare --before reports/weapon_on_memdiff.json --label off --out reports/weapon_on_to_off_diff.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import time
from pathlib import Path

from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory

NEEDLE_VALUES = [1492, 1732, 240, 40, 6, 14, 36]


def make_patterns(values: list[int]) -> list[tuple[str, int, bytes]]:
    pats = []
    for v in values:
        for typ, raw in [
            ("i16", int(v).to_bytes(2, "little", signed=True)),
            ("u16", int(v).to_bytes(2, "little", signed=False)),
            ("i32", int(v).to_bytes(4, "little", signed=True)),
            ("u32", int(v).to_bytes(4, "little", signed=False)),
            ("f32", struct.pack("<f", float(v))),
        ]:
            pats.append((typ, v, raw))
    return pats


def contains_any(data: bytes, pats: list[tuple[str, int, bytes]]) -> bool:
    return any(p in data for _typ, _v, p in pats)


def snapshot(process: str, label: str, out_path: Path, *, chunk_size: int, max_region_mb: int, keep_matching_chunks: bool) -> int:
    pid = find_pid(process)
    if pid is None:
        print(f"Process not found: {process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print("OpenProcess failed")
        return 1
    pats = make_patterns(NEEDLE_VALUES)
    chunks = []
    try:
        regs = enumerate_regions(handle, max_region_mb=max_region_mb)
        print(f"snapshot label={label} pid={pid} regions={len(regs)}", flush=True)
        for r in regs:
            base = int(r["base"])
            size = min(int(r["size"]), max_region_mb * 1024 * 1024)
            for start in range(0, size, chunk_size):
                addr = base + start
                raw = read_memory(handle, addr, min(chunk_size, size - start))
                if not raw:
                    continue
                entry = {
                    "address": addr,
                    "size": len(raw),
                    "region_type": r.get("type"),
                    "sha1": hashlib.sha1(raw).hexdigest(),
                }
                # Store only chunks containing values of interest; this avoids dumping full memory.
                if keep_matching_chunks and contains_any(raw, pats):
                    entry["data_b64"] = base64.b64encode(raw).decode("ascii")
                chunks.append(entry)
        doc = {"process": process, "pid": pid, "label": label, "created_at": time.time(), "chunk_size": chunk_size, "max_region_mb": max_region_mb, "chunks": chunks}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        stored = sum(1 for c in chunks if "data_b64" in c)
        print(f"saved {out_path}; chunks={len(chunks)} stored_matching_chunks={stored}")
        return 0
    finally:
        close_handle(handle)


def find_patterns(data: bytes, base: int, pats: list[tuple[str, int, bytes]]) -> list[dict]:
    hits = []
    for typ, val, pat in pats:
        pos = 0
        while True:
            idx = data.find(pat, pos)
            if idx < 0:
                break
            hits.append({"address": base + idx, "type": typ, "value": val})
            pos = idx + 1
    return hits


def compare(process: str, before_path: Path, label: str, out_path: Path) -> int:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    before_by_addr = {int(c["address"]): c for c in before["chunks"]}
    pid = find_pid(process)
    if pid is None:
        print(f"Process not found: {process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print("OpenProcess failed")
        return 1
    pats = make_patterns(NEEDLE_VALUES)
    changed = []
    try:
        print(f"compare {before.get('label')} -> {label}; chunks={len(before_by_addr)}", flush=True)
        for addr, old in before_by_addr.items():
            raw = read_memory(handle, addr, int(old["size"]))
            if not raw:
                continue
            new_sha = hashlib.sha1(raw).hexdigest()
            if new_sha == old["sha1"]:
                continue
            entry = {
                "address": addr,
                "size": len(raw),
                "region_type": old.get("region_type"),
                "old_sha1": old["sha1"],
                "new_sha1": new_sha,
                "new_hits": find_patterns(raw, addr, pats),
            }
            if "data_b64" in old:
                old_raw = base64.b64decode(old["data_b64"])
                entry["old_hits"] = find_patterns(old_raw, addr, pats)
                # record changed byte spans, capped
                spans = []
                i = 0
                max_len = min(len(old_raw), len(raw))
                while i < max_len and len(spans) < 50:
                    if old_raw[i] == raw[i]:
                        i += 1
                        continue
                    j = i + 1
                    while j < max_len and old_raw[j] != raw[j] and j - i < 64:
                        j += 1
                    spans.append({"offset": i, "address": addr + i, "old_hex": old_raw[i:j].hex(), "new_hex": raw[i:j].hex()})
                    i = j
                entry["changed_spans"] = spans
            changed.append(entry)
        changed.sort(key=lambda e: (0 if e.get("new_hits") or e.get("old_hits") else 1, e["address"]))
        doc = {"process": process, "pid": pid, "before": str(before_path), "from_label": before.get("label"), "to_label": label, "created_at": time.time(), "changed_chunks": changed[:5000], "changed_count": len(changed)}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        interesting = [c for c in changed if c.get("new_hits") or c.get("old_hits")]
        print(f"saved {out_path}; changed_chunks={len(changed)} interesting={len(interesting)}")
        for c in interesting[:50]:
            print(f"  chunk 0x{c['address']:08x} {c.get('region_type')} old_hits={c.get('old_hits', [])[:5]} new_hits={c.get('new_hits', [])[:5]}")
        return 0 if changed else 2
    finally:
        close_handle(handle)


def main() -> int:
    ap = argparse.ArgumentParser(description="Snapshot/compare readable memory chunks")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--process", default="mt2portugalia")
    s.add_argument("--label", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--chunk-size", type=int, default=4096)
    s.add_argument("--max-region-mb", type=int, default=64)
    s.add_argument("--keep-matching-chunks", action="store_true", default=True)
    c = sub.add_parser("compare")
    c.add_argument("--process", default="mt2portugalia")
    c.add_argument("--before", required=True)
    c.add_argument("--label", required=True)
    c.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "snapshot":
        return snapshot(args.process, args.label, Path(args.out), chunk_size=args.chunk_size, max_region_mb=args.max_region_mb, keep_matching_chunks=args.keep_matching_chunks)
    return compare(args.process, Path(args.before), args.label, Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
