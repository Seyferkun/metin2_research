from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

# SendInput keyboard flags. Scan-code input is more reliable with DirectX games
# than legacy keybd_event virtual-key injection.
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

SCANCODES = {
    "1": 0x02,
    "f1": 0x3B,
    "space": 0x39,
    "esc": 0x01,
    "tab": 0x0F,
    "q": 0x10,
    "w": 0x11,
    "e": 0x12,
    "r": 0x13,
    "t": 0x14,
    "a": 0x1E,
    "s": 0x1F,
    "d": 0x20,
    "f": 0x21,
    "g": 0x22,
    "z": 0x2C,
    "m": 0x32,
}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


def _send_input(inp: INPUT) -> None:
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if sent != 1:
        raise ctypes.WinError()


def _keyboard_input(scancode: int, *, keyup: bool = False) -> INPUT:
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if keyup else 0)
    return INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(0, scancode, flags, 0, None)))


def key_down(key: str) -> None:
    _send_input(_keyboard_input(SCANCODES[key.lower()], keyup=False))


def key_up(key: str) -> None:
    _send_input(_keyboard_input(SCANCODES[key.lower()], keyup=True))


def tap_key(key: str, hold: float = 0.06) -> None:
    key_down(key)
    time.sleep(hold)
    key_up(key)


def hold_key(key: str, duration: float) -> None:
    key_down(key)
    try:
        time.sleep(duration)
    finally:
        key_up(key)


class SmoothMover:
    """Hold directional keys across navigation cycles and only change deltas."""

    def __init__(self):
        self.held: set[str] = set()

    def move(self, keys: set[str], hold_seconds: float) -> None:
        keys = {str(key).lower() for key in keys}
        for key in sorted(self.held - keys):
            key_up(key)
        for key in sorted(keys - self.held):
            key_down(key)
        self.held = set(keys)
        time.sleep(float(hold_seconds))

    def release_all(self) -> None:
        for key in sorted(self.held):
            key_up(key)
        self.held = set()


def hold_key_with_periodic_tap(key: str, duration: float, *, tap: str = "1", tap_every: float = 5.0) -> int:
    key_down(key)
    taps = 0
    start = time.monotonic()
    next_tap = start + tap_every
    try:
        while time.monotonic() - start < duration:
            now = time.monotonic()
            if now >= next_tap:
                key_up(key)
                tap_key(tap)
                taps += 1
                key_down(key)
                next_tap = now + tap_every
            time.sleep(0.05)
    finally:
        key_up(key)
    return taps


def click_xy(x: int, y: int, *, clicks: int = 1, delay: float = 0.12) -> None:
    user32 = ctypes.windll.user32
    for _ in range(clicks):
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(delay)
