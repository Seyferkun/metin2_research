from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_STATE_JSON = Path(r"D:/Games/MT2Portugalia/app/hermes_state.json")


def _clean_attr(attr: dict[str, Any]) -> dict[str, int]:
    return {
        "index": int(attr.get("index", 0)),
        "type": int(attr.get("type", 0)),
        "value": int(attr.get("value", 0)),
    }


def clean_item(item: dict[str, Any]) -> dict[str, Any]:
    attrs = []
    for attr in item.get("attrs") or []:
        if isinstance(attr, dict) and attr.get("type") is not None:
            attrs.append(_clean_attr(attr))
    sockets = item.get("sockets") if isinstance(item.get("sockets"), list) else []
    return {
        "slot": item.get("slot"),
        "vnum": item.get("vnum"),
        "count": item.get("count"),
        "name": item.get("name"),
        "attrs": attrs[:7],
        "sockets": sockets[:6],
    }


def attrs_signature(item: dict[str, Any] | None) -> tuple[tuple[int, int, int], ...]:
    if not isinstance(item, dict):
        return tuple()
    attrs = []
    for attr in item.get("attrs") or []:
        if isinstance(attr, dict) and attr.get("type") is not None:
            attrs.append((int(attr.get("index", 0)), int(attr.get("type", 0)), int(attr.get("value", 0))))
    return tuple(sorted(attrs))


def read_raw_state(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            data["_file_mtime"] = Path(path).stat().st_mtime
            return data
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__}
    return {"available": False}


def item_candidates_from_state(state: dict[str, Any], target_slot: int | None = None, target_vnum: int | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in state.get("inventory") or []:
        if isinstance(item, dict) and item.get("attrs"):
            candidates.append(clean_item(item))
    equipped_weapon = state.get("equipped_weapon")
    if isinstance(equipped_weapon, dict) and equipped_weapon.get("attrs"):
        candidates.append(clean_item(equipped_weapon))

    def score(item: dict[str, Any]) -> tuple[int, int]:
        slot_match = target_slot is not None and str(item.get("slot")) == str(target_slot)
        vnum_match = target_vnum is not None and str(item.get("vnum")) == str(target_vnum)
        attrs_len = len(item.get("attrs") or [])
        return (int(slot_match) * 10 + int(vnum_match) * 5 + min(attrs_len, 4), attrs_len)

    filtered = []
    for item in candidates:
        if target_slot is not None and str(item.get("slot")) != str(target_slot):
            continue
        if target_vnum is not None and str(item.get("vnum")) != str(target_vnum):
            continue
        filtered.append(item)
    chosen = filtered or candidates
    return sorted(chosen, key=score, reverse=True)


def build_reroll_summary(events_path: str | Path) -> dict[str, Any]:
    path = Path(events_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    samples = [row for row in rows if row.get("type") == "sample"]
    observed_rolls: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, str, tuple[tuple[int, int, int], ...]]] = set()
    values_by_attr: dict[str, set[int]] = {}
    for row in samples:
        row_items = row.get("items") if isinstance(row.get("items"), list) else None
        if row_items is None:
            item = row.get("item") if isinstance(row.get("item"), dict) else None
            row_items = [item] if item else []
        for item in row_items:
            if not isinstance(item, dict):
                continue
            sig = attrs_signature(item)
            identity_sig = (item.get("slot"), item.get("vnum"), str(item.get("name") or ""), sig)
            if not sig or identity_sig in seen:
                continue
            seen.add(identity_sig)
            roll = {
                "roll_id": len(observed_rolls) + 1,
                "t": row.get("t"),
                "slot": item.get("slot"),
                "vnum": item.get("vnum"),
                "name": item.get("name"),
                "attrs": item.get("attrs") or [],
            }
            observed_rolls.append(roll)
            for attr in item.get("attrs") or []:
                key = str(int(attr.get("type", 0)))
                values_by_attr.setdefault(key, set()).add(int(attr.get("value", 0)))
    observed_values_by_attr = {key: sorted(values) for key, values in sorted(values_by_attr.items(), key=lambda pair: int(pair[0]))}
    state_ages = [float(row.get("state_age_seconds")) for row in samples if row.get("state_age_seconds") is not None]
    stale_state_samples = sum(1 for age in state_ages if age > 2.0)
    max_state_age_seconds = round(max(state_ages), 3) if state_ages else None
    observed_bounds_by_attr = {
        key: {"min": min(values), "max": max(values), "count": len(values)}
        for key, values in observed_values_by_attr.items()
        if values
    }
    notes = [
        "Observation-only reroll recorder: sends no keys/clicks.",
        "Bounds are observed from this recording only; they are not claimed server-wide limits.",
    ]
    if stale_state_samples:
        notes.append(f"WARNING: {stale_state_samples}/{len(samples)} samples used stale state (>2s old); reroll changes may be missed until the client logger updates the state file.")
    return {
        "events_path": str(path),
        "samples": len(samples),
        "stale_state_samples": stale_state_samples,
        "max_state_age_seconds": max_state_age_seconds,
        "roll_change_count": len(observed_rolls),
        "observed_rolls": observed_rolls,
        "observed_values_by_attr": observed_values_by_attr,
        "observed_bounds_by_attr": observed_bounds_by_attr,
        "notes": notes,
    }


