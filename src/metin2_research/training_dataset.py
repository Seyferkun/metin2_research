from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _read_csv(path: str | Path | None) -> list[dict[str, str]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _safe_stem(path: str | Path) -> str:
    return Path(path).stem.replace(" ", "_")


def _image_files(source_dataset: Path) -> list[Path]:
    return sorted(p for p in source_dataset.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def _matching_corrected_label(corrected_labels_dir: Path | None, image_stem: str) -> Path | None:
    if not corrected_labels_dir or not corrected_labels_dir.exists():
        return None
    exact = corrected_labels_dir / f"{image_stem}.txt"
    if exact.exists():
        return exact
    matches = sorted(corrected_labels_dir.glob(f"*_{image_stem}.txt"))
    return matches[0] if matches else None


def _copy_pair(image: Path, label: Path, out_images: Path, out_labels: Path, out_stem: str) -> None:
    out_images.mkdir(parents=True, exist_ok=True)
    out_labels.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image, out_images / f"{out_stem}{image.suffix.lower()}")
    if label.exists():
        shutil.copy2(label, out_labels / f"{out_stem}.txt")
    else:
        (out_labels / f"{out_stem}.txt").write_text("", encoding="utf-8")


def _load_eval_by_image(eval_report: str | Path | None) -> dict[str, dict[str, Any]]:
    if not eval_report:
        return {}
    p = Path(eval_report)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {Path(item["image"]).name: item for item in data.get("images", [])}


def _box_iou(a: dict[str, float], b: dict[str, float]) -> float:
    x1 = max(float(a["xmin"]), float(b["xmin"]))
    y1 = max(float(a["ymin"]), float(b["ymin"]))
    x2 = min(float(a["xmax"]), float(b["xmax"]))
    y2 = min(float(a["ymax"]), float(b["ymax"]))
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, float(a["xmax"]) - float(a["xmin"])) * max(0.0, float(a["ymax"]) - float(a["ymin"]))
    area_b = max(0.0, float(b["xmax"]) - float(b["xmin"])) * max(0.0, float(b["ymax"]) - float(b["ymin"]))
    denom = area_a + area_b - inter
    return inter / denom if denom else 0.0


def _false_positive_detections(item: dict[str, Any], threshold: float = 0.3) -> list[dict[str, Any]]:
    gts = item.get("ground_truth", [])
    out = []
    for det in item.get("detections", []):
        best = max((_box_iou(det, gt) for gt in gts), default=0.0)
        if best < threshold:
            out.append(det)
    return out


def _save_hard_negative_crops(
    *,
    row: dict[str, str],
    eval_by_image: dict[str, dict[str, Any]],
    out_images: Path,
    out_labels: Path,
    start_index: int,
) -> int:
    image = Path(row["image"])
    item = eval_by_image.get(image.name)
    if not item:
        _copy_pair(image, Path("__missing_empty_label__.txt"), out_images, out_labels, f"{image.stem}_hardneg{start_index:03d}")
        (out_labels / f"{image.stem}_hardneg{start_index:03d}.txt").write_text("", encoding="utf-8")
        return 1

    detections = _false_positive_detections(item)
    if not detections:
        return 0

    written = 0
    with Image.open(image).convert("RGB") as im:
        for det in detections:
            pad = 0.35
            w = float(det["xmax"]) - float(det["xmin"])
            h = float(det["ymax"]) - float(det["ymin"])
            xmin = max(0, int(float(det["xmin"]) - w * pad))
            ymin = max(0, int(float(det["ymin"]) - h * pad))
            xmax = min(im.width, int(float(det["xmax"]) + w * pad))
            ymax = min(im.height, int(float(det["ymax"]) + h * pad))
            if xmax <= xmin or ymax <= ymin:
                continue
            stem = f"{image.stem}_hardneg{start_index + written:03d}"
            im.crop((xmin, ymin, xmax, ymax)).save(out_images / f"{stem}.jpg", quality=92)
            (out_labels / f"{stem}.txt").write_text("", encoding="utf-8")
            written += 1
    return written


