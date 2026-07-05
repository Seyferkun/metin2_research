#!/usr/bin/env python
"""Diagnose Metin movement/probe mismatches without blind navigation.

Default mode is read-only: it prints current client state, nearby-Metin evidence,
and distances to a requested coordinate. With --move it performs bounded per-key
movement probes, releasing keys after every probe, and writes JSONL evidence.
"""
from __future__ import annotations

import argparse
import json
import math
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

from metin2_research.win_input import SmoothMover, key_up
from metin2_research.window_capture import activate_window, find_window

DEFAULT_STATE_JSON = Path("D:/Games/MT2Portugalia/app/hermes_state.json")
DEFAULT_OUT = Path("reports/movement_debug/latest_metin_movement_debug.jsonl")
KEYSETS = [
    {"w"},
    {"a"},
    {"s"},
    {"d"},
    {"w", "a"},
    {"a", "s"},
    {"s", "d"},
    {"d", "w"},
]
RELEASE_KEYS = ["w", "a", "s", "d", "space", "q", "e", "r", "f"]


def read_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def player_xy(state: dict[str, Any]) -> tuple[float, float]:
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    return float(player.get("x") or 0.0), float(player.get("y") or 0.0)


def display_xy(x: float, y: float) -> dict[str, float]:
    return {"x": round(x / 100.0, 2), "y": round(y / 100.0, 2)}


def dist_to_target(x: float, y: float, target_raw: tuple[float, float]) -> float:
    return math.hypot(target_raw[0] - x, target_raw[1] - y)


def metin_probe_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in state.get("named_metin_probe") or []:
        if isinstance(item, dict):
            rows.append({k: item.get(k) for k in ("name", "vid", "alive", "type")})
    return rows


def probe_has_vid(state: dict[str, Any], vid: int | None) -> bool:
    if vid is None:
        return bool(metin_probe_rows(state))
    for item in state.get("named_metin_probe") or []:
        if isinstance(item, dict) and int(item.get("vid") or -1) == int(vid) and item.get("alive") is True:
            return True
    return False


def normalize_target(x: float, y: float) -> tuple[float, float]:
    # User/operator coords like 284,218 are display coords; client state is raw.
    if abs(x) < 5000 and abs(y) < 5000:
        return float(x) * 100.0, float(y) * 100.0
    return float(x), float(y)


def release_all() -> None:
    for key in RELEASE_KEYS:
        try:
            key_up(key)
        except Exception:
            pass


def find_nearby(radius: float, limit: int) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "scripts/find_nearby_metins.py",
        "--source",
        "hybrid",
        "--radius",
        str(radius),
        "--limit",
        str(limit),
    ]
    completed = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        return {"error": completed.stderr or completed.stdout, "returncode": completed.returncode}
    decoder = json.JSONDecoder()
    text = completed.stdout.lstrip()
    if not text:
        return {}
    result, _ = decoder.raw_decode(text)
    return result if isinstance(result, dict) else {}