def write_observed_rolls_csv(summary: dict[str, Any], path: str | Path) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["roll_id", "t", "slot", "vnum", "name", "attr_index", "attr_type", "attr_value"])
        writer.writeheader()
        for roll in summary.get("observed_rolls") or []:
            for attr in roll.get("attrs") or []:
                writer.writerow({
                    "roll_id": roll.get("roll_id"),
                    "t": roll.get("t"),
                    "slot": roll.get("slot"),
                    "vnum": roll.get("vnum"),
                    "name": roll.get("name"),
                    "attr_index": attr.get("index"),
                    "attr_type": attr.get("type"),
                    "attr_value": attr.get("value"),
                })


def should_stop(stop_file: Path | None, deadline: float) -> bool:
    return bool(stop_file and stop_file.exists()) or time.time() >= deadline


def record_rerolls(args) -> dict[str, Any]:
    run_id = args.run_id or os.environ.get("HERMES_RUN_ID") or time.strftime("reroll-%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    stop_file = Path(args.stop_file or os.environ.get("HERMES_STOP_FILE", "")) if (args.stop_file or os.environ.get("HERMES_STOP_FILE")) else None
    target_slot = int(args.target_slot) if args.target_slot not in (None, "") else None
    target_vnum = int(args.target_vnum) if args.target_vnum not in (None, "") else None
    start = time.time()
    deadline = start + max(0.0, float(args.duration))
    previous_sigs: dict[tuple[Any, Any, str], tuple[tuple[int, int, int], ...]] = {}
    sample_count = 0
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "start", "t": 0.0, "run_id": run_id, "state_json": str(args.state_json), "target_slot": target_slot, "target_vnum": target_vnum, "observation_only": True}) + "\n")
        while not should_stop(stop_file, deadline):
            now = round(time.time() - start, 3)
            state = read_raw_state(args.state_json)
            state_mtime = state.get("_file_mtime")
            try:
                state_age_seconds = round(time.time() - float(state_mtime), 3) if state_mtime else None
            except (TypeError, ValueError):
                state_age_seconds = None
            candidates = item_candidates_from_state(state, target_slot=target_slot, target_vnum=target_vnum)
            changed_items = []
            for item in candidates:
                sig = attrs_signature(item)
                identity = (item.get("slot"), item.get("vnum"), str(item.get("name") or ""))
                if sig and sig != previous_sigs.get(identity):
                    changed_items.append(item)
                    previous_sigs[identity] = sig
            primary = candidates[0] if candidates else None
            fh.write(json.dumps({
                "type": "sample",
                "t": now,
                "item": primary,
                "items": candidates,
                "changed_items": changed_items,
                "attrs_signature": attrs_signature(primary),
                "changed": bool(changed_items),
                "candidate_count": len(candidates),
                "state_mtime": state_mtime,
                "state_age_seconds": state_age_seconds,
                "state_available": state.get("available", True),
            }, ensure_ascii=False) + "\n")
            fh.flush()
            sample_count += 1
            time.sleep(max(0.01, float(args.interval)))
        fh.write(json.dumps({"type": "stop", "t": round(time.time() - start, 3), "samples": sample_count, "reason": "stop_file" if stop_file and stop_file.exists() else "duration"}) + "\n")
    summary = build_reroll_summary(events_path)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    csv_path = out_dir / "observed_rolls.csv"
    write_observed_rolls_csv(summary, csv_path)
    return {"run_id": run_id, "out_dir": str(out_dir), "events_path": str(events_path), "summary_path": str(summary_path), "csv_path": str(csv_path), **summary}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Observation-only Metin2 item reroll recorder; sends no keys/clicks.")
    ap.add_argument("--duration", type=float, default=180.0)
    ap.add_argument("--interval", type=float, default=0.10)
    ap.add_argument("--state-json", default=str(DEFAULT_STATE_JSON))
    ap.add_argument("--target-slot", default=None, help="inventory/equipment slot to track, optional")
    ap.add_argument("--target-vnum", default=None, help="item vnum to track, optional")
    ap.add_argument("--out-dir", default="reports/reroll_recordings")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--stop-file", default=None)
    args = ap.parse_args(argv)
    result = record_rerolls(args)
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
