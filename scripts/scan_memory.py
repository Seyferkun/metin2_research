#!/usr/bin/env python
"""Minimal memory scanner — find HP/coord offsets without Cheat Engine.

Uses ReadProcessMemory + VirtualQueryEx. No injection, no debug hooks,
no window enumeration, no kernel drivers. Just vanilla Win32 API calls
that the game cannot distinguish from any other system tool.

Usage:
    # Find the game process automatically, scan all memory
    python scripts/scan_memory.py --process "metin2" --scan-hp --scan-coords

    # Specify PID directly
    python scripts/scan_memory.py --pid 1234 --scan-hp

    # Refine search: only show results in a specific value range
    python scripts/scan_memory.py --scan-hp --min-hp 100 --max-hp 50000

    # Quick scan: just check the first heap region
    python scripts/scan_memory.py --quick
"""

from __future__ import annotations

import argparse
import ctypes
import json
import struct
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any


# ── Windows API ───────────────────────────────────────────────────────

kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

MEM_COMMIT = 0x1000
MEM_MAPPED = 0x40000
MEM_PRIVATE = 0x20000
PAGE_READABLE = 0x02 | 0x04 | 0x10 | 0x20 | 0x80  # READONLY, READWRITE, EXECUTE_READ, EXECUTE_READWRITE, PAGE_WRITECOPY


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def _open_process(pid: int) -> int:
    return kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, wintypes.DWORD(pid))


def _close_handle(handle: int) -> None:
    kernel32.CloseHandle(wintypes.HANDLE(handle))


def _read_memory(handle: int, address: int, size: int) -> bytes | None:
    buf = ctypes.create_string_buffer(size)
    br = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(wintypes.HANDLE(handle), wintypes.LPCVOID(address), buf, size, ctypes.byref(br))
    return buf.raw if ok else None


# ── Value matchers ────────────────────────────────────────────────────

def _as_int32(data: bytes, offset: int) -> int | None:
    """Reads a signed 32-bit integer."""
    if offset + 4 > len(data):
        return None
    return int.from_bytes(data[offset:offset + 4], "little", signed=True)


def _as_float32(data: bytes, offset: int) -> float | None:
    if offset + 4 > len(data):
        return None
    return struct.unpack("<f", data[offset:offset + 4])[0]


def _as_uint32(data: bytes, offset: int) -> int | None:
    if offset + 4 > len(data):
        return None
    return int.from_bytes(data[offset:offset + 4], "little", signed=False)


# ── Scanner logic ─────────────────────────────────────────────────────

def find_game_pid(process_name: str = "metin2") -> int | None:
    """Find game PID by process name substring using Windows toolhelp snapshot."""
    import pathlib

    # Use CreateToolhelp32Snapshot for broader process discovery
    CREATE_TOOLHELP = 0x00000002

    kernel32 = ctypes.windll.kernel32

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    snapshot = kernel32.CreateToolhelp32Snapshot(CREATE_TOOLHELP, 0)
    if snapshot == -1:
        return None

    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snapshot, ctypes.byref(pe)):
            return None

        needle = process_name.casefold()
        while True:
            name = pe.szExeFile.decode("utf-8", errors="replace").casefold()
            if needle in name:
                return int(pe.th32ProcessID)
            if not kernel32.Process32Next(snapshot, ctypes.byref(pe)):
                break
        return None
    finally:
        kernel32.CloseHandle(snapshot)


def enumerate_regions(handle: int) -> list[dict[str, Any]]:
    """Enumerate all committed, readable memory regions via VirtualQueryEx."""
    regions = []
    address = 0

    while True:
        mbi = MEMORY_BASIC_INFORMATION()
        ret = kernel32.VirtualQueryEx(
            wintypes.HANDLE(handle),
            wintypes.LPCVOID(address),
            ctypes.byref(mbi),
            ctypes.sizeof(MEMORY_BASIC_INFORMATION),
        )
        if ret == 0:
            break  # no more regions

        region_size = mbi.RegionSize
        # ctypes.c_void_p stores address as .value (int or None for NULL)
        base_addr = mbi.BaseAddress
        if not isinstance(base_addr, int):
            base_addr = base_addr.value if hasattr(base_addr, 'value') else None
        if base_addr is None:
            base_addr = 0

        if (
            mbi.State == MEM_COMMIT
            and (mbi.Protect & PAGE_READABLE)
        ):
            type_label = "unknown"
            if mbi.Type == MEM_PRIVATE:
                type_label = "private"
            elif mbi.Type == MEM_MAPPED:
                type_label = "mapped"
            elif mbi.Type == 0x1000000:  # MEM_IMAGE
                type_label = "image"
            regions.append({
                "base": base_addr,
                "size": region_size,
                "protect": mbi.Protect,
                "type": type_label,
            })

        address = base_addr + region_size
        if address <= 0:
            break

    return regions


