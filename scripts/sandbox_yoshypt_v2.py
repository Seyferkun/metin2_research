from __future__ import annotations

import argparse
import ctypes
import json
import time
from pathlib import Path
from typing import Any

from PIL import ImageGrab

from metin2_research.detector import YoloV5Detector, normalize_detection_box
from metin2_research.predict import build_prediction_report
from metin2_research.screenshot_state import save_annotated_preview
from metin2_research.win_input import click_xy, hold_key_with_periodic_tap, key_down, key_up, tap_key
from metin2_research.window_capture import activate_window, find_window


def hold_key_with_potions(key: str, duration: float, *, potion_every: float = 4.0) -> int:
    """Hold a key while tapping potion 1 occasionally. Returns potion taps."""
    return hold_key_with_periodic_tap(key, duration, tap="1", tap_every=potion_every)


def move_window_onscreen(hwnd: int, width: int, height: int) -> None:
    """Move the game window fully onto the primary monitor without resizing."""
    user32 = ctypes.windll.user32
    SWP_NOSIZE = 0x0001
    SWP_NOZORDER = 0x0004
    SWP_SHOWWINDOW = 0x0040
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW)


def capture_window(window_query: str, output: Path, *, ensure_onscreen: bool = True) -> tuple[Path, tuple[int, int, int, int], Any]:
    window = find_window(window_query)
    if ensure_onscreen and (window.bbox[0] < 0 or window.bbox[1] < 0):
        move_window_onscreen(window.hwnd, window.width, window.height)
        time.sleep(0.25)
        window = find_window(window_query)
    activate_window(window)
    time.sleep(0.15)
    output.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=window.bbox).save(output)
    return output, window.bbox, window


def screen_xy_from_box(box: dict[str, Any], bbox: tuple[int, int, int, int]) -> list[int]:
    # Click the visible body of the Metin, slightly below center, not the far ground.
    left, top, _right, _bottom = bbox
    x = float(box["x_center"])
    y = float(box["ymin"]) + float(box["height"]) * 0.58
    return [int(round(left + x)), int(round(top + y))]


def filter_world_metin_boxes(detections: list[dict[str, Any]], image_size: tuple[int, int]) -> list[dict[str, Any]]:
    width, height = image_size
    boxes = [normalize_detection_box(d) for d in detections]
    kept = []
    for box in boxes:
        conf = float(box.get("confidence", 0.0))
        x = float(box["x_center"])
        y = float(box["y_center"])
        bw = float(box["width"])
        bh = float(box["height"])
        # Do not chase low-confidence flickers, hotbar/minimap/UI, or tiny fragments.
        if conf < 0.35:
            continue
        if bh < 60 or bw < 40:
            continue
        if y > height * 0.86:  # hotbar/bottom UI
            continue
        if x > width * 0.82 and y < height * 0.30:  # minimap/top-right UI
            continue
        kept.append(box)
    kept.sort(key=lambda b: (float(b["confidence"]), float(b["height"])), reverse=True)
    return kept


def build_state_for_kept_target(image: Path, report: dict[str, Any], bbox: tuple[int, int, int, int]) -> dict[str, Any]:
    state = report["state"]
    kept = filter_world_metin_boxes(report["detections"], (int(state["image_width"]), int(state["image_height"])))
    if kept:
        target = kept[0]
        state["boxes"] = kept
        state["box_count"] = len(kept)
        state["target_visible"] = True
        state["target_confirmed"] = True
        state["target_box"] = target
        state["target_xy"] = [target["x_center"], target["y_center"]]
        state["screen_xy"] = screen_xy_from_box(target, bbox)
        state["target_confidence"] = float(target["confidence"])
        state["recommended_action"] = {
            "action": "APPROACH_OR_ATTACK" if float(target["confidence"]) >= 0.60 else "INVESTIGATE_TARGET",
            "confidence": float(target["confidence"]),
            "target_xy": state["target_xy"],
            "screen_xy": state["screen_xy"],
            "reason": "Filtered world Metin candidate",
        }
    else:
        state["boxes"] = []
        state["box_count"] = 0
        state["target_visible"] = False
        state["target_confirmed"] = False
        state["target_box"] = None
        state["target_xy"] = None
        state["screen_xy"] = None
        state["target_confidence"] = 0.0
        state["recommended_action"] = {"action": "ROTATE_CAMERA", "reason": "No filtered world Metin target"}
    state["image"] = str(image)
    state["capture_region"] = {"left": bbox[0], "top": bbox[1], "right": bbox[2], "bottom": bbox[3]}
    return state


