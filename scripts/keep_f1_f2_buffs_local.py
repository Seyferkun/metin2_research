#!/usr/bin/env python
"""Bounded local MT2Portugalia F1/F2 buff refresher.

Runs elevated when needed because MT2Portugalia/pgclient.app is often elevated
and unelevated SendInput/PostMessage can fail silently or return 0.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

USER32 = ctypes.windll.user32
SHELL32 = ctypes.windll.shell32
KERNEL32 = ctypes.windll.kernel32

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
SCANCODES = {
    "f1": 0x3B,
    "f2": 0x3C,
    "g": 0x22,
    "ctrl": 0x1D,
}

ULONG_PTR = wintypes.WPARAM

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]

def is_admin() -> bool:
    try:
        return bool(SHELL32.IsUserAnAdmin())
    except Exception:
        return False

def relaunch_elevated() -> int:
    params = " ".join(_quote(arg) for arg in sys.argv)
    rc = SHELL32.ShellExecuteW(None, "runas", sys.executable, params, os.getcwd(), 1)
    return int(rc)

def _quote(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'

def send_scan(key: str, *, keyup: bool = False) -> int:
    sc = SCANCODES[key.lower()]
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if keyup else 0)
    inp = INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(0, sc, flags, 0, 0)))
    ctypes.set_last_error(0)
    sent = int(USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))
    return sent

def tap(key: str, hold: float = 0.08) -> tuple[int, int]:
    down = send_scan(key, keyup=False)
    time.sleep(hold)
    up = send_scan(key, keyup=True)
    return down, up

def chord_ctrl_g() -> dict:
    out = {"ctrl_down": send_scan("ctrl", keyup=False)}
    time.sleep(0.04)
    out["g_down"] = send_scan("g", keyup=False)
    time.sleep(0.08)
    out["g_up"] = send_scan("g", keyup=True)
    time.sleep(0.04)
    out["ctrl_up"] = send_scan("ctrl", keyup=True)
    return out

def find_window(title_part: str = "MT2Portugalia") -> int:
    matches: list[int] = []
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        if USER32.IsWindowVisible(hwnd):
            n = USER32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                USER32.GetWindowTextW(hwnd, buf, n + 1)
                if title_part.lower() in buf.value.lower():
                    matches.append(int(hwnd))
        return True
    USER32.EnumWindows(cb, 0)
    return matches[0] if matches else 0

def focus_window(hwnd: int) -> None:
    if not hwnd:
        return
    USER32.ShowWindow(hwnd, 5)
    USER32.BringWindowToTop(hwnd)
    USER32.SetForegroundWindow(hwnd)
    time.sleep(0.15)

def read_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {"exists": False}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return {"exists": True, "error": repr(exc)}
    player = data.get("player") if isinstance(data.get("player"), dict) else {}
    flags = data.get("player_flags") if isinstance(data.get("player_flags"), dict) else {}
    return {
        "exists": True,
        "age_sec": round(time.time() - state_path.stat().st_mtime, 3),
        "map": data.get("map"),
        "player": player.get("name"),
        "hp": player.get("hp"),
        "mounted_state": flags.get("mounted"),
        "buffs_count": len(data.get("buffs") or []),
    }

def emit(log_path: Path, event: dict) -> None:
    event = {"wall_time": round(time.time(), 3), **event}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(json.dumps(event, ensure_ascii=False), flush=True)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=300.0)
    ap.add_argument("--interval", type=float, default=55.0)
    ap.add_argument("--log", type=Path, default=Path("reports/dashboard_runs/f1_f2_buff_keepalive_elevated.jsonl"))
    ap.add_argument("--state", type=Path, default=Path(r"D:/Games/MT2Portugalia/app/hermes_state.json"))
    ap.add_argument("--dismount-first", action="store_true", help="Send Ctrl+G once before first buff only when the operator knows the character is mounted.")
    ap.add_argument("--no-elevate", action="store_true")
    args = ap.parse_args()

    if not is_admin() and not args.no_elevate:
        rc = relaunch_elevated()
        print(f"requested_elevation rc={rc}; approve UAC to keep F1/F2 buffs active", flush=True)
        return 0

    args.log.write_text("", encoding="utf-8")
    hwnd = find_window()
    focus_window(hwnd)
    emit(args.log, {"event": "start", "admin": is_admin(), "hwnd": hwnd, "duration": args.duration, "interval": args.interval, "state": read_state(args.state)})

    if args.dismount_first:
        result = chord_ctrl_g()
        emit(args.log, {"event": "dismount_ctrl_g", "result": result, "state": read_state(args.state)})
        time.sleep(0.8)

    start = time.monotonic()
    next_due = start
    cycle = 0
    while time.monotonic() - start <= args.duration:
        now = time.monotonic()
        if now < next_due:
            time.sleep(min(0.25, next_due - now))
            continue
        cycle += 1
        focus_window(hwnd)
        state = read_state(args.state)
        if state.get("mounted_state") is True:
            emit(args.log, {"event": "skip_mounted", "cycle": cycle, "state": state})
        else:
            f1 = tap("f1")
            time.sleep(0.35)
            f2 = tap("f2")
            emit(args.log, {"event": "buff_refresh", "cycle": cycle, "keys": ["f1", "f2"], "sendinput": {"f1": f1, "f2": f2}, "state": state, "ctrl_g_sent": False})
        next_due += args.interval
    emit(args.log, {"event": "done", "elapsed": round(time.monotonic() - start, 2), "state": read_state(args.state), "log": str(args.log)})
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
