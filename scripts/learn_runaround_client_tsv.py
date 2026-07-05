#!/usr/bin/env python
"""Learn Metin2 movement from client Python TSV state, not vision.

Private Yoshypt sandbox only. This script uses the local read-only
D:/Games/MT2Portugalia/app/hermes_state.tsv feedback path as the source of
truth. It does not detect Metins, click targets, scan packets, or inspect server
state. It presses bounded WASD key holds and records the coordinate deltas the
client reports.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for _path in (PROJECT_ROOT, SRC_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from metin2_research.client_state.navigation import movement_delta, summarize_movement_observations
from metin2_research.client_state.tsv_state import DEFAULT_TSV_PATH, TsvClientStateSource
from metin2_research.win_input import hold_key, tap_key
from metin2_research.window_capture import activate_window, find_window

MOVEMENT_KEYS = ("w", "a", "s", "d")


def read_state(path: Path, *, max_age_seconds: float = 3.0):
    state = TsvClientStateSource(path, max_age_seconds=max_age_seconds).read()
    if state.game is None or state.game.player_coord is None:
        warnings = "; ".join(state.warnings) if state.warnings else "no client game state"
        raise RuntimeError(f"Cannot read fresh client TSV coordinates from {path}: {warnings}")
    return state


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def maybe_use_potion(state, *, threshold: float | None) -> bool:
    if threshold is None or state.game is None or not state.game.max_hp:
        return False
    hp_ratio = (state.game.hp or 0) / state.game.max_hp
    if hp_ratio <= threshold:
        tap_key("1")
        return True
    return False


def calibrate_once(
    *,
    key: str,
    seconds: float,
    pause: float,
    tsv_path: Path,
    max_age_seconds: float,
    potion_hp_ratio: float | None,
) -> dict[str, Any]:
    before = read_state(tsv_path, max_age_seconds=max_age_seconds)
    used_potion = maybe_use_potion(before, threshold=potion_hp_ratio)
    hold_key(key, seconds)
    time.sleep(pause)
    after = read_state(tsv_path, max_age_seconds=max_age_seconds)
    observation = movement_delta(before, after, key=key, seconds=seconds)
    observation["used_potion_before_move"] = used_potion
    observation["warnings"] = [*before.warnings, *after.warnings]
    return observation


def main() -> int:
    parser = argparse.ArgumentParser(description="Learn/run around using client TSV coordinate feedback, not vision.")
    parser.add_argument("--window", default="MT2Portugalia")
    parser.add_argument("--tsv-state", default=str(DEFAULT_TSV_PATH))
    parser.add_argument("--seconds", type=float, default=0.8, help="Hold duration for each WASD probe.")
    parser.add_argument("--pause", type=float, default=0.7, help="Wait after each key so TSV catches up.")
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--keys", default="w,a,s,d", help="Comma-separated movement keys to probe.")
    parser.add_argument("--out-jsonl", default="reports/client_tsv_runaround/movement_observations.jsonl")
    parser.add_argument("--model-out", default="reports/client_tsv_runaround/live_navigation_model.json")
    parser.add_argument("--max-age-seconds", type=float, default=3.0)
    parser.add_argument("--potion-hp-ratio", type=float, default=0.50, help="Tap quickslot 1 before a move if HP/max HP is at or below this ratio; set negative to disable.")
    parser.add_argument("--no-focus", action="store_true", help="Do not activate the game window before key holds.")
    args = parser.parse_args()

    tsv_path = Path(args.tsv_state)
    out_jsonl = Path(args.out_jsonl)
    model_out = Path(args.model_out)
    keys = [k.strip().lower() for k in args.keys.split(",") if k.strip()]
    bad = [k for k in keys if k not in MOVEMENT_KEYS]
    if bad:
        raise SystemExit(f"unsupported movement key(s): {bad}")
    if out_jsonl.exists():
        out_jsonl.unlink()

    if not args.no_focus:
        activate_window(find_window(args.window))
        time.sleep(0.2)

    potion_threshold = args.potion_hp_ratio if args.potion_hp_ratio >= 0 else None
    observations: list[dict[str, Any]] = []
    start = read_state(tsv_path, max_age_seconds=args.max_age_seconds)
    print(json.dumps({"event": "start", "state": start.to_dict()}, ensure_ascii=False), flush=True)

    for cycle in range(1, args.cycles + 1):
        for key in keys:
            observation = calibrate_once(
                key=key,
                seconds=args.seconds,
                pause=args.pause,
                tsv_path=tsv_path,
                max_age_seconds=args.max_age_seconds,
                potion_hp_ratio=potion_threshold,
            )
            observation["cycle"] = cycle
            observations.append(observation)
            append_jsonl(out_jsonl, observation)
            print(json.dumps(observation, ensure_ascii=False), flush=True)

    model = {
        "version": 1,
        "source": "client_python_tsv",
        "tsv_state": str(tsv_path),
        "created_at_unix": time.time(),
        "cycles": args.cycles,
        "hold_seconds": args.seconds,
        "observations": len(observations),
        "navigation_model": summarize_movement_observations(observations),
    }
    model_out.parent.mkdir(parents=True, exist_ok=True)
    model_out.write_text(json.dumps(model, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"event": "saved", "jsonl": str(out_jsonl), "model": str(model_out), "navigation_model": model["navigation_model"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
