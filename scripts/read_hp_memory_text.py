#!/usr/bin/env python
"""Read visible HP from confirmed pgclient.app memory text mirrors.

This is read-only. It reads the game window process (`pgclient.app`) and parses
known HP UI text mirrors found by the weapon toggle workflow:

- 0x22c47104: text like "VD : 1492 / 1492"
- 0x22e2e411: text like "1492/1492"

These addresses are confirmed for the current running session. They may need to
be rediscovered after a client restart because they are heap/UI text mirrors.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass

from refine_value import close_handle, find_pid, open_process, read_memory

DEFAULT_ADDRESSES = [0x22C47104, 0x22E2E411]
HP_RE = re.compile(rb"(?:(?:VD\s*:\s*)?)(\d{2,6})\s*/\s*(\d{2,6})")


@dataclass(frozen=True)
class HpRead:
    address: int
    current: int
    maximum: int
    raw: str


def clean_ascii(raw: bytes) -> str:
    # Keep printable-ish bytes; stop long null padding from flooding output.
    text = raw.decode("latin1", errors="replace")
    text = text.replace("\x00", " ")
    return " ".join(text.split())


def read_hp_from_address(handle: int, address: int, size: int) -> HpRead | None:
    raw = read_memory(handle, address, size)
    if not raw:
        return None
    match = HP_RE.search(raw)
    if not match:
        # Also allow address landing a few bytes before the actual digits.
        text = clean_ascii(raw)
        return None
    current = int(match.group(1))
    maximum = int(match.group(2))
    return HpRead(address=address + match.start(), current=current, maximum=maximum, raw=clean_ascii(raw[:96]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Read confirmed Metin2 HP UI text mirrors from process memory")
    ap.add_argument("--process", default="pgclient.app")
    ap.add_argument("--address", action="append", help="Address to read, e.g. 0x22e2e411. Can repeat.")
    ap.add_argument("--size", type=int, default=128)
    args = ap.parse_args()

    addresses = [int(a, 0) for a in args.address] if args.address else DEFAULT_ADDRESSES
    pid = find_pid(args.process)
    if pid is None:
        print(f"Process not found: {args.process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print(f"OpenProcess failed for pid={pid}")
        return 1
    try:
        reads = []
        for addr in addresses:
            # Read a little before the address too, because the address may point to the digits
            # while another confirmed mirror includes the "VD : " prefix nearby.
            for start in (addr, max(0, addr - 16), max(0, addr - 96)):
                got = read_hp_from_address(handle, start, args.size)
                if got:
                    reads.append(got)
                    break
        if not reads:
            print(f"No HP text found at {', '.join(hex(a) for a in addresses)}")
            return 2
        # Prefer the compact exact current/max mirror if present; otherwise first parse.
        best = sorted(reads, key=lambda r: ("/" not in r.raw, r.address))[0]
        print(f"hp={best.current}/{best.maximum}")
        for r in reads:
            print(f"0x{r.address:08x}: {r.current}/{r.maximum} raw={r.raw!r}")
        return 0
    finally:
        close_handle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
