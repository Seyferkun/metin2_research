#!/usr/bin/env python
"""Dashboard-managed direct key test / timed key macro for MT2Portugalia.

Private Yoshy sandbox only. Dry-run logs intent only. Live mode focuses the
configured local game window and sends scan-code key taps through SendInput.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for _path in (PROJECT_ROOT, SRC_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from metin2_research.win_input import SCANCODES, tap_chord, tap_key
from metin2_research.window_capture import WindowInfo, find_window


def should_stop(stop_file: Path | None) -> bool:
    return bool(stop_file and stop_file.exists())


def validate_key(key: str) -> str:
    key = str(key or "").strip().lower()
    parts = [part.strip() for part in key.split("+") if part.strip()]
    if not parts:
        raise ValueError("unsupported empty key")
    unknown = [part for part in parts if part not in SCANCODES]
    if unknown:
        allowed = ", ".join(sorted(SCANCODES))
        raise ValueError(f"unsupported key/chord {key!r}; unknown={unknown}; allowed: {allowed}")
    return "+".join(parts)


def tap_key_or_chord(key: str, hold: float = 0.06) -> None:
    parts = [part.strip() for part in validate_key(key).split("+") if part.strip()]
    if len(parts) == 1:
        tap_key(parts[0], hold=hold)
    else:
        tap_chord(parts, hold=hold)


def is_admin() -> bool:
    if not hasattr(ctypes, "windll"):
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def shell_quote_win(arg: str) -> str:
    return subprocess.list2cmdline([str(arg)])


def relaunch_elevated(argv: list[str], cwd: Path) -> int:
    """Relaunch this script with UAC so it can send input to an elevated game client, then wait."""
    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("fMask", ctypes.c_ulong),
            ("hwnd", ctypes.c_void_p),
            ("lpVerb", ctypes.c_wchar_p),
            ("lpFile", ctypes.c_wchar_p),
            ("lpParameters", ctypes.c_wchar_p),
            ("lpDirectory", ctypes.c_wchar_p),
            ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.c_void_p),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", ctypes.c_wchar_p),
            ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", ctypes.c_ulong),
            ("hIcon", ctypes.c_void_p),
            ("hProcess", ctypes.c_void_p),
        ]
    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    INFINITE = 0xFFFFFFFF
    params = subprocess.list2cmdline([str(Path(__file__).resolve()), *argv])
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = params
    info.lpDirectory = str(cwd)
    info.nShow = 1
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        raise RuntimeError(f"ShellExecuteExW runas failed with WinError {err}")
    print("relaunched_elevated_for_live_key_macro approve_windows_uac_prompt waiting_for_elevated_child", flush=True)
    if info.hProcess:
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, INFINITE)
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
        print(f"elevated_child_exit_code {exit_code.value}", flush=True)
        return int(exit_code.value)
    return 0


def _window_title(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def foreground_info() -> tuple[int, str]:
    hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    return hwnd, _window_title(hwnd) if hwnd else ""


def robust_activate_window(window: WindowInfo, *, retries: int = 5) -> bool:
    """Best-effort foregrounding for elevated game windows before global SendInput."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = int(window.hwnd)
    SW_RESTORE = 9
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    current_thread = kernel32.GetCurrentThreadId()
    for attempt in range(1, max(1, retries) + 1):
        before_hwnd, before_title = foreground_info()
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetActiveWindow(hwnd)
        attached = False
        foreground_thread = user32.GetWindowThreadProcessId(before_hwnd, None) if before_hwnd else 0
        if target_thread and current_thread:
            attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))
            if foreground_thread and foreground_thread != target_thread:
                user32.AttachThreadInput(current_thread, foreground_thread, True)
        try:
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, target_thread, False)
            if foreground_thread and foreground_thread != target_thread:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
        after_hwnd, after_title = foreground_info()
        print(
            json.dumps(
                {
                    "event": "focus_attempt",
                    "attempt": attempt,
                    "target_hwnd": hwnd,
                    "foreground_before": before_hwnd,
                    "foreground_before_title": before_title,
                    "foreground_after": after_hwnd,
                    "foreground_after_title": after_title,
                    "focused": after_hwnd == hwnd,
                }
            ),
            flush=True,
        )
        if after_hwnd == hwnd:
            return True
        # Neutral ALT tap often unlocks Windows foreground restrictions for the caller.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 0x0002, 0)
        time.sleep(0.12)
    return False