def event_snapshot(*, state: dict[str, Any], target_raw: tuple[float, float], target_display: tuple[float, float], vid: int | None, label: str) -> dict[str, Any]:
    x, y = player_xy(state)
    target_distance = dist_to_target(x, y, target_raw)
    return {
        "event": label,
        "time": time.time(),
        "map": state.get("map") or state.get("map_name"),
        "player_raw": {"x": round(x, 3), "y": round(y, 3)},
        "player_display": display_xy(x, y),
        "target_raw": {"x": round(target_raw[0], 3), "y": round(target_raw[1], 3)},
        "target_display": {"x": target_display[0], "y": target_display[1]},
        "distance_raw": round(target_distance, 3),
        "distance_display": round(target_distance / 100.0, 3),
        "target": state.get("target"),
        "named_metin_probe": metin_probe_rows(state),
        "probe_has_requested_vid": probe_has_vid(state, vid),
    }


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def probe_keysets(args: argparse.Namespace, target_raw: tuple[float, float], target_display: tuple[float, float]) -> list[dict[str, Any]]:
    activate_window(find_window(args.window_query))
    time.sleep(args.settle_seconds)
    release_all()
    mover = SmoothMover()
    rows: list[dict[str, Any]] = []
    try:
        for keyset in KEYSETS:
            before = read_state(args.state_json)
            bx, by = player_xy(before)
            before_dist = dist_to_target(bx, by, target_raw)
            mover.move(set(keyset), args.probe_hold)
            mover.release_all()
            time.sleep(args.after_probe_sleep)
            after = read_state(args.state_json)
            ax, ay = player_xy(after)
            after_dist = dist_to_target(ax, ay, target_raw)
            row = {
                "event": "probe_keyset",
                "keys": sorted(keyset),
                "hold_seconds": args.probe_hold,
                "before_raw": {"x": round(bx, 3), "y": round(by, 3)},
                "after_raw": {"x": round(ax, 3), "y": round(ay, 3)},
                "before_display": display_xy(bx, by),
                "after_display": display_xy(ax, ay),
                "delta_raw": {"dx": round(ax - bx, 3), "dy": round(ay - by, 3)},
                "distance_before_raw": round(before_dist, 3),
                "distance_after_raw": round(after_dist, 3),
                "distance_gain_raw": round(before_dist - after_dist, 3),
                "probe_before": metin_probe_rows(before),
                "probe_after": metin_probe_rows(after),
                "probe_requested_vid_before": probe_has_vid(before, args.vid),
                "probe_requested_vid_after": probe_has_vid(after, args.vid),
            }
            rows.append(row)
            append_jsonl(args.out, row)
            if args.stop_on_probe_loss and row["probe_requested_vid_before"] and not row["probe_requested_vid_after"]:
                break
    finally:
        mover.release_all()
        release_all()
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Debug Metin movement/probe mismatch with bounded evidence collection.")
    parser.add_argument("--state-json", type=Path, default=DEFAULT_STATE_JSON)
    parser.add_argument("--target-x", type=float, default=284.0, help="Target x, display coords by default if small")
    parser.add_argument("--target-y", type=float, default=218.0, help="Target y, display coords by default if small")
    parser.add_argument("--vid", type=int, default=3846, help="Expected live Metin VID; omit by passing 0 to accept any named probe")
    parser.add_argument("--nearby-radius", type=float, default=300.0)
    parser.add_argument("--nearby-limit", type=int, default=8)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--move", action="store_true", help="Perform bounded key probes. Default is read-only.")
    parser.add_argument("--probe-hold", type=float, default=0.16)
    parser.add_argument("--after-probe-sleep", type=float, default=0.10)
    parser.add_argument("--settle-seconds", type=float, default=0.20)
    parser.add_argument("--window-query", default="MT2Portugalia")
    parser.add_argument("--stop-on-probe-loss", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    if args.vid == 0:
        args.vid = None

    target_raw = normalize_target(args.target_x, args.target_y)
    target_display = (round(target_raw[0] / 100.0, 3), round(target_raw[1] / 100.0, 3))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("", encoding="utf-8")

    state = read_state(args.state_json)
    start = event_snapshot(state=state, target_raw=target_raw, target_display=target_display, vid=args.vid, label="start")
    nearby = find_nearby(args.nearby_radius, args.nearby_limit)
    start["nearby"] = {
        "count": nearby.get("count"),
        "source": nearby.get("source"),
        "diagnostics": nearby.get("diagnostics"),
        "metins": nearby.get("metins", []),
    }
    append_jsonl(args.out, start)

    print(json.dumps(start, indent=2, ensure_ascii=False))
    if not args.move:
        print(f"READ_ONLY: wrote {args.out}")
        return 0

    rows = probe_keysets(args, target_raw, target_display)
    end_state = read_state(args.state_json)
    end = event_snapshot(state=end_state, target_raw=target_raw, target_display=target_display, vid=args.vid, label="end")
    append_jsonl(args.out, end)
    print(json.dumps({"probe_rows": rows, "end": end, "out": str(args.out)}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
