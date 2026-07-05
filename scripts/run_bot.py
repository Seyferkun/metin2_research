#!/usr/bin/env python
"""Unified Metin2 sandbox bot — runs a state machine with ONNX detection.

Usage:
    # Dry run (no keypresses/clicks)
    python scripts/run_bot.py --duration 30 --dry-run

    # Live run
    python scripts/run_bot.py --duration 120 --execute

    # Test detection once
    python scripts/run_bot.py --once

    # Use PyTorch YOLOv5 backend instead of ONNX
    python scripts/run_bot.py --backend yolov5 --duration 30
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Ensure src is on the path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from metin2_research.config import load_config


def setup_logging(level: str = "info") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%H:%M:%S",
    )


def build_detector(cfg: dict, backend: str = "onnx", trust_checkpoint: bool = False):
    """Build a detector from config. Returns a detector object with .detect(path) method."""
    model_cfg = cfg.get("model", {})
    detection_cfg = cfg.get("detection", {})

    if backend == "onnx":
        onnx_path = model_cfg.get("onnx_weights", "best.onnx")
        p = Path(onnx_path)
        if not p.is_absolute():
            p = ROOT / p
        if not p.exists():
            print(f"ONNX model not found at {p}. Run 'python scripts/export_onnx.py' first.")
            sys.exit(1)

        from metin2_research.onnx_detector import OnnxYoloDetector
        return OnnxYoloDetector(
            str(p),
            input_size=model_cfg.get("input_size", [640, 640])[0],
            conf_threshold=detection_cfg.get("min_confidence", 0.25),
            nms_iou=detection_cfg.get("nms_iou", 0.45),
        )

    elif backend == "yolov5":
        # PyTorch YOLOv5 (fallback, requires full yolov5 repo)
        if not trust_checkpoint:
            print("YOLOv5 backend requires --trust-checkpoint for legacy .pt loading")
            sys.exit(1)
        from metin2_research.detector import YoloV5Detector
        return YoloV5Detector(
            model_cfg.get("yolov5_dir", ""),
            model_cfg.get("yolov5_weights", ""),
            trust_checkpoint=True,
        )

    else:
        print(f"Unknown backend: {backend}. Use 'onnx' or 'yolov5'.")
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified Metin2 sandbox bot controller.")
    parser.add_argument("--config", help="Path to config.yaml (default: auto-detect)")
    parser.add_argument("--duration", type=float, default=60.0, help="Run duration in seconds")
    parser.add_argument("--once", action="store_true", help="Single detect+state output, no loop")
    parser.add_argument("--dry-run", action="store_true", help="No keypresses/clicks")
    parser.add_argument("--execute", dest="dry_run", action="store_false", help="Enable keypresses/clicks")
    parser.add_argument("--backend", choices=["onnx", "yolov5"], default="onnx", help="Detection backend")
    parser.add_argument("--trust-checkpoint", action="store_true", help="Allow YOLOv5 .pt loading")
    parser.add_argument("--out-dir", help="Output directory for captures/reports")
    parser.add_argument("--window-query", help="Window title/process to target")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warn", "error"])
    args = parser.parse_args()

    setup_logging(args.log_level)
    cfg = load_config(args.config)

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = ROOT / cfg.get("output", {}).get("out_dir", "reports/bot_run")

    if args.window_query:
        cfg.setdefault("window", {})["query"] = args.window_query

    detector = build_detector(cfg, backend=args.backend, trust_checkpoint=args.trust_checkpoint)

    if args.once:
        # Single detection cycle
        from metin2_research.controller import MetinController
        controller = MetinController(
            cfg,
            detector=detector,
            window_query=cfg.get("window", {}).get("query"),
            out_dir=out_dir,
            dry_run=True,
        )
        controller._capture_and_detect()
        state = controller.state
        print(f"\n{'='*60}")
        print(f"  Target visible: {state.target_visible}")
        print(f"  Confidence:     {state.target_confidence:.3f}")
        print(f"  Distance:       {state.distance_label} (~{state.distance_meters}m)")
        print(f"  Boxes:          {1 if state.target_visible else 0}")
        if state.screen_xy:
            print(f"  Screen xy:      ({state.screen_xy[0]}, {state.screen_xy[1]})")
        print(f"{'='*60}\n")
        return 0

    print(f"\n{'='*60}")
    print(f"  Metin2 Bot Controller")
    print(f"  Duration:  {args.duration}s")
    print(f"  Backend:   {args.backend}")
    print(f"  Dry run:   {args.dry_run}")
    print(f"  Out dir:   {out_dir}")
    print(f"{'='*60}\n")

    from metin2_research.controller import MetinController
    controller = MetinController(
        cfg,
        detector=detector,
        window_query=cfg.get("window", {}).get("query"),
        out_dir=out_dir,
        dry_run=args.dry_run,
    )

    summary = controller.run(duration=args.duration)

    print(f"\n{'='*60}")
    print(f"  Run complete!")
    print(f"  Duration:  {summary['duration']:.1f}s")
    print(f"  Steps:     {summary['total_steps']}")
    print(f"  Potions:   {summary['potions_used']}")
    print(f"  Destroyed: {summary['metins_destroyed_estimate']} (est.)")
    print(f"  Summary:   {summary['summary_path']}")
    print(f"{'='*60}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())