from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageGrab

@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    process_name: str
    title: str
    bbox: tuple[int, int, int, int]

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    @property
    def region_arg(self) -> str:
        left, top, _right, _bottom = self.bbox
        return f"{left},{top},{self.width},{self.height}"


def _ensure_windows() -> None:
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("window capture discovery is only available on Windows")


def _window_text(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    # Prefer DWM extended frame bounds when available; it avoids thick invisible borders.
    try:
        dwmapi = ctypes.windll.dwmapi
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        if dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        ) != 0:
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
    except Exception:
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    bbox = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    if any(abs(v) > 10000 for v in bbox):
        fallback = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(fallback)):
            bbox = (int(fallback.left), int(fallback.top), int(fallback.right), int(fallback.bottom))
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _process_name(pid: int) -> str:
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        query = kernel32.QueryFullProcessImageNameW
        query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        query.restype = wintypes.BOOL
        if query(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name
        return ""
    finally:
        kernel32.CloseHandle(handle)


def list_windows() -> list[WindowInfo]:
    """List visible top-level Windows windows with title, process name, pid, and bbox."""
    _ensure_windows()
    user32 = ctypes.windll.user32
    windows: list[WindowInfo] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        title = _window_text(hwnd).strip()
        bbox = _window_rect(hwnd)
        if not bbox:
            return True
        if not title and (bbox[2] - bbox[0] < 100 or bbox[3] - bbox[1] < 100):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = _process_name(int(pid.value))
        windows.append(WindowInfo(int(hwnd), int(pid.value), process_name, title, bbox))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return windows


def find_window(query: str) -> WindowInfo:
    """Find a visible top-level window by case-insensitive title/process substring."""
    needle = query.casefold().strip()
    if not needle:
        raise ValueError("window query must not be empty")

    matches = [
        w
        for w in list_windows()
        if needle in w.title.casefold() or needle in w.process_name.casefold() or needle == str(w.pid)
    ]
    if not matches:
        available = [
            f"pid={w.pid} process={w.process_name!r} title={w.title!r} bbox={w.bbox}"
            for w in list_windows()
            if w.title or w.process_name
        ][:30]
        raise RuntimeError("no visible window matched query " + repr(query) + "\nAvailable windows:\n" + "\n".join(available))
    # Prefer sizeable windows over patchers/utility windows if the query matches more than one.
    matches.sort(key=lambda w: (w.width * w.height, bool(w.title)), reverse=True)
    return matches[0]


def foreground_window_hwnd() -> int:
    _ensure_windows()
    return int(ctypes.windll.user32.GetForegroundWindow())


def is_foreground_window(window: WindowInfo) -> bool:
    try:
        return foreground_window_hwnd() == int(window.hwnd)
    except Exception:
        return False


def activate_window(window: WindowInfo) -> None:
    """Restore and foreground a window before a screen-region capture."""
    _ensure_windows()
    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    hwnd = wintypes.HWND(window.hwnd)
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


def is_plausible_game_window(window: WindowInfo, *, min_width: int = 640, min_height: int = 480, max_abs_origin: int = 10000) -> bool:
    """Reject stale/offscreen hwnd geometry that would poison mouse-relative learning."""
    left, top, right, bottom = window.bbox
    return (
        window.width >= min_width
        and window.height >= min_height
        and abs(left) <= max_abs_origin
        and abs(top) <= max_abs_origin
        and abs(right) <= max_abs_origin
        and abs(bottom) <= max_abs_origin
    )


def capture_window_screen_image(window: WindowInfo, output_path: str | Path) -> Path:
    """Capture the visible desktop region for DirectX game windows.

    PrintWindow often returns only title-bar/chrome for MT2Portugalia's DirectX
    surface. For manual training recordings the game window is visible, so a
    desktop crop is the reliable learning image.
    """
    if not is_plausible_game_window(window):
        raise RuntimeError(f"implausible window geometry for screen crop: {window.bbox}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image = ImageGrab.grab(bbox=window.bbox).convert("RGB")
    image.save(output)
    return output


def capture_window_image(window: WindowInfo, output_path: str | Path, *, backend: str = "screen") -> Path:
    """Capture a specific window.

    backend='screen' is the default for DirectX game training because it captures
    the actual visible game surface. backend='printwindow' keeps the old HWND
    path for hidden/overlapped diagnostic captures.
    """
    if backend == "screen":
        return capture_window_screen_image(window, output_path)
    return capture_window_printwindow_image(window, output_path)


def capture_window_printwindow_image(window: WindowInfo, output_path: str | Path) -> Path:
    """Capture a specific window HWND using Win32 PrintWindow, avoiding overlapping windows when supported."""
    _ensure_windows()
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hwnd = wintypes.HWND(window.hwnd)
    width, height = window.width, window.height
    if width <= 0 or height <= 0:
        raise RuntimeError(f"invalid window size for capture: {window.bbox}")

    hdc_window = user32.GetWindowDC(hwnd)
    if not hdc_window:
        raise RuntimeError("GetWindowDC failed")
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    if not hdc_mem:
        user32.ReleaseDC(hwnd, hdc_window)
        raise RuntimeError("CreateCompatibleDC failed")
    bitmap = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    if not bitmap:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)
        raise RuntimeError("CreateCompatibleBitmap failed")

    old_obj: Any = None
    try:
        old_obj = gdi32.SelectObject(hdc_mem, bitmap)
        # 0x2 = PW_RENDERFULLCONTENT on newer Windows. If DirectX refuses PrintWindow,
        # fall back to BitBlt from the window DC; callers can inspect the result.
        rendered = user32.PrintWindow(hwnd, hdc_mem, 0x2)
        backend = "PrintWindow"
        if not rendered:
            SRCCOPY = 0x00CC0020
            if not gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_window, 0, 0, SRCCOPY):
                raise RuntimeError("PrintWindow and BitBlt failed")
            backend = "BitBlt"

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(hdc_mem, bitmap, 0, height, buffer, ctypes.byref(bmi), 0)
        if rows != height:
            raise RuntimeError(f"GetDIBits returned {rows}, expected {height}")
        image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1).convert("RGB")
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output)
        # Attach lightweight metadata for callers that keep the object around.
        image.info["capture_backend"] = backend
        return output
    finally:
        if old_obj:
            gdi32.SelectObject(hdc_mem, old_obj)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="List or locate visible Windows windows for screenshot cropping.")
    parser.add_argument("--query", help="Title/process/pid substring to locate")
    args = parser.parse_args(argv)

    windows = [w.__dict__ | {"width": w.width, "height": w.height, "region_arg": w.region_arg} for w in list_windows()]
    if args.query:
        w = find_window(args.query)
        print(json.dumps(w.__dict__ | {"width": w.width, "height": w.height, "region_arg": w.region_arg}, indent=2))
    else:
        print(json.dumps(windows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
