"""ONNX YOLOv5 detector — the model output is already fully decoded.

Uses onnxruntime. ~10MB model vs 1.5GB PyTorch. ~50ms inference on CPU.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def _letterbox(
    img: Image.Image, target_size: int = 640, color: tuple[int, int, int] = (114, 114, 114)
) -> tuple[np.ndarray, float, int, int]:
    """Resize with aspect-ratio preservation + padding. Returns CHW [0,1] float32 tensor."""
    w, h = img.size
    scale = min(target_size / w, target_size / h)
    nw, nh = int(w * scale), int(h * scale)
    resized = img.resize((nw, nh), Image.LANCZOS)
    pad = Image.new("RGB", (target_size, target_size), color)
    px, py = (target_size - nw) // 2, (target_size - nh) // 2
    pad.paste(resized, (px, py))
    arr = np.asarray(pad, dtype=np.float32).transpose(2, 0, 1) / 255.0
    return arr, scale, px, py


def _nms(boxes_xyxy: np.ndarray, scores: np.ndarray, iou_threshold: float = 0.45) -> np.ndarray:
    """Greedy NMS. Returns boolean keep mask."""
    order = np.argsort(-scores)
    keep = np.ones(len(scores), dtype=bool)
    for i in range(len(scores)):
        if not keep[i]:
            continue
        best = order[i]
        # IoU of best vs rest
        rest = order[i + 1:][keep[order[i + 1:]]]
        if len(rest) == 0:
            break
        inter_x1 = np.maximum(boxes_xyxy[best, 0], boxes_xyxy[rest, 0])
        inter_y1 = np.maximum(boxes_xyxy[best, 1], boxes_xyxy[rest, 1])
        inter_x2 = np.minimum(boxes_xyxy[best, 2], boxes_xyxy[rest, 2])
        inter_y2 = np.minimum(boxes_xyxy[best, 3], boxes_xyxy[rest, 3])
        inter = np.maximum(0.0, inter_x2 - inter_x1) * np.maximum(0.0, inter_y2 - inter_y1)
        area_best = (boxes_xyxy[best, 2] - boxes_xyxy[best, 0]) * (boxes_xyxy[best, 3] - boxes_xyxy[best, 1])
        area_rest = (boxes_xyxy[rest, 2] - boxes_xyxy[rest, 0]) * (boxes_xyxy[rest, 3] - boxes_xyxy[rest, 1])
        union = area_best + area_rest - inter
        iou = np.where(union > 0, inter / union, 0.0)
        # Suppress overlapping boxes
        suppressed = np.zeros(len(scores), dtype=bool)
        suppressed[rest[iou > iou_threshold]] = True
        keep &= ~suppressed
    return keep


def _scale_back(
    dets: list[dict], input_size: int, ow: int, oh: int, scale: float, px: int, py: int
) -> list[dict]:
    """Undo letterbox padding/rescale — model space → original image coords."""
    for d in dets:
        # Clip to model input bounds
        for k in ("xmin", "xmax", "x_center"):
            d[k] = max(0.0, min(float(d[k]), float(input_size)))
        for k in ("ymin", "ymax", "y_center"):
            d[k] = max(0.0, min(float(d[k]), float(input_size)))
        d["width"] = float(d["xmax"]) - float(d["xmin"])
        d["height"] = float(d["ymax"]) - float(d["ymin"])

    for d in dets:
        # Remove padding, rescale
        for k in ("xmin", "xmax", "x_center"):
            d[k] = (float(d[k]) - px) / scale
        for k in ("ymin", "ymax", "y_center"):
            d[k] = (float(d[k]) - py) / scale
        d["xmax"] = min(float(ow), d["xmax"])
        d["ymax"] = min(float(oh), d["ymax"])
        d["width"] = d["xmax"] - d["xmin"]
        d["height"] = d["ymax"] - d["ymin"]
        d["x_center"] = d["xmin"] + d["width"] / 2
    return dets


class OnnxYoloDetector:
    """YOLOv5 detection via ONNX Runtime. No PyTorch dependency.

    The ONNX model output is fully decoded: [x_center, y_center, width, height, confidence, class_score]
    in pixel coordinates. We just filter by confidence, NMS, and scale back.
    """

    def __init__(
        self,
        onnx_path: str | Path,
        *,
        input_size: int = 640,
        conf_threshold: float = 0.25,
        nms_iou: float = 0.45,
        providers: list[str] | None = None,
    ):
        import onnxruntime as ort

        self.onnx_path = Path(onnx_path)
        if not self.onnx_path.exists():
            raise FileNotFoundError(f"ONNX model not found: {self.onnx_path}")
        if providers is None:
            providers = ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(self.onnx_path), providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.input_size = input_size
        self.conf_threshold = conf_threshold
        self.nms_iou = nms_iou

    def detect(self, image_path: str | Path) -> list[dict[str, Any]]:
        """Run detection. Returns list of {xmin, ymin, xmax, ymax, x_center, y_center,
        width, height, confidence, class_id, name}."""
        img = Image.open(image_path).convert("RGB")
        ow, oh = img.size
        tensor, scale, px, py = _letterbox(img, self.input_size)
        outputs = self.session.run(None, {self.input_name: tensor[np.newaxis, :]})
        raw = outputs[0][0]  # (25500, 6) — decoded: [cx, cy, w, h, conf, cls]

        # Filter by confidence
        mask = raw[:, 4] >= self.conf_threshold
        candidates = raw[mask]
        if len(candidates) == 0:
            return []

        # Build xyxy for NMS
        cx, cy, bw, bh, conf = candidates[:, 0], candidates[:, 1], candidates[:, 2], candidates[:, 3], candidates[:, 4]
        x1 = cx - bw / 2
        y1 = cy - bh / 2
        x2 = cx + bw / 2
        y2 = cy + bh / 2
        xyxy = np.stack([x1, y1, x2, y2], axis=1)

        # NMS
        keep = _nms(xyxy, conf, self.nms_iou)
        if not np.any(keep):
            return []

        dets = [
            {
                "xmin": float(x1[i]),
                "ymin": float(y1[i]),
                "xmax": float(x2[i]),
                "ymax": float(y2[i]),
                "x_center": float(cx[i]),
                "y_center": float(cy[i]),
                "width": float(bw[i]),
                "height": float(bh[i]),
                "confidence": round(float(conf[i]), 6),
                "class_id": 0,
                "name": "metin_stone",
            }
            for i in np.where(keep)[0]
        ]

        # Scale back to original image space
        dets = _scale_back(dets, self.input_size, ow, oh, scale, px, py)
        return dets


def estimate_distance(box: dict[str, Any]) -> str:
    """Distance label based on box height in original image pixels."""
    h = float(box.get("height", 0))
    if h >= 160:
        return "close"
    elif h >= 70:
        return "medium"
    elif h >= 30:
        return "far"
    return "very_far"


def estimate_distance_meters(box: dict[str, Any]) -> float:
    """Rough distance. ~200px=0m, ~20px=30m at ~640-wide view."""
    h = float(box.get("height", 0))
    if h < 5:
        return 99.0
    return round(30.0 * (20.0 / max(h, 5.0)), 1)