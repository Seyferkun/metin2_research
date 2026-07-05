from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .schema import ClientState, ProcessInfo, WindowInfo


DEFAULT_TITLE_KEYWORDS = ("metin", "mt2", "portugalia", "pgclient")


@dataclass
class ProcessWindowSnapshot:
    pid: int | None = None
    process_name: str | None = None
    executable_path: str | None = None
    hwnd: int | None = None
    window_title: str | None = None
    focused: bool | None = None
    rect: list[int] | None = None


class ProcessWindowProbe:
    """Read-only OS metadata probe for local Metin2 process/window state.

    This adapter uses Windows user32/kernel32 metadata only. It does not read
    process memory, inspect packets, inject code, or touch server-side state.
    """

    name = "process_window_probe"

    def __init__(self, title_keywords: Iterable[str] = DEFAULT_TITLE_KEYWORDS):
        self.title_keywords = tuple(keyword.lower() for keyword in title_keywords)

    def read(self) -> ClientState:
        snapshot = find_metin_window(self.title_keywords)
        if snapshot is None:
            return ClientState(
                sources={self.name: "read_only_os_metadata"},
                warnings=["No matching Metin2 client window found."],
            )
        return snapshot_to_client_state(snapshot)


def snapshot_to_client_state(snapshot: ProcessWindowSnapshot) -> ClientState:
    return ClientState(
        process=ProcessInfo(
            pid=snapshot.pid,
            name=snapshot.process_name,
            executable_path=snapshot.executable_path,
        ),
        window=WindowInfo(
            hwnd=snapshot.hwnd,
            title=snapshot.window_title,
            focused=snapshot.focused,
            rect=snapshot.rect,
        ),
        sources={"process_window_probe": "read_only_os_metadata"},
    )


def find_metin_window(title_keywords: Iterable[str] = DEFAULT_TITLE_KEYWORDS) -> ProcessWindowSnapshot | None:
    if not hasattr(ctypes, "WinDLL"):
        return None

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    foreground = user32.GetForegroundWindow()
    matches: list[ProcessWindowSnapshot] = []
    keywords = tuple(keyword.lower() for keyword in title_keywords)

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(user32, hwnd)
        if not title:
            return True
        haystack = title.lower()
        if not any(keyword in haystack for keyword in keywords):
            return True
        pid = _window_pid(user32, hwnd)
        executable = _process_executable_path(pid)
        process_name = Path(executable).name if executable else None
        matches.append(
            ProcessWindowSnapshot(
                pid=pid,
                process_name=process_name,
                executable_path=executable,
                hwnd=int(hwnd),
                window_title=title,
                focused=int(hwnd) == int(foreground),
                rect=_window_rect(user32, hwnd),
            )
        )
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    focused = [match for match in matches if match.focused]
    return (focused or matches or [None])[0]


def _window_title(user32, hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _window_pid(user32, hwnd) -> int | None:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) if pid.value else None


def _window_rect(user32, hwnd) -> list[int] | None:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]


def _process_executable_path(pid: int | None) -> str | None:
    if not pid or not hasattr(ctypes, "WinDLL"):
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return None
    finally:
        kernel32.CloseHandle(handle)
