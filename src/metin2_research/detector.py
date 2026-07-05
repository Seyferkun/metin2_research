from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw

from .dataset import IMAGE_SUFFIXES, parse_yolo_annotation
from .policy import decide_next_action
from .screenshot_state import save_annotated_preview

Box = dict[str, float | int | str]


def _as_float(value: Any) -> float:
    return float(value)


def normalize_detection_box(box: dict[str, Any]) -> dict[str, Any]:
    """Normalize a detector box to the harness box schema."""
    xmin = _as_float(box["xmin"])
    ymin = _as_float(box["ymin"])
    xmax = _as_float(box["xmax"])
    ymax = _as_float(box["ymax"])
    width = xmax - xmin
    height = ymax - ymin
    normalized = {
        "class_id": int(box.get("class_id", box.get("class", 0))),
        "name": str(box.get("name", "metin")),
        "confidence": round(float(box.get("confidence", 1.0)), 6),
        "x_center": round(xmin + width / 2, 3),
        "y_center": round(ymin + height / 2, 3),
        "width": round(width, 3),
        "height": round(height, 3),
        "xmin": round(xmin, 3),
        "ymin": round(ymin, 3),
        "xmax": round(xmax, 3),
        "ymax": round(ymax, 3),
    }
    return normalized


def box_iou(first: dict[str, Any], second: dict[str, Any]) -> float:
    """Compute intersection-over-union for two xmin/ymin/xmax/ymax boxes."""
    inter_xmin = max(_as_float(first["xmin"]), _as_float(second["xmin"]))
    inter_ymin = max(_as_float(first["ymin"]), _as_float(second["ymin"]))
    inter_xmax = min(_as_float(first["xmax"]), _as_float(second["xmax"]))
    inter_ymax = min(_as_float(first["ymax"]), _as_float(second["ymax"]))
    inter_width = max(0.0, inter_xmax - inter_xmin)
    inter_height = max(0.0, inter_ymax - inter_ymin)
    intersection = inter_width * inter_height
    first_area = max(0.0, _as_float(first["xmax"]) - _as_float(first["xmin"])) * max(
        0.0, _as_float(first["ymax"]) - _as_float(first["ymin"])
    )
    second_area = max(0.0, _as_float(second["xmax"]) - _as_float(second["xmin"])) * max(
        0.0, _as_float(second["ymax"]) - _as_float(second["ymin"])
    )
    union = first_area + second_area - intersection
    return 0.0 if union <= 0 else intersection / union


def build_state_from_detections(
    image_path: str | Path,
    detections: Iterable[dict[str, Any]],
    *,
    image_size: tuple[int, int] | None = None,
    hp_percent: int = 100,
    min_confidence: float = 0.25,
) -> dict[str, Any]:
    """Build advisory state from detector output instead of ground-truth annotations."""
    image = Path(image_path)
    if image_size is None:
        with Image.open(image) as img:
            image_size = img.size
    width, height = image_size

    boxes = [normalize_detection_box(d) for d in detections if float(d.get("confidence", 1.0)) >= min_confidence]
    target_box = max(boxes, key=lambda b: float(b["confidence"]), default=None)
    target_visible = target_box is not None
    state: dict[str, Any] = {
        "image": str(image),
        "image_width": width,
        "image_height": height,
        "box_count": len(boxes),
        "boxes": boxes,
        "player_dead": False,
        "hp_percent": hp_percent,
        "inventory_full": False,
        "target_visible": target_visible,
        "target_confirmed": target_visible,
        "target_type": "metin_stone" if target_visible else None,
        "target_confidence": float(target_box["confidence"]) if target_box else 0.0,
        "no_target_seconds": 0 if target_visible else 999,
        "target_xy": [target_box["x_center"], target_box["y_center"]] if target_box else None,
        "target_box": target_box,
    }
    state["recommended_action"] = decide_next_action(state)
    return state


