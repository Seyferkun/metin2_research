from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

# Match coordinates from screenshots/window rectangles. Without process DPI awareness,
# Windows can report a 1536x864 scaled desktop while screenshots/window bboxes are
# 1920x1080 physical pixels; absolute SendInput then lands far below/right.
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

# SendInput keyboard flags. Scan-code input is more reliable with DirectX games
# than legacy keybd_event virtual-key injection.
INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SCANCODES = {
    "1": 0x02,
    "f1": 0x3B,
    "f2": 0x3C,
    "f3": 0x3D,
    "f4": 0x3E,
    "space": 0x39,
    "esc": 0x01,
    "escape": 0x01,
    "tab": 0x0F,
    "ctrl": 0x1D,
    "lctrl": 0x1D,
    "q": 0x10,
    "w": 0x11,
    "e": 0x12,
    "r": 0x13,
    "t": 0x14,
    "a": 0x1E,
    "s": 0x1F,
    "d": 0x20,
    "f": 0x21,
    "i": 0x17,
    "c": 0x2E,
    "g": 0x22,
    "z": 0x2C,
    "x": 0x2D,
    "m": 0x32,
}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
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
    return INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(0, scancode, flags, 0, 0)))


def key_down(key: str) -> None:
    _send_input(_keyboard_input(SCANCODES[key.lower()], keyup=False))


def key_up(key: str) -> None:
    _send_input(_keyboard_input(SCANCODES[key.lower()], keyup=True))


def tap_key(key: str, hold: float = 0.06) -> None:
    key_down(key)
    time.sleep(hold)
    key_up(key)


def tap_chord(keys: list[str] | tuple[str, ...], hold: float = 0.06) -> None:
    keys = [str(key).lower() for key in keys]
    for key in keys:
        key_down(key)
    time.sleep(hold)
    for key in reversed(keys):
        key_up(key)


def _mouse_input(dx: int, dy: int, flags: int) -> INPUT:
    return INPUT(type=INPUT_MOUSE, union=INPUT_UNION(mi=MOUSEINPUT(dx, dy, 0, flags, 0, 0)))


def _absolute_mouse_coords(x: int, y: int) -> tuple[int, int]:
    user32 = ctypes.windll.user32
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79
    vx = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    vy = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    vw = max(1, int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)))
    vh = max(1, int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))
    ax = round((int(x) - vx) * 65535 / max(1, vw - 1))
    ay = round((int(y) - vy) * 65535 / max(1, vh - 1))
    return max(0, min(65535, ax)), max(0, min(65535, ay))


def move_mouse_to(x: int, y: int) -> None:
    """Move the cursor, falling back to absolute SendInput when SetCursorPos is blocked."""
    user32 = ctypes.windll.user32
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = ctypes.c_bool
    if user32.SetCursorPos(int(x), int(y)):
        return
    ax, ay = _absolute_mouse_coords(int(x), int(y))
    _send_input(_mouse_input(ax, ay, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK))


def click_at(x: int, y: int, hold: float = 0.03) -> None:
    move_mouse_to(int(x), int(y))
    down = _mouse_input(0, 0, MOUSEEVENTF_LEFTDOWN)
    up = _mouse_input(0, 0, MOUSEEVENTF_LEFTUP)
    _send_input(down)
    time.sleep(hold)
    _send_input(up)


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
    for _ in range(clicks):
        click_at(int(x), int(y), hold=0.05)
        time.sleep(delay)
