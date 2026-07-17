#!/usr/bin/env python
"""Fixed-spawn Sapo de Pedra Space-only test bench control.

Private Yoshy MT2Portugalia sandbox only. This script deliberately does not
click, move, target-search, or press potion keys. In live mode it focuses the
MT2Portugalia window, holds/pulses Space while the fixed Sapo probe remains
alive, optionally pickups with Z after destruction, and writes a compact
scorecard plus screenshots for control-panel tuning.
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

from metin2_research.win_input import click_at, key_down, key_up, tap_key
from metin2_research.window_capture import WindowInfo, capture_window_image, find_window

DEFAULT_STATE_JSON = Path(r"D:/Games/MT2Portugalia/app/hermes_state.json")
DEFAULT_OUT = Path("reports/dashboard_runs/fixed_sapo_space_control.jsonl")
DEFAULT_SUMMARY = Path("reports/dashboard_runs/fixed_sapo_space_control_summary.json")
DEFAULT_CHANNEL_STATE = Path("reports/dashboard_runs/fixed_sapo_channel_cycle_state.json")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


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
    print("relaunched_elevated_for_fixed_sapo_space approve_windows_uac_prompt waiting_for_elevated_child", flush=True)
    if info.hProcess:
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
        print(f"elevated_child_exit_code {exit_code.value}", flush=True)
        return int(exit_code.value)
    return 0


def read_state(path: Path, *, attempts: int = 5, retry_delay: float = 0.04) -> dict[str, Any]:
    """Read the live client JSON state with short retries for non-atomic writes.

    The MT2 client rewrites hermes_state.json frequently and can briefly expose
    an empty/truncated file. A single transient JSONDecodeError should not stop a
    live sweep that is otherwise healthy, so retry for a small bounded window and
    only raise the final error if every attempt fails.
    """
    last_exc: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            if not text.strip():
                raise json.JSONDecodeError("empty state file", text, 0)
            data = json.loads(text)
            stat = path.stat()
            data["_file_mtime"] = stat.st_mtime
            data["_age_seconds"] = max(0.0, time.time() - stat.st_mtime)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            last_exc = exc
            if attempt + 1 >= max(1, int(attempts)):
                break
            time.sleep(max(0.0, float(retry_delay)))
    assert last_exc is not None
    raise last_exc


def player_from_state(state: dict[str, Any]) -> dict[str, Any]:
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    return player


def _is_sapo_row(row: dict[str, Any]) -> bool:
    return str(row.get("name") or "").lower() == "sapo de pedra"


def _row_pixel_position(row: dict[str, Any]) -> list[Any] | tuple[Any, ...] | None:
    pos = row.get("pixel_position")
    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        return pos
    return None


def _distance_from_player(player: dict[str, Any], row: dict[str, Any]) -> float | None:
    pos = _row_pixel_position(row)
    if pos is None:
        return None
    try:
        dx = float(player.get("x")) - float(pos[0])
        dy = float(player.get("y")) - float(pos[1])
        return (dx * dx + dy * dy) ** 0.5
    except Exception:
        return None


def sapo_probe_from_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return the best current Sapo evidence row.

    The client state can contain both a selected target and broader probe lists.
    During Fixed Sapo sweeps, the selected target is the actionable one: a stale
    or far first row in named_metin_probe must not cause a false
    too_far_from_fixed_spawn skip while the target board has a nearby selected
    Sapo. Prefer alive rows with coordinates closest to the player; otherwise
    keep the old first-matching-row behavior.
    """
    rows: list[dict[str, Any]] = []
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    if target:
        rows.append({**target, "source": target.get("source") or "target"})
    for key in ("named_metin_probe", "nearby_entities"):
        value = state.get(key)
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict):
                    rows.append({**row, "source": row.get("source") or key})
    sapos = [row for row in rows if _is_sapo_row(row)]
    if not sapos:
        return {}
    player = player_from_state(state)
    ranked: list[tuple[int, float, int, dict[str, Any]]] = []
    for idx, row in enumerate(sapos):
        alive_rank = 0 if row.get("alive") is True else 1
        dist = _distance_from_player(player, row)
        dist_rank = dist if dist is not None else float("inf")
        ranked.append((alive_rank, dist_rank, idx, row))
    ranked.sort(key=lambda item: item[:3])
    return ranked[0][3]