def evaluate_image_detections(
    image_path: str | Path,
    annotation_path: str | Path,
    detections: Iterable[dict[str, Any]],
    *,
    iou_threshold: float = 0.5,
) -> dict[str, Any]:
    """Compare detector boxes against one YOLO annotation file."""
    image = Path(image_path)
    with Image.open(image) as img:
        width, height = img.size
    ground_truth = parse_yolo_annotation(annotation_path, width, height)
    detection_boxes = [normalize_detection_box(d) for d in detections]

    unmatched_gt = set(range(len(ground_truth)))
    matched_pairs: list[dict[str, Any]] = []
    false_positives = 0
    best_iou = 0.0

    for detection_index, detection in enumerate(sorted(detection_boxes, key=lambda b: float(b["confidence"]), reverse=True)):
        candidate = None
        candidate_iou = 0.0
        for gt_index in list(unmatched_gt):
            iou = box_iou(detection, ground_truth[gt_index])
            if iou > candidate_iou:
                candidate = gt_index
                candidate_iou = iou
        best_iou = max(best_iou, candidate_iou)
        if candidate is not None and candidate_iou >= iou_threshold:
            unmatched_gt.remove(candidate)
            matched_pairs.append({"detection_index": detection_index, "ground_truth_index": candidate, "iou": round(candidate_iou, 6)})
        else:
            false_positives += 1

    true_positives = len(matched_pairs)
    false_negatives = len(unmatched_gt)
    precision = true_positives / len(detection_boxes) if detection_boxes else 0.0
    recall = true_positives / len(ground_truth) if ground_truth else 0.0
    return {
        "image": str(image),
        "annotation": str(annotation_path),
        "ground_truth_count": len(ground_truth),
        "detection_count": len(detection_boxes),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "best_iou": round(best_iou, 6),
        "matches": matched_pairs,
        "ground_truth": ground_truth,
        "detections": detection_boxes,
    }


def _draw_box(draw: ImageDraw.ImageDraw, box: dict[str, Any], *, color: str, label: str, width: int = 3) -> None:
    xy = [float(box["xmin"]), float(box["ymin"]), float(box["xmax"]), float(box["ymax"])]
    draw.rectangle(xy, outline=color, width=width)
    draw.text((xy[0] + 3, max(0, xy[1] - 14)), label, fill=color)


