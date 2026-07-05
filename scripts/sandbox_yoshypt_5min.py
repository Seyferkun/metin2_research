from __future__ import annotations

import argparse
import ctypes
import json
import time
from pathlib import Path
from typing import Any

from PIL import ImageGrab

from metin2_research.detector import YoloV5Detector
from metin2_research.predict import build_prediction_report
from metin2_research.screenshot_state import save_annotated_preview
from metin2_research.window_capture import activate_window, find_window


KEYEVENTF_KEYUP = 0x0002
VK_SPACE = 0x20
VK_1 = 0x31
VK_A = 0x41
VK_D = 0x44


def key_down(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)


def key_up(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def tap_key(vk: int, hold: float = 0.06) -> None:
    key_down(vk)
    time.sleep(hold)
    key_up(vk)


def hold_key(vk: int, duration: float) -> None:
    key_down(vk)
    time.sleep(duration)
    key_up(vk)


def click_xy(x: int, y: int, duration: float = 0.06) -> None:
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    time.sleep(duration)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.04)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def capture_region(output_path: Path, bbox: tuple[int, int, int, int]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=bbox).save(output_path)
    return output_path


def add_screen_coordinates(state: dict[str, Any], bbox: tuple[int, int, int, int]) -> None:
    if not state.get("target_xy"):
        return
    left, top, _right, _bottom = bbox
    x, y = state["target_xy"]
    screen_xy = [round(float(x) + left, 3), round(float(y) + top, 3)]
    state["screen_xy"] = screen_xy
    if isinstance(state.get("recommended_action"), dict):
        state["recommended_action"]["screen_xy"] = screen_xy


def choose_approach_point(state: dict[str, Any], bbox: tuple[int, int, int, int]) -> tuple[int, int] | None:
    box = state.get("target_box")
    if not box:
        return None
    left, top, _right, _bottom = bbox
    image_width = int(state.get("image_width", 1920))
    image_height = int(state.get("image_height", 1030))
    # Click ground just below the Metin, not the center. This tends to walk next to it instead of only selecting it.
    x = float(box["x_center"])
    y = float(box["ymax"]) + max(45.0, min(110.0, float(box.get("height", 100)) * 0.45))
    x = max(20, min(image_width - 20, x))
    y = max(20, min(image_height - 95, y))
    return int(round(x + left)), int(round(y + top))


def close_enough_to_attack(state: dict[str, Any]) -> bool:
    box = state.get("target_box") or {}
    confidence = float(state.get("target_confidence", 0.0) or 0.0)
    height = float(box.get("height", 0.0) or 0.0)
    width = float(box.get("width", 0.0) or 0.0)
    # Conservative: high confidence and visibly large target. This preserves the safe thresholds.
    return confidence >= 0.60 and (height >= 150 or width >= 115)


