#!/usr/bin/env python
"""Stateful Metin combat loop driven by client TSV state.

Private Yoshypt sandbox only. The policy is intentionally conservative:
- HP/potion and buff checks run before every attack decision.
- Target switching to mobs is treated as spawned-add handling, not failure.
- Success requires Metin entity absence; reward text alone is not enough.
- Dry-run is the default. Pass --live to send bounded key presses.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import math
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for _path in (PROJECT_ROOT, SRC_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from metin2_research.client_state.combat import CombatAction, CombatConfig, CombatSnapshot, decide_combat_action
from metin2_research.client_state.json_state import DEFAULT_JSON_PATH, JsonClientStateSource
from metin2_research.client_state.navigation import OnlineNavModel, choose_key_for_direction, choose_movement_steps
from metin2_research.win_input import SmoothMover, hold_key, key_down, key_up, tap_key
from metin2_research.window_capture import activate_window, find_window

DEFAULT_LOG = Path("reports/client_tsv_runaround/combat_metin_state_machine.jsonl")
NAV_CYCLE_SLEEP = 0.05
ATTACK_CYCLE_SLEEP = 0.25


def read_game(_tsv_path: Path | None = None, *, json_path: Path | None = DEFAULT_JSON_PATH, max_age_seconds: float = 2):
    if json_path is None:
        raise RuntimeError("JSON client state is required for combat decisions")
    state = JsonClientStateSource(json_path, max_age_seconds=max_age_seconds).read()
    if state.game and state.game.player_coord and not state.warnings:
        return state.game
    raise RuntimeError(f"No fresh JSON client game state: {state.warnings}")


def should_stop(stop_file: Path | None) -> bool:
    return bool(stop_file and Path(stop_file).exists())


def normalize_metin_coord(x: int | None, y: int | None) -> list[int] | None:
    if x is None or y is None:
        return None
    ix = int(x)
    iy = int(y)
    # Operator-facing Nearby Metins coordinates are display/map coords (e.g.
    # 846,442), while hermes_state.json player coords are raw client units
    # (e.g. 84600,44200). Convert small map coords before distance checks.
    if abs(ix) < 5000 and abs(iy) < 5000:
        return [ix * 100, iy * 100]
    return [ix, iy]


def load_navigation_model(path: Path | None) -> dict:
    if path is None or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    model = data.get("navigation_model") if isinstance(data, dict) else None
    return model if isinstance(model, dict) else {}


TRUSTED_SESSION_START_SOURCE_TYPES = {"live_memory_visible_text"}


def choose_session_start_metin(result: dict | None) -> dict | None:
    """Choose the first trusted exact-coordinate Metin from a pre-combat nearby scan."""
    if not isinstance(result, dict):
        return None
    for row in result.get("metins") or []:
        if not isinstance(row, dict):
            continue
        source_type = str(row.get("source_type") or "")
        if source_type not in TRUSTED_SESSION_START_SOURCE_TYPES:
            continue
        location = row.get("location") if isinstance(row.get("location"), dict) else None
        if not location or location.get("x") is None or location.get("y") is None:
            continue
        return {
            "metin_name": row.get("metin_name"),
            "metin_x": int(location["x"]),
            "metin_y": int(location["y"]),
            "metin_vid": row.get("vid"),
            "metin_coord_source": source_type,
        }
    return None


def _parse_first_json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    stripped = str(text or "").lstrip()
    if not stripped:
        return {}
    obj, _idx = decoder.raw_decode(stripped)
    return obj if isinstance(obj, dict) else {}


def run_session_start_scan(
    *,
    radius: float = 600.0,
    limit: int = 8,
    runner=subprocess.run,
    project_root: Path = PROJECT_ROOT,
) -> tuple[dict | None, dict]:
    command = [
        sys.executable,
        "scripts/find_nearby_metins.py",
        "--source",
        "hybrid",
        "--radius",
        str(int(radius) if float(radius).is_integer() else radius),
        "--limit",
        str(int(limit)),
    ]
    completed = runner(command, cwd=project_root, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        return None, {"error": completed.stderr or completed.stdout, "returncode": completed.returncode}
    result = _parse_first_json_object(completed.stdout)
    return choose_session_start_metin(result), result


def apply_session_start_metin_choice(args, choice: dict | None) -> bool:
    if not choice:
        return False
    args.metin_name = choice.get("metin_name") or args.metin_name
    args.metin_vid = choice.get("metin_vid")
    args.metin_x = choice.get("metin_x")
    args.metin_y = choice.get("metin_y")
    args.metin_coord_source = choice.get("metin_coord_source")
    return True


def _is_selected_metin_target(game, metin_name: str | None) -> bool:
    name = str(getattr(game, "target_name", None) or "")
    if "metin" not in name.lower():
        return False
    if metin_name and metin_name.lower() not in name.lower() and str(metin_name).lower() != "metin":
        return False
    return bool(getattr(game, "target_vid", None) and getattr(game, "target_alive", None) is True)


def choose_exact_target_evidence_from_game(game, metin_name: str | None) -> dict | None:
    """Return trusted exact target evidence for a manually selected Metin.

    The manual-select flow is only trusted when the client logger confirms a
    live selected Metin by VID/name/alive state and exports target projection
    data. A target name alone, visual-only detection, or a named-probe row that
    lacks exact target projection is not enough to start combat.
    """
    if not _is_selected_metin_target(game, metin_name):
        return None
    project = getattr(game, "target_project_position", None)
    pixel = getattr(game, "target_pixel_position", None)
    if not isinstance(project, list) or len(project) < 2:
        return None
    if pixel is not None and (not isinstance(pixel, list) or len(pixel) < 2):
        return None
    return {
        "metin_name": str(getattr(game, "target_name", None) or metin_name or "Metin"),
        "metin_vid": int(getattr(game, "target_vid")),
        "metin_x": int(float(project[0])),
        "metin_y": int(float(project[1])),
        "metin_coord_source": "target_selected_project_position",
        "evidence_source": "manual_selected_target",
        "target_pixel_position": pixel,
        "target_project_position": project,
        "target_liveness_source": getattr(game, "target_liveness_source", None),
    }


def apply_exact_target_evidence(args, evidence: dict | None) -> bool:
    if not evidence:
        return False
    args.metin_name = evidence.get("metin_name") or args.metin_name
    args.metin_vid = evidence.get("metin_vid")
    args.metin_x = evidence.get("metin_x")
    args.metin_y = evidence.get("metin_y")
    args.metin_coord_source = evidence.get("metin_coord_source")
    return True


def choose_movement_key_toward_metin(action_args: dict | None, model: dict) -> str:
    action_args = action_args or {}
    current = action_args.get("current")
    target = action_args.get("target")

    if current and target and model:
        return choose_key_for_direction((int(current[0]), int(current[1])), (int(target[0]), int(target[1])), model)
    # Conservative fallback if no calibrated model is available. The caller still
    # uses very short holds and re-reads JSON every loop.
    return "w"


def choose_movement_step_toward_metin(
    action_args: dict | None,
    model: dict,
    *,
    calibrated_hold_seconds: float = 0.6,
    max_hold: float = 0.45,
    min_hold: float = 0.08,
) -> tuple[str, float]:
    key = choose_movement_key_toward_metin(action_args, model)
    action_args = action_args or {}
    distance = action_args.get("distance")
    entry = model.get(key) if isinstance(model, dict) else None
    mean_distance = entry.get("mean_distance_xy") if isinstance(entry, dict) else None
    try:
        distance_f = float(distance)
        mean_distance_f = float(mean_distance)
        calibrated_hold = float(calibrated_hold_seconds)
        units_per_second = mean_distance_f / calibrated_hold if calibrated_hold > 0 else 0.0
        if units_per_second <= 0:
            raise ValueError("bad movement speed")
        desired = (distance_f / units_per_second) * 0.8
        hold = max(float(min_hold), min(float(max_hold), desired))
        return key, round(hold, 3)
    except Exception:
        return key, round(max(float(min_hold), min(float(max_hold), 0.12)), 3)


def normalize_step_keys(keys) -> set[str]:
    if isinstance(keys, str):
        return {keys}
    return {str(key) for key in keys}


def serialize_nav_steps(steps) -> list[list]:
    serialized = []
    for keys, hold in steps or []:
        key_set = normalize_step_keys(keys)
        key_value = sorted(key_set)[0] if len(key_set) == 1 else sorted(key_set)
        serialized.append([key_value, float(hold)])
    return serialized


def choose_movement_steps_toward_metin(
    action_args: dict | None,
    model: dict,
    *,
    max_hold: float = 0.45,
    prefer_secondary: bool = False,
) -> list[tuple[set[str], float]]:
    action_args = action_args or {}
    current = action_args.get("current")
    target = action_args.get("target")
    if current and target and model:
        return [(normalize_step_keys(key), hold) for key, hold in choose_movement_steps((int(current[0]), int(current[1])), (int(target[0]), int(target[1])), model, max_hold=max_hold, prefer_secondary=prefer_secondary)]
    return [({"w"}, round(max(0.08, min(max_hold, 0.12)), 3))]


class ProbeLossGrace:
    """Short grace window for transient named-probe loss during active attacks."""

    def __init__(self, grace_cycles: int = 2):
        self.grace_cycles = grace_cycles
        self.missing_cycles = 0
        self.recent_tracked_attack = False

    @staticmethod
    def _probe_has_tracked_metin(game, locked_metin_vid: int | None, metin_name: str | None) -> bool:
        for entry in getattr(game, "named_metin_probe", None) or []:
            name = str(entry.get("name") or "")
            vid = entry.get("vid")
            if entry.get("alive") is not True:
                continue
            if locked_metin_vid and vid == locked_metin_vid:
                return True
            if metin_name and "metin" in name.lower() and metin_name.lower() in name.lower():
                return True
        return False

    def apply(self, action: CombatAction, game, locked_metin_vid: int | None, metin_name: str | None) -> CombatAction:
        selected_tracked_alive = bool(
            locked_metin_vid
            and getattr(game, "target_vid", None) == locked_metin_vid
            and getattr(game, "target_alive", None) is True
        )
        probe_present = self._probe_has_tracked_metin(game, locked_metin_vid, metin_name)
        if action.state == "ATTACK_METIN" or selected_tracked_alive or probe_present:
            self.recent_tracked_attack = action.state == "ATTACK_METIN" or selected_tracked_alive
            self.missing_cycles = 0
            return action
        if self.recent_tracked_attack and action.state == "REACQUIRE_METIN":
            self.missing_cycles += 1
            if self.missing_cycles <= self.grace_cycles:
                return CombatAction(
                    "ATTACK_METIN",
                    "hold_space",
                    f"probe loss grace cycle {self.missing_cycles}/{self.grace_cycles}; previous tracked Metin attack still recent; original: {action.reason}",
                    args=action.args,
                )
        self.recent_tracked_attack = False
        return action


def should_decay_after_divergence(tracker: "DistanceTracker") -> bool:
    if not tracker.is_diverging() or len(tracker.history) < 3:
        return False
    return tracker.best_distance() < tracker.history[0] * 0.95


class NavMilestones:
    def __init__(self, thresholds: list[int] | None = None):
        self.thresholds = thresholds or [25000, 20000, 15000, 10000, 5000, 2000, 1000, 500]
        self.seen: set[int] = set()

    def update(self, *, previous_distance: float | None, current_distance: float | None, cycle: int, elapsed_seconds: float) -> list[dict]:
        if previous_distance is None or current_distance is None:
            return []
        events = []
        for milestone in self.thresholds:
            if milestone in self.seen:
                continue
            if float(previous_distance) > milestone >= float(current_distance):
                self.seen.add(milestone)
                events.append(
                    {
                        "milestone_raw": milestone,
                        "milestone_display": milestone // 100,
                        "cycle": int(cycle),
                        "elapsed_s": round(float(elapsed_seconds), 1),
                    }
                )
        return events


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

    def best_distance(self) -> float:
        return min(self.history) if self.history else float("inf")

    def recent_trend(self) -> float:
        if len(self.history) < 2:
            return 0.0
        return self.history[-1] - self.history[0]

    def clear(self) -> None:
        self.history.clear()


def probe_best_key(
    *,
    read_player_pos,
    target: tuple[int, int] | list[int],
    online_model: OnlineNavModel,
    hold_func=hold_key,
    sleep_func=time.sleep,
    hold: float = 0.3,
    keys: tuple[str, ...] = ("w", "a", "s", "d"),
) -> tuple[str | None, float]:
    best_key: str | None = None
    best_dist = float("inf")
    for key in keys:
        before = read_player_pos()
        hold_func(key, hold)
        sleep_func(0.15)
        after = read_player_pos()
        online_model.record_step(key, before, after)
        dist = math.hypot(float(target[0]) - float(after[0]), float(target[1]) - float(after[1]))
        if dist < best_dist:
            best_dist = dist
            best_key = key
        sleep_func(0.1)
    return best_key, best_dist


def choose_unstuck_key(last_key: str | None) -> str:
    return {"w": "a", "a": "s", "s": "d", "d": "w"}.get(str(last_key or ""), "a")


def movement_stuck(previous: list[int] | tuple[int, ...] | None, current: list[int] | tuple[int, ...] | None, *, threshold: float = 50.0) -> bool:
    if not previous or not current or len(previous) < 2 or len(current) < 2:
        return False
    return math.hypot(float(current[0]) - float(previous[0]), float(current[1]) - float(previous[1])) < threshold


def format_structured_log_line(
    *,
    state: str,
    action: str,
    hp: int | None,
    max_hp: int | None,
    sp: int | None,
    max_sp: int | None,
    target_name: str | None,
    target_vid: int | None,
    target_alive: bool | None,
    nav_steps: list[tuple[str, float]] | None = None,
    distance: float | None = None,
    trend: float | None = None,
    stuck: bool | None = None,
) -> str:
    line = (
        f"[state={state}] [action={action}] [hp={hp}/{max_hp}] [sp={sp}/{max_sp}] "
        f"[target={target_name or 'none'} vid={target_vid or 0} alive={target_alive}]"
    )
    if nav_steps:
        keys = ",".join("+".join(sorted(normalize_step_keys(key_set))) for key_set, _hold in nav_steps)
        holds = ",".join(str(round(float(hold), 3)) for _key, hold in nav_steps)
        line += f" [key={keys}] [hold={holds}]"
    if distance is not None:
        line += f" [dist={round(float(distance), 1)}]"
    if trend is not None:
        line += f" [trend={round(float(trend), 1)}]"
    if stuck is not None:
        line += f" [stuck={stuck}]"
    return line


def emit(log_path: Path, event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def combat_snapshot(args, game, last_buff_ts: float | None, reward_seen: bool) -> CombatSnapshot:
    seconds_since_buff = None if last_buff_ts is None else time.monotonic() - last_buff_ts
    return CombatSnapshot(
        game=game,
        metin_vid=args.metin_vid,
        metin_name=args.metin_name,
        metin_coord=[args.metin_x, args.metin_y] if args.metin_x is not None and args.metin_y is not None else None,
        metin_coord_source=getattr(args, "metin_coord_source", None),
        buff_active=game.buff_active,
        seconds_since_buff=seconds_since_buff,
        reward_seen=reward_seen,
    )


def run_live_command(action, args, navigation_model: dict | None = None) -> float | None:
    """Translate one symbolic action to bounded input. Returns new buff timestamp if buff pressed."""
    if action.command == "press_potion":
        tap_key(args.potion_key, 0.06)
    elif action.command == "press_potion_and_disengage":
        tap_key(args.potion_key, 0.06)
        hold_key("s", 0.35)
    elif action.command == "press_buff":
        tap_key(args.buff_key, 0.08)
        return time.monotonic()
    elif action.command in {"attack_target", "hold_space", "space_probe"}:
        key_down("space")
        time.sleep(args.burst_seconds if action.command != "space_probe" else min(args.burst_seconds, 0.8))
        key_up("space")
    elif action.command == "navigate_to_metin":
        steps = action.args.get("nav_steps") if isinstance(action.args, dict) else None
        if not steps:
            steps = choose_movement_steps_toward_metin(action.args, navigation_model or {}, max_hold=min(args.move_step_seconds, 0.45), prefer_secondary=bool((action.args or {}).get("prefer_secondary")))
        for keys, hold in steps:
            for key in sorted(normalize_step_keys(keys)):
                hold_key(key, float(hold))
            time.sleep(0.05)
    elif action.command == "micro_position":
        steps = action.args.get("nav_steps") if isinstance(action.args, dict) else None
        if not steps:
            steps = choose_movement_steps_toward_metin(action.args, navigation_model or {}, max_hold=min(args.micro_move_seconds, 0.16), prefer_secondary=bool((action.args or {}).get("prefer_secondary")))
        for keys, hold in steps:
            for key in sorted(normalize_step_keys(keys)):
                hold_key(key, float(hold))
            time.sleep(0.05)
    elif action.command == "return_to_last_metin_coord":
        if isinstance(action.args, dict) and action.args.get("target") and action.args.get("current"):
            steps = action.args.get("nav_steps") or choose_movement_steps_toward_metin(
                action.args,
                navigation_model or {},
                max_hold=min(args.move_step_seconds, 0.45),
                prefer_secondary=bool(action.args.get("prefer_secondary")),
            )
            for keys, hold in steps:
                for key in sorted(normalize_step_keys(keys)):
                    hold_key(key, float(hold))
                time.sleep(0.05)
        else:
            key_down("space")
            time.sleep(min(args.burst_seconds, 0.8))
            key_up("space")
    elif action.command == "stop_success":
        pass
    return None


def release_all_keys() -> None:
    for key in ("space", "w", "a", "s", "d"):
        try:
            key_up(key)
        except Exception:
            pass


def append_state_once(states_visited: list[str], state: str) -> None:
    if not states_visited or states_visited[-1] != state:
        states_visited.append(state)


def write_combat_report(
    *,
    report_dir: Path,
    run_id: str | None,
    outcome: str,
    metin_name: str | None,
    metin_vid: int | None,
    start_time: float,
    states_visited: list[str],
    hp_at_end: int | None,
    max_hp: int | None,
    potions_used: int,
    stop_reason: str,
) -> Path:
    rid = run_id or "manual-combat-run"
    report = {
        "run_id": rid,
        "outcome": outcome,
        "metin_name": metin_name,
        "metin_vid": metin_vid,
        "duration_seconds": round(time.time() - start_time, 1),
        "states_visited": states_visited,
        "hp_at_end": hp_at_end,
        "max_hp": max_hp,
        "potions_used": potions_used,
        "stop_reason": stop_reason,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{rid}_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Client-state Metin combat state machine. Dry-run by default.")
    ap.add_argument("--tsv", type=Path, default=None, help="Deprecated compatibility option; combat decisions use --json-state only")
    ap.add_argument("--json-state", type=Path, default=DEFAULT_JSON_PATH, help="Client Python hermes_state.json preferred state path; pass empty string via API only to disable")
    ap.add_argument("--metin-name", default="Metin da Batalha")
    ap.add_argument("--metin-vid", type=int, default=None)
    ap.add_argument("--metin-x", type=int, default=None)
    ap.add_argument("--metin-y", type=int, default=None)
    ap.add_argument("--metin-coord-source", default=None, help="trusted coordinate source, e.g. live_memory_visible_text or table")
    ap.add_argument("--max-cycles", type=int, default=60)
    ap.add_argument("--burst-seconds", type=float, default=2.5)
    ap.add_argument("--move-step-seconds", type=float, default=0.35)
    ap.add_argument("--micro-move-seconds", type=float, default=0.12)
    ap.add_argument("--navigation-model", type=Path, default=Path("reports/client_tsv_runaround/live_navigation_model.json"))
    ap.add_argument("--buff-key", default="f1")
    ap.add_argument("--potion-key", default="1")
    ap.add_argument("--live", action="store_true", help="Actually send bounded inputs. Default is dry-run.")
    ap.add_argument("--out", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--run-id", default=None, help="Dashboard run id used for cooperative stop files")
    ap.add_argument("--stop-file", type=Path, default=None, help="Cooperative stop-file path; defaults from HERMES_STOP_FILE or reports/dashboard_runs/<run_id>.stop")
    ap.add_argument("--report-dir", type=Path, default=Path("reports/dashboard_runs"), help="Directory for <run_id>_report.json post-combat reports")
    ap.add_argument("--max-state-age-seconds", type=float, default=2.0)
    ap.add_argument("--session-start-scan", action=argparse.BooleanOptionalAction, default=True, help="Before combat, run read-only find_nearby_metins --source hybrid and use the first trusted exact-coordinate result if any")
    ap.add_argument("--session-start-scan-radius", type=float, default=600.0)
    ap.add_argument("--session-start-scan-limit", type=int, default=8)
    ap.add_argument(
        "--allow-selected-vid-without-exact-coords",
        action="store_true",
        help="Operator-approved fallback: allow Space-only attack when selected target is a live Metin VID but exact target projection is missing. Movement/navigation still requires exact coordinates.",
    )
    args = ap.parse_args()

    args.out.write_text("", encoding="utf-8")
    cfg = CombatConfig()
    last_buff_ts: float | None = None
    reward_seen = False
    run_id = args.run_id or os.environ.get("HERMES_RUN_ID")
    stop_file = args.stop_file or (Path(os.environ["HERMES_STOP_FILE"]) if os.environ.get("HERMES_STOP_FILE") else None)
    if stop_file is None and run_id:
        stop_file = Path("reports/dashboard_runs") / f"{run_id}.stop"
    session_scan_choice = None
    session_scan_result = None
    if args.session_start_scan and args.live:
        try:
            session_scan_choice, session_scan_result = run_session_start_scan(radius=args.session_start_scan_radius, limit=args.session_start_scan_limit)
            applied = apply_session_start_metin_choice(args, session_scan_choice)
            emit(
                args.out,
                {
                    "cycle": 0,
                    "state": "SESSION_START_SCAN",
                    "command": "find_nearby_metins",
                    "reason": "session-start scan for fresh live/exact Metin coordinate",
                    "applied": applied,
                    "choice": session_scan_choice,
                    "result_count": (session_scan_result or {}).get("count") if isinstance(session_scan_result, dict) else None,
                    "source": (session_scan_result or {}).get("source") if isinstance(session_scan_result, dict) else None,
                    "diagnostics": (session_scan_result or {}).get("diagnostics") if isinstance(session_scan_result, dict) else None,
                    "run_id": run_id,
                },
            )
        except Exception as exc:
            emit(args.out, {"cycle": 0, "state": "SESSION_START_SCAN", "command": "find_nearby_metins", "reason": f"session-start scan failed: {exc}", "applied": False, "run_id": run_id})
    locked_metin_vid = args.metin_vid
    locked_metin_name = args.metin_name
    navigation_model = load_navigation_model(args.navigation_model)
    online_nav_model = OnlineNavModel(navigation_model)
    start_time = time.time()
    states_visited: list[str] = []
    last_game = None
    potions_used = 0
    last_move_start_coord = None
    last_move_key: str | None = None
    stuck_count = 0
    nav_stuck_cycles = 0
    distance_tracker = DistanceTracker(window=6)
    nav_milestones = NavMilestones()
    previous_nav_distance: float | None = None
    last_primary_key: str | None = None
    probe_loss_grace = ProbeLossGrace(grace_cycles=2)
    mover = SmoothMover()

    def finish(outcome: str, stop_reason: str, exit_code: int) -> int:
        write_combat_report(
            report_dir=args.report_dir,
            run_id=run_id,
            outcome=outcome,
            metin_name=locked_metin_name,
            metin_vid=locked_metin_vid or getattr(last_game, "target_vid", None),
            start_time=start_time,
            states_visited=states_visited,
            hp_at_end=getattr(last_game, "hp", None),
            max_hp=getattr(last_game, "max_hp", None),
            potions_used=potions_used,
            stop_reason=stop_reason,
        )
        return exit_code

    if args.live:
        activate_window(find_window("MT2Portugalia"))
        time.sleep(0.2)

    try:
        for cycle in range(1, args.max_cycles + 1):
            if should_stop(stop_file):
                release_all_keys()
                emit(args.out, {"cycle": cycle, "state": "STOP_REQUESTED", "command": "release_all_keys", "reason": "Graceful stop requested", "run_id": run_id})
                print("[state=STOP_REQUESTED] [action=release_all_keys] [hp=?/?] [sp=?/?] [target=none vid=0 alive=None]", flush=True)
                append_state_once(states_visited, "STOP_REQUESTED")
                return finish("stopped", "stop_file", 0)
            try:
                game = read_game(args.tsv, json_path=args.json_state, max_age_seconds=args.max_state_age_seconds)
                last_game = game
            except RuntimeError as exc:
                emit(args.out, {"cycle": cycle, "state": "WAIT_FRESH_STATE", "command": "pause", "reason": str(exc), "run_id": run_id})
                time.sleep(0.5)
                continue
            # Current JSON cannot read chat rewards; keep the hook for a future chat/log adapter.
            if locked_metin_vid is None and args.metin_x is None and args.metin_y is None:
                exact_evidence = choose_exact_target_evidence_from_game(game, locked_metin_name)
                if exact_evidence:
                    apply_exact_target_evidence(args, exact_evidence)
                    locked_metin_vid = args.metin_vid
                    locked_metin_name = args.metin_name
                    emit(
                        args.out,
                        {
                            "cycle": cycle,
                            "dry_run": not args.live,
                            "state": "EXACT_TARGET_LOCKED",
                            "command": "lock_manual_selected_target",
                            "reason": "manual selected Metin has trusted VID/alive/project-position evidence",
                            "exact_target_evidence": exact_evidence,
                            "run_id": run_id,
                        },
                    )
                elif _is_selected_metin_target(game, locked_metin_name):
                    if args.allow_selected_vid_without_exact_coords:
                        locked_metin_vid = game.target_vid
                        locked_metin_name = game.target_name or locked_metin_name
                        args.metin_vid = game.target_vid
                        args.metin_name = locked_metin_name
                        emit(
                            args.out,
                            {
                                "cycle": cycle,
                                "dry_run": not args.live,
                                "state": "TRACKED_VID_ATTACK_WITHOUT_EXACT_COORDS",
                                "command": "lock_selected_vid_for_space_only_attack",
                                "reason": "operator-approved fallback: selected Metin VID/name/alive is trusted for Space-only attack; exact target projection is still missing, so movement remains blocked",
                                "target_vid": game.target_vid,
                                "target_name": game.target_name,
                                "target_alive": game.target_alive,
                                "target_pixel_position": game.target_pixel_position,
                                "target_project_position": game.target_project_position,
                                "movement_allowed": False,
                                "run_id": run_id,
                            },
                        )
                    else:
                        emit(
                            args.out,
                            {
                                "cycle": cycle,
                                "dry_run": not args.live,
                                "state": "NEED_EXACT_TARGET",
                                "command": "stop_need_exact_target_projection",
                                "reason": "selected Metin has VID/name/alive evidence but missing target project/pixel position; refresh the client logger or run same-elevation/admin capture before live combat",
                                "target_vid": game.target_vid,
                                "target_name": game.target_name,
                                "target_alive": game.target_alive,
                                "target_pixel_position": game.target_pixel_position,
                                "target_project_position": game.target_project_position,
                                "run_id": run_id,
                            },
                        )
                        print("[state=NEED_EXACT_TARGET] [action=stop_need_exact_target_projection] [reason=selected Metin missing target projection evidence]", flush=True)
                        append_state_once(states_visited, "NEED_EXACT_TARGET")
                        return finish("aborted", "need_exact_target_projection", 2)

            snap = CombatSnapshot(
                game=game,
                metin_vid=locked_metin_vid,
                metin_name=locked_metin_name,
                metin_coord=normalize_metin_coord(args.metin_x, args.metin_y),
                metin_coord_source=args.metin_coord_source,
                buff_active=game.buff_active,
                seconds_since_buff=None if last_buff_ts is None else time.monotonic() - last_buff_ts,
                reward_seen=reward_seen,
            )
            action = decide_combat_action(snap, cfg)
            action = probe_loss_grace.apply(action, game, locked_metin_vid, locked_metin_name)
            append_state_once(states_visited, action.state)
            if action.state == "ATTACK_METIN" and locked_metin_vid is None and game.target_vid:
                locked_metin_vid = game.target_vid
                locked_metin_name = game.target_name or locked_metin_name
            nav_steps = None
            nav_distance = None
            nav_trend = None
            nav_stuck = None
            nav_observation_before = None
            if action.command in {"navigate_to_metin", "micro_position"}:
                if action.args is None:
                    action_args = {}
                    object.__setattr__(action, "args", action_args)
                else:
                    action_args = action.args
                nav_distance = action_args.get("distance")
                milestone_events = nav_milestones.update(
                    previous_distance=previous_nav_distance,
                    current_distance=nav_distance,
                    cycle=cycle,
                    elapsed_seconds=time.time() - start_time,
                )
                if milestone_events:
                    action_args["nav_milestones"] = milestone_events
                distance_tracker.update(nav_distance)
                nav_trend = distance_tracker.recent_trend()
                nav_stuck = distance_tracker.is_stuck()
                prefer_secondary = distance_tracker.is_diverging()
                use_waypoint = nav_stuck or prefer_secondary
                if should_decay_after_divergence(distance_tracker) and last_primary_key:
                    before_summary = online_nav_model.summary().get(last_primary_key)
                    online_nav_model.decay_suspect_key(last_primary_key)
                    action_args["nav_recovery"] = {
                        "reason": "diverging after best_distance",
                        "decayed_key": last_primary_key,
                        "best_dist": distance_tracker.best_distance(),
                        "current_dist": nav_distance,
                        "before": before_summary,
                        "after": online_nav_model.summary().get(last_primary_key),
                    }
                action_args["distance_trend"] = nav_trend
                action_args["distance_stuck"] = nav_stuck
                action_args["distance_diverging"] = prefer_secondary
                action_args["best_distance"] = distance_tracker.best_distance()
                action_args["prefer_secondary"] = prefer_secondary
                action_args["use_waypoint"] = use_waypoint
                max_hold = min(args.micro_move_seconds, 0.16) if action.command == "micro_position" else min(args.move_step_seconds, 0.45)
                current = action_args.get("current")
                target = action_args.get("target")
                if current and target:
                    nav_steps = online_nav_model.choose_steps_with_waypoint((int(current[0]), int(current[1])), (int(target[0]), int(target[1])), use_waypoint=use_waypoint, max_hold=max_hold, prefer_secondary=prefer_secondary)
                else:
                    nav_steps = choose_movement_steps_toward_metin(action_args, navigation_model or {}, max_hold=max_hold, prefer_secondary=prefer_secondary)
                action_args["nav_steps"] = serialize_nav_steps(nav_steps)
                if nav_steps:
                    last_primary_key = sorted(normalize_step_keys(nav_steps[0][0]))[0]
                action_args["nav_model_summary"] = online_nav_model.summary()
                if game.player_coord and len(game.player_coord) >= 2:
                    nav_observation_before = (int(game.player_coord[0]), int(game.player_coord[1]))
                if isinstance(nav_distance, (int, float)):
                    previous_nav_distance = float(nav_distance)
            emit(
                args.out,
                {
                    "cycle": cycle,
                    "dry_run": not args.live,
                    "state": action.state,
                    "command": action.command,
                    "reason": action.reason,
                    "success": action.success,
                    "coord": game.player_coord,
                    "hp": game.hp,
                    "max_hp": game.max_hp,
                    "sp": game.sp,
                    "max_sp": game.max_sp,
                    "buff_active": game.buff_active,
                    "target_vid": game.target_vid,
                    "target_name": game.target_name,
                    "target_alive": game.target_alive,
                    "target_type": game.target_type,
                    "locked_metin_vid": locked_metin_vid,
                    "locked_metin_name": locked_metin_name,
                    "nearby_entities": game.nearby_entities,
                    "args": action.args,
                },
            )
            print(
                format_structured_log_line(
                    state=action.state,
                    action=action.command,
                    hp=game.hp,
                    max_hp=game.max_hp,
                    sp=game.sp,
                    max_sp=game.max_sp,
                    target_name=game.target_name,
                    target_vid=game.target_vid,
                    target_alive=game.target_alive,
                    nav_steps=nav_steps,
                    distance=nav_distance,
                    trend=nav_trend,
                    stuck=nav_stuck,
                ),
                flush=True,
            )
            if action.success or action.state in {"ABORT_SAFE", "NEED_METIN_TARGET"}:
                if action.success:
                    return finish("destroyed", "destroyed", 0)
                return finish("aborted", action.state.lower(), 2)
            if args.live:
                if action.command in {"navigate_to_metin", "micro_position"}:
                    nav_stuck_cycles = nav_stuck_cycles + 1 if nav_stuck else 0
                    if movement_stuck(last_move_start_coord, game.player_coord):
                        stuck_count += 1
                    else:
                        stuck_count = 0
                    if (nav_stuck_cycles >= 5 or stuck_count >= 5) and isinstance(action.args, dict) and action.args.get("target"):
                        target_for_probe = action.args["target"]

                        def _read_player_xy():
                            probe_game = read_game(args.tsv, json_path=args.json_state, max_age_seconds=args.max_state_age_seconds)
                            return int(probe_game.player_coord[0]), int(probe_game.player_coord[1])

                        best_key, best_dist = probe_best_key(
                            read_player_pos=_read_player_xy,
                            target=(int(target_for_probe[0]), int(target_for_probe[1])),
                            online_model=online_nav_model,
                            hold_func=hold_key,
                            sleep_func=time.sleep,
                            hold=0.3,
                        )
                        emit(
                            args.out,
                            {
                                "cycle": cycle,
                                "state": "NAV_PROBE_ALL_KEYS",
                                "command": "probe_best_key",
                                "reason": "stuck_5_cycles",
                                "best_key": best_key,
                                "best_distance": best_dist,
                                "nav_model_summary": online_nav_model.summary(),
                            },
                        )
                        stuck_count = 0
                        nav_stuck_cycles = 0
                        distance_tracker.clear()
                        time.sleep(0.25)
                        continue
                    last_move_start_coord = game.player_coord
                    if nav_steps:
                        last_move_key = sorted(normalize_step_keys(nav_steps[-1][0]))[0]
                    else:
                        last_move_key = choose_movement_key_toward_metin(action.args, navigation_model or {})
                new_buff_ts = None
                if action.command in {"navigate_to_metin", "micro_position"} and nav_steps:
                    for keys, hold in nav_steps:
                        mover.move(normalize_step_keys(keys), float(hold))
                else:
                    mover.release_all()
                    if action.command in {"press_potion", "press_potion_and_disengage"}:
                        potions_used += 1
                    new_buff_ts = run_live_command(action, args, navigation_model)
                if args.live and nav_steps and nav_observation_before is not None and action.command in {"navigate_to_metin", "micro_position"}:
                    try:
                        after_game = read_game(args.tsv, json_path=args.json_state, max_age_seconds=args.max_state_age_seconds)
                        if after_game.player_coord and len(after_game.player_coord) >= 2:
                            primary_key = sorted(normalize_step_keys(nav_steps[0][0]))[0]
                            online_nav_model.record_step(primary_key, nav_observation_before, (int(after_game.player_coord[0]), int(after_game.player_coord[1])))
                    except RuntimeError:
                        pass
                if new_buff_ts is not None:
                    last_buff_ts = new_buff_ts
            time.sleep(NAV_CYCLE_SLEEP if action.command in {"navigate_to_metin", "micro_position"} else ATTACK_CYCLE_SLEEP)
        return finish("max_cycles", "max_cycles", 0 if not args.live else 1)
    finally:
        mover.release_all()


if __name__ == "__main__":
    raise SystemExit(main())