def save_failure_preview(image_path: str | Path, evaluation_result: dict[str, Any], output_path: str | Path) -> Path:
    """Save an image overlay showing ground truth and detector boxes for a failed case.

    Ground truth boxes are green; detector boxes are red. The header includes
    TP/FP/FN and best IoU to make failure triage fast.
    """
    image = Path(image_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(image).convert("RGB") as img:
        draw = ImageDraw.Draw(img)
        for index, gt_box in enumerate(evaluation_result.get("ground_truth", []), start=1):
            _draw_box(draw, gt_box, color="lime", label=f"GT {index}")
        for index, det_box in enumerate(evaluation_result.get("detections", []), start=1):
            confidence = float(det_box.get("confidence", 0.0))
            _draw_box(draw, det_box, color="red", label=f"DET {index} {confidence:.2f}")

        header = (
            f"TP={evaluation_result.get('true_positives', 0)} "
            f"FP={evaluation_result.get('false_positives', 0)} "
            f"FN={evaluation_result.get('false_negatives', 0)} "
            f"best_iou={float(evaluation_result.get('best_iou', 0.0)):.3f}"
        )
        draw.rectangle([0, 0, min(img.width, 620), 28], fill="black")
        draw.text((8, 8), header, fill="white")
        img.save(output)

    return output


class YoloV5Detector:
    """Local YOLOv5 detector wrapper for the metin2bot repo weights.

    Loading .pt checkpoints requires PyTorch pickle loading. Only use this with
    local weights you trust inside the research sandbox.
    """

    def __init__(self, yolov5_dir: str | Path, weights: str | Path, *, device: str = "cpu", trust_checkpoint: bool = False):
        if not trust_checkpoint:
            raise ValueError("YOLOv5 .pt loading requires --trust-checkpoint for this local research sandbox")
        self.yolov5_dir = Path(yolov5_dir)
        self.weights = Path(weights)
        self.device = device
        self.model = self._load_model()

    def _load_model(self):
        import torch

        repo_root = self.yolov5_dir.parent
        for path in (str(repo_root), str(self.yolov5_dir)):
            if path not in sys.path:
                sys.path.insert(0, path)

        # PyTorch 2.6 defaults torch.load(weights_only=True), which cannot load
        # old YOLOv5 checkpoints. This scoped monkeypatch restores legacy loading
        # only after explicit --trust-checkpoint.
        original_load = torch.load

        def trusted_load(*args, **kwargs):
            kwargs.setdefault("weights_only", False)
            return original_load(*args, **kwargs)

        torch.load = trusted_load
        try:
            return torch.hub.load(
                str(self.yolov5_dir),
                "custom",
                path=str(self.weights),
                source="local",
                force_reload=False,
                device=self.device,
            )
        finally:
            torch.load = original_load

    def detect(self, image_path: str | Path) -> list[dict[str, Any]]:
        results = self.model(str(image_path))
        rows = results.pandas().xyxy[0].to_dict(orient="records")
        detections: list[dict[str, Any]] = []
        for row in rows:
            detections.append(
                normalize_detection_box(
                    {
                        "xmin": row["xmin"],
                        "ymin": row["ymin"],
                        "xmax": row["xmax"],
                        "ymax": row["ymax"],
                        "confidence": row.get("confidence", 1.0),
                        "class_id": row.get("class", 0),
                        "name": row.get("name", "metin"),
                    }
                )
            )
        return detections


def _paired_images(dataset: Path) -> list[tuple[Path, Path]]:
    images = sorted(p for p in dataset.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    return [(image, image.with_suffix(".txt")) for image in images if image.with_suffix(".txt").exists()]


def evaluate_dataset(
    detector: YoloV5Detector,
    dataset: str | Path,
    *,
    limit: int | None = None,
    iou_threshold: float = 0.5,
    failure_dir: str | Path | None = None,
) -> dict[str, Any]:
    pairs = _paired_images(Path(dataset))
    if limit is not None:
        pairs = pairs[:limit]

    failure_output_dir = Path(failure_dir) if failure_dir is not None else None
    image_results = []
    failure_previews = []
    totals = {"true_positives": 0, "false_positives": 0, "false_negatives": 0, "ground_truth_count": 0, "detection_count": 0}
    for image, annotation in pairs:
        detections = detector.detect(image)
        result = evaluate_image_detections(image, annotation, detections, iou_threshold=iou_threshold)
        has_failure = result["false_positives"] > 0 or result["false_negatives"] > 0
        if failure_output_dir is not None and has_failure:
            preview_path = failure_output_dir / f"{image.stem}_fp{result['false_positives']}_fn{result['false_negatives']}_iou{result['best_iou']:.3f}.jpg"
            save_failure_preview(image, result, preview_path)
            result["failure_preview"] = str(preview_path)
            failure_previews.append(str(preview_path))
        image_results.append(result)
        for key in totals:
            totals[key] += int(result[key])

    precision = totals["true_positives"] / totals["detection_count"] if totals["detection_count"] else 0.0
    recall = totals["true_positives"] / totals["ground_truth_count"] if totals["ground_truth_count"] else 0.0
    return {
        "dataset": str(dataset),
        "evaluated_images": len(pairs),
        "iou_threshold": iou_threshold,
        **totals,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "failure_preview_count": len(failure_previews),
        "failure_previews": failure_previews,
        "images": image_results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local YOLOv5 detector and compare detections against YOLO annotations.")
    parser.add_argument("image_or_dataset", help="Annotated image or dataset directory")
    parser.add_argument("--annotation", help="Annotation path for single-image mode")
    parser.add_argument("--yolov5-dir", required=True, help="Path to local yolov5 directory")
    parser.add_argument("--weights", required=True, help="Path to YOLOv5 .pt weights")
    parser.add_argument("--trust-checkpoint", action="store_true", help="Allow trusted local .pt pickle loading")
    parser.add_argument("--limit", type=int, default=None, help="Max images for dataset mode")
    parser.add_argument("--iou", type=float, default=0.5, help="IoU threshold")
    parser.add_argument("--state-out", help="Single-image state JSON output path")
    parser.add_argument("--preview-out", help="Single-image annotated preview output path")
    parser.add_argument("--eval-out", help="Evaluation JSON output path")
    parser.add_argument("--failure-dir", help="Dataset mode: save FP/FN annotated failure previews here")
    args = parser.parse_args(argv)

    detector = YoloV5Detector(args.yolov5_dir, args.weights, trust_checkpoint=args.trust_checkpoint)
    target = Path(args.image_or_dataset)

    if target.is_dir():
        report = evaluate_dataset(detector, target, limit=args.limit, iou_threshold=args.iou, failure_dir=args.failure_dir)
    else:
        annotation = Path(args.annotation) if args.annotation else target.with_suffix(".txt")
        detections = detector.detect(target)
        state = build_state_from_detections(target, detections)
        evaluation = evaluate_image_detections(target, annotation, detections, iou_threshold=args.iou)
        report = {"state": state, "evaluation": evaluation}
        if args.state_out:
            state_path = Path(args.state_out)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if args.preview_out:
            save_annotated_preview(target, state, args.preview_out)

    if args.eval_out:
        out = Path(args.eval_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
