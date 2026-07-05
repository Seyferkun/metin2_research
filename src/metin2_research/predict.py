from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .detector import YoloV5Detector, build_state_from_detections, normalize_detection_box
from .screenshot_state import save_annotated_preview


def build_prediction_report(image_path: str | Path, detections: Iterable[dict[str, Any]], *, min_confidence: float = 0.25) -> dict[str, Any]:
    """Build an annotation-free prediction report for trying a detector on any screenshot."""
    image = Path(image_path)
    normalized = [normalize_detection_box(d) for d in detections]
    state = build_state_from_detections(image, normalized, min_confidence=min_confidence)
    return {
        "image": str(image),
        "mode": "prediction_only_no_ground_truth",
        "min_confidence": min_confidence,
        "detections": normalized,
        "state": state,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a YOLOv5 Metin detector on any screenshot without requiring annotations.")
    parser.add_argument("image", help="Screenshot/image path")
    parser.add_argument("--yolov5-dir", required=True, help="Path to local YOLOv5 directory")
    parser.add_argument("--weights", required=True, help="YOLOv5 .pt weights")
    parser.add_argument("--trust-checkpoint", action="store_true", help="Allow trusted local .pt pickle loading")
    parser.add_argument("--min-confidence", type=float, default=0.25)
    parser.add_argument("--state-out", help="Write state JSON")
    parser.add_argument("--report-out", help="Write full prediction report JSON")
    parser.add_argument("--preview-out", help="Write annotated image preview")
    args = parser.parse_args(argv)

    detector = YoloV5Detector(args.yolov5_dir, args.weights, trust_checkpoint=args.trust_checkpoint)
    detections = detector.detect(args.image)
    report = build_prediction_report(args.image, detections, min_confidence=args.min_confidence)

    if args.state_out:
        path = Path(args.state_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report["state"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.report_out:
        path = Path(args.report_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.preview_out:
        save_annotated_preview(args.image, report["state"], args.preview_out)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