def distance_to_sapo(state: dict[str, Any]) -> float | None:
    player = player_from_state(state)
    probe = sapo_probe_from_state(state)
    return _distance_from_player(player, probe)


def target_name(state: dict[str, Any]) -> str | None:
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    name = target.get("name")
    return str(name) if name else None


def hp_value(state: dict[str, Any]) -> int | None:
    player = player_from_state(state)
    try:
        return int(float(player.get("hp")))
    except Exception:
        return None


def emit(out: Path, event: dict[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(event, ensure_ascii=False, default=str), flush=True)


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
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = int(window.hwnd)
    SW_RESTORE = 9
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    current_thread = kernel32.GetCurrentThreadId()
    for _attempt in range(max(1, retries)):
        before_hwnd, _before_title = foreground_info()
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetActiveWindow(hwnd)
        attached_target = False
        foreground_thread = user32.GetWindowThreadProcessId(before_hwnd, None) if before_hwnd else 0
        if target_thread and current_thread:
            attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))
            if foreground_thread and foreground_thread != target_thread:
                user32.AttachThreadInput(current_thread, foreground_thread, True)
        try:
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached_target:
                user32.AttachThreadInput(current_thread, target_thread, False)
            if foreground_thread and foreground_thread != target_thread:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
        after_hwnd, _after_title = foreground_info()
        if after_hwnd == hwnd:
            return True
        time.sleep(0.12)
    return False


def release_space() -> None:
    try:
        key_up("space")
    except Exception:
        pass


