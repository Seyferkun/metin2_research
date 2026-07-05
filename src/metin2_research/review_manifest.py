from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path
from typing import Any

from .failure_analysis import categorize_failure, is_failure

SUGGESTED_ACTIONS = {
    "localization_below_iou_threshold": "review_box_alignment; maybe_relabel_box; maybe_lower_iou_threshold",
    "duplicate_or_extra_detection": "add_hard_negative; inspect_extra_detection",
    "missed_detection": "add_small_object_augmentation; oversample_image",
    "wrong_location_or_label_mismatch": "verify_annotation; fix_or_remove_bad_label",
    "low_confidence_or_filtered_detection": "review_confidence_threshold; oversample_image",
    "mixed_duplicate_and_miss": "inspect_extra_detection; verify_annotation",
}

FIELDNAMES = [
    "id",
    "image",
    "annotation",
    "preview",
    "category",
    "best_iou",
    "false_positives",
    "false_negatives",
    "suggested_action",
    "status",
    "notes",
]


def suggested_action_for_category(category: str) -> str:
    return SUGGESTED_ACTIONS.get(category, "manual_review")


def build_review_manifest(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Build CSV/JSON-friendly rows for human review from detector evaluation output."""
    rows: list[dict[str, Any]] = []
    for image_result in report.get("images", []):
        if not is_failure(image_result):
            continue
        category = categorize_failure(image_result)
        rows.append(
            {
                "id": f"{len(rows) + 1:04d}",
                "image": str(image_result.get("image", "")),
                "annotation": str(image_result.get("annotation", "")),
                "preview": str(image_result.get("failure_preview", "")),
                "category": category,
                "best_iou": image_result.get("best_iou", 0.0),
                "false_positives": int(image_result.get("false_positives", 0)),
                "false_negatives": int(image_result.get("false_negatives", 0)),
                "suggested_action": suggested_action_for_category(category),
                "status": "pending",
                "notes": "",
            }
        )
    return rows


def _write_json(rows: list[dict[str, Any]], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _write_csv(rows: list[dict[str, Any]], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDNAMES})
    return out


def _copy_review_previews(rows: list[dict[str, Any]], review_dir: str | Path) -> Path:
    root = Path(review_dir)
    root.mkdir(parents=True, exist_ok=True)
    for row in rows:
        preview = Path(str(row.get("preview", "")))
        if not preview.exists():
            continue
        category_dir = root / str(row["category"])
        category_dir.mkdir(parents=True, exist_ok=True)
        suffix = preview.suffix or ".jpg"
        destination = category_dir / f"{row['id']}_{preview.stem}{suffix}"
        shutil.copy2(preview, destination)
        row["review_copy"] = str(destination)
    return root


def write_review_manifest(
    rows: list[dict[str, Any]],
    *,
    json_out: str | Path | None = None,
    csv_out: str | Path | None = None,
    review_dir: str | Path | None = None,
) -> dict[str, str]:
    """Write manifest rows to JSON/CSV and optionally copy previews by category."""
    outputs: dict[str, str] = {}
    if review_dir is not None:
        outputs["review_dir"] = str(_copy_review_previews(rows, review_dir))
    if json_out is not None:
        outputs["json"] = str(_write_json(rows, json_out))
    if csv_out is not None:
        outputs["csv"] = str(_write_csv(rows, csv_out))
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create JSON/CSV human-review manifests and categorized review folders from detector failures.")
    parser.add_argument("eval_json", help="Evaluation JSON from metin2_research.detector")
    parser.add_argument("--json-out", required=True, help="Output review manifest JSON")
    parser.add_argument("--csv-out", required=True, help="Output review manifest CSV")
    parser.add_argument("--review-dir", required=True, help="Directory where failure previews are copied by category")
    args = parser.parse_args(argv)

    report = json.loads(Path(args.eval_json).read_text(encoding="utf-8"))
    rows = build_review_manifest(report)
    outputs = write_review_manifest(rows, json_out=args.json_out, csv_out=args.csv_out, review_dir=args.review_dir)
    result = {"row_count": len(rows), "outputs": outputs}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
