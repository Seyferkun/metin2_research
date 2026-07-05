from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_yolo_annotation(path: str | Path, image_width: int, image_height: int) -> list[dict[str, float | int]]:
    """Parse a YOLO txt annotation file and return pixel-space boxes.

    YOLO rows are: class_id x_center y_center width height, normalized to [0, 1].
    Blank and malformed rows are skipped so imperfect research datasets remain scannable.
    """
    annotation_path = Path(path)
    boxes: list[dict[str, float | int]] = []
    if not annotation_path.exists():
        return boxes

    for raw_line in annotation_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = raw_line.strip().split()
        if len(parts) < 5:
            continue
        try:
            class_id = int(float(parts[0]))
            x_center_n, y_center_n, width_n, height_n = map(float, parts[1:5])
        except ValueError:
            continue

        x_center = x_center_n * image_width
        y_center = y_center_n * image_height
        width = width_n * image_width
        height = height_n * image_height
        boxes.append(
            {
                "class_id": class_id,
                "x_center": x_center,
                "y_center": y_center,
                "width": width,
                "height": height,
                "xmin": x_center - width / 2,
                "ymin": y_center - height / 2,
                "xmax": x_center + width / 2,
                "ymax": y_center + height / 2,
            }
        )
    return boxes


def count_yolo_rows(path: str | Path) -> tuple[int, Counter[str]]:
    """Count valid YOLO rows and class IDs without needing image dimensions."""
    annotation_path = Path(path)
    total = 0
    classes: Counter[str] = Counter()
    if not annotation_path.exists():
        return total, classes
    for raw_line in annotation_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = raw_line.strip().split()
        if len(parts) < 5:
            continue
        try:
            class_id = str(int(float(parts[0])))
            # Validate numeric coordinates.
            list(map(float, parts[1:5]))
        except ValueError:
            continue
        total += 1
        classes[class_id] += 1
    return total, classes


def scan_yolo_dataset(root: str | Path) -> dict[str, Any]:
    """Scan a YOLO-style image/label directory.

    Pairs are matched by file stem in the same directory tree. The function is deliberately
    read-only and does not import game automation modules.
    """
    dataset_root = Path(root)
    images = sorted(p for p in dataset_root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    annotations = sorted(p for p in dataset_root.rglob("*.txt") if p.is_file())

    image_keys = {(p.parent.relative_to(dataset_root), p.stem): p for p in images}
    annotation_keys = {(p.parent.relative_to(dataset_root), p.stem): p for p in annotations}

    paired_keys = sorted(set(image_keys) & set(annotation_keys))
    orphan_annotation_keys = sorted(set(annotation_keys) - set(image_keys))
    orphan_image_keys = sorted(set(image_keys) - set(annotation_keys))

    classes: Counter[str] = Counter()
    total_boxes = 0
    examples = []
    for key in paired_keys:
        annotation_path = annotation_keys[key]
        count, annotation_classes = count_yolo_rows(annotation_path)
        total_boxes += count
        classes.update(annotation_classes)
        if len(examples) < 10:
            examples.append(
                {
                    "image": str(image_keys[key]),
                    "annotation": str(annotation_path),
                    "boxes": count,
                }
            )

    return {
        "root": str(dataset_root),
        "image_count": len(images),
        "annotation_count": len(annotations),
        "paired_count": len(paired_keys),
        "orphan_annotation_count": len(orphan_annotation_keys),
        "orphan_image_count": len(orphan_image_keys),
        "total_boxes": total_boxes,
        "classes": dict(sorted(classes.items())),
        "examples": examples,
    }
