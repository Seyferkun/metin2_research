#!/usr/bin/env python
"""Send keyboard scan-code input to the Metin2 window.

This avoids mouse clicks and uses SendInput KEYEVENTF_SCANCODE, which games often
accept more reliably than virtual-key keybd_event.
"""

from __future__ import annotations

import argparse
import ctypes
import time
from ctypes import wintypes

from metin2_research.window_capture import activate_window, find_window

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
SCANCODES = {
    "w": 0x11,
    "a": 0x1E,
    "s": 0x1F,
    "d": 0x20,
    "q": 0x10,
    "e": 0x12,
    "c": 0x2E,
    "i": 0x17,
    "space": 0x39,
    "1": 0x02,
    "tab": 0x0F,
    "m": 0x32,
}


def send_scan(scancode: int, keyup: bool = False) -> None:
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if keyup else 0)
    inp = INPUT(type=INPUT_KEYBOARD, union=INPUTUNION(ki=KEYBDINPUT(0, scancode, flags, 0, 0)))
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    if sent != 1:
        raise ctypes.WinError()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("key", choices=sorted(SCANCODES))
    ap.add_argument("--seconds", type=float, default=1.0)
    ap.add_argument("--window", default="MT2Portugalia")
    args = ap.parse_args()

    window = find_window(args.window)
    activate_window(window)
    sc = SCANCODES[args.key]
    send_scan(sc, False)
    time.sleep(args.seconds)
    send_scan(sc, True)
    print(f"sent scan-code key={args.key} seconds={args.seconds} window={window}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
