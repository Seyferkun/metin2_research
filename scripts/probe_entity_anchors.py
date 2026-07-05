#!/usr/bin/env python
"""Probe Metin2 client memory for entity/object anchors around visible Metin strings.

Read-only workflow:
1. Scan pgclient.app private memory for live coordinate strings like
   `Metin da Batalha(284, 218)`.
2. For each live Metin text address, search process memory for 32-bit pointers
   to the text address and nearby offsets.
3. Around pointer-reference sites, decode nearby i16/u16/i32 coordinate-like
   values and printable text to identify possible entity structs/lists.

This does not modify game memory and does not inject packets.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory

METIN_COORD_RE = re.compile(
    r"(?P<name>Metin\s+(?:da|do|de|dos|das)?\s*[A-Za-zÀ-ÿ0-9_ '\-]+)\(\s*(?P<x>\d{1,4})\s*,\s*(?P<y>\d{1,4})\s*\)",
    re.IGNORECASE,
)
ANY_COORD_RE = re.compile(
    r"(?P<name>[A-Za-zÀ-ÿ0-9_ '\-]{3,40})\(\s*(?P<x>\d{1,4})\s*,\s*(?P<y>\d{1,4})\s*\)"
)


@dataclass
class TextAnchor:
    address: int
    name: str
    x: int
    y: int
    raw: str
    region_base: int
    region_size: int


@dataclass
class PointerRef:
    anchor_address: int
    ref_address: int
    pointed_value: int
    delta_to_anchor: int
    region_base: int
    nearby_text: str
    coord_candidates: list[dict]


def printable(raw: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 or 160 <= b <= 255 else " " for b in raw)


def iter_private_regions(h_process, max_region_mb: int):
    for region in enumerate_regions(h_process, max_region_mb=max_region_mb):
        if region.get("type") == "image":
            continue
        yield region


def scan_text_anchors(h_process, *, max_region_mb: int = 64, context: int = 180) -> list[TextAnchor]:
    anchors: list[TextAnchor] = []
    seen: set[tuple[str, int, int, int]] = set()
    pattern = b"Metin "
    for region in iter_private_regions(h_process, max_region_mb):
        base = int(region["base"])
        size = min(int(region["size"]), max_region_mb * 1024 * 1024)
        step = 1024 * 1024
        overlap = context + 128
        prev = b""
        prev_base = base
        for off in range(0, size, step):
            chunk = read_memory(h_process, base + off, min(step, size - off))
            if not chunk:
                continue
            data = prev + chunk
            data_base = prev_base
            start = 0
            while True:
                idx = data.find(pattern, start)
                if idx < 0:
                    break
                lo = max(0, idx - context)
                hi = min(len(data), idx + context)
                text = printable(data[lo:hi])
                for match in METIN_COORD_RE.finditer(text):
                    name = " ".join(match.group("name").split()).rstrip(" ,.;:")
                    x = int(match.group("x")); y = int(match.group("y"))
                    addr = data_base + lo + match.start()
                    key = (name.lower(), x, y, addr)
                    if key in seen:
                        continue
                    seen.add(key)
                    anchors.append(TextAnchor(addr, name, x, y, text, base, int(region["size"])))
                start = idx + 1
            if len(chunk) > overlap:
                prev = chunk[-overlap:]
                prev_base = base + off + len(chunk) - overlap
            else:
                prev = chunk
                prev_base = base + off
    return anchors


def decode_coord_candidates(raw: bytes, base_addr: int, anchor_x: int, anchor_y: int, radius: int = 400) -> list[dict]:
    rows: list[dict] = []
    for off in range(0, max(0, len(raw) - 4), 2):
        addr = base_addr + off
        if off + 2 <= len(raw):
            u16 = struct.unpack_from("<H", raw, off)[0]
            i16 = struct.unpack_from("<h", raw, off)[0]
            for typ, val in (("u16", u16), ("i16", i16)):
                if abs(val - anchor_x) <= radius or abs(val - anchor_y) <= radius:
                    rows.append({"address": addr, "type": typ, "value": val, "offset": off})
        if off + 4 <= len(raw):
            u32 = struct.unpack_from("<I", raw, off)[0]
            i32 = struct.unpack_from("<i", raw, off)[0]
            for typ, val in (("u32", u32), ("i32", i32)):
                if 0 <= val <= 2000 and (abs(val - anchor_x) <= radius or abs(val - anchor_y) <= radius):
                    rows.append({"address": addr, "type": typ, "value": val, "offset": off})
    # Keep output bounded and most relevant first.
    rows.sort(key=lambda r: min(abs(int(r["value"]) - anchor_x), abs(int(r["value"]) - anchor_y)))
    return rows[:80]


def scan_pointer_refs(h_process, anchors: list[TextAnchor], *, max_region_mb: int = 64, context: int = 192) -> list[PointerRef]:
    refs: list[PointerRef] = []
    if not anchors:
        return refs
    # Search not only exact text start; object structs sometimes point a few bytes into or before string buffers.
    pointer_targets: dict[int, TextAnchor] = {}
    for anchor in anchors:
        for delta in range(-16, 17, 4):
            if anchor.address + delta > 0:
                pointer_targets[anchor.address + delta] = anchor
    patterns = {struct.pack("<I", target): target for target in pointer_targets}
    for region in iter_private_regions(h_process, max_region_mb):
        base = int(region["base"])
        size = min(int(region["size"]), max_region_mb * 1024 * 1024)
        step = 1024 * 1024
        overlap = 8 + context
        prev = b""
        prev_base = base
        for off in range(0, size, step):
            chunk = read_memory(h_process, base + off, min(step, size - off))
            if not chunk:
                continue
            data = prev + chunk
            data_base = prev_base
            for pat, target_addr in patterns.items():
                start = 0
                while True:
                    idx = data.find(pat, start)
                    if idx < 0:
                        break
                    ref_addr = data_base + idx
                    anchor = pointer_targets[target_addr]
                    lo = max(0, idx - context)
                    hi = min(len(data), idx + 4 + context)
                    window = data[lo:hi]
                    refs.append(PointerRef(
                        anchor_address=anchor.address,
                        ref_address=ref_addr,
                        pointed_value=target_addr,
                        delta_to_anchor=target_addr - anchor.address,
                        region_base=base,
                        nearby_text=printable(window),
                        coord_candidates=decode_coord_candidates(window, data_base + lo, anchor.x, anchor.y),
                    ))
                    start = idx + 1
            if len(chunk) > overlap:
                prev = chunk[-overlap:]
                prev_base = base + off + len(chunk) - overlap
            else:
                prev = chunk
                prev_base = base + off
    refs.sort(key=lambda r: (r.anchor_address, abs(r.ref_address - r.anchor_address)))
    return refs


def nearby_any_coord_strings(h_process, anchors: list[TextAnchor], *, window: int = 4096) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, int, int, int]] = set()
    for anchor in anchors:
        raw = read_memory(h_process, max(0, anchor.address - window), window * 2) or b""
        text = printable(raw)
        base = max(0, anchor.address - window)
        for m in ANY_COORD_RE.finditer(text):
            name = " ".join(m.group("name").split()).strip()
            x = int(m.group("x")); y = int(m.group("y"))
            key = (name, x, y, base + m.start())
            if key in seen:
                continue
            seen.add(key)
            rows.append({"address": base + m.start(), "near_anchor": anchor.address, "name": name, "x": x, "y": y})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="pgclient.app")
    ap.add_argument("--out", default="reports/entity_anchor_probe.json")
    ap.add_argument("--max-region-mb", type=int, default=64)
    ap.add_argument("--show", type=int, default=40)
    args = ap.parse_args()

    pid = find_pid(args.process)
    if pid is None:
        print(f"process not found: {args.process}")
        return 1
    h = open_process(pid)
    if not h:
        print(f"OpenProcess failed pid={pid}")
        return 1
    try:
        anchors = scan_text_anchors(h, max_region_mb=args.max_region_mb)
        refs = scan_pointer_refs(h, anchors, max_region_mb=args.max_region_mb)
        nearby = nearby_any_coord_strings(h, anchors)
    finally:
        close_handle(h)

    result = {
        "pid": pid,
        "anchors": [asdict(a) for a in anchors],
        "pointer_refs": [asdict(r) for r in refs],
        "nearby_coord_strings": nearby,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"anchors={len(anchors)} pointer_refs={len(refs)} nearby_coord_strings={len(nearby)} out={out}")
    for a in anchors[:args.show]:
        print(f"ANCHOR 0x{a.address:08x} {a.name}({a.x},{a.y}) raw={a.raw[:160]!r}")
    for r in refs[:args.show]:
        best = r.coord_candidates[:6]
        print(f"REF anchor=0x{r.anchor_address:08x} ref=0x{r.ref_address:08x} ptr=0x{r.pointed_value:08x} d={r.delta_to_anchor} coords={best}")
    for row in nearby[:args.show]:
        print(f"NEAR 0x{row['address']:08x} {row['name']}({row['x']},{row['y']}) near=0x{row['near_anchor']:08x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
