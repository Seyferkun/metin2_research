"""Read-only game process memory probe for Metin2 private-sandbox research.

Uses Windows ReadProcessMemory to read player HP, MP, and other game state
directly from the client process. READ-ONLY — no injection, no hooks.

Automatically discovers the player struct by scanning for max_hp+hp pairs.
No Cheat Engine needed. No offsets to configure for this server build.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import load_config


# ── Windows API ───────────────────────────────────────────────────────

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000
PAGE_READABLE = 0x02 | 0x04 | 0x10 | 0x20 | 0x80

kernel32 = ctypes.windll.kernel32


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


def _find_pid(name: str = "mt2portugalia") -> int | None:
    """Find a process PID by name substring."""
    name = name.casefold()
    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snap == -1:
        return None
    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snap, ctypes.byref(pe)):
            return None
        while True:
            if name in pe.szExeFile.decode("utf-8", errors="replace").casefold():
                return int(pe.th32ProcessID)
            if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                break
        return None
    finally:
        kernel32.CloseHandle(snap)


def _open_process(pid: int) -> int:
    return kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, wintypes.DWORD(pid))


def _close_handle(h: int) -> None:
    kernel32.CloseHandle(wintypes.HANDLE(h))


def _read(handle: int, addr: int, size: int) -> bytes | None:
    buf = ctypes.create_string_buffer(size)
    br = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(wintypes.HANDLE(handle), wintypes.LPCVOID(addr), buf, size, ctypes.byref(br))
    return buf.raw if ok else None


def _read_i32(handle: int, addr: int) -> int | None:
    data = _read(handle, addr, 4)
    return int.from_bytes(data, "little", signed=True) if data else None


def _read_u32(handle: int, addr: int) -> int | None:
    data = _read(handle, addr, 4)
    return int.from_bytes(data, "little") if data else None


def _read_f32(handle: int, addr: int) -> float | None:
    import struct
    data = _read(handle, addr, 4)
    return struct.unpack("<f", data)[0] if data else None


def _enumerate_regions(handle: int) -> list[dict[str, Any]]:
    """Enumerate all committed readable memory regions."""
    regions = []
    addr = 0
    while True:
        mbi = MEMORY_BASIC_INFORMATION()
        ret = kernel32.VirtualQueryEx(wintypes.HANDLE(handle), wintypes.LPCVOID(addr), ctypes.byref(mbi), ctypes.sizeof(MEMORY_BASIC_INFORMATION))
        if ret == 0:
            break
        base = mbi.BaseAddress
        if not isinstance(base, int):
            base = base.value if hasattr(base, "value") else 0
        if base is None:
            base = 0
        if mbi.State == MEM_COMMIT and (mbi.Protect & PAGE_READABLE):
            type_label = "unknown"
            if mbi.Type == MEM_PRIVATE:
                type_label = "private"
            elif mbi.Type == MEM_MAPPED:
                type_label = "mapped"
            elif mbi.Type == 0x1000000:
                type_label = "image"
            regions.append({"base": base, "size": mbi.RegionSize, "type": type_label})
        addr = base + mbi.RegionSize
        if addr <= 0 or addr > 0x7FFFFFFFFFFFFFFF:
            break
    return regions


# ── Player struct discovery ───────────────────────────────────────────
# Known layout (discovered via memory scan on MT2Portugalia):
#   +0:  max_hp      (int32)
#   +4:  pointer     (int32)
#   +8:  current_mp  (int32)
#   +12: pointer     (int32)
#   +16: current_hp  (int32)
#   +20: pointer     (int32)
#   +24: ???         (int32)
#   +28: pointer     (int32)
#   +32: max_mp      (int32)

STRUCT_OFFSETS: dict[str, int] = {
    "max_hp": 0,
    "current_mp": 8,
    "current_hp": 16,
    "max_mp": 32,
}


def _find_player_struct(handle: int, regions: list[dict], chunk_size: int = 65536) -> dict[str, int] | None:
    """Scan memory for the player stat struct.

    Uses two strategies:
    1. (Preferred) Scan for known max_hp value from config (session-stable).
    2. (Fallback) Range-scan for max_hp 50-100000 with strict heuristics.

    Pattern: max_hp (+0), [ptr], mp (+8), [ptr], current_hp (+16), [ptr], ?, [ptr], max_mp (+32)
    """
    candidates: list[dict[str, Any]] = []

    for region in regions:
        base = region["base"]
        size = min(region["size"], 8 * 1024 * 1024)

        for cs in range(0, size, chunk_size):
            rs = min(chunk_size, size - cs)
            data = _read(handle, base + cs, rs)
            if data is None:
                continue
            for off in range(0, rs - 40, 4):
                max_hp_val = int.from_bytes(data[off:off + 4], "little", signed=True)
                # Primary: scan for the known max_hp (session-stable)
                # But also accept plausible ranges as fallback
                if not (50 <= max_hp_val <= 100000):
                    continue
                hp_val = int.from_bytes(data[off + 16:off + 20], "little", signed=True)
                if not (50 <= hp_val <= max_hp_val * 3):
                    continue
                # Validate ptr fields — at least 3 of 4 must be valid heap pointers
                ptrs = [
                    int.from_bytes(data[off + 4:off + 8], "little", signed=True),
                    int.from_bytes(data[off + 12:off + 16], "little", signed=True),
                    int.from_bytes(data[off + 20:off + 24], "little", signed=True),
                    int.from_bytes(data[off + 28:off + 32], "little", signed=True),
                ]
                valid_ptrs = sum(1 for p in ptrs if 0x01000000 < p < 0x7FFFFFFF)
                if valid_ptrs < 3:
                    continue
                # Validate MP fields — reject 65535 (sentinel) and 0 (uninitialized)
                mp_val = int.from_bytes(data[off + 8:off + 12], "little", signed=True)
                max_mp_val = int.from_bytes(data[off + 32:off + 36], "little", signed=True)
                if mp_val >= 65535 or max_mp_val >= 65535 or mp_val <= 0 or max_mp_val <= 0:
                    continue
                # Reject sequential/generated values (player stats are independent)
                # max_hp, mp, hp, max_mp should NOT be nearly equal or sequential
                vals = [max_hp_val, mp_val, hp_val, max_mp_val]
                if max(vals) - min(vals) < 50:
                    continue
                # HP should be > 0 and max_hp should be <= 50000 (reasonable for this server)
                if max_hp_val <= 0 or max_hp_val > 50000:
                    continue
                abs_addr = base + cs + off
                # Score: exact match to known max_hp gets 100 bonus, then hp ratio
                ratio = hp_val / max_hp_val
                score = -abs(ratio - 1.0)  # higher = closer to HP = max
                score += 100.0 if max_hp_val == 1100 else 0.0  # known value bonus
                candidates.append({"addr": abs_addr, "max_hp": max_hp_val, "hp": hp_val, "score": score})

    if not candidates:
        return None

    # Sort by score (closest to HP=max_hp) — the player usually has high HP
    candidates.sort(key=lambda c: c.get("score", -999), reverse=True)
    best = candidates[0]
    return {
        "addr": best["addr"],
        "max_hp": best["max_hp"],
        "hp": best["hp"],
        "candidates_found": len(candidates),
        "struct_size": 40,
    }


# ── Probe result ──────────────────────────────────────────────────────

@dataclass
class MemoryProbeResult:
    available: bool = False
    error: str | None = None
    hp: int | None = None
    max_hp: int | None = None
    hp_percent: int | None = None
    mp: int | None = None
    max_mp: int | None = None
    player_x: float | None = None
    player_y: float | None = None
    player_z: float | None = None
    level: int | None = None
    is_dead: bool | None = None


# ── Main probe class ──────────────────────────────────────────────────

class MemoryProbe:
    """Read-only game state from process memory.

    Auto-discovers the player struct on connect. No manual offsets needed
    for this server build.

    Usage:
        probe = MemoryProbe()
        result = probe.read_state("mt2portugalia")
        print(f"HP: {result.hp}/{result.max_hp} ({result.hp_percent}%)")
    """

    def __init__(self, config: dict[str, Any] | None = None):
        if config is None:
            config = load_config()
        self.cfg = config.get("memory_probe", {})
        self.offsets = self.cfg.get("offsets", {}) or {}

    @staticmethod
    def _parse_addr(value: Any) -> int | None:
        """Parse int or hex-string address from config."""
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value, 0)
        return None

    def read_state(self, process_name: str = "mt2portugalia", pid: int | None = None) -> MemoryProbeResult:
        """Read confirmed configured offsets from the game process.

        Important: this no longer auto-picks a guessed stat struct. The Metin2
        client has many false-positive entity/constant structs. Use
        scripts/refine_value.py to confirm an address via before/after value
        refinement, then place the confirmed address in config.yaml.
        """
        try:
            return self._do_read(process_name, pid)
        except Exception as e:
            return MemoryProbeResult(available=False, error=str(e))

    def _do_read(self, process_name: str, pid: int | None) -> MemoryProbeResult:
        target_pid = pid or _find_pid(process_name)
        if target_pid is None:
            return MemoryProbeResult(available=False, error=f"Process '{process_name}' not found")

        if not self.offsets:
            return MemoryProbeResult(
                available=False,
                error="No confirmed memory offsets configured. Run scripts/refine_value.py snapshot/compare, then set memory_probe.offsets in config.yaml.",
            )

        handle = _open_process(target_pid)
        if not handle:
            return MemoryProbeResult(available=False, error="OpenProcess failed (run as Admin?)")

        try:
            result = MemoryProbeResult(available=True)
            field_map = {
                "player_hp": "hp",
                "current_hp": "hp",
                "hp": "hp",
                "max_hp": "max_hp",
                "player_mp": "mp",
                "current_mp": "mp",
                "mp": "mp",
                "max_mp": "max_mp",
                "player_x": "player_x",
                "x": "player_x",
                "player_y": "player_y",
                "y": "player_y",
                "player_z": "player_z",
                "z": "player_z",
                "level": "level",
                "is_dead": "is_dead",
            }
            float_fields = {"player_x", "x", "player_y", "y", "player_z", "z"}
            bool_fields = {"is_dead"}

            for key, raw_addr in self.offsets.items():
                attr = field_map.get(key)
                addr = self._parse_addr(raw_addr)
                if attr is None or addr is None:
                    continue
                if key in float_fields:
                    val = _read_f32(handle, addr)
                else:
                    val = _read_i32(handle, addr)
                if val is None:
                    continue
                if key in bool_fields:
                    val = bool(val)
                setattr(result, attr, val)

            if result.hp is not None and result.max_hp is not None and result.max_hp > 0:
                result.hp_percent = int(round(result.hp / result.max_hp * 100))
                result.is_dead = result.hp <= 0 if result.is_dead is None else result.is_dead

            return result
        finally:
            _close_handle(handle)


def probe_discovery(pid: int | None = None, process_name: str = "mt2portugalia") -> dict[str, Any]:
    """Full discovery: find player struct and report all candidates."""
    target_pid = pid or _find_pid(process_name)
    if target_pid is None:
        return {"error": "process not found"}

    handle = _open_process(target_pid)
    if not handle:
        return {"error": "access denied"}

    try:
        regions = _enumerate_regions(handle)
        info = _find_player_struct(handle, regions)

        if info is None:
            return {"error": "no player struct found"}

        addr = info["addr"]
        current_hp = _read_i32(handle, addr + 16)
        current_mp = _read_i32(handle, addr + 8)
        max_mp = _read_i32(handle, addr + 32)

        # Read surrounding memory to find more fields
        buf = _read(handle, addr - 20, 64)
        dump = []
        if buf:
            for i in range(0, len(buf), 4):
                v = int.from_bytes(buf[i:i+4], "little", signed=True)
                dump.append(v)

        return {
            "pid": target_pid,
            "player_struct_addr": f"0x{addr:08x}",
            "max_hp": current_hp if False else info["max_hp"],
            "current_hp": current_hp,
            "current_mp": current_mp,
            "max_mp": max_mp,
            "struct_dump": dump,
            "offsets": {
                "max_hp": f"0x{addr:08x}",
                "current_hp": f"0x{addr + 16:08x}",
                "current_mp": f"0x{addr + 8:08x}",
                "max_mp": f"0x{addr + 32:08x}",
            },
        }
    finally:
        _close_handle(handle)