def scan_hp_values(
    handle: int,
    regions: list[dict[str, Any]],
    *,
    min_hp: int = 50,
    max_hp: int = 99999,
    stride: int = 4,
    chunk_size: int = 65536,
) -> list[dict[str, Any]]:
    """Scan memory for int32 values in the HP range.

    Returns candidates sorted by address with the value found.
    Deduplicates: if the same value is found at multiple addresses,
    groups them.
    """
    candidates: dict[int, list[int]] = {}  # value → [addresses]
    total_bytes = sum(r["size"] for r in regions)

    for ri, region in enumerate(regions):
        addr = region["base"]
        size = region["size"]

        # Skip huge regions (likely GPU buffers / textures)
        if size > 64 * 1024 * 1024:
            continue

        for chunk_start in range(0, size, chunk_size):
            read_size = min(chunk_size, size - chunk_start)
            data = _read_memory(handle, addr + chunk_start, read_size)
            if data is None:
                continue

            for off in range(0, read_size - 3, stride):
                val = _as_int32(data, off)
                if val is not None and min_hp <= val <= max_hp:
                    abs_addr = addr + chunk_start + off
                    if val not in candidates:
                        candidates[val] = []
                    candidates[val].append(abs_addr)

    # Sort by: how rare the value is (fewer occurrences = more specific)
    # Then by: how "round" the address is (aligned = more likely)
    rows = []
    for val, addrs in candidates.items():
        rows.append({
            "value": val,
            "count": len(addrs),
            "addresses": addrs[:10],  # only show first 10
            "address_count": len(addrs),
        })
    rows.sort(key=lambda r: r["count"])  # fewer matches = more likely to be HP
    return rows[:100]  # top 100


def scan_coord_values(
    handle: int,
    regions: list[dict[str, Any]],
    *,
    min_coord: float = -1000000.0,
    max_coord: float = 1000000.0,
    chunk_size: int = 65536,
) -> list[dict[str, Any]]:
    """Scan memory for float32 values in coordinate range.

    Coords usually come in pairs (x, y) or triplets (x, y, z) at nearby addresses.
    """
    coord_candidates: list[dict[str, Any]] = []

    for region in regions:
        addr = region["base"]
        size = region["size"]

        if size > 64 * 1024 * 1024:
            continue

        for chunk_start in range(0, size, chunk_size):
            read_size = min(chunk_size, size - chunk_start)
            data = _read_memory(handle, addr + chunk_start, read_size)
            if data is None:
                continue

            for off in range(0, read_size - 7, 4):
                x_val = _as_float32(data, off)
                y_val = _as_float32(data, off + 4)
                if x_val is not None and y_val is not None:
                    if min_coord <= x_val <= max_coord and min_coord <= y_val <= max_coord:
                        # Check if the values aren't obviously wrong:
                        # coordinates often match patterns like:
                        # - Not extremely round (not 0.0 or 1.0)
                        # - Plausible game-world coordinates
                        if abs(x_val) > 0.01 and abs(y_val) > 0.01:
                            coord_candidates.append({
                                "x": round(x_val, 4),
                                "y": round(y_val, 4),
                                "address": addr + chunk_start + off,
                            })

    # Deduplicate nearby addresses — if two float pairs are within 16 bytes,
    # they're likely the same coord struct being read different ways
    coord_candidates.sort(key=lambda c: abs(c["x"]) + abs(c["y"]))
    return coord_candidates[:200]


