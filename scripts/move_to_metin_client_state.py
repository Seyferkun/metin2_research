#!/usr/bin/env python
"""Movement-only Metin approach loop driven by client JSON state.

Private Yoshypt sandbox only. Dry-run is default. Live mode only sends bounded WASD
movement toward a trusted coordinate; it does not attack, click, or press skills.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for _path in (PROJECT_ROOT, SRC_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from metin2_research.client_state.json_state import DEFAULT_JSON_PATH, JsonClientStateSource
from metin2_research.client_state.navigation import OnlineNavModel
from metin2_research.win_input import SmoothMover, key_up
from metin2_research.window_capture import activate_window, find_window

TRUSTED_MOVE_COORD_SOURCES = {"live_memory_visible_text", "named_metin_probe"}
NAV_CYCLE_SLEEP = 0.05


def normalize_metin_coord(x: int | None, y: int | None) -> list[int] | None:
    if x is None or y is None:
        return None
    ix = int(x)
    iy = int(y)
    if abs(ix) < 5000 and abs(iy) < 5000:
        return [ix * 100, iy * 100]
    return [ix, iy]


def load_navigation_model(path: Path | None) -> dict:
    if path is None or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    model = data.get("navigation_model") if isinstance(data, dict) else None
    return model if isinstance(model, dict) else {}


def read_game(json_path: Path, max_age_seconds: float):
    state = JsonClientStateSource(json_path, max_age_seconds=max_age_seconds).read()
    if state.game and state.game.player_coord and not state.warnings:
        return state.game
    raise RuntimeError(f"No fresh JSON client game state: {state.warnings}")


def should_stop(stop_file: Path | None) -> bool:
    return bool(stop_file and Path(stop_file).exists())


def release_all_keys() -> None:
    for key in ("space", "w", "a", "s", "d"):
        try:
            key_up(key)
        except Exception:
            pass


def distance_xy(current: list[int] | tuple[int, ...], target: list[int] | tuple[int, ...]) -> float:
    return math.hypot(float(target[0]) - float(current[0]), float(target[1]) - float(current[1]))


class DistanceTracker:
    def __init__(self, window: int = 6):
        self.history: list[float] = []
        self.window = int(window)

    def update(self, distance: float | int | None) -> None:
        if distance is None:
            return
        self.history.append(float(distance))
        if len(self.history) > self.window:
            self.history.pop(0)

    def is_stuck(self, threshold: float = 80.0) -> bool:
        if len(self.history) < self.window:
            return False
        return (self.history[0] - self.history[-1]) < float(threshold)

    def is_diverging(self) -> bool:
        if len(self.history) < 3:
            return False
        return self.history[-1] > self.history[-2] > self.history[-3]

    def trend(self) -> float:
        if len(self.history) < 2:
            return 0.0
        return self.history[-1] - self.history[0]

    def best(self) -> float | None:
        return min(self.history) if self.history else None


def serialize_nav_steps(steps) -> list[list]:
    serialized = []
    for keys, hold in steps or []:
        key_set = {str(key) for key in keys} if not isinstance(keys, str) else {keys}
        key_value = sorted(key_set)[0] if len(key_set) == 1 else sorted(key_set)
        serialized.append([key_value, float(hold)])
    return serialized


def emit(out: Path, event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def format_structured_line(*, state: str, action: str, current: list[int] | tuple[int, ...] | None, target: list[int], distance: float | None, steps) -> str:
    pos = f"{int(current[0])},{int(current[1])}" if current and len(current) >= 2 else "?,?"
    step_text = ";".join(
        ("+".join(sorted({str(k) for k in keys})) if not isinstance(keys, str) else keys) + f":{float(hold):.3f}"
        for keys, hold in (steps or [])
    )
    return f"[state={state}] [action={action}] [pos={pos}] [target={int(target[0])},{int(target[1])}] [dist={round(float(distance), 1) if distance is not None else '?'}] [steps={step_text}]"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Move directly toward a trusted Metin coordinate. Dry-run by default.")
    ap.add_argument("--json-state", type=Path, default=DEFAULT_JSON_PATH)
    ap.add_argument("--metin-name", default="Metin da Batalha")
    ap.add_argument("--metin-x", type=int, required=True)
    ap.add_argument("--metin-y", type=int, required=True)
    ap.add_argument("--metin-coord-source", default=None)
    ap.add_argument("--max-cycles", type=int, default=90)
    ap.add_argument("--arrival-radius", type=float, default=260.0)
    ap.add_argument("--move-step-seconds", type=float, default=0.45)
    ap.add_argument("--navigation-model", type=Path, default=Path("reports/client_tsv_runaround/live_navigation_model.json"))
    ap.add_argument("--max-state-age-seconds", type=float, default=2.0)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("reports/dashboard_runs/move_to_metin.jsonl"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--stop-file", type=Path, default=None)
    args = ap.parse_args(argv)

    run_id = args.run_id or os.environ.get("HERMES_RUN_ID")
    stop_file = args.stop_file or (Path(os.environ["HERMES_STOP_FILE"]) if os.environ.get("HERMES_STOP_FILE") else None)
    if stop_file is None and run_id:
        stop_file = Path("reports/dashboard_runs") / f"{run_id}.stop"
    args.out.write_text("", encoding="utf-8")

    target = normalize_metin_coord(args.metin_x, args.metin_y)
    if target is None:
        emit(args.out, {"state": "ABORT", "reason": "missing target coordinate", "run_id": run_id})
        return 2
    if args.live and args.metin_coord_source not in TRUSTED_MOVE_COORD_SOURCES:
        emit(
            args.out,
            {
                "state": "ABORT",
                "reason": f"untrusted coordinate source for live movement: {args.metin_coord_source}",
                "trusted_sources": sorted(TRUSTED_MOVE_COORD_SOURCES),
                "run_id": run_id,
            },
        )
        return 2

    nav_model = load_navigation_model(args.navigation_model)
    if not nav_model:
        emit(args.out, {"state": "ABORT", "reason": f"navigation model missing or empty: {args.navigation_model}", "run_id": run_id})
        return 2
    online_model = OnlineNavModel(nav_model)
    tracker = DistanceTracker(window=6)
    mover = SmoothMover()

    if args.live:
        activate_window(find_window("MT2Portugalia"))
        time.sleep(0.2)

    try:
        for cycle in range(1, args.max_cycles + 1):
            if should_stop(stop_file):
                release_all_keys()
                emit(args.out, {"cycle": cycle, "state": "STOP_REQUESTED", "command": "release_all_keys", "run_id": run_id})
                print("[state=STOP_REQUESTED] [action=release_all_keys] [pos=?,?] [target=%d,%d] [dist=?] [steps=]" % (target[0], target[1]), flush=True)
                return 0
            try:
                game = read_game(args.json_state, args.max_state_age_seconds)
            except RuntimeError as exc:
                emit(args.out, {"cycle": cycle, "state": "WAIT_FRESH_STATE", "command": "pause", "reason": str(exc), "run_id": run_id})
                time.sleep(0.5)
                continue
            current = game.player_coord[:2]
            dist = distance_xy(current, target)
            tracker.update(dist)
            if dist <= args.arrival_radius:
                mover.release_all()
                event = {
                    "cycle": cycle,
                    "dry_run": not args.live,
                    "state": "ARRIVED",
                    "command": "release_all_keys",
                    "metin_name": args.metin_name,
                    "current": current,
                    "target": target,
                    "distance": round(dist, 1),
                    "arrival_radius": args.arrival_radius,
                    "run_id": run_id,
                }
                emit(args.out, event)
                print(format_structured_line(state="ARRIVED", action="release_all_keys", current=current, target=target, distance=dist, steps=[]), flush=True)
                return 0
            prefer_secondary = tracker.is_diverging()
            use_waypoint = tracker.is_stuck() or prefer_secondary
            steps = online_model.choose_steps_with_waypoint(
                (int(current[0]), int(current[1])),
                (int(target[0]), int(target[1])),
                use_waypoint=use_waypoint,
                max_hold=min(args.move_step_seconds, 0.45),
                prefer_secondary=prefer_secondary,
            )
            event = {
                "cycle": cycle,
                "dry_run": not args.live,
                "state": "MOVE_TO_METIN",
                "command": "smooth_move",
                "metin_name": args.metin_name,
                "coord_source": args.metin_coord_source,
                "current": current,
                "target": target,
                "distance": round(dist, 1),
                "distance_trend": round(tracker.trend(), 1),
                "best_distance": round(tracker.best(), 1) if tracker.best() is not None else None,
                "distance_stuck": tracker.is_stuck(),
                "distance_diverging": tracker.is_diverging(),
                "use_waypoint": use_waypoint,
                "nav_steps": serialize_nav_steps(steps),
                "nav_model_summary": online_model.summary(),
                "run_id": run_id,
            }
            emit(args.out, event)
            print(format_structured_line(state="MOVE_TO_METIN", action="smooth_move", current=current, target=target, distance=dist, steps=steps), flush=True)
            if args.live:
                before = (int(current[0]), int(current[1]))
                for keys, hold in steps:
                    mover.move({str(key) for key in keys}, float(hold))
                try:
                    after_game = read_game(args.json_state, args.max_state_age_seconds)
                    after = (int(after_game.player_coord[0]), int(after_game.player_coord[1]))
                    if steps:
                        primary = sorted({str(k) for k in steps[0][0]})[0]
                        online_model.record_step(primary, before, after)
                except RuntimeError:
                    pass
            time.sleep(NAV_CYCLE_SLEEP)
        mover.release_all()
        emit(args.out, {"state": "MAX_CYCLES", "command": "release_all_keys", "target": target, "run_id": run_id})
        return 1 if args.live else 0
    finally:
        mover.release_all()


if __name__ == "__main__":
    raise SystemExit(main())
