#!/usr/bin/env python
"""Before/after value refiner for ReadProcessMemory research.

This is the Cheat-Engine-style workflow without Cheat Engine:

  # 1) Snapshot all memory representations of the current visible value
  python scripts/refine_value.py snapshot --process mt2portugalia --value 1412 --out reports/hp_1412_before.json

  # 2) Change the value in-game (stat point, potion, damage, move, etc.)

  # 3) Compare: which former 1412 addresses now equal 1452?
  python scripts/refine_value.py compare --process mt2portugalia --snapshot reports/hp_1412_before.json --new-value 1452

It scans multiple encodings:
- int32 signed
- uint32 unsigned
- int16/uint16
- float32 exact-ish
- ASCII decimal text ("1412")
- UTF-16LE decimal text ("1\0 4\0 1\0 2\0")

Read-only only. No injection, no debugger attach, no hooks.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import struct
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000
MEM_IMAGE = 0x1000000
PAGE_READABLE = 0x02 | 0x04 | 0x10 | 0x20 | 0x80


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


def find_pid(process_name: str) -> int | None:
    needle = process_name.casefold()
    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snap == -1:
        return None
    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snap, ctypes.byref(pe)):
            return None
        while True:
            exe = pe.szExeFile.decode("utf-8", errors="replace")
            if needle in exe.casefold():
                return int(pe.th32ProcessID)
            if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                return None
    finally:
        kernel32.CloseHandle(snap)


def open_process(pid: int) -> int:
    return kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, wintypes.DWORD(pid))


def close_handle(handle: int) -> None:
    kernel32.CloseHandle(wintypes.HANDLE(handle))


def read_memory(handle: int, address: int, size: int) -> bytes | None:
    buf = ctypes.create_string_buffer(size)
    br = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(wintypes.HANDLE(handle), wintypes.LPCVOID(address), buf, size, ctypes.byref(br))
    return buf.raw if ok else None


def read_bytes(handle: int, address: int, size: int) -> bytes | None:
    return read_memory(handle, address, size)


def ptr_to_int(ptr: Any) -> int:
    if isinstance(ptr, int):
        return ptr
    val = getattr(ptr, "value", 0)
    return 0 if val is None else int(val)


def enumerate_regions(handle: int, max_region_mb: int = 64) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    address = 0
    max_region = max_region_mb * 1024 * 1024
    while True:
        mbi = MEMORY_BASIC_INFORMATION()
        ret = kernel32.VirtualQueryEx(
            wintypes.HANDLE(handle),
            wintypes.LPCVOID(address),
            ctypes.byref(mbi),
            ctypes.sizeof(MEMORY_BASIC_INFORMATION),
        )
        if ret == 0:
            break
        base = ptr_to_int(mbi.BaseAddress)
        size = int(mbi.RegionSize)
        if mbi.State == MEM_COMMIT and (mbi.Protect & PAGE_READABLE) and size > 0:
            type_label = "unknown"
            if mbi.Type == MEM_PRIVATE:
                type_label = "private"
            elif mbi.Type == MEM_MAPPED:
                type_label = "mapped"
            elif mbi.Type == MEM_IMAGE:
                type_label = "image"
            if size <= max_region:
                regions.append({"base": base, "size": size, "type": type_label, "protect": int(mbi.Protect)})
        address = base + size
        if address <= 0 or address > 0x7FFFFFFFFFFFFFFF:
            break
    return regions


def encodings_for(value: int) -> dict[str, bytes]:
    text = str(value)
    return {
        "i32": int(value).to_bytes(4, "little", signed=True),
        "u32": int(value).to_bytes(4, "little", signed=False),
        "i16": int(value).to_bytes(2, "little", signed=True),
        "u16": int(value).to_bytes(2, "little", signed=False),
        "f32": struct.pack("<f", float(value)),
        "ascii": text.encode("ascii"),
        "utf16le": text.encode("utf-16le"),
    }


def scan_value(handle: int, regions: list[dict[str, Any]], value: int, *, chunk_size: int = 1024 * 1024) -> list[dict[str, Any]]:
    pats = encodings_for(value)
    hits: list[dict[str, Any]] = []
    for region in regions:
        base = region["base"]
        size = region["size"]
        overlap = max(len(p) for p in pats.values()) - 1
        previous_tail = b""
        for chunk_start in range(0, size, chunk_size):
            read_size = min(chunk_size, size - chunk_start)
            data = read_memory(handle, base + chunk_start, read_size)
            if data is None:
                previous_tail = b""
                continue
            block = previous_tail + data
            block_base = base + chunk_start - len(previous_tail)
            for kind, pat in pats.items():
                start = 0
                while True:
                    idx = block.find(pat, start)
                    if idx == -1:
                        break
                    addr = block_base + idx
                    if addr >= base + chunk_start:  # avoid duplicate overlap hits
                        hits.append({"address": addr, "type": kind, "value": value, "region_type": region["type"]})
                    start = idx + 1
            previous_tail = data[-overlap:] if overlap > 0 else b""
    hits.sort(key=lambda h: (h["type"], h["address"]))
    return hits


def read_as_type(handle: int, address: int, kind: str) -> Any:
    if kind in ("i32", "u32", "f32"):
        data = read_bytes(handle, address, 4)
    elif kind in ("i16", "u16"):
        data = read_bytes(handle, address, 2)
    elif kind == "ascii":
        data = read_bytes(handle, address, 16)
    elif kind == "utf16le":
        data = read_bytes(handle, address, 32)
    else:
        return None
    if not data:
        return None
    try:
        if kind == "i32":
            return int.from_bytes(data[:4], "little", signed=True)
        if kind == "u32":
            return int.from_bytes(data[:4], "little", signed=False)
        if kind == "i16":
            return int.from_bytes(data[:2], "little", signed=True)
        if kind == "u16":
            return int.from_bytes(data[:2], "little", signed=False)
        if kind == "f32":
            f = struct.unpack("<f", data[:4])[0]
            return f if math.isfinite(f) else None
        if kind == "ascii":
            s = data.split(b"\x00", 1)[0].decode("ascii", errors="ignore")
            return "".join(ch for ch in s if ch.isdigit())
        if kind == "utf16le":
            chars = []
            for i in range(0, min(len(data), 32), 2):
                ch = data[i:i+2].decode("utf-16le", errors="ignore")
                if ch.isdigit():
                    chars.append(ch)
                elif chars:
                    break
            return "".join(chars)
    except Exception:
        return None
    return None


def snapshot_cmd(args: argparse.Namespace) -> int:
    pid = args.pid or find_pid(args.process)
    if pid is None:
        print(f"Process not found: {args.process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print("OpenProcess failed. Try running as Administrator.")
        return 1
    try:
        regions = enumerate_regions(handle, max_region_mb=args.max_region_mb)
        t0 = time.monotonic()
        hits = scan_value(handle, regions, args.value)
        elapsed = time.monotonic() - t0
        out = {
            "pid": pid,
            "process": args.process,
            "value": args.value,
            "created_at": time.time(),
            "regions": len(regions),
            "hits": hits,
        }
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        by_type: dict[str, int] = {}
        for h in hits:
            by_type[h["type"]] = by_type.get(h["type"], 0) + 1
        print(f"Snapshot value={args.value}: {len(hits)} hits in {elapsed:.1f}s; saved {args.out}")
        print("By type:", ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) or "none")
        for h in hits[:args.show]:
            print(f"  0x{h['address']:08x} {h['type']} [{h['region_type']}]")
        return 0
    finally:
        close_handle(handle)


def compare_cmd(args: argparse.Namespace) -> int:
    snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    pid = args.pid or find_pid(args.process or snap.get("process", "mt2portugalia"))
    if pid is None:
        print("Process not found")
        return 1
    handle = open_process(pid)
    if not handle:
        print("OpenProcess failed. Try running as Administrator.")
        return 1
    try:
        changed = []
        for h in snap["hits"]:
            value_now = read_as_type(handle, int(h["address"]), h["type"])
            target = str(args.new_value) if h["type"] in ("ascii", "utf16le") else args.new_value
            if value_now == target or (h["type"] == "f32" and isinstance(value_now, float) and abs(value_now - args.new_value) < args.float_tolerance):
                changed.append({**h, "new_value": value_now})
        out = {
            "snapshot": args.snapshot,
            "old_value": snap["value"],
            "new_value": args.new_value,
            "pid": pid,
            "matches": changed,
        }
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"Compare {snap['value']} -> {args.new_value}: {len(changed)} matching changed addresses")
        for h in changed[:args.show]:
            print(f"  0x{h['address']:08x} {h['type']} {snap['value']} -> {h['new_value']} [{h['region_type']}]")
        if not changed:
            print("No matches. The visible value may be recomputed/display text, stored compressed/encoded, or the snapshot missed its region/type.")
        return 0 if changed else 2
    finally:
        close_handle(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="ReadProcessMemory before/after value refiner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_snap = sub.add_parser("snapshot", help="snapshot all addresses containing a visible value")
    p_snap.add_argument("--process", default="mt2portugalia")
    p_snap.add_argument("--pid", type=int)
    p_snap.add_argument("--value", type=int, required=True)
    p_snap.add_argument("--out", required=True)
    p_snap.add_argument("--show", type=int, default=40)
    p_snap.add_argument("--max-region-mb", type=int, default=64)
    p_snap.set_defaults(func=snapshot_cmd)

    p_cmp = sub.add_parser("compare", help="compare a snapshot against a new visible value")
    p_cmp.add_argument("--process", default="mt2portugalia")
    p_cmp.add_argument("--pid", type=int)
    p_cmp.add_argument("--snapshot", required=True)
    p_cmp.add_argument("--new-value", type=int, required=True)
    p_cmp.add_argument("--out")
    p_cmp.add_argument("--show", type=int, default=40)
    p_cmp.add_argument("--float-tolerance", type=float, default=0.25)
    p_cmp.set_defaults(func=compare_cmd)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
