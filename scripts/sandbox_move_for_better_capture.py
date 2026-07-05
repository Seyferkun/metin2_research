from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import time
from pathlib import Path

from metin2_research.window_capture import activate_window, find_window


def press_key_vk(vk: int, hold: float = 0.05) -> None:
    user32 = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x0002
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(hold)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def click_xy(x: int, y: int, duration: float = 0.08) -> None:
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    time.sleep(duration)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # left down
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # left up


def choose_ground_move_point(state: dict) -> tuple[int, int] | None:
    box = state.get("target_box")
    if not box:
        return None
    region = state.get("capture_region") or {"left": 0, "top": 0}
    left = int(round(float(region.get("left", 0))))
    top = int(round(float(region.get("top", 0))))
    # Avoid clicking the stone itself at safe thresholds. Click on ground below/right of the box,
    # clamped to the game window, to move closer and get a larger future capture.
    x = float(box["xmax"]) + 90
    y = float(box["ymax"]) + 90
    width = int(state.get("image_width", 1920))
    height = int(state.get("image_height", 1080))
    x = max(10, min(width - 10, x))
    y = max(10, min(height - 90, y))  # keep away from bottom hotbar
    return int(round(x + left)), int(round(y + top))


def main() -> int:
    parser = argparse.ArgumentParser(description="Sandbox-only Metin2 movement step for better window capture.")
    parser.add_argument("state_json")
    parser.add_argument("--window-query", default="MT2Portugalia")
    parser.add_argument("--out", default="reports/live_game_window_capture_raise/move_for_better_capture_plan.json")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--use-potion-key", default="1")
    args = parser.parse_args()

    state = json.loads(Path(args.state_json).read_text(encoding="utf-8"))
    window = find_window(args.window_query)
    move_xy = choose_ground_move_point(state)
    plan = {
        "mode": "execute" if args.execute else "dry_run",
        "safety": "private sandbox confirmed by user; standard OS input only; no memory/packet/anti-cheat bypass",
        "source_state": args.state_json,
        "window": {"pid": window.pid, "process_name": window.process_name, "title": window.title, "bbox": list(window.bbox)},
        "target_confidence": state.get("target_confidence"),
        "recommended_action": (state.get("recommended_action") or {}).get("action"),
        "potion_key": args.use_potion_key,
        "move_xy": list(move_xy) if move_xy else None,
        "steps": [],
        "executed": False,
    }
    if args.use_potion_key:
        plan["steps"].append({"op": "press_key", "key": args.use_potion_key, "reason": "keep character alive before movement"})
    if move_xy:
        plan["steps"].append({"op": "click_ground_near_target", "x": move_xy[0], "y": move_xy[1], "reason": "move closer for better capture without clicking target center"})
    else:
        plan["steps"].append({"op": "noop", "reason": "no target_box available"})

    if args.execute and move_xy:
        activate_window(window)
        time.sleep(0.25)
        if args.use_potion_key == "1":
            press_key_vk(0x31)
            time.sleep(0.15)
        click_xy(*move_xy)
        plan["executed"] = True

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
