#!/usr/bin/env python
"""Export YOLOv5 .pt checkpoint to ONNX for lightweight inference.

Usage:
    python scripts/export_onnx.py <path-to-best.pt>

    # Default: exports the current best model
    python scripts/export_onnx.py

    # Custom export
    python scripts/export_onnx.py path/to/custom_model.pt --opset 12 --input-size 640
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Export YOLOv5 .pt checkpoint to ONNX")
    parser.add_argument("weights", nargs="?", default=None, help="Path to .pt weights")
    parser.add_argument("--opset", type=int, default=12, help="ONNX opset version")
    parser.add_argument("--input-size", type=int, default=640, help="Model input size (px)")
    parser.add_argument("--yolov5-dir", help="Path to the YOLOv5 repo directory")
    args = parser.parse_args()

    # Default weights
    if args.weights is None:
        default_path = (
            ROOT / "reports" / "yolo_easy_retrain_runs" / "round2_hardneg_10ep_lowlr" / "weights" / "best.pt"
        )
        if not default_path.exists():
            print("No weights path given and default not found.")
            print(f"  (looked: {default_path})")
            return 1
        args.weights = str(default_path)

    weights = Path(args.weights)
    if not weights.exists():
        print(f"Weights not found: {weights}")
        return 1

    onnx_path = weights.with_suffix(".onnx")
    print(f"Exporting {weights} → {onnx_path}")
    print(f"  ONNX opset: {args.opset}")
    print(f"  Input size: {args.input_size}")

    # Set up YOLOv5 repo path
    yolov5_dir = args.yolov5_dir
    if not yolov5_dir:
        yolov5_dir = r"C:\Users\blade\AppData\Local\Temp\metin2bot\yolov5"
    yolov5_dir = Path(yolov5_dir)

    if not yolov5_dir.exists():
        print(f"YOLOv5 repo not found at {yolov5_dir}")
        print("Clone it: git clone https://github.com/ultralytics/yolov5.git")
        return 1

    import sys as _sys
    _sys.path.insert(0, str(yolov5_dir))
    _sys.path.insert(0, str(yolov5_dir.parent))

    import yolov5.utils  # noqa: F401
    import models.common  # noqa: F401
    import torch

    print("Loading checkpoint...")
    ckpt = torch.load(str(weights), map_location="cpu", weights_only=False)
    model = ckpt["model"].float()
    model.eval()

    dummy = torch.randn(1, 3, args.input_size, args.input_size)

    print("Exporting to ONNX...")
    torch.onnx.export(
        model,
        dummy,
        str(onnx_path),
        input_names=["images"],
        output_names=["output0"],
        opset_version=args.opset,
        do_constant_folding=True,
        dynamic_axes={"images": {0: "batch"}, "output0": {0: "batch", 1: "num_detections"}},
    )

    size_mb = onnx_path.stat().st_size / (1024 * 1024)
    print(f"\n Done! {onnx_path.name} ({size_mb:.1f} MB)")

    # Verify with onnxruntime
    try:
        import onnxruntime as ort
        session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        inp = session.get_inputs()[0]
        out = session.get_outputs()[0]
        print(f"  Verified: {inp.name}{inp.shape} → {out.name}{out.shape}")
        print(f"  ONNX model is ready for inference!")
    except ImportError:
        print("  (onnxruntime not installed; install with 'pip install onnxruntime' to verify)")
    except Exception as e:
        print(f"  Verification warning: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())