def run_key_macro(
    *,
    key: str,
    live: bool,
    interval_seconds: float,
    presses: int,
    hold_seconds: float,
    window_query: str,
    stop_file: Path | None,
) -> dict:
    key = validate_key(key)
    interval_seconds = max(0.05, float(interval_seconds))
    hold_seconds = max(0.01, float(hold_seconds))
    presses = int(presses)
    unlimited = presses <= 0
    count = 0
    window = None
    if live:
        window = find_window(window_query)
        focused = robust_activate_window(window)
        print(f"live_key_macro window={window.title!r} pid={window.pid} hwnd={window.hwnd} key={key} interval={interval_seconds:g}s presses={'infinite' if unlimited else presses} focused={focused}", flush=True)
    else:
        print(f"dry_run_key_macro key={key} interval={interval_seconds:g}s presses={'infinite' if unlimited else presses}", flush=True)

    started = time.time()
    while unlimited or count < presses:
        if should_stop(stop_file):
            print("stop_file_seen", flush=True)
            break
        count += 1
        if live:
            # Re-focus before every tap so manual testing survives accidental focus changes.
            if window is not None:
                focused = robust_activate_window(window, retries=3)
            else:
                focused = False
            tap_key_or_chord(key, hold=hold_seconds)
            action = "tap_key"
        else:
            action = "dry_run_tap_key"
        print(
            json.dumps(
                {
                    "event": "key_macro_press",
                    "key": key,
                    "press": count,
                    "action": action,
                    "live": live,
                    "interval_seconds": interval_seconds,
                    "hold_seconds": hold_seconds,
                    "foreground_focused": focused if live else None,
                }
            ),
            flush=True,
        )
        if not unlimited and count >= presses:
            break
        deadline = time.time() + interval_seconds
        while time.time() < deadline:
            if should_stop(stop_file):
                print("stop_file_seen", flush=True)
                return {"key": key, "presses_completed": count, "stopped": True, "duration_seconds": round(time.time() - started, 3)}
            time.sleep(min(0.1, max(0.01, deadline - time.time())))
    return {"key": key, "presses_completed": count, "stopped": False, "duration_seconds": round(time.time() - started, 3)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dashboard-managed direct key press / timed key macro")
    parser.add_argument("--live", action="store_true", help="Actually focus MT2Portugalia and send key taps. Default is dry-run logging only.")
    parser.add_argument("--key", default="f1", help="Key to tap, e.g. f1 or f2")
    parser.add_argument("--interval-seconds", type=float, default=35.0, help="Seconds between taps when presses > 1 or presses <= 0")
    parser.add_argument("--presses", type=int, default=1, help="Number of key taps; <=0 runs until stopped")
    parser.add_argument("--hold-seconds", type=float, default=0.06, help="How long to hold the key per tap")
    parser.add_argument("--window-query", default="MT2Portugalia", help="Window title/process query to focus in live mode")
    parser.add_argument("--out", default="reports/dashboard_runs/key_macro_control.json", help="Summary JSON path")
    parser.add_argument("--elevate", action="store_true", help="In live mode, relaunch this key sender elevated via UAC before sending input")
    parser.add_argument("--elevated-log", help="Path where the elevated child writes detailed focus/send diagnostics")
    args = parser.parse_args(argv)

    if args.live and args.elevate and not is_admin():
        relaunch_argv = list(sys.argv[1:] if argv is None else argv)
        if "--elevated-log" not in relaunch_argv:
            relaunch_argv.extend(["--elevated-log", str(Path(args.out).with_suffix(".elevated.log"))])
        return relaunch_elevated(relaunch_argv, PROJECT_ROOT)

    if args.elevated_log:
        log_path = Path(args.elevated_log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("a", encoding="utf-8", buffering=1)
        sys.stdout = log
        sys.stderr = log
        print(f"elevated_child_log_started admin={is_admin()} argv={sys.argv[1:]}", flush=True)

    stop_file = Path(os.environ["HERMES_STOP_FILE"]) if os.environ.get("HERMES_STOP_FILE") else None
    try:
        result = run_key_macro(
            key=args.key,
            live=args.live,
            interval_seconds=args.interval_seconds,
            presses=args.presses,
            hold_seconds=args.hold_seconds,
            window_query=args.window_query,
            stop_file=stop_file,
        )
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
        raise
    result["live"] = bool(args.live)
    result["window_query"] = args.window_query
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("summary " + json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