def rotate_search(step: int) -> str:
    # Gentle alternating camera/body movement for search; no blind long runs.
    if step % 2 == 0:
        hold_key(VK_D, 0.35)
        return "search_rotate_D"
    hold_key(VK_A, 0.35)
    return "search_rotate_A"


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded sandbox Metin2 Yoshypt loop: keep alive, find Metins, approach, hold Space.")
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--window-query", default="MT2Portugalia")
    parser.add_argument("--yolov5-dir", default="/tmp/metin2bot/yolov5")
    parser.add_argument("--weights", default="reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt")
    parser.add_argument("--out-dir", default="reports/yoshypt_5min_run")
    parser.add_argument("--potion-interval", type=float, default=12.0)
    parser.add_argument("--capture-interval", type=float, default=1.8)
    parser.add_argument("--space-hold", type=float, default=1.2)
    parser.add_argument("--trust-checkpoint", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "loop_events.jsonl"
    summary_path = out_dir / "summary.json"

    window = find_window(args.window_query)
    activate_window(window)
    time.sleep(0.25)

    detector = YoloV5Detector(args.yolov5_dir, args.weights, trust_checkpoint=args.trust_checkpoint)
    start = time.monotonic()
    next_potion = 0.0
    step = 0
    counters = {"captures": 0, "potions": 0, "approach_clicks": 0, "space_attacks": 0, "search_moves": 0, "detections": 0}
    last_state: dict[str, Any] | None = None

    with log_path.open("w", encoding="utf-8") as log:
        while time.monotonic() - start < args.duration:
            now = time.monotonic() - start
            window = find_window(args.window_query)
            activate_window(window)
            bbox = window.bbox

            if now >= next_potion:
                tap_key(VK_1)
                counters["potions"] += 1
                next_potion = now + args.potion_interval
                log.write(json.dumps({"t": round(now, 2), "event": "potion", "key": "1"}) + "\n")
                log.flush()
                time.sleep(0.08)

            screenshot = capture_region(out_dir / f"capture_{step:04d}.jpg", bbox)
            detections = detector.detect(screenshot)
            report = build_prediction_report(screenshot, detections, min_confidence=0.25)
            state = report["state"]
            add_screen_coordinates(state, bbox)
            state["capture_window"] = {"pid": window.pid, "process_name": window.process_name, "title": window.title, "bbox": list(window.bbox)}
            state["capture_region"] = {"left": bbox[0], "top": bbox[1], "right": bbox[2], "bottom": bbox[3]}
            last_state = state
            counters["captures"] += 1
            if state.get("target_visible"):
                counters["detections"] += 1

            action_taken = "noop"
            if close_enough_to_attack(state):
                # Keep attention on/near the target, then hold Space for attack.
                sx, sy = [int(round(float(v))) for v in state.get("screen_xy", state["target_xy"])]
                click_xy(sx, sy)
                hold_key(VK_SPACE, args.space_hold)
                counters["space_attacks"] += 1
                action_taken = "click_target_and_hold_space"
            elif state.get("target_visible") and float(state.get("target_confidence", 0.0) or 0.0) >= 0.35:
                move_xy = choose_approach_point(state, bbox)
                if move_xy:
                    click_xy(*move_xy)
                    counters["approach_clicks"] += 1
                    action_taken = f"approach_click:{move_xy[0]},{move_xy[1]}"
            else:
                action_taken = rotate_search(step)
                counters["search_moves"] += 1

            event = {
                "t": round(now, 2),
                "step": step,
                "target_visible": state.get("target_visible"),
                "confidence": state.get("target_confidence"),
                "box_count": state.get("box_count"),
                "recommended_action": (state.get("recommended_action") or {}).get("action"),
                "screen_xy": state.get("screen_xy"),
                "action_taken": action_taken,
                "screenshot": str(screenshot),
            }
            log.write(json.dumps(event, ensure_ascii=False) + "\n")
            log.flush()
            print(json.dumps(event, ensure_ascii=False), flush=True)

            if step % 10 == 0:
                state_path = out_dir / "latest_state.json"
                preview_path = out_dir / "latest_preview.jpg"
                state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
                save_annotated_preview(screenshot, state, preview_path)

            step += 1
            time.sleep(args.capture_interval)

    if last_state is not None:
        (out_dir / "latest_state.json").write_text(json.dumps(last_state, indent=2) + "\n", encoding="utf-8")
        save_annotated_preview(Path(last_state["image"]), last_state, out_dir / "latest_preview.jpg")
    summary = {
        "duration_requested_seconds": args.duration,
        "duration_actual_seconds": round(time.monotonic() - start, 2),
        "window_query": args.window_query,
        "window": {"pid": window.pid, "process_name": window.process_name, "title": window.title, "bbox": list(window.bbox)},
        "counters": counters,
        "last_state": last_state,
        "artifacts": {"log": str(log_path), "summary": str(summary_path), "latest_preview": str(out_dir / "latest_preview.jpg"), "latest_state": str(out_dir / "latest_state.json")},
        "safety": "private sandbox server; standard OS input only; bounded duration; no memory/packet/anti-cheat bypass",
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
