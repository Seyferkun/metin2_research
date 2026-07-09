from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

DEFAULT_STATE_JSON = Path(r"D:/Games/MT2Portugalia/app/hermes_state.json")
DEFAULT_KEYS = "w,a,s,d,q,e,r,f,t,g,space,tab,z,1,f1,f2,x,m,ctrl+g"
SENSITIVE_FRAGMENTS = ("password", "passwd", "token", "secret", "credential", "session", "cookie", "auth")

MOUSE_BUTTON_VK = {"left": 0x01, "right": 0x02, "middle": 0x04}

VK_CODES = {
    "w": 0x57, "a": 0x41, "s": 0x53, "d": 0x44,
    "q": 0x51, "e": 0x45, "r": 0x52, "f": 0x46, "t": 0x54, "g": 0x47,
    "z": 0x5A, "x": 0x58, "m": 0x4D, "1": 0x31,
    "space": 0x20, "tab": 0x09, "f1": 0x70, "f2": 0x71, "ctrl": 0x11,
}


def _is_sensitive_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(fragment in lowered for fragment in SENSITIVE_FRAGMENTS)


def _project_allowed(src: dict[str, Any], allowed: Iterable[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in allowed:
        if key in src and not _is_sensitive_key(key):
            out[key] = src[key]
    return out


def sanitize_state_snapshot(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only gameplay-learning fields; drop secret-looking fields."""
    if not isinstance(raw, dict):
        return {"available": False}
    snapshot: dict[str, Any] = {}
    for key in ("available", "map", "map_name", "timestamp_ms", "_file_mtime"):
        if key in raw and not _is_sensitive_key(key):
            snapshot[key] = raw[key]
    player = raw.get("player") if isinstance(raw.get("player"), dict) else {}
    if player:
        snapshot["player"] = _project_allowed(player, ("name", "x", "y", "z", "hp", "max_hp", "sp", "max_sp", "mounted", "flags"))
    else:
        snapshot["player"] = _project_allowed(raw, ("player_name", "x", "y", "z", "hp", "max_hp", "sp", "max_sp"))
    target = raw.get("target") if isinstance(raw.get("target"), dict) else None
    if target:
        snapshot["target"] = _project_allowed(target, ("vid", "name", "type", "alive", "hp", "max_hp", "hp_pct", "pixel_position", "project_position"))
    elif raw.get("target_vid") or raw.get("target_name"):
        snapshot["target"] = _project_allowed(raw, ("target_vid", "target_name", "target_alive", "target_hp", "target_max_hp"))
    else:
        snapshot["target"] = None
    entities = []
    for entity in raw.get("nearby_entities") or []:
        if isinstance(entity, dict):
            entities.append(_project_allowed(entity, ("vid", "name", "kind", "type", "hostile", "distance", "pixel_position", "project_position")))
    snapshot["nearby_entities"] = entities[:20]
    if isinstance(raw.get("named_metin_probe"), dict):
        snapshot["named_metin_probe"] = _project_allowed(raw["named_metin_probe"], ("vid", "name", "alive", "distance", "pixel_position", "project_position"))
    # Observation-only item/reroll tracking. Keep numeric client API values but
    # still pass through the same allow-list sanitizer so no account/session data
    # can leak into recordings.
    inventory = []
    for item in raw.get("inventory") or []:
        if not isinstance(item, dict):
            continue
        clean_item = _project_allowed(item, ("slot", "vnum", "count", "name", "attrs", "sockets"))
        attrs = []
        for attr in clean_item.get("attrs") or []:
            if isinstance(attr, dict):
                attrs.append(_project_allowed(attr, ("index", "type", "value")))
        clean_item["attrs"] = attrs[:7]
        sockets = clean_item.get("sockets") if isinstance(clean_item.get("sockets"), list) else []
        clean_item["sockets"] = sockets[:6]
        inventory.append(clean_item)
    if inventory:
        snapshot["inventory"] = inventory[:180]
    if isinstance(raw.get("equipped_weapon"), dict):
        weapon = _project_allowed(raw["equipped_weapon"], ("slot", "vnum", "count", "name", "attrs", "sockets"))
        attrs = []
        for attr in weapon.get("attrs") or []:
            if isinstance(attr, dict):
                attrs.append(_project_allowed(attr, ("index", "type", "value")))
        weapon["attrs"] = attrs[:7]
        sockets = weapon.get("sockets") if isinstance(weapon.get("sockets"), list) else []
        weapon["sockets"] = sockets[:6]
        snapshot["equipped_weapon"] = weapon
    return snapshot


def read_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            data["_file_mtime"] = path.stat().st_mtime
            data["available"] = True
            return sanitize_state_snapshot(data)
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__}
    return {"available": False}


def parse_keys(spec: str) -> list[str]:
    return [part.strip().lower() for part in spec.split(",") if part.strip()]


def _key_down(name: str) -> bool:
    if name == "ctrl+g":
        return _key_down("ctrl") and _key_down("g")
    try:
        import ctypes
        user32 = ctypes.windll.user32
    except Exception:
        return False
    code = VK_CODES.get(name)
    if code is None:
        return False
    return bool(user32.GetAsyncKeyState(code) & 0x8000)


def current_pressed_keys(keys: Iterable[str]) -> set[str]:
    return {key for key in keys if _key_down(key)}


def key_edge_events(previous: set[str], current: set[str], now: float) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for key in sorted(current - previous):
        events.append({"type": "key_down", "key": key, "t": round(float(now), 3)})
    for key in sorted(previous - current):
        events.append({"type": "key_up", "key": key, "t": round(float(now), 3)})
    return events


def _mouse_button_down(name: str) -> bool:
    try:
        import ctypes
        user32 = ctypes.windll.user32
    except Exception:
        return False
    code = MOUSE_BUTTON_VK.get(name)
    if code is None:
        return False
    return bool(user32.GetAsyncKeyState(code) & 0x8000)


def current_mouse_buttons(buttons: Iterable[str] = ("left", "right", "middle")) -> set[str]:
    return {button for button in buttons if _mouse_button_down(button)}


def current_mouse_position(window: Any | None = None) -> dict[str, Any]:
    try:
        import ctypes
        from ctypes import wintypes
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return {"available": False}
        pos: dict[str, Any] = {"screen_x": int(point.x), "screen_y": int(point.y)}
        if window is not None and hasattr(window, "bbox"):
            left, top, right, bottom = window.bbox
            pos.update({
                "window_x": int(point.x - left),
                "window_y": int(point.y - top),
                "window_w": int(right - left),
                "window_h": int(bottom - top),
                "inside_window": bool(left <= point.x < right and top <= point.y < bottom),
            })
        return pos
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__}


def _mouse_event_payload(event_type: str, button: str, now: float, position: dict[str, Any]) -> dict[str, Any]:
    event: dict[str, Any] = {"type": event_type, "button": button, "t": round(float(now), 3), "position": position}
    if isinstance(position, dict) and position.get("available", True) is not False:
        if "screen_x" in position and "screen_y" in position:
            event["pos"] = [int(position["screen_x"]), int(position["screen_y"])]
        if "window_x" in position and "window_y" in position:
            event["window_pos"] = [int(position["window_x"]), int(position["window_y"])]
            w = float(position.get("window_w") or 0)
            h = float(position.get("window_h") or 0)
            if w > 0 and h > 0:
                event["window_relative"] = [round(float(position["window_x"]) / w, 4), round(float(position["window_y"]) / h, 4)]
                event["inside_window"] = bool(position.get("inside_window"))
    return event


def mouse_edge_events(previous: set[str], current: set[str], now: float, position: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for button in sorted(current - previous):
        events.append(_mouse_event_payload("mouse_down", button, now, position))
    for button in sorted(previous - current):
        events.append(_mouse_event_payload("mouse_up", button, now, position))
    return events


def _target_id(state: dict[str, Any] | None) -> Any:
    target = state.get("target") if isinstance(state, dict) else None
    if not isinstance(target, dict):
        return None
    vid = target.get("vid") or target.get("target_vid")
    name = target.get("name") or target.get("target_name")
    alive = target.get("alive") if "alive" in target else target.get("target_alive")
    if not vid and not name:
        return None
    if alive is False:
        return None
    return vid or name


def _xy_from_mouse(row: dict[str, Any], key: str) -> list[float] | None:
    value = row.get(key)
    if isinstance(value, list) and len(value) >= 2:
        try:
            return [float(value[0]), float(value[1])]
        except (TypeError, ValueError):
            return None
    pos = row.get("position") if isinstance(row.get("position"), dict) else {}
    if key == "pos" and "screen_x" in pos and "screen_y" in pos:
        return [float(pos["screen_x"]), float(pos["screen_y"])]
    if key == "window_pos" and "window_x" in pos and "window_y" in pos:
        return [float(pos["window_x"]), float(pos["window_y"])]
    if key == "window_relative":
        w = float(pos.get("window_w") or 0)
        h = float(pos.get("window_h") or 0)
        if w > 0 and h > 0 and "window_x" in pos and "window_y" in pos:
            return [float(pos["window_x"]) / w, float(pos["window_y"]) / h]
    return None


def _avg_xy(points: list[list[float]]) -> list[float] | None:
    if not points:
        return None
    return [round(sum(p[0] for p in points) / len(points), 4), round(sum(p[1] for p in points) / len(points), 4)]


def _compact_click(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"t": round(float(row.get("t", 0)), 3), "button": row.get("button")}
    for key in ("pos", "window_pos", "window_relative"):
        xy = _xy_from_mouse(row, key)
        if xy is not None:
            out[key] = [round(xy[0], 4), round(xy[1], 4)]
    pos = row.get("position") if isinstance(row.get("position"), dict) else {}
    if "inside_window" in row:
        out["inside_window"] = bool(row.get("inside_window"))
    elif "inside_window" in pos:
        out["inside_window"] = bool(pos.get("inside_window"))
    return out


def build_training_summary(events_path: str | Path) -> dict[str, Any]:
    path = Path(events_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    samples = [row for row in rows if row.get("type") == "sample"]
    key_downs = [row for row in rows if row.get("type") == "key_down"]
    mouse_downs = [row for row in rows if row.get("type") == "mouse_down"]
    screenshot_samples = [row for row in samples if row.get("screenshot")]
    foreground_samples = [row for row in samples if row.get("window_foreground") is True]
    background_samples = [row for row in samples if row.get("window_foreground") is False]
    key_counts = dict(sorted(Counter(str(row.get("key")) for row in key_downs).items()))
    mouse_counts = dict(sorted(Counter(str(row.get("button")) for row in mouse_downs).items()))

    target_lock_count = 0
    destroy_candidates = 0
    target_to_attack: list[float] = []
    target_to_left_click: list[float] = []
    destroy_to_pickup: list[float] = []
    active_target = None
    target_start_t: float | None = None
    last_destroy_t: float | None = None
    first_attack_recorded = False
    target_left_clicks: list[dict[str, Any]] = []
    destroy_followup_clicks: list[dict[str, Any]] = []
    all_mouse_down_points: list[dict[str, Any]] = []
    last_channel_menu_t: float | None = None

    by_time = sorted(rows, key=lambda row: float(row.get("t", 0)))
    for row in by_time:
        t = float(row.get("t", 0))
        if row.get("type") == "sample":
            target = _target_id(row.get("state") or {})
            if target and not active_target:
                target_lock_count += 1
                active_target = target
                target_start_t = t
                first_attack_recorded = False
            elif not target and active_target:
                destroy_candidates += 1
                active_target = None
                target_start_t = None
                last_destroy_t = t
        elif row.get("type") == "key_down":
            key = str(row.get("key"))
            if active_target and key in {"space", "tab"} and target_start_t is not None and not first_attack_recorded:
                target_to_attack.append(round(t - target_start_t, 3))
                first_attack_recorded = True
            if last_destroy_t is not None and key == "z":
                destroy_to_pickup.append(round(t - last_destroy_t, 3))
                last_destroy_t = None
            if key == "x":
                last_channel_menu_t = t
        elif row.get("type") == "mouse_down":
            button = str(row.get("button"))
            click = _compact_click(row)
            all_mouse_down_points.append(click)
            if active_target and button == "left" and target_start_t is not None:
                target_to_left_click.append(round(t - target_start_t, 3))
                target_left_clicks.append(click | {"seconds_after_target_lock": round(t - target_start_t, 3)})
            if button == "left" and last_channel_menu_t is not None and 0 <= t - last_channel_menu_t <= 8.0:
                destroy_followup_clicks.append(click | {"seconds_after_x": round(t - last_channel_menu_t, 3)})
                last_channel_menu_t = None

    recommendations = []
    if target_to_attack:
        recommendations.append({"topic": "attack_start", "observed_avg_seconds": round(sum(target_to_attack) / len(target_to_attack), 3), "suggestion": "after a selected Metin appears, start bounded Space attack pulses near this delay instead of passively monitoring"})
    if target_to_left_click:
        recommendations.append({"topic": "mouse_targeting", "observed_avg_seconds": round(sum(target_to_left_click) / len(target_to_left_click), 3), "suggestion": "compare left-click positions against target bars/Metin body to learn player-like mouse selection"})
    if destroy_to_pickup:
        recommendations.append({"topic": "pickup_after_destroy", "observed_avg_seconds": round(sum(destroy_to_pickup) / len(destroy_to_pickup), 3), "suggestion": "after target disappearance, begin Z pickup spam near this delay"})
    if key_counts.get("q") or key_counts.get("e"):
        recommendations.append({"topic": "camera_search", "q_presses": key_counts.get("q", 0), "e_presses": key_counts.get("e", 0), "suggestion": "use player-like short camera sweeps when no Metin is targetable"})

    return {
        "events_path": str(path),
        "samples": len(samples),
        "screenshot_count": len(screenshot_samples),
        "window_foreground_samples": len(foreground_samples),
        "window_background_samples": len(background_samples),
        "key_down_counts": key_counts,
        "mouse_down_counts": mouse_counts,
        "target_lock_count": target_lock_count,
        "destroy_candidates": destroy_candidates,
        "avg_seconds_target_to_attack": round(sum(target_to_attack) / len(target_to_attack), 3) if target_to_attack else None,
        "avg_seconds_target_to_left_click": round(sum(target_to_left_click) / len(target_to_left_click), 3) if target_to_left_click else None,
        "avg_seconds_destroy_to_pickup": round(sum(destroy_to_pickup) / len(destroy_to_pickup), 3) if destroy_to_pickup else None,
        "mouse_down_points": all_mouse_down_points[:200],
        "target_left_clicks": target_left_clicks,
        "target_left_click_avg_window_relative": _avg_xy([p["window_relative"] for p in target_left_clicks if isinstance(p.get("window_relative"), list)]),
        "target_left_click_avg_window_pos": _avg_xy([p["window_pos"] for p in target_left_clicks if isinstance(p.get("window_pos"), list)]),
        "channel_followup_clicks": destroy_followup_clicks,
        "channel_followup_avg_window_relative": _avg_xy([p["window_relative"] for p in destroy_followup_clicks if isinstance(p.get("window_relative"), list)]),
        "channel_followup_avg_window_pos": _avg_xy([p["window_pos"] for p in destroy_followup_clicks if isinstance(p.get("window_pos"), list)]),
        "recommendations": recommendations,
    }


def write_recommendations_md(summary: dict[str, Any], path: str | Path) -> None:
    lines = ["# Player training analysis", "", f"Events: `{summary['events_path']}`", f"Samples: {summary['samples']}", f"Target locks: {summary['target_lock_count']}", f"Destroy candidates: {summary['destroy_candidates']}", "", "## Key down counts"]
    for key, count in summary.get("key_down_counts", {}).items():
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Mouse down counts"])
    for button, count in summary.get("mouse_down_counts", {}).items():
        lines.append(f"- {button}: {count}")
    if summary.get("target_left_clicks") or summary.get("channel_followup_clicks"):
        lines.extend(["", "## Learned click coordinates"])
        if summary.get("target_left_clicks"):
            lines.append(f"- Target left-click avg window-relative: {summary.get('target_left_click_avg_window_relative')}")
            lines.append(f"- Target left-click avg window-pos: {summary.get('target_left_click_avg_window_pos')}")
            for click in summary.get("target_left_clicks", [])[:10]:
                lines.append(f"  - t={click.get('t')} rel={click.get('window_relative')} window={click.get('window_pos')} screen={click.get('pos')} after_target={click.get('seconds_after_target_lock')}")
        if summary.get("channel_followup_clicks"):
            lines.append(f"- Channel/menu follow-up avg window-relative: {summary.get('channel_followup_avg_window_relative')}")
            lines.append(f"- Channel/menu follow-up avg window-pos: {summary.get('channel_followup_avg_window_pos')}")
            for click in summary.get("channel_followup_clicks", [])[:10]:
                lines.append(f"  - t={click.get('t')} rel={click.get('window_relative')} window={click.get('window_pos')} screen={click.get('pos')} after_x={click.get('seconds_after_x')}")
    lines.extend(["", "## Recommendations"])
    if summary.get("recommendations"):
        for rec in summary["recommendations"]:
            lines.append(f"- {rec['topic']}: {rec['suggestion']}")
    else:
        lines.append("- Not enough target/attack/pickup evidence yet. Record a longer manual Metin kill.")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def should_stop(stop_file: Path | None, deadline: float) -> bool:
    if stop_file and stop_file.exists():
        return True
    return time.time() >= deadline


def record_training(args) -> dict[str, Any]:
    run_id = args.run_id or os.environ.get("HERMES_RUN_ID") or time.strftime("manual-%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    screenshot_dir = out_dir / "screenshots"
    stop_file = Path(args.stop_file or os.environ.get("HERMES_STOP_FILE", "")) if (args.stop_file or os.environ.get("HERMES_STOP_FILE")) else None
    keys = parse_keys(args.keys)
    previous: set[str] = set()
    previous_mouse: set[str] = set()
    start = time.time()
    deadline = start + max(0.0, float(args.duration))
    sample_index = 0
    window = None
    capture_error = None
    last_window_refresh = 0.0
    if args.capture_screenshots or args.record_mouse:
        try:
            from metin2_research.window_capture import find_window, is_plausible_game_window
            window = find_window(args.window_query)
            if not is_plausible_game_window(window):
                raise RuntimeError(f"implausible window geometry: {window.bbox}")
            last_window_refresh = time.time()
        except Exception as exc:
            capture_error = f"{type(exc).__name__}: {exc}"

    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "start", "t": 0.0, "run_id": run_id, "keys": keys, "record_mouse": bool(args.record_mouse), "state_json": str(args.state_json), "capture_screenshots": bool(args.capture_screenshots), "screenshot_backend": args.screenshot_backend, "window_query": args.window_query, "window": getattr(window, "__dict__", None), "capture_error": capture_error}) + "\n")
        while not should_stop(stop_file, deadline):
            now_abs = time.time()
            now = round(now_abs - start, 3)
            state = read_state(Path(args.state_json))
            if (args.capture_screenshots or args.record_mouse) and now_abs - last_window_refresh >= max(0.25, float(args.refresh_window_every)):
                try:
                    from metin2_research.window_capture import find_window, is_plausible_game_window
                    refreshed = find_window(args.window_query)
                    if is_plausible_game_window(refreshed):
                        window = refreshed
                        capture_error = None
                    else:
                        capture_error = f"implausible window geometry: {refreshed.bbox}"
                except Exception as exc:
                    capture_error = f"{type(exc).__name__}: {exc}"
                last_window_refresh = now_abs
            current = current_pressed_keys(keys)
            for event in key_edge_events(previous, current, now):
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            previous = current
            if args.record_mouse:
                mouse_position = current_mouse_position(window)
                current_mouse = current_mouse_buttons()
                for event in mouse_edge_events(previous_mouse, current_mouse, now, mouse_position):
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                previous_mouse = current_mouse
            sample: dict[str, Any] = {"type": "sample", "t": now, "state": state}
            if window is not None:
                sample["window"] = getattr(window, "__dict__", None)
                try:
                    from metin2_research.window_capture import is_foreground_window
                    sample["window_foreground"] = is_foreground_window(window)
                except Exception:
                    sample["window_foreground"] = None
            if capture_error:
                sample["capture_error"] = capture_error
            if window and args.capture_screenshots and sample_index % max(1, int(args.screenshot_every)) == 0:
                try:
                    from metin2_research.window_capture import capture_window_image
                    shot_path = screenshot_dir / f"frame-{sample_index:05d}.jpg"
                    capture_window_image(window, shot_path, backend=args.screenshot_backend)
                    sample["screenshot"] = str(shot_path)
                    sample["screenshot_backend"] = args.screenshot_backend
                except Exception as exc:
                    sample["screenshot_error"] = f"{type(exc).__name__}: {exc}"
            fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
            fh.flush()
            sample_index += 1
            time.sleep(max(0.02, float(args.interval)))
        fh.write(json.dumps({"type": "stop", "t": round(time.time() - start, 3), "reason": "stop_file" if stop_file and stop_file.exists() else "duration"}) + "\n")

    summary = build_training_summary(events_path)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_recommendations_md(summary, out_dir / "recommendations.md")
    return {"run_id": run_id, "out_dir": str(out_dir), "events_path": str(events_path), "summary_path": str(summary_path), **summary}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Observation-only player training recorder for private Metin2 sandbox gameplay.")
    ap.add_argument("--duration", type=float, default=180.0, help="seconds to record")
    ap.add_argument("--interval", type=float, default=0.25, help="state/input sample interval")
    ap.add_argument("--state-json", default=str(DEFAULT_STATE_JSON), help="client-state JSON path")
    ap.add_argument("--keys", default=DEFAULT_KEYS, help="comma-separated keys to observe")
    ap.add_argument("--out-dir", default="reports/player_training_runs", help="base output directory")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--stop-file", default=None)
    ap.add_argument("--window-query", default="MT2Portugalia")
    ap.add_argument("--refresh-window-every", type=float, default=1.0, help="seconds between window geometry refreshes for reliable mouse-relative coordinates")
    ap.add_argument("--record-mouse", action="store_true", default=True, help="record mouse button edges and cursor positions; no inputs are sent")
    ap.add_argument("--no-record-mouse", dest="record_mouse", action="store_false", help="disable mouse observation")
    ap.add_argument("--capture-screenshots", action="store_true", help="capture game window screenshots for visual analysis; no inputs are sent")
    ap.add_argument("--screenshot-backend", choices=("screen", "printwindow"), default="screen", help="screen crop captures DirectX game content; printwindow keeps the old HWND diagnostic path")
    ap.add_argument("--screenshot-every", type=int, default=4, help="capture every N samples when screenshots are enabled")
    args = ap.parse_args(argv)
    result = record_training(args)
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
