from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from PIL import ImageGrab

from .detector import YoloV5Detector
from .predict import build_prediction_report
from .screenshot_state import save_annotated_preview
from .window_capture import WindowInfo, activate_window, find_window


def parse_region(value: str | None) -> tuple[int, int, int, int] | None:
    """Parse x,y,width,height into an ImageGrab bbox tuple."""
    if not value:
        return None
    parts = [int(p.strip()) for p in value.split(",")]
    if len(parts) != 4:
        raise ValueError("region must be x,y,width,height")
    x, y, width, height = parts
    if width <= 0 or height <= 0:
        raise ValueError("region width/height must be positive")
    return (x, y, x + width, y + height)


def capture_screenshot(output_path: str | Path, *, region: tuple[int, int, int, int] | None = None) -> Path:
    """Capture the visible desktop or a region. Read-only: no clicks/keypresses."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image = ImageGrab.grab(bbox=region)
    image.save(output)
    return output


def add_screen_coordinates(state: dict[str, Any], region: tuple[int, int, int, int] | None) -> None:
    """Add absolute screen coordinates for cropped captures without changing preview-relative boxes."""
    if not region or not state.get("target_xy"):
        return
    left, top, _right, _bottom = region
    x, y = state["target_xy"]
    screen_xy = [round(float(x) + left, 3), round(float(y) + top, 3)]
    state["screen_xy"] = screen_xy
    recommended = state.get("recommended_action")
    if isinstance(recommended, dict):
        recommended["screen_xy"] = screen_xy


def run_live_prediction_once(
    *,
    detector: YoloV5Detector,
    output_dir: str | Path,
    prefix: str = "live",
    region: tuple[int, int, int, int] | None = None,
    window: WindowInfo | None = None,
    raise_window: bool = False,
    min_confidence: float = 0.25,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if window and raise_window:
        activate_window(window)
        time.sleep(0.25)
    screenshot = capture_screenshot(output / f"{prefix}_screenshot.jpg", region=region)
    detections = detector.detect(screenshot)
    report = build_prediction_report(screenshot, detections, min_confidence=min_confidence)
    add_screen_coordinates(report["state"], region)
    if region:
        left, top, right, bottom = region
        report["state"]["capture_region"] = {"left": left, "top": top, "right": right, "bottom": bottom}
    if window:
        report["state"]["capture_window"] = {
            "hwnd": window.hwnd,
            "pid": window.pid,
            "process_name": window.process_name,
            "title": window.title,
            "bbox": list(window.bbox),
        }

    state_path = output / f"{prefix}_state.json"
    report_path = output / f"{prefix}_report.json"
    preview_path = output / f"{prefix}_preview.jpg"
    state_path.write_text(json.dumps(report["state"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    save_annotated_preview(screenshot, report["state"], preview_path)
    report["artifacts"] = {
        "screenshot": str(screenshot),
        "state": str(state_path),
        "report": str(report_path),
        "preview": str(preview_path),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Screenshot-only live Metin2 detector test. No clicks, keypresses, or automation.")
    parser.add_argument("--yolov5-dir", required=True)
    parser.add_argument("--weights", required=True)
    parser.add_argument("--trust-checkpoint", action="store_true")
    parser.add_argument("--out-dir", default="reports/live_game_test")
    parser.add_argument("--region", help="Optional screen crop: x,y,width,height")
    parser.add_argument("--window-query", help="Find a visible Windows window by title/process/pid substring and crop to it")
    parser.add_argument("--raise-window", action="store_true", help="Restore/foreground the matched window before capture to avoid overlapping windows")
    parser.add_argument("--min-confidence", type=float, default=0.25)
    parser.add_argument("--count", type=int, default=1, help="Number of screenshots to process")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between screenshots when count > 1")
    args = parser.parse_args(argv)

    if args.count <= 0:
        raise SystemExit("--count must be positive")
    window = find_window(args.window_query) if args.window_query else None
    region = window.bbox if window else parse_region(args.region)
    if window:
        print(
            "capture_window: "
            f"pid={window.pid} process={window.process_name} title={window.title!r} "
            f"bbox={window.bbox} region={window.region_arg}"
        )
    detector = YoloV5Detector(args.yolov5_dir, args.weights, trust_checkpoint=args.trust_checkpoint)

    last_report: dict[str, Any] | None = None
    for i in range(args.count):
        prefix = f"live_{i + 1:03d}" if args.count > 1 else "live"
        last_report = run_live_prediction_once(
            detector=detector,
            output_dir=args.out_dir,
            prefix=prefix,
            region=region,
            window=window,
            raise_window=args.raise_window,
            min_confidence=args.min_confidence,
        )
        state = last_report["state"]
        action = state["recommended_action"]["action"]
        print(
            f"{prefix}: target_visible={state['target_visible']} "
            f"boxes={state['box_count']} confidence={state['target_confidence']:.3f} action={action} "
            f"preview={last_report['artifacts']['preview']}"
        )
        if i + 1 < args.count:
            time.sleep(args.interval)

    if last_report is not None:
        print(json.dumps(last_report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