def gentle_search(step: int) -> str:
    # Only rotate. No blind pathing/running.
    if step % 2 == 0:
        key_down("d"); time.sleep(0.22); key_up("d")
        return "rotate_D_only"
    key_down("a"); time.sleep(0.22); key_up("a")
    return "rotate_A_only"


def main() -> int:
    parser = argparse.ArgumentParser(description="Yoshypt v2: target Metins directly, hold Space, no random run clicks.")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--window-query", default="MT2Portugalia")
    parser.add_argument("--yolov5-dir", default="/tmp/metin2bot/yolov5")
    parser.add_argument("--weights", default="reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt")
    parser.add_argument("--out-dir", default="reports/yoshypt_v2_run")
    parser.add_argument("--trust-checkpoint", action="store_true")
    parser.add_argument("--attack-hold", type=float, default=5.0)
    parser.add_argument("--capture-interval", type=float, default=1.0)
    parser.add_argument("--test-once", action="store_true", help="One detect/select/space cycle then exit")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "events.jsonl"
    detector = YoloV5Detector(args.yolov5_dir, args.weights, trust_checkpoint=args.trust_checkpoint)
    counters = {"captures": 0, "potions": 0, "target_clicks": 0, "space_holds": 0, "search_rotates": 0, "detections": 0}
    last_state = None
    start = time.monotonic()
    step = 0
    next_potion = 0.0

    with log_path.open("w", encoding="utf-8") as log:
        while args.test_once or time.monotonic() - start < args.duration:
            t = time.monotonic() - start
            if t >= next_potion:
                tap_key("1")
                counters["potions"] += 1
                next_potion = t + 10.0

            img, bbox, window = capture_window(args.window_query, out / f"capture_{step:04d}.jpg")
            detections = detector.detect(img)
            report = build_prediction_report(img, detections, min_confidence=0.25)
            state = build_state_for_kept_target(img, report, bbox)
            state["capture_window"] = {"pid": window.pid, "process_name": window.process_name, "title": window.title, "bbox": list(window.bbox)}
            counters["captures"] += 1
            last_state = state

            if state["target_visible"]:
                counters["detections"] += 1
                sx, sy = state["screen_xy"]
                click_xy(sx, sy, clicks=2)
                counters["target_clicks"] += 2
                potions = hold_key_with_potions("space", args.attack_hold)
                counters["space_holds"] += 1
                counters["potions"] += potions
                action = f"double_click_metin_and_hold_space_{args.attack_hold:.1f}s"
            else:
                action = gentle_search(step)
                counters["search_rotates"] += 1

            save_annotated_preview(img, state, out / "latest_preview.jpg")
            (out / "latest_state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            event = {
                "t": round(t, 2),
                "step": step,
                "target_visible": state["target_visible"],
                "confidence": state["target_confidence"],
                "screen_xy": state.get("screen_xy"),
                "action": action,
                "image": str(img),
            }
            print(json.dumps(event), flush=True)
            log.write(json.dumps(event) + "\n")
            log.flush()
            step += 1
            if args.test_once:
                break
            time.sleep(args.capture_interval)

    summary = {
        "duration_actual_seconds": round(time.monotonic() - start, 2),
        "counters": counters,
        "last_state": last_state,
        "artifacts": {
            "events": str(log_path),
            "latest_preview": str(out / "latest_preview.jpg"),
            "latest_state": str(out / "latest_state.json"),
            "summary": str(out / "summary.json"),
        },
        "safety": "private sandbox server; standard OS input only; no random blind run clicks; no memory/packet/anti-cheat bypass",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
