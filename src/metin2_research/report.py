from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .dataset import scan_yolo_dataset
from .policy import decide_next_action


def build_report(repo_root: str | Path) -> dict[str, Any]:
    repo = Path(repo_root)
    candidate_datasets = [
        repo / "metin_farm_bot" / "classifier" / "ervelia" / "metin120" / "images",
        repo / "metin_farm_bot" / "classifier" / "masno" / "dang25" / "pos",
        repo / "metin_farm_bot" / "classifier" / "masno" / "metin45" / "pos",
    ]
    datasets = {}
    for dataset in candidate_datasets:
        if dataset.exists():
            datasets[str(dataset.relative_to(repo))] = scan_yolo_dataset(dataset)

    sample_decisions = {
        "low_hp": decide_next_action({"hp_percent": 25, "target_visible": True, "target_confirmed": True, "target_confidence": 0.9}),
        "confirmed_target": decide_next_action({"hp_percent": 90, "target_visible": True, "target_confirmed": True, "target_confidence": 0.82}),
        "no_target": decide_next_action({"hp_percent": 90, "target_visible": False, "no_target_seconds": 8}),
    }

    return {
        "repo_root": str(repo),
        "datasets": datasets,
        "sample_policy_decisions": sample_decisions,
        "safety_note": "This harness is read-only and outputs advisory symbolic actions only; it does not inject game input.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a safe Metin2 research dataset/policy report.")
    parser.add_argument("repo_root", help="Path to cloned metin2bot repository")
    parser.add_argument("--out", help="Optional JSON output path")
    args = parser.parse_args(argv)

    report = build_report(args.repo_root)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
