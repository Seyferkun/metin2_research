#!/usr/bin/env python
"""Observation-first boss farm tracker for the Metin2 control panel.

This script intentionally sends no keys/clicks. It records state freshness and a
simple spawn-cycle ledger while Yoshy teaches/records the actual boss farm loop.
Later automation can use these artifacts as training/evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_STATE_JSON = Path(r"D:/Games/MT2Portugalia/app/hermes_state.json")


def read_state(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"available": False, "_file_mtime": None, "error": "missing_state_json"}
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            data = {"raw": data}
    except Exception as exc:
        data = {"available": False, "error": f"json_error: {exc}"}
    try:
        data["_file_mtime"] = p.stat().st_mtime
    except Exception:
        data["_file_mtime"] = None
    data.setdefault("available", True)
    return data


def state_age_seconds(state: dict[str, Any]) -> float | None:
    mtime = state.get("_file_mtime")
    try:
        return round(time.time() - float(mtime), 3) if mtime else None
    except (TypeError, ValueError):
        return None


def inventory_item_count(state: dict[str, Any], *, loot_name: str = "", loot_vnum: int | None = None) -> int | None:
    name_lc = str(loot_name or "").casefold()
    for item in state.get("inventory") or []:
        if not isinstance(item, dict):
            continue
        vnum_match = loot_vnum is not None and item.get("vnum") == loot_vnum
        name_match = bool(name_lc and name_lc in str(item.get("name") or "").casefold())
        if vnum_match or name_match:
            try:
                return int(item.get("count", 0))
            except (TypeError, ValueError):
                return None
    return None


def compact_state(state: dict[str, Any]) -> dict[str, Any]:
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    target = state.get("target") if isinstance(state.get("target"), dict) else None
    return {
        "available": state.get("available", True),
        "state_age_seconds": state_age_seconds(state),
        "map": state.get("map"),
        "player": {
            "name": player.get("name"),
            "x": player.get("x"),
            "y": player.get("y"),
            "hp": player.get("hp"),
            "max_hp": player.get("max_hp"),
            "mounted": player.get("mounted"),
        },
        "target": target,
    }


def summarize_events(events_path: str | Path, *, spawn_interval_minutes: float, channels: int) -> dict[str, Any]:
    path = Path(events_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    samples = [row for row in rows if row.get("type") == "sample"]
    stale_samples = sum(1 for row in samples if (row.get("state") or {}).get("state_age_seconds") not in (None, "") and float((row.get("state") or {}).get("state_age_seconds")) > 2.0)
    boss_target_samples = [row for row in samples if row.get("boss_target_match")]
    loot_increments = []
    last_loot_count = None
    for row in samples:
        count = row.get("loot_count")
        if count is None:
            continue
        count = int(count)
        if last_loot_count is not None and count > last_loot_count:
            loot_increments.append({"t": row.get("t"), "from": last_loot_count, "to": count, "delta": count - last_loot_count})
        last_loot_count = count
    session_seconds = float(samples[-1].get("t", 0.0)) if samples else 0.0
    boss_kills = sum(int(row["delta"]) for row in loot_increments)
    return {
        "events_path": str(path),
        "samples": len(samples),
        "session_seconds": round(session_seconds, 3),
        "spawn_interval_minutes": spawn_interval_minutes,
        "channels": channels,
        "expected_spawn_windows_elapsed": int(session_seconds // max(1.0, spawn_interval_minutes * 60.0)),
        "boss_target_samples": len(boss_target_samples),
        "boss_kills_confirmed": boss_kills,
        "channels_cleared": boss_kills,
        "loot_pickups_observed": boss_kills,
        "loot_increments": loot_increments,
        "stale_state_samples": stale_samples,
        "notes": [
            "Observation/tracking scaffold only: sends no keys/clicks and does not yet farm automatically.",
            "Boss kills are counted from positive tracked loot-count deltas when loot_count is available.",
            "Use player-training recordings to teach the boss select/kill/loot/channel/menu-wait loop before enabling any live farm actions.",
        ],
    }


def should_stop(stop_file: Path | None, deadline: float) -> bool:
    return bool(stop_file and stop_file.exists()) or time.time() >= deadline


def run_tracker(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or os.environ.get("HERMES_RUN_ID") or time.strftime("boss-farm-%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    stop_file = Path(args.stop_file or os.environ.get("HERMES_STOP_FILE", "")) if (args.stop_file or os.environ.get("HERMES_STOP_FILE")) else None
    spawn_interval_minutes = max(1.0, float(args.spawn_interval_minutes))
    channels = max(1, int(args.channels))
    start = time.time()
    deadline = start + max(1.0, float(args.duration))
    next_spawn_eta = start + spawn_interval_minutes * 60.0
    sample_count = 0
    boss_name_lc = str(args.boss_name or "").casefold()
    loot_vnum = int(args.loot_vnum) if str(args.loot_vnum or "").strip() else None
    loot_name = str(args.loot_name or "")
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "start",
            "t": 0.0,
            "run_id": run_id,
            "boss_name": args.boss_name,
            "spawn_interval_minutes": spawn_interval_minutes,
            "channels": channels,
            "wait_menu": args.wait_menu,
            "loot_name": loot_name,
            "loot_vnum": loot_vnum,
            "state_json": str(args.state_json),
            "observation_only": True,
        }, ensure_ascii=False) + "\n")
        while not should_stop(stop_file, deadline):
            now = time.time()
            raw_state = read_state(args.state_json)
            state = compact_state(raw_state)
            target = state.get("target") if isinstance(state.get("target"), dict) else None
            target_name = str((target or {}).get("name") or "")
            boss_match = bool(boss_name_lc and boss_name_lc in target_name.casefold())
            loot_count = inventory_item_count(raw_state, loot_name=loot_name, loot_vnum=loot_vnum)
            fh.write(json.dumps({
                "type": "sample",
                "t": round(now - start, 3),
                "state": state,
                "loot_count": loot_count,
                "boss_target_match": boss_match,
                "next_spawn_eta_seconds": round(max(0.0, next_spawn_eta - now), 3),
                "phase": "WAITING_ALTERAR_PERSONAGEM_MENU" if args.wait_menu else "OBSERVING",
            }, ensure_ascii=False) + "\n")
            fh.flush()
            sample_count += 1
            time.sleep(max(0.05, float(args.interval)))
        fh.write(json.dumps({"type": "stop", "t": round(time.time() - start, 3), "samples": sample_count, "reason": "stop_file" if stop_file and stop_file.exists() else "duration"}) + "\n")
    summary = summarize_events(events_path, spawn_interval_minutes=spawn_interval_minutes, channels=channels)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"run_id": run_id, "out_dir": str(out_dir), "summary_path": str(summary_path), **summary}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Observation-first boss farm tracker; sends no keys/clicks.")
    ap.add_argument("--duration", type=float, default=3600.0)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--state-json", default=str(DEFAULT_STATE_JSON))
    ap.add_argument("--boss-name", default="")
    ap.add_argument("--spawn-interval-minutes", type=float, default=30.0)
    ap.add_argument("--channels", type=int, default=8)
    ap.add_argument("--wait-menu", default="alterar personagem")
    ap.add_argument("--loot-name", default="Cofre do Chefe Orc")
    ap.add_argument("--loot-vnum", default="50070")
    ap.add_argument("--out-dir", default="reports/boss_farm_runs")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--stop-file", default=None)
    args = ap.parse_args(argv)
    print(json.dumps(run_tracker(args), indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
