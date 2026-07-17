#!/usr/bin/env python
"""Bounded live channel-switch calibration for MT2Portugalia.

Presses X, clicks each configured channel menu row, waits between attempts,
and records screenshots/state. No combat, movement, or potion keys.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for _path in (PROJECT_ROOT, SRC_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from metin2_research.win_input import click_at, tap_key
from metin2_research.window_capture import capture_window_image, find_window
from fixed_sapo_space_control import parse_channel_click_points, channel_click_screen_point, robust_activate_window
from key_macro_control import is_admin


def relaunch_this_script_elevated(argv: list[str], cwd: Path) -> int:
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
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = subprocess.list2cmdline([str(Path(__file__).resolve()), *argv])
    info.lpDirectory = str(cwd)
    info.nShow = 1
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    print("relaunched_elevated_for_channel_switch approve_windows_uac_prompt waiting_for_elevated_child", flush=True)
    if info.hProcess:
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
        print(f"elevated_child_exit_code {exit_code.value}", flush=True)
        return int(exit_code.value)
    return 0

DEFAULT_STATE_JSON = Path(r"D:/Games/MT2Portugalia/app/hermes_state.json")
DEFAULT_POINTS = "0.4990,0.3986;0.4990,0.4326;0.4990,0.4665;0.4990,0.5005;0.4990,0.5344;0.4990,0.5684;0.4990,0.6023;0.4990,0.6363"


def read_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        data["_file_mtime"] = path.stat().st_mtime
        data["_age_seconds"] = max(0.0, time.time() - path.stat().st_mtime)
        return data
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def slim_state(state: dict[str, Any]) -> dict[str, Any]:
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    nearby = state.get("nearby_entities") if isinstance(state.get("nearby_entities"), list) else []
    return {
        "map": state.get("map"),
        "age_seconds": round(float(state.get("_age_seconds", 0.0)), 3) if state.get("_age_seconds") is not None else None,
        "player": {k: player.get(k) for k in ("name", "x", "y", "hp", "max_hp")},
        "target": {k: target.get(k) for k in ("name", "vid", "alive")},
        "nearby_count": len(nearby),
    }


def emit(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(event, ensure_ascii=False, default=str), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-query", default="MT2Portugalia")
    ap.add_argument("--json-state", type=Path, default=DEFAULT_STATE_JSON)
    ap.add_argument("--channel-click-points", default=DEFAULT_POINTS)
    ap.add_argument("--wait-seconds", type=float, default=5.0)
    ap.add_argument("--menu-delay-seconds", type=float, default=0.8)
    ap.add_argument("--out", type=Path, default=Path("reports/dashboard_runs/channel_switch_all_test.jsonl"))
    ap.add_argument("--summary-out", type=Path, default=Path("reports/dashboard_runs/channel_switch_all_test_summary.json"))
    ap.add_argument("--screenshot-dir", type=Path, default=Path("reports/dashboard_runs/channel_switch_all_test_screens"))
    ap.add_argument("--start-index", type=int, default=0)
    ap.add_argument("--max-count", type=int, default=0, help="0 means all points")
    ap.add_argument("--elevate", action="store_true", help="relaunch through UAC so mouse/key input can reach an elevated game client")
    ap.add_argument("--assume-menu-open", action="store_true", help="do not press X before each click; use when the channel menu is already open for calibration")
    args = ap.parse_args()

    if args.elevate and not is_admin():
        argv = [arg for arg in sys.argv[1:] if arg != "--elevate"]
        return relaunch_this_script_elevated(argv, PROJECT_ROOT)

    args.out.write_text("", encoding="utf-8")
    args.screenshot_dir.mkdir(parents=True, exist_ok=True)
    points = parse_channel_click_points(args.channel_click_points)
    if not points:
        raise SystemExit("no channel points configured")
    indices = list(range(len(points)))
    if args.start_index:
        indices = indices[args.start_index:] + indices[:args.start_index]
    if args.max_count > 0:
        indices = indices[: args.max_count]

    run_id = f"channel_all_{int(time.time())}"
    attempts: list[dict[str, Any]] = []
    emit(args.out, {"state": "START", "run_id": run_id, "indices": indices, "wait_seconds": args.wait_seconds, "no_combat": True, "no_movement": True, "no_potion_1": True})

    window = find_window(args.window_query)
    focused = robust_activate_window(window)
    window = find_window(args.window_query)
    pre = args.screenshot_dir / f"{run_id}_pre.jpg"
    capture_window_image(window, pre, backend="screen")
    emit(args.out, {"state": "PREFLIGHT", "focused": focused, "window_bbox": list(window.bbox), "window_size": [window.width, window.height], "state_snapshot": slim_state(read_state(args.json_state)), "screenshot": str(pre)})

    for seq, idx in enumerate(indices, 1):
        window = find_window(args.window_query)
        focused = robust_activate_window(window)
        point = points[idx]
        sx, sy = channel_click_screen_point(window, point)
        before_state = slim_state(read_state(args.json_state))
        before_img = args.screenshot_dir / f"{run_id}_{seq:02d}_idx{idx}_before.jpg"
        after_img = args.screenshot_dir / f"{run_id}_{seq:02d}_idx{idx}_after.jpg"
        try:
            capture_window_image(window, before_img, backend="screen")
        except Exception as exc:
            before_img = Path(f"capture_failed:{exc}")
        emit(args.out, {"state": "ATTEMPT_START", "seq": seq, "channel_index": idx, "point": list(point), "screen_point": [sx, sy], "focused": focused, "before_state": before_state, "before_screenshot": str(before_img)})
        sent_sequence = []
        if not args.assume_menu_open:
            tap_key("x", hold=0.06)
            sent_sequence.append("x")
            time.sleep(max(0.0, args.menu_delay_seconds))
        click_at(sx, sy)
        sent_sequence.append("left_click_channel_point")
        emit(args.out, {"state": "CLICK_SENT", "seq": seq, "channel_index": idx, "screen_point": [sx, sy], "sent_sequence": sent_sequence})
        time.sleep(max(0.0, args.wait_seconds))
        window = find_window(args.window_query)
        after_state = slim_state(read_state(args.json_state))
        try:
            capture_window_image(window, after_img, backend="screen")
        except Exception as exc:
            after_img = Path(f"capture_failed:{exc}")
        attempt = {"seq": seq, "channel_index": idx, "point": list(point), "screen_point": [sx, sy], "before_state": before_state, "after_state": after_state, "after_screenshot": str(after_img)}
        attempts.append(attempt)
        emit(args.out, {"state": "ATTEMPT_DONE", **attempt})

    summary = {"run_id": run_id, "attempt_count": len(attempts), "wait_seconds": args.wait_seconds, "attempts": attempts, "log": str(args.out), "screenshot_dir": str(args.screenshot_dir)}
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    emit(args.out, {"state": "FINISH", "run_id": run_id, "summary": str(args.summary_out), "attempt_count": len(attempts)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
