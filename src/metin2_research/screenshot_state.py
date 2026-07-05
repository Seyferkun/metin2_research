from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .dataset import parse_yolo_annotation
from .policy import decide_next_action


def _round_box(box: dict[str, float | int], ndigits: int = 3) -> dict[str, float | int]:
    rounded: dict[str, float | int] = {}
    for key, value in box.items():
        if isinstance(value, float):
            rounded[key] = round(value, ndigits)
        else:
            rounded[key] = value
    return rounded


def _box_area(box: dict[str, float | int]) -> float:
    return float(box["width"]) * float(box["height"])


def build_state_from_annotation(
    image_path: str | Path,
    annotation_path: str | Path | None = None,
    *,
    hp_percent: int = 100,
) -> dict[str, Any]:
    """Convert one annotated screenshot into a symbolic advisory state.

    This uses ground-truth YOLO annotations, not a detector. It is intended for
    research-sandbox evaluation and bootstrapping the screenshot -> state ->
    recommendation loop.
    """
    image = Path(image_path)
    annotation = Path(annotation_path) if annotation_path is not None else image.with_suffix(".txt")

    with Image.open(image) as img:
        width, height = img.size

    boxes = parse_yolo_annotation(annotation, image_width=width, image_height=height)
    boxes = [_round_box(box) for box in boxes]
    target_box = max(boxes, key=_box_area) if boxes else None

    state: dict[str, Any] = {
        "image": str(image),
        "annotation": str(annotation),
        "image_width": width,
        "image_height": height,
        "box_count": len(boxes),
        "boxes": boxes,
        "player_dead": False,
        "hp_percent": hp_percent,
        "inventory_full": False,
        "target_visible": target_box is not None,
        "target_confirmed": target_box is not None,
        "target_type": "metin_stone" if target_box is not None else None,
        "target_confidence": 1.0 if target_box is not None else 0.0,
        "no_target_seconds": 0 if target_box is not None else 999,
    }

    if target_box is not None:
        state["target_xy"] = [target_box["x_center"], target_box["y_center"]]
        state["target_box"] = target_box
    else:
        state["target_xy"] = None
        state["target_box"] = None

    state["recommended_action"] = decide_next_action(state)
    return state


def save_annotated_preview(image_path: str | Path, state: dict[str, Any], output_path: str | Path) -> Path:
    """Draw target boxes and the recommended action onto a screenshot."""
    image = Path(image_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(image).convert("RGB") as img:
        draw = ImageDraw.Draw(img)
        boxes = state.get("boxes") or []
        for index, box in enumerate(boxes, start=1):
            xy = [float(box["xmin"]), float(box["ymin"]), float(box["xmax"]), float(box["ymax"])]
            color = "red" if box == state.get("target_box") else "yellow"
            draw.rectangle(xy, outline=color, width=3)
            label = f"metin #{index}"
            draw.text((xy[0] + 3, max(0, xy[1] - 14)), label, fill=color)

        action = (state.get("recommended_action") or {}).get("action", "UNKNOWN")
        target_xy = state.get("target_xy")
        overlay = f"action: {action}"
        if target_xy:
            overlay += f" | target: ({target_xy[0]:.0f}, {target_xy[1]:.0f})"
        draw.rectangle([0, 0, min(img.width, 620), 28], fill="black")
        draw.text((8, 8), overlay, fill="white")
        img.save(output)

    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build state JSON and annotated preview from one annotated Metin2 screenshot.")
    parser.add_argument("image", help="Screenshot image path")
    parser.add_argument("--annotation", help="YOLO annotation path; defaults to image path with .txt suffix")
    parser.add_argument("--state-out", required=True, help="Output JSON path")
    parser.add_argument("--preview-out", required=True, help="Output annotated image path")
    parser.add_argument("--hp", type=int, default=100, help="Optional simulated HP percent for policy testing")
    args = parser.parse_args(argv)

    state = build_state_from_annotation(args.image, args.annotation, hp_percent=args.hp)
    state_out = Path(args.state_out)
    state_out.parent.mkdir(parents=True, exist_ok=True)
    state_out.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    save_annotated_preview(args.image, state, args.preview_out)
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
