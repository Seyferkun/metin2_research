#!/usr/bin/env python
"""Read visible player coordinates from pgclient.app memory text mirrors.

This is read-only. It scans the visible game window process for text like:

    Yoshypt(834, 489)
    Yoshypt(834,489)

Addresses are heap/UI mirrors and may change after restart, so this reader can
scan to rediscover them each run. Use --addresses to read already-known mirrors.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory

COORD_ASCII_RE = re.compile(rb"(?P<name>[A-Za-z0-9_]{2,24})\((?P<x>\d{1,5})\s*,\s*(?P<y>\d{1,5})\)")
# UTF-16LE-ish regex by looking for ASCII chars separated with NULs after decode.
COORD_TEXT_RE = re.compile(r"(?P<name>[A-Za-z0-9_]{2,24})\((?P<x>\d{1,5})\s*,\s*(?P<y>\d{1,5})\)")


@dataclass(frozen=True)
class CoordHit:
    address: int
    encoding: str
    name: str
    x: int
    y: int
    region_type: str | None
    raw: str


def clean(raw: bytes, encoding: str) -> str:
    if encoding == "utf16le":
        text = raw.decode("utf-16le", errors="replace")
    else:
        text = raw.decode("latin1", errors="replace")
    text = text.replace("\x00", " ")
    return " ".join(text.split())


def scan_chunk(raw: bytes, base: int, region_type: str | None) -> list[CoordHit]:
    hits: list[CoordHit] = []
    for m in COORD_ASCII_RE.finditer(raw):
        start = max(0, m.start() - 48)
        end = min(len(raw), m.end() + 48)
        hits.append(
            CoordHit(
                address=base + m.start(),
                encoding="ascii",
                name=m.group("name").decode("latin1", errors="replace"),
                x=int(m.group("x")),
                y=int(m.group("y")),
                region_type=region_type,
                raw=clean(raw[start:end], "ascii"),
            )
        )
    # UTF-16LE scan: decode chunk and map approximate byte offset.
    try:
        text = raw.decode("utf-16le", errors="ignore")
    except Exception:
        text = ""
    for m in COORD_TEXT_RE.finditer(text):
        # Approximate byte address. Good enough for rediscovery/readback.
        addr = base + (m.start() * 2)
        start = max(0, (m.start() - 24) * 2)
        end = min(len(raw), (m.end() + 24) * 2)
        hits.append(
            CoordHit(
                address=addr,
                encoding="utf16le",
                name=m.group("name"),
                x=int(m.group("x")),
                y=int(m.group("y")),
                region_type=region_type,
                raw=clean(raw[start:end], "utf16le"),
            )
        )
    return hits


def read_known(handle: int, address: int, size: int) -> list[CoordHit]:
    base = max(0, address - 64)
    raw = read_memory(handle, base, size + 128)
    if not raw:
        return []
    return scan_chunk(raw, base, "known")


def dedupe(hits: list[CoordHit]) -> list[CoordHit]:
    seen = set()
    out = []
    for h in hits:
        key = (h.address, h.encoding, h.name, h.x, h.y)
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Read Metin2 coordinate text mirrors from pgclient.app memory")
    ap.add_argument("--process", default="pgclient.app")
    ap.add_argument("--address", action="append", help="Known address to read; can repeat")
    ap.add_argument("--scan", action="store_true", help="Scan readable private/mapped memory for coordinate text")
    ap.add_argument("--name", default="Yoshypt", help="Prefer/filter this player name")
    ap.add_argument("--out", help="Optional JSON output path")
    ap.add_argument("--max-region-mb", type=int, default=64)
    ap.add_argument("--chunk-size", type=int, default=1024 * 1024)
    args = ap.parse_args()

    if not args.scan and not args.address:
        args.scan = True

    pid = find_pid(args.process)
    if pid is None:
        print(f"Process not found: {args.process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print(f"OpenProcess failed for pid={pid}")
        return 1

    hits: list[CoordHit] = []
    try:
        if args.address:
            for addr_s in args.address:
                hits.extend(read_known(handle, int(addr_s, 0), args.chunk_size))
        if args.scan:
            regions = enumerate_regions(handle, max_region_mb=args.max_region_mb)
            for region in regions:
                # Coordinate UI strings should be in private/mapped text buffers; image/code creates too much noise.
                if region.get("type") == "image":
                    continue
                base = int(region["base"])
                size = min(int(region["size"]), args.max_region_mb * 1024 * 1024)
                for off in range(0, size, args.chunk_size):
                    raw = read_memory(handle, base + off, min(args.chunk_size, size - off))
                    if not raw:
                        continue
                    # Cheap prefilter before regex.
                    if b"(" not in raw or b")" not in raw:
                        continue
                    hits.extend(scan_chunk(raw, base + off, region.get("type")))
    finally:
        close_handle(handle)

    hits = dedupe(hits)
    preferred = [h for h in hits if h.name.lower() == args.name.lower()]
    if args.name and not preferred:
        # Fail closed for navigation: do not fall back to unrelated code/UI strings like range(0,256).
        selected = []
    else:
        selected = preferred or hits
    selected.sort(key=lambda h: (h.name.lower() != args.name.lower(), h.encoding != "ascii", h.address))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"process": args.process, "pid": pid, "hits": [asdict(h) for h in hits]}, indent=2), encoding="utf-8")

    if not selected:
        print("No coordinate text found in memory")
        return 2

    best = selected[0]
    print(f"coord={best.name}({best.x}, {best.y})")
    for h in selected[:50]:
        print(f"0x{h.address:08x} {h.encoding:<7} {h.name}({h.x}, {h.y}) [{h.region_type}] raw={h.raw!r}")
    if args.out:
        print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