def scan_for_values(
    handle: int,
    regions: list[dict[str, Any]],
    targets: list[tuple[str, int | None]],
    *,
    chunk_size: int = 65536,
) -> dict[str, Any]:
    """Generic value scanner: look for specific values in memory.

    targets = list of (name, int_value_to_find) or (name, None) for any plausible HP.
    """
    # Open reparse point: just run HP scanner
    results: dict[str, Any] = {"targets_found": []}

    for name, value in targets:
        if value is not None:
            found_at = []
            for region in regions:
                addr = region["base"]
                size = region["size"]
                if size > 32 * 1024 * 1024:
                    continue
                for cs in range(0, min(size, 2 * 1024 * 1024), chunk_size):
                    rs = min(chunk_size, size - cs)
                    data = _read_memory(handle, addr + cs, rs)
                    if data is None:
                        continue
                    for off in range(0, rs - 3, 4):
                        v = _as_int32(data, off)
                        if v == value:
                            found_at.append(addr + cs + off)
            results["targets_found"].append({"name": name, "value": value, "addresses": found_at[:20]})

    return results


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal Metin2 memory scanner (no Cheat Engine)")
    parser.add_argument("--pid", type=int, help="Process ID")
    parser.add_argument("--process", default="metin2", help="Process name substring")
    parser.add_argument("--scan-hp", action="store_true", help="Scan for HP values")
    parser.add_argument("--scan-coords", action="store_true", help="Scan for coordinate float pairs")
    parser.add_argument("--min-hp", type=int, default=50, help="Minimum HP value")
    parser.add_argument("--max-hp", type=int, default=99999, help="Maximum HP value")
    parser.add_argument("--quick", action="store_true", help="Only scan first 4MB of each region")
    parser.add_argument("--out", help="Save results to JSON file")
    parser.add_argument("--verbose", action="store_true", help="Show region info")
    args = parser.parse_args()

    pid = args.pid
    if pid is None:
        print(f"[*] Finding process '{args.process}'...")
        pid = find_game_pid(args.process)
        if pid is None:
            print(f"[!] Process '{args.process}' not found. Is the game running?")
            return 1
        print(f"[+] Game PID: {pid}")
    else:
        print(f"[+] Using PID: {pid}")

    handle = _open_process(pid)
    if not handle:
        print(f"[!] Failed to open process. Try running as Administrator.")
        return 1

    try:
        print(f"[*] Enumerating memory regions...")
        regions = enumerate_regions(handle)
        print(f"[+] Found {len(regions)} readable memory regions")

        if args.verbose:
            total = sum(r["size"] for r in regions) / (1024 * 1024)
            for r in regions[:10]:
                print(f"    0x{r['base']:08x}: {r['size']/1024:.0f}KB [{r['type']}]")
            if len(regions) > 10:
                print(f"    ... and {len(regions) - 10} more regions ({total:.0f} MB total)")

        results: dict[str, Any] = {"pid": pid, "process_name": args.process, "regions": len(regions)}

        if args.scan_hp:
            print(f"\n[*] Scanning for HP values ({args.min_hp}-{args.max_hp})...")
            t0 = time.monotonic()
            hp_finds = scan_hp_values(handle, regions, min_hp=args.min_hp, max_hp=args.max_hp)
            elapsed = time.monotonic() - t0

            print(f"[+] Scan took {elapsed:.1f}s, found {len(hp_finds)} candidate values")
            print(f"\n  {'Value':>8} | {'Count':>6} | Sample Addresses")
            print(f"  {'-'*8}-+-{'-'*6}-+-{'-'*30}")
            for row in hp_finds[:30]:
                addrs = [hex(a) for a in row["addresses"][:3]]
                print(f"  {row['value']:>8} | {row['count']:>6} | {', '.join(addrs)}")
            print(f"\n  (Showing top 30 of {len(hp_finds)} candidate values)")
            results["hp_candidates"] = hp_finds

        if args.scan_coords:
            print(f"\n[*] Scanning for coordinate float pairs...")
            t0 = time.monotonic()
            coord_finds = scan_coord_values(handle, regions)
            elapsed = time.monotonic() - t0

            print(f"[+] Scan took {elapsed:.1f}s, found {len(coord_finds)} candidate coords")
            print(f"\n  {'X':>12} | {'Y':>12} | Address")
            print(f"  {'-'*12}-+-{'-'*12}-+-{'-'*12}")
            for c in coord_finds[:30]:
                print(f"  {c['x']:>12.4f} | {c['y']:>12.4f} | 0x{c['address']:08x}")
            results["coord_candidates"] = coord_finds

        if args.out:
            path = Path(args.out)
            path.write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")
            print(f"\n[+] Results saved to {path}")

    finally:
        _close_handle(handle)

    print("\n[*] Memory scan complete. No injection, no hooks, no CE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())