def parse_channel_click_points(spec: str | None) -> list[tuple[float, float]]:
    """Parse `x,y;x,y` channel click points.

    Values in 0..1 are interpreted as window-relative fractions; larger values
    are absolute screen pixels. This mirrors the combat script but keeps the
    Fixed Sapo bench self-contained for focused tests.
    """
    points: list[tuple[float, float]] = []
    for chunk in str(spec or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [part.strip() for part in chunk.split(",")]
        if len(parts) != 2:
            raise ValueError(f"invalid channel point {chunk!r}; expected x,y")
        x, y = float(parts[0]), float(parts[1])
        if x < 0 or y < 0:
            raise ValueError(f"invalid channel point {chunk!r}; coordinates must be non-negative")
        points.append((x, y))
    return points


def read_channel_cycle_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_channel_cycle_state(path: Path, *, used_index: int, points_count: int, run_id: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_channel_index": int(used_index),
        "points_count": int(points_count),
        "next_channel_index": (int(used_index) + 1) % max(1, int(points_count)),
        "updated_at": time.time(),
        "run_id": run_id,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_channel_index(*, requested_index: int, points_count: int, auto_cycle: bool, cycle_state_path: Path) -> dict[str, Any]:
    fallback = int(requested_index) % max(1, int(points_count))
    if not auto_cycle:
        return {"selected_index": fallback, "auto_cycle": False, "cycle_state": {}, "cycle_state_path": str(cycle_state_path)}
    state = read_channel_cycle_state(cycle_state_path)
    previous = state.get("last_channel_index")
    try:
        selected = (int(previous) + 1) % max(1, int(points_count))
    except Exception:
        selected = fallback
    return {
        "selected_index": selected,
        "auto_cycle": True,
        "previous_index": previous,
        "cycle_state": state,
        "cycle_state_path": str(cycle_state_path),
    }


def channel_click_screen_point(window: WindowInfo, point: tuple[float, float]) -> tuple[int, int]:
    x, y = point
    if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
        return int(window.bbox[0] + window.width * x), int(window.bbox[1] + window.height * y)
    return int(x), int(y)


def pickup_then_channel_switch(
    *,
    window: WindowInfo,
    window_query: str | None = None,
    points_spec: str,
    channel_index: int,
    channel_auto_cycle: bool,
    channel_cycle_state: Path,
    pickup_count: int,
    pickup_interval: float,
    menu_delay: float,
    switch_wait: float,
    live: bool,
    run_id: str | None = None,
    defer_cycle_state: bool = False,
) -> dict[str, Any]:
    points = parse_channel_click_points(points_spec)
    if not points:
        raise ValueError("channel click points are required for channel switch test")
    index_info = resolve_channel_index(
        requested_index=int(channel_index),
        points_count=len(points),
        auto_cycle=bool(channel_auto_cycle),
        cycle_state_path=channel_cycle_state,
    )
    selected_index = int(index_info["selected_index"])
    point = points[selected_index]
    if live:
        focused = robust_activate_window(window)
        # Refresh geometry after restore/focus; minimized/restored windows can
        # report stale or off-screen bounds before activation.
        if window_query:
            window = find_window(window_query)
    sx, sy = channel_click_screen_point(window, point)
    result = {
        "pickup_count": max(0, int(pickup_count)),
        "channel_index": selected_index,
        "channel_auto_cycle": bool(channel_auto_cycle),
        "channel_cycle_state_path": str(channel_cycle_state),
        "channel_cycle_previous_index": index_info.get("previous_index"),
        "channel_cycle_next_index": (selected_index + 1) % len(points),
        "points_count": len(points),
        "point": [point[0], point[1]],
        "screen_point": [sx, sy],
        "window_bbox": list(window.bbox),
        "window_size": [window.width, window.height],
        "live": bool(live),
    }
    if sx < 0 or sy < 0:
        result["warning"] = "computed channel click point is off-screen; window geometry may be stale/minimized"
    if not live:
        result["would_press"] = ["z"] * result["pickup_count"] + ["x", "left_click_channel_point"]
        if channel_auto_cycle:
            result["would_update_cycle_state"] = {"last_channel_index": selected_index, "next_channel_index": (selected_index + 1) % len(points)}
        return result
    for _idx in range(result["pickup_count"]):
        tap_key("z", hold=0.03)
        time.sleep(max(0.02, float(pickup_interval)))
    tap_key("x", hold=0.06)
    time.sleep(max(0.0, float(menu_delay)))
    click_at(sx, sy)
    time.sleep(max(0.0, float(switch_wait)))
    result["focused_before_channel_click"] = focused
    result["sent_sequence"] = ["z"] * result["pickup_count"] + ["x", "left_click_channel_point"]
    if channel_auto_cycle and not defer_cycle_state:
        write_channel_cycle_state(channel_cycle_state, used_index=selected_index, points_count=len(points), run_id=run_id)
        result["cycle_state_written"] = True
    elif channel_auto_cycle:
        result["cycle_state_deferred"] = True
    return result


def write_summary(summary_path: Path, payload: dict[str, Any]) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually focus MT2Portugalia and hold Space; dry-run only records preflight")
    ap.add_argument("--elevate", action="store_true", help="relaunch elevated via UAC before sending live input")
    ap.add_argument("--json-state", dest="state_json", type=Path, default=DEFAULT_STATE_JSON)
    ap.add_argument("--window-query", default="MT2Portugalia")
    ap.add_argument("--duration", type=float, default=30.0, help="max seconds to hold/pulse Space; 0 means preflight only")
    ap.add_argument("--until-destroyed", action="store_true", help="continue until Sapo probe/target says destroyed or duration timeout")
    ap.add_argument("--hp-stop-threshold", type=int, default=2500, help="stop Space if HP falls below this value")
    ap.add_argument("--max-state-age-seconds", type=float, default=2.0)
    ap.add_argument("--pickup-after-destroy", action="store_true")
    ap.add_argument("--pickup-count", type=int, default=20)
    ap.add_argument("--pickup-interval", type=float, default=0.08)
    ap.add_argument("--channel-switch-after-pickup", action="store_true", help="after Sapo is gone/items are picked up, press X and click configured next-channel point")
    ap.add_argument("--channel-click-points", default="", help="semicolon-separated channel menu click points as window fractions or screen pixels")
    ap.add_argument("--channel-index", type=int, default=0, help="manual/seed channel row index; used directly when auto-cycle is off, or as first index when no cycle state exists")
    ap.add_argument("--channel-auto-cycle", action="store_true", help="choose next channel row from persistent cycle state instead of reusing channel-index")
    ap.add_argument("--channel-cycle-state", type=Path, default=DEFAULT_CHANNEL_STATE, help="JSON file that stores last/next channel index for auto-cycle")
    ap.add_argument("--channel-menu-delay-seconds", type=float, default=1.25)
    ap.add_argument("--channel-switch-wait-seconds", type=float, default=4.0)
    ap.add_argument("--min-distance", type=float, default=350.0, help="warn/block live if player is farther than this raw distance from Sapo probe")
    ap.add_argument("--no-distance-block", action="store_true", help="warn but do not block if distance is too high")
    ap.add_argument("--screenshot-backend", default="screen", choices=["screen", "printwindow"])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--summary-out", type=Path, default=DEFAULT_SUMMARY)
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()
    if args.elevate and args.live and not is_admin():
        forwarded = [arg for arg in sys.argv[1:] if arg != "--elevate"]
        return relaunch_this_script_elevated(forwarded, PROJECT_ROOT)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("", encoding="utf-8")
    run_id = args.run_id or f"fixed_sapo_{int(time.time())}"
    start = time.time()
    pre_screenshot = args.out.with_name(f"{run_id}_pre.jpg")
    post_screenshot = args.out.with_name(f"{run_id}_post.jpg")
    outcome = "blocked"
    reason = "not_started"
    destroyed = False
    pickup_sent = 0
    channel_switch_result = None
    hp_min = None
    window: WindowInfo | None = None

    try:
        state = read_state(args.state_json)
    except Exception as exc:
        emit(args.out, {"state": "PREFLIGHT_BLOCKED", "reason": f"state_read_failed: {exc}", "run_id": run_id})
        write_summary(args.summary_out, {"run_id": run_id, "outcome": "blocked", "reason": str(exc)})
        return 2

    player = player_from_state(state)
    probe = sapo_probe_from_state(state)
    dist = distance_to_sapo(state)
    age = state.get("_age_seconds")
    hp = hp_value(state)
    hp_min = hp
    sapo_alive = probe.get("alive")
    emit(
        args.out,
        {
            "state": "PREFLIGHT",
            "dry_run": not args.live,
            "run_id": run_id,
            "age_seconds": round(float(age), 3) if age is not None else None,
            "player": player,
            "target_name": target_name(state),
            "sapo_probe": probe,
            "distance_to_sapo": round(dist, 3) if dist is not None else None,
            "no_click_mode": True,
            "no_potion_1": True,
        },
    )

    try:
        window = find_window(args.window_query)
        if args.live:
            focused = robust_activate_window(window)
        else:
            focused = False
        capture_window_image(window, pre_screenshot, backend=args.screenshot_backend)
        emit(args.out, {"state": "SCREENSHOT", "kind": "pre", "path": str(pre_screenshot), "focused": focused, "run_id": run_id})
    except Exception as exc:
        if args.live:
            emit(args.out, {"state": "PREFLIGHT_BLOCKED", "reason": f"window_or_screenshot_failed: {exc}", "run_id": run_id})
            write_summary(args.summary_out, {"run_id": run_id, "outcome": "blocked", "reason": str(exc)})
            return 2

    channel_switch_only = bool(args.channel_switch_after_pickup and args.duration <= 0)
    if age is None or float(age) > float(args.max_state_age_seconds):
        reason = "state_stale"
    elif channel_switch_only and sapo_alive is True:
        reason = "sapo_still_alive_channel_switch_blocked"
    elif channel_switch_only and hp is not None and hp <= 0:
        reason = "player_dead"
    elif not channel_switch_only and (not probe or sapo_alive is not True):
        reason = "sapo_probe_not_alive"
    elif not channel_switch_only and dist is not None and dist > float(args.min_distance) and not args.no_distance_block:
        reason = "too_far_from_fixed_spawn"
    elif hp is not None and hp <= 0:
        reason = "player_dead"
    else:
        reason = "preflight_ok"

    if channel_switch_only:
        if window is None:
            reason = "window_not_available"
        if not args.live:
            try:
                channel_switch_result = pickup_then_channel_switch(
                    window=window,
                    window_query=args.window_query,
                    points_spec=args.channel_click_points,
                    channel_index=args.channel_index,
                    channel_auto_cycle=args.channel_auto_cycle,
                    channel_cycle_state=args.channel_cycle_state,
                    pickup_count=args.pickup_count,
                    pickup_interval=args.pickup_interval,
                    menu_delay=args.channel_menu_delay_seconds,
                    switch_wait=args.channel_switch_wait_seconds,
                    live=False,
                    run_id=run_id,
                ) if window is not None else None
            except Exception as exc:
                channel_switch_result = {"error": str(exc)}
            outcome = "dry_run"
            summary = {
                "run_id": run_id,
                "outcome": outcome,
                "reason": reason,
                "live": False,
                "player": player,
                "sapo_probe": probe,
                "channel_switch_after_pickup": True,
                "channel_switch_result": channel_switch_result,
                "pre_screenshot": str(pre_screenshot),
                "no_potion_1": True,
            }
            write_summary(args.summary_out, summary)
            emit(args.out, {"state": "CHANNEL_SWITCH_DRY_RUN", "reason": reason, "result": channel_switch_result, "run_id": run_id})
            return 0
        if reason != "preflight_ok":
            emit(args.out, {"state": "CHANNEL_SWITCH_BLOCKED", "reason": reason, "run_id": run_id})
            write_summary(args.summary_out, {"run_id": run_id, "outcome": "blocked", "reason": reason, "live": True, "channel_switch_after_pickup": True, "player": player, "sapo_probe": probe})
            return 2
        try:
            channel_switch_result = pickup_then_channel_switch(
                window=window,
                window_query=args.window_query,
                points_spec=args.channel_click_points,
                channel_index=args.channel_index,
                channel_auto_cycle=args.channel_auto_cycle,
                channel_cycle_state=args.channel_cycle_state,
                pickup_count=args.pickup_count,
                pickup_interval=args.pickup_interval,
                menu_delay=args.channel_menu_delay_seconds,
                switch_wait=args.channel_switch_wait_seconds,
                live=True,
                run_id=run_id,
            )
            outcome = "channel_switched"
            reason = "pickup_then_channel_switch_sent"
            emit(args.out, {"state": "CHANNEL_SWITCH_SENT", "result": channel_switch_result, "run_id": run_id})
        except Exception as exc:
            outcome = "failed"
            reason = f"channel_switch_failed: {exc}"
            emit(args.out, {"state": "CHANNEL_SWITCH_FAILED", "reason": reason, "run_id": run_id})
        try:
            if window is not None:
                capture_window_image(window, post_screenshot, backend=args.screenshot_backend)
                emit(args.out, {"state": "SCREENSHOT", "kind": "post", "path": str(post_screenshot), "run_id": run_id})
        except Exception as exc:
            emit(args.out, {"state": "SCREENSHOT_FAILED", "kind": "post", "reason": str(exc), "run_id": run_id})
        summary = {
            "run_id": run_id,
            "outcome": outcome,
            "reason": reason,
            "live": True,
            "channel_switch_after_pickup": True,
            "channel_switch_result": channel_switch_result,
            "player": player,
            "sapo_probe": probe,
            "pre_screenshot": str(pre_screenshot),
            "post_screenshot": str(post_screenshot),
            "log": str(args.out),
            "no_potion_1": True,
        }
        write_summary(args.summary_out, summary)
        emit(args.out, {"state": "FINISH", **summary})
        return 0 if outcome == "channel_switched" else 2

    if not args.live or args.duration <= 0:
        outcome = "dry_run" if not args.live else ("preflight_ok" if reason == "preflight_ok" else "blocked")
        write_summary(
            args.summary_out,
            {
                "run_id": run_id,
                "outcome": outcome,
                "reason": reason,
                "live": bool(args.live),
                "player": player,
                "sapo_probe": probe,
                "distance_to_sapo": dist,
                "pre_screenshot": str(pre_screenshot),
                "no_click_mode": True,
                "no_potion_1": True,
            },
        )
        return 0 if outcome in {"dry_run", "preflight_ok"} else 2

    if reason != "preflight_ok":
        emit(args.out, {"state": "LIVE_BLOCKED", "reason": reason, "run_id": run_id})
        write_summary(args.summary_out, {"run_id": run_id, "outcome": "blocked", "reason": reason, "player": player, "sapo_probe": probe, "distance_to_sapo": dist})
        return 2

    emit(args.out, {"state": "SPACE_ONLY_START", "command": "hold_space", "duration": args.duration, "until_destroyed": args.until_destroyed, "run_id": run_id})
    release_space()
    key_down("space")
    try:
        deadline = time.time() + max(0.1, float(args.duration))
        tick = 0
        while time.time() < deadline:
            time.sleep(1.0)
            tick += 1
            try:
                state = read_state(args.state_json)
            except Exception as exc:
                outcome = "blocked"
                reason = f"state_read_failed: {exc}"
                break
            player = player_from_state(state)
            hp = hp_value(state)
            if hp is not None:
                hp_min = hp if hp_min is None else min(hp_min, hp)
            probe = sapo_probe_from_state(state)
            target = state.get("target") if isinstance(state.get("target"), dict) else {}
            probe_alive = probe.get("alive")
            target_alive = target.get("alive")
            target_is_sapo = str(target.get("name") or "").lower() == "sapo de pedra"
            if tick == 1 or tick % 5 == 0 or target_is_sapo or probe_alive is False or (hp is not None and hp <= args.hp_stop_threshold):
                emit(
                    args.out,
                    {
                        "state": "SPACE_ONLY_TICK",
                        "t": tick,
                        "hp": hp,
                        "target_name": target.get("name"),
                        "target_alive": target_alive,
                        "probe_alive": probe_alive,
                        "run_id": run_id,
                    },
                )
            if (target_is_sapo and target_alive is False) or probe_alive is False:
                destroyed = True
                outcome = "destroyed"
                reason = "sapo_destroyed"
                break
            if hp is not None and hp <= 0:
                outcome = "failed"
                reason = "player_dead"
                break
            if hp is not None and hp <= int(args.hp_stop_threshold):
                outcome = "stopped"
                reason = "hp_stop_threshold"
                break
        else:
            outcome = "timeout"
            reason = "duration_elapsed"
    finally:
        release_space()

    if destroyed and args.pickup_after_destroy:
        for _idx in range(max(0, int(args.pickup_count))):
            tap_key("z", hold=0.03)
            pickup_sent += 1
            time.sleep(max(0.02, float(args.pickup_interval)))
        emit(args.out, {"state": "PICKUP_SENT", "key": "z", "count": pickup_sent, "run_id": run_id})

    if destroyed and args.channel_switch_after_pickup:
        try:
            window = find_window(args.window_query)
            focused = robust_activate_window(window)
            channel_switch_result = pickup_then_channel_switch(
                window=window,
                window_query=args.window_query,
                points_spec=args.channel_click_points,
                channel_index=args.channel_index,
                channel_auto_cycle=args.channel_auto_cycle,
                channel_cycle_state=args.channel_cycle_state,
                pickup_count=0 if args.pickup_after_destroy else args.pickup_count,
                pickup_interval=args.pickup_interval,
                menu_delay=args.channel_menu_delay_seconds,
                switch_wait=args.channel_switch_wait_seconds,
                live=True,
                run_id=run_id,
            )
            channel_switch_result["focused_before_sequence"] = focused
            emit(args.out, {"state": "CHANNEL_SWITCH_SENT", "result": channel_switch_result, "run_id": run_id})
        except Exception as exc:
            emit(args.out, {"state": "CHANNEL_SWITCH_FAILED", "reason": str(exc), "run_id": run_id})
            if outcome == "destroyed":
                outcome = "channel_switch_failed"
                reason = f"channel_switch_failed: {exc}"

    try:
        window = find_window(args.window_query)
        capture_window_image(window, post_screenshot, backend=args.screenshot_backend)
        emit(args.out, {"state": "SCREENSHOT", "kind": "post", "path": str(post_screenshot), "run_id": run_id})
    except Exception as exc:
        emit(args.out, {"state": "SCREENSHOT_FAILED", "kind": "post", "reason": str(exc), "run_id": run_id})

    final_state = None
    try:
        final_state = read_state(args.state_json)
    except Exception:
        final_state = None
    summary = {
        "run_id": run_id,
        "outcome": outcome,
        "reason": reason,
        "live": True,
        "elapsed_seconds": round(time.time() - start, 3),
        "destroyed": destroyed,
        "pickup_sent": pickup_sent,
        "channel_switch_result": channel_switch_result,
        "hp_min": hp_min,
        "hp_final": hp_value(final_state) if isinstance(final_state, dict) else None,
        "target_final": final_state.get("target") if isinstance(final_state, dict) else None,
        "sapo_probe_final": sapo_probe_from_state(final_state) if isinstance(final_state, dict) else None,
        "pre_screenshot": str(pre_screenshot),
        "post_screenshot": str(post_screenshot),
        "log": str(args.out),
        "no_click_mode": True,
        "no_potion_1": True,
    }
    write_summary(args.summary_out, summary)
    emit(args.out, {"state": "FINISH", **summary})
    return 0 if outcome in {"destroyed", "timeout", "stopped"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
