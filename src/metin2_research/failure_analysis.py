from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


def is_failure(image_result: dict[str, Any]) -> bool:
    return int(image_result.get("false_positives", 0)) > 0 or int(image_result.get("false_negatives", 0)) > 0


def categorize_failure(image_result: dict[str, Any]) -> str:
    """Heuristically label an image-level detector failure.

    These categories are meant for triage, not final truth. The preview images
    remain the source of evidence for deciding whether a label or detector is wrong.
    """
    fp = int(image_result.get("false_positives", 0))
    fn = int(image_result.get("false_negatives", 0))
    detections = int(image_result.get("detection_count", 0))
    best_iou = float(image_result.get("best_iou", 0.0))

    if fp > 0 and fn == 0:
        return "duplicate_or_extra_detection"
    if fn > 0 and fp == 0:
        if detections == 0 or best_iou == 0:
            return "missed_detection"
        return "low_confidence_or_filtered_detection"
    if fp > 0 and fn > 0:
        if best_iou == 0:
            return "wrong_location_or_label_mismatch"
        if best_iou < 0.5:
            return "localization_below_iou_threshold"
        return "mixed_duplicate_and_miss"
    return "not_a_failure"


def _failure_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for image_result in report.get("images", []):
        if not is_failure(image_result):
            continue
        row = dict(image_result)
        row["category"] = categorize_failure(image_result)
        rows.append(row)
    return rows


def summarize_failures(report: dict[str, Any], *, markdown_out: str | Path | None = None) -> dict[str, Any]:
    failures = _failure_rows(report)
    category_counts = Counter(row["category"] for row in failures)
    fp_fn_counts = Counter(f"fp{row.get('false_positives', 0)}_fn{row.get('false_negatives', 0)}" for row in failures)

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in failures:
        by_category[row["category"]].append(
            {
                "image": row.get("image"),
                "false_positives": row.get("false_positives", 0),
                "false_negatives": row.get("false_negatives", 0),
                "best_iou": row.get("best_iou", 0.0),
                "failure_preview": row.get("failure_preview"),
            }
        )

    for rows in by_category.values():
        rows.sort(key=lambda item: float(item.get("best_iou", 0.0)))

    best_ious = [float(row.get("best_iou", 0.0)) for row in failures]
    summary = {
        "evaluated_images": report.get("evaluated_images", 0),
        "precision": report.get("precision", 0.0),
        "recall": report.get("recall", 0.0),
        "failure_count": len(failures),
        "category_counts": dict(category_counts),
        "fp_fn_counts": dict(fp_fn_counts),
        "best_iou": {
            "min": min(best_ious) if best_ious else None,
            "max": max(best_ious) if best_ious else None,
            "avg": round(sum(best_ious) / len(best_ious), 6) if best_ious else None,
        },
        "by_category": dict(by_category),
    }

    if markdown_out is not None:
        write_markdown_summary(summary, markdown_out)
    return summary


def write_markdown_summary(summary: dict[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# YOLOv5 Failure Analysis",
        "",
        f"- Evaluated images: {summary.get('evaluated_images')}",
        f"- Precision: {summary.get('precision')}",
        f"- Recall: {summary.get('recall')}",
        f"- Failure images: {summary.get('failure_count')}",
        f"- Failure best-IoU min/avg/max: {summary['best_iou']['min']} / {summary['best_iou']['avg']} / {summary['best_iou']['max']}",
        "",
        "## Category counts",
        "",
    ]
    for category, count in sorted(summary.get("category_counts", {}).items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {category}: {count}")

    lines.extend(["", "## FP/FN pattern counts", ""])
    for pattern, count in sorted(summary.get("fp_fn_counts", {}).items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {pattern}: {count}")

    lines.extend(["", "## Worst examples by category", ""])
    for category, rows in sorted(summary.get("by_category", {}).items()):
        lines.extend([f"### {category}", ""])
        for row in rows[:10]:
            image_name = Path(str(row.get("image"))).name
            preview = row.get("failure_preview") or ""
            lines.append(
                f"- {image_name}: FP={row.get('false_positives')} FN={row.get('false_negatives')} "
                f"best_iou={row.get('best_iou')} preview={preview}"
            )
        lines.append("")

    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output


def create_contact_sheets(
    summary: dict[str, Any],
    output_dir: str | Path,
    *,
    max_per_category: int = 12,
    thumb_size: tuple[int, int] = (320, 240),
    columns: int = 3,
) -> list[str]:
    """Create one contact sheet per failure category from existing preview images."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for category, rows in summary.get("by_category", {}).items():
        preview_paths = [Path(str(row.get("failure_preview"))) for row in rows if row.get("failure_preview")]
        preview_paths = [path for path in preview_paths if path.exists()][:max_per_category]
        if not preview_paths:
            continue

        cell_w, cell_h = thumb_size[0], thumb_size[1] + 34
        sheet_rows = (len(preview_paths) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * cell_w, sheet_rows * cell_h), "white")
        draw = ImageDraw.Draw(sheet)
        for index, preview in enumerate(preview_paths):
            x = (index % columns) * cell_w
            y = (index // columns) * cell_h
            with Image.open(preview).convert("RGB") as img:
                img.thumbnail(thumb_size)
                sheet.paste(img, (x, y))
            label = preview.name[:48]
            draw.text((x + 4, y + thumb_size[1] + 4), label, fill="black")

        out_path = out_dir / f"{category}.jpg"
        sheet.save(out_path)
        written.append(str(out_path))

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Group YOLOv5 FP/FN failures by likely cause and write analysis artifacts.")
    parser.add_argument("eval_json", help="Evaluation JSON from metin2_research.detector")
    parser.add_argument("--json-out", help="Failure analysis JSON output")
    parser.add_argument("--markdown-out", help="Failure analysis Markdown output")
    parser.add_argument("--contact-sheet-dir", help="Directory for per-category contact sheets")
    parser.add_argument("--max-per-category", type=int, default=12)
    args = parser.parse_args(argv)

    report = json.loads(Path(args.eval_json).read_text(encoding="utf-8"))
    summary = summarize_failures(report, markdown_out=args.markdown_out)

    if args.contact_sheet_dir:
        summary["contact_sheets"] = create_contact_sheets(
            summary,
            args.contact_sheet_dir,
            max_per_category=args.max_per_category,
        )

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
