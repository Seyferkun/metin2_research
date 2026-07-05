from __future__ import annotations

import argparse
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


def capture(output: Path, bbox: tuple[int, int, int, int]) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=bbox).save(output)
    return output


def detect_state(detector: YoloV5Detector, image: Path, bbox: tuple[int, int, int, int]) -> dict[str, Any]:
    detections = detector.detect(image)
    report = build_prediction_report(image, detections, min_confidence=0.20)
    state = report["state"]
    boxes = [normalize_detection_box(d) for d in report["detections"]]
    width, height = state["image_width"], state["image_height"]
    world = []
    for b in boxes:
        x = float(b["x_center"]); y = float(b["y_center"])
        # Avoid minimap/hotbar only; allow low confidence because close/occluded Metin is often missed.
        if y > height * 0.88:
            continue
        if x > width * 0.82 and y < height * 0.32:
            continue
        if float(b["height"]) < 55 or float(b["width"]) < 35:
            continue
        world.append(b)
    world.sort(key=lambda b: (float(b["confidence"]), float(b["height"])), reverse=True)
    if world:
        b = world[0]
        state["boxes"] = world
        state["box_count"] = len(world)
        state["target_visible"] = True
        state["target_box"] = b
        state["target_confidence"] = float(b["confidence"])
        state["target_xy"] = [b["x_center"], b["y_center"]]
        state["screen_xy"] = [int(round(bbox[0] + float(b["x_center"]))), int(round(bbox[1] + float(b["y_center"])))]
    else:
        state["boxes"] = []
        state["box_count"] = 0
        state["target_visible"] = False
        state["target_box"] = None
        state["target_confidence"] = 0.0
        state["target_xy"] = None
        state["screen_xy"] = None
    return state


def close_big_map() -> None:
    # First try the visible X on the big map, then Esc/M scan-code toggles.
    click_xy(1468, 158, clicks=1)
    time.sleep(0.25)
    tap_key("esc")
    time.sleep(0.2)
    tap_key("m")
    time.sleep(0.2)


def attack_sequence(x: int, y: int, *, seconds: float, potion_every: float = 5.0) -> None:
    # Select/engage target, then hold Space using DirectInput-friendly scan-code SendInput.
    tap_key("1")
    click_xy(x, y, clicks=2)
    time.sleep(0.3)
    hold_key_with_periodic_tap("space", seconds, tap="1", tap_every=potion_every)


def click_hold_mouse_attack(x: int, y: int, *, seconds: float) -> None:
    # Alternative probe: hold left mouse on target. Some clients auto-attack/approach from mouse hold.
    import ctypes
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    start = time.monotonic()
    try:
        while time.monotonic() - start < seconds:
            if int((time.monotonic() - start) * 10) % 50 == 0:
                tap_key("1")
            time.sleep(0.05)
    finally:
        user32.mouse_event(0x0004, 0, 0, 0, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Destroy current visible Metin with debug captures and fallback probes.")
    parser.add_argument("--window-query", default="MT2Portugalia")
    parser.add_argument("--yolov5-dir", default="/tmp/metin2bot/yolov5")
    parser.add_argument("--weights", default="reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt")
    parser.add_argument("--out-dir", default="reports/destroy_current_metin_debug")
    parser.add_argument("--trust-checkpoint", action="store_true")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    detector = YoloV5Detector(args.yolov5_dir, args.weights, trust_checkpoint=args.trust_checkpoint)
    events = []

    window = find_window(args.window_query)
    activate_window(window)
    time.sleep(0.2)
    close_big_map()
    window = find_window(args.window_query)
    activate_window(window)
    bbox = window.bbox

    before_img = capture(out / "before.jpg", bbox)
    before_state = detect_state(detector, before_img, bbox)
    save_annotated_preview(before_img, before_state, out / "before_preview.jpg")
    events.append({"event": "before", "state": before_state})

    # If detector misses, use the known/current visual position: Yoshypt is next to/over the Metin.
    # This is a conservative body point near the visible stone, not a random run click.
    xy = before_state.get("screen_xy") or [975, 420]
    x, y = int(xy[0]), int(xy[1])

    # Attempt 1: scan-code Space, capture during and after by splitting into bursts.
    for attempt in range(1, 4):
        attack_sequence(x, y, seconds=5)
        mid_img = capture(out / f"attempt_{attempt}_after_space.jpg", bbox)
        mid_state = detect_state(detector, mid_img, bbox)
        save_annotated_preview(mid_img, mid_state, out / f"attempt_{attempt}_preview.jpg")
        events.append({"event": f"attempt_{attempt}_space", "clicked_xy": [x, y], "state": mid_state})
        # Update target if detector still sees it.
        if mid_state.get("screen_xy"):
            x, y = map(int, mid_state["screen_xy"])
        # If target disappeared, stop.
        if not mid_state.get("target_visible"):
            break

    # If still present, try mouse-hold fallback once.
    last = events[-1]["state"]
    if last.get("target_visible"):
        if last.get("screen_xy"):
            x, y = map(int, last["screen_xy"])
        click_hold_mouse_attack(x, y, seconds=6)
        final_img = capture(out / "after_mouse_hold.jpg", bbox)
        final_state = detect_state(detector, final_img, bbox)
        save_annotated_preview(final_img, final_state, out / "after_mouse_hold_preview.jpg")
        events.append({"event": "mouse_hold_fallback", "clicked_xy": [x, y], "state": final_state})

    summary = {
        "events": events,
        "artifacts": {
            "before_preview": str(out / "before_preview.jpg"),
            "latest_preview": str(out / ("after_mouse_hold_preview.jpg" if (out / "after_mouse_hold_preview.jpg").exists() else f"attempt_{len(events)-1}_preview.jpg")),
            "summary": str(out / "summary.json"),
        },
        "input_backend": "SendInput scan-code for keyboard; normal OS mouse input",
        "safety": "private sandbox; bounded attempts; no memory/packet/anti-cheat bypass",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