def build_training_dataset(
    *,
    source_dataset: str | Path,
    output_dir: str | Path,
    corrected_labels_dir: str | Path | None = None,
    hard_negative_manifest: str | Path | None = None,
    oversample_manifest: str | Path | None = None,
    eval_report: str | Path | None = None,
    val_fraction: float = 0.15,
    oversample_copies: int = 1,
) -> dict[str, Any]:
    source = Path(source_dataset)
    output = Path(output_dir)
    corrected_dir = Path(corrected_labels_dir) if corrected_labels_dir else None
    if output.exists():
        shutil.rmtree(output)
    for split in ["train", "val"]:
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)

    images = _image_files(source)
    val_count = max(1, round(len(images) * val_fraction)) if images else 0
    val_names = {p.name for p in images[-val_count:]}
    corrected_applied = 0

    for image in images:
        split = "val" if image.name in val_names else "train"
        label = _matching_corrected_label(corrected_dir, image.stem) or (source / f"{image.stem}.txt")
        if corrected_dir and _matching_corrected_label(corrected_dir, image.stem):
            corrected_applied += 1
        _copy_pair(image, label, output / "images" / split, output / "labels" / split, image.stem)

    oversample_rows = _read_csv(oversample_manifest)
    oversample_written = 0
    for row in oversample_rows:
        image = Path(row["image"])
        src_image = source / image.name if (source / image.name).exists() else image
        label = _matching_corrected_label(corrected_dir, src_image.stem) or (source / f"{src_image.stem}.txt")
        if not src_image.exists():
            continue
        for copy_index in range(1, oversample_copies + 1):
            _copy_pair(
                src_image,
                label,
                output / "images" / "train",
                output / "labels" / "train",
                f"{src_image.stem}_aug{copy_index}",
            )
            oversample_written += 1

    hard_negative_rows = _read_csv(hard_negative_manifest)
    eval_by_image = _load_eval_by_image(eval_report)
    hard_neg_written = 0
    for row in hard_negative_rows:
        hard_neg_written += _save_hard_negative_crops(
            row=row,
            eval_by_image=eval_by_image,
            out_images=output / "images" / "train",
            out_labels=output / "labels" / "train",
            start_index=hard_neg_written + 1,
        )

    dataset_yaml = output / "dataset.yaml"
    dataset_yaml.write_text(
        f"path: {output.resolve().as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "nc: 1\n"
        "names: ['metin_stone']\n",
        encoding="utf-8",
    )

    summary = {
        "output_dir": str(output),
        "dataset_yaml": str(dataset_yaml),
        "base_images": len(images),
        "train_images": len(list((output / "images" / "train").glob("*.jpg"))),
        "val_images": len(list((output / "images" / "val").glob("*.jpg"))),
        "train_labels": len(list((output / "labels" / "train").glob("*.txt"))),
        "val_labels": len(list((output / "labels" / "val").glob("*.txt"))),
        "corrected_labels_applied": corrected_applied,
        "hard_negative_images": hard_neg_written,
        "oversample_images": oversample_written,
        "val_fraction": val_fraction,
    }
    (output / "build_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build a YOLO training dataset from corrected labels and review decisions.")
    parser.add_argument("source_dataset")
    parser.add_argument("--out", required=True)
    parser.add_argument("--corrected-labels-dir")
    parser.add_argument("--hard-negative-manifest")
    parser.add_argument("--oversample-manifest")
    parser.add_argument("--eval-report")
    parser.add_argument("--val-fraction", type=float, default=0.15)
    parser.add_argument("--oversample-copies", type=int, default=1)
    args = parser.parse_args(argv)
    summary = build_training_dataset(
        source_dataset=args.source_dataset,
        output_dir=args.out,
        corrected_labels_dir=args.corrected_labels_dir,
        hard_negative_manifest=args.hard_negative_manifest,
        oversample_manifest=args.oversample_manifest,
        eval_report=args.eval_report,
        val_fraction=args.val_fraction,
        oversample_copies=args.oversample_copies,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
