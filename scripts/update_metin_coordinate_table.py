#!/usr/bin/env python
"""Build/update a persistent table of Metin coordinates found in memory/reports.

The table stores in-game Metin stone coordinates and the map they were seen on.
When a map is not known from evidence, it is kept as `unknown_current_map`
instead of guessing.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

METIN_RE = re.compile(r"(?P<name>Metin\s+(?:da|do|de|dos|das)?\s*[A-Za-zÀ-ÿ0-9_ '\-]+)\(\s*(?P<x>\d{1,4})\s*,\s*(?P<y>\d{1,4})\s*\)", re.IGNORECASE)

DEFAULT_COLUMNS = [
    "id",
    "map_name",
    "metin_name",
    "x",
    "y",
    "source_type",
    "source_path",
    "evidence",
    "confidence",
    "notes",
]

@dataclass(frozen=True)
class MetinCoord:
    id: str
    map_name: str
    metin_name: str
    x: int
    y: int
    source_type: str
    source_path: str
    evidence: str
    confidence: float
    notes: str = ""


def sanitize_name(name: str) -> str:
    name = " ".join(name.strip().split())
    return name.rstrip(" ,.;:")


def coord_id(map_name: str, name: str, x: int, y: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", f"{map_name}_{name}_{x}_{y}".lower()).strip("_")
    return slug


def infer_map_from_path_or_text(path: Path, text: str) -> str:
    # Do not overclaim map names. Current reports contain dungeon completion text,
    # but no reliable explicit current map label for the Metin coordinate itself.
    # Keep a stable placeholder until a map OCR/memory label is confirmed.
    return "unknown_current_map"


def iter_json_texts(path: Path) -> Iterable[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    texts: list[str] = []
    def walk(obj):
        if isinstance(obj, str):
            texts.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    walk(data)
    return texts


def extract_from_json(path: Path) -> list[MetinCoord]:
    rows: list[MetinCoord] = []
    for text in iter_json_texts(path):
        for match in METIN_RE.finditer(text):
            name = sanitize_name(match.group("name"))
            x = int(match.group("x")); y = int(match.group("y"))
            map_name = infer_map_from_path_or_text(path, text)
            evidence = text[max(0, match.start() - 80): match.end() + 80]
            rows.append(MetinCoord(
                id=coord_id(map_name, name, x, y),
                map_name=map_name,
                metin_name=name,
                x=x,
                y=y,
                source_type="memory_text_report",
                source_path=str(path),
                evidence=evidence,
                confidence=0.95,
                notes="Extracted from memory text/report; map label not yet confirmed.",
            ))
    return rows


def load_existing(csv_path: Path) -> dict[str, MetinCoord]:
    rows: dict[str, MetinCoord] = {}
    if not csv_path.exists():
        return rows
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                coord = MetinCoord(
                    id=row["id"],
                    map_name=row["map_name"],
                    metin_name=row["metin_name"],
                    x=int(row["x"]),
                    y=int(row["y"]),
                    source_type=row.get("source_type", ""),
                    source_path=row.get("source_path", ""),
                    evidence=row.get("evidence", ""),
                    confidence=float(row.get("confidence", 0) or 0),
                    notes=row.get("notes", ""),
                )
                rows[coord.id] = coord
            except Exception:
                continue
    return rows


def write_outputs(rows: dict[str, MetinCoord], csv_path: Path, json_path: Path) -> None:
    ordered = sorted(rows.values(), key=lambda r: (r.map_name, r.metin_name, r.x, r.y, r.id))
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DEFAULT_COLUMNS)
        writer.writeheader()
        for row in ordered:
            writer.writerow(asdict(row))
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps({"version": 1, "metin_coordinates": [asdict(r) for r in ordered]}, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--csv", default="data/metin_coordinates.csv")
    parser.add_argument("--json", default="data/metin_coordinates.json")
    parser.add_argument("--include", action="append", default=[], help="Extra JSON report path to scan")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    json_path = Path(args.json)
    rows = load_existing(csv_path)

    paths: list[Path] = []
    reports_dir = Path(args.reports_dir)
    if reports_dir.exists():
        # Keep scan bounded to JSON reports most likely to contain memory text coordinates.
        for pattern in ("nav_*.json", "coords*.json", "*metin*.json"):
            paths.extend(reports_dir.glob(pattern))
    paths.extend(Path(p) for p in args.include)

    found = 0
    for path in sorted(set(paths)):
        if not path.exists() or path.suffix.lower() != ".json":
            continue
        for coord in extract_from_json(path):
            same_coord_keys = [
                key
                for key, existing in rows.items()
                if existing.metin_name.lower() == coord.metin_name.lower()
                and existing.x == coord.x
                and existing.y == coord.y
            ]
            known_same_coord = next((rows[key] for key in same_coord_keys if rows[key].map_name != "unknown_current_map"), None)
            if known_same_coord is not None and coord.map_name == "unknown_current_map":
                # Preserve manually/externally confirmed map labels when rescanning
                # memory reports that do not carry a reliable map name. Do not add
                # a duplicate unknown_current_map row for the same Metin coordinate.
                found += 1
                continue

            # If this scan has a known map, remove older unknown rows for the same
            # Metin coordinate before writing the confirmed-map row.
            if coord.map_name != "unknown_current_map":
                for key in same_coord_keys:
                    if rows[key].map_name == "unknown_current_map":
                        rows.pop(key, None)

            old = rows.get(coord.id)
            if old is None or coord.confidence >= old.confidence:
                rows[coord.id] = coord
            found += 1

    write_outputs(rows, csv_path, json_path)
    print(f"found_mentions={found} unique_rows={len(rows)} csv={csv_path} json={json_path}")
    for row in sorted(rows.values(), key=lambda r: (r.map_name, r.metin_name, r.x, r.y)):
        print(f"{row.id}: map={row.map_name} {row.metin_name} ({row.x},{row.y}) confidence={row.confidence}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
