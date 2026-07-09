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
import itertools
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
from metin2_research.win_input import SmoothMover, click_at, hold_key, key_down, key_up, tap_key
from metin2_research.window_capture import activate_window, capture_window_image, find_window
from metin2_dashboard.config import normalize_buff_config, normalize_combat_config

DEFAULT_LOG = Path("reports/client_tsv_runaround/combat_metin_state_machine.jsonl")
DEFAULT_METIN_ONNX = Path("reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.onnx")
NAV_CYCLE_SLEEP = 0.05
ATTACK_CYCLE_SLEEP = 0.25
_ONNX_DETECTOR_CACHE = None


def max_cycles_exit_code(*, live: bool, buff_only: bool) -> int:
    """Return process exit code when the loop ends by max-cycles.

    Live combat max-cycles means no Metin was destroyed, so it remains non-success.
    Buff-only mode is a timed keepalive test; reaching max-cycles cleanly is success.
    """
    if buff_only:
        return 0
    return 0 if not live else 1


def _load_json_config(path: Path | None) -> dict:
    if path is None:
        return {}
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_combat_config(path: Path | None) -> dict:
    return normalize_combat_config(_load_json_config(path))


def load_buff_config(path: Path | None) -> dict:
    return normalize_buff_config(_load_json_config(path))


def _hostile_mob_entity(game) -> dict | None:
    target_name = str(getattr(game, "target_name", None) or "")
    target_is_metin = "metin" in target_name.lower()
    if target_name and not target_is_metin and getattr(game, "target_alive", None) is True:
        return {"vid": getattr(game, "target_vid", None), "name": target_name, "source": "target"}
    return None


def choose_nearby_mob_attack(game, *, enabled: bool) -> CombatAction | None:
    if not enabled:
        return None
    mob = _hostile_mob_entity(game)
    if not mob:
        return None
    return CombatAction(
        "ATTACK_NEARBY_MOBS",
        "attack_target",
        "attack-nearby-mobs enabled and client state reports a valid mob target/nearby hostile entity",
        args={"mob": mob, "state_fields_needed_for_better_targeting": ["nearby_entities[].vid", "nearby_entities[].name", "nearby_entities[].distance", "nearby_entities[].project_position"]},
    )


def choose_mouse_target_action(game, *, enabled: bool) -> CombatAction | None:
    if not enabled:
        return None
    target_name = str(getattr(game, "target_name", None) or "")
    target_vid = getattr(game, "target_vid", None)
    target_alive = getattr(game, "target_alive", None)
    if target_name and target_vid and target_alive is not False:
        return None
    candidates: list[tuple[int, float, dict, list[int]]] = []
    for entity in getattr(game, "nearby_entities", None) or []:
        if not isinstance(entity, dict):
            continue
        name = str(entity.get("name") or "")
        kind = str(entity.get("kind") or entity.get("type") or "").lower()
        is_metin = "metin" in name.lower() or kind == "metin"
        hostile = entity.get("hostile") is True or kind in {"mob", "monster", "hostile"}
        if not (is_metin or hostile):
            continue
        pixel = entity.get("pixel_position") or entity.get("screen_position") or entity.get("project_pixel")
        if not isinstance(pixel, list | tuple) or len(pixel) < 2:
            continue
        try:
            point = [int(round(float(pixel[0]))), int(round(float(pixel[1])))]
        except (TypeError, ValueError):
            continue
        distance = float(entity.get("distance", 999999.0) or 999999.0)
        # Prefer Metins over mobs, then closer targets.
        candidates.append((0 if is_metin else 1, distance, entity, point))
    if not candidates:
        return None
    _priority, _distance, entity, point = sorted(candidates, key=lambda row: (row[0], row[1]))[0]
    return CombatAction(
        "ACQUIRE_TARGET",
        "mouse_target_entity",
        "attack-nearby-mobs enabled and no target is selected; click a visible mob/Metin pixel from client-state evidence, then re-read selected target",
        args={"pixel_position": point, "entity": entity},
    )


def parse_channel_click_points(spec: str | None) -> list[tuple[float, float]]:
    """Parse channel menu click points.

    Format: `x,y;x,y`. Values in 0..1 are treated as window-relative
    fractions; values >1 are treated as absolute screen pixels.
    """
    points: list[tuple[float, float]] = []
    for chunk in str(spec or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [part.strip() for part in chunk.split(",")]
        if len(parts) != 2:
            raise ValueError(f"invalid channel point {chunk!r}; expected x,y")
        x, y = float(parts[0]), float(parts[1])
        if x < 0 or y < 0:
            raise ValueError(f"invalid channel point {chunk!r}; coordinates must be non-negative")
        points.append((x, y))
    return points


def channel_click_screen_point(window, point: tuple[float, float]) -> tuple[int, int]:
    x, y = point
    if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
        return int(window.bbox[0] + window.width * x), int(window.bbox[1] + window.height * y)
    return int(x), int(y)


def run_pickup_spam_sequence(args) -> dict:
    """After destroying a Metin: spam pickup Z without changing channel."""
    pickup_count = max(0, int(getattr(args, "pickup_spam_count", 12)))
    pickup_interval = max(0.01, float(getattr(args, "pickup_spam_interval", 0.08)))
    for _ in range(pickup_count):
        tap_key("z", 0.03)
        time.sleep(pickup_interval)
    return {"pickup_count": pickup_count}


def run_channel_rotation_sequence(args, *, channel_index: int) -> dict:
    """After destroying a Metin: spam pickup, open channel menu, click next channel."""
    points = parse_channel_click_points(getattr(args, "channel_click_points", None))
    if not points:
        raise ValueError("--channel-click-points is required when --channel-rotate-after-destroy is enabled")
    point = points[channel_index % len(points)]
    result = run_pickup_spam_sequence(args)
    window = find_window(getattr(args, "window_query", None) or "MT2Portugalia")
    activate_window(window)
    tap_key("x", 0.06)
    time.sleep(max(0.0, float(getattr(args, "channel_menu_delay_seconds", 0.35))))
    sx, sy = channel_click_screen_point(window, point)
    click_at(sx, sy)
    time.sleep(max(0.0, float(getattr(args, "channel_switch_wait_seconds", 4.0))))
    result.update({"channel_index": channel_index, "point": [point[0], point[1]], "screen_point": [sx, sy]})
    return result


def due_buff_actions(buff_config: dict, last_cast: dict[str, float], *, now: float) -> list[dict]:
    if not buff_config.get("use_buff_config") and not buff_config.get("buffs"):
        return []
    due = []
    for row in buff_config.get("buffs") or []:
        if not row.get("enabled"):
            continue
        key = str(row.get("key") or "").lower()
        if not key:
            continue
        interval = float(row.get("interval_seconds", 35.0))
        pre_cast = float(row.get("pre_cast_seconds", 0.0))
        elapsed = now - last_cast.get(key, 0.0)
        if key not in last_cast or elapsed >= max(0.0, interval - pre_cast):
            due.append({"key": key, "interval_seconds": interval, "pre_cast_seconds": pre_cast, "elapsed_seconds": round(elapsed, 3) if key in last_cast else None})
    return due


def buff_config_from_cli(buff_keys: str | None, buff_durations: str | None, *, pre_cast_seconds: float) -> dict | None:
    keys = [key.strip().lower() for key in str(buff_keys or "").split(",") if key.strip()]
    if not keys:
        return None
    raw_durations = [item.strip() for item in str(buff_durations or "").split(",") if item.strip()]
    if not raw_durations:
        raw_durations = ["35"]
    durations = [float(item) for item in raw_durations]
    if len(durations) == 1 and len(keys) > 1:
        durations = durations * len(keys)
    if len(durations) != len(keys):
        raise ValueError("--buff-durations must contain one value or the same count as --buff-keys")
    return normalize_buff_config(
        {
            "use_buff_config": True,
            "buffs": [
                {
                    "key": key,
                    "enabled": True,
                    "interval_seconds": duration,
                    "pre_cast_seconds": float(pre_cast_seconds),
                }
                for key, duration in zip(keys, durations)
            ],
        }
    )


def merge_cli_buff_config(buff_config: dict, cli_config: dict | None) -> dict:
    if cli_config is None:
        return buff_config
    return cli_config


def key_macro_out_path(base_out: Path, key: str, run_id: str | None) -> Path:
    base = Path(base_out)
    stem = base.stem or "buff_keepalive"
    safe_key = str(key).lower().replace("+", "_").replace(" ", "_")
    suffix = f".{safe_key}"
    if run_id:
        suffix = f".{run_id}{suffix}"
    return base.with_name(stem + suffix + ".key_macro.json")


def mounted_state_from_game(game) -> bool | None:
    flags = getattr(game, "player_flags", {}) or {}
    if "mounted" not in flags:
        return None
    value = flags.get("mounted")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "mounted"}:
            return True
        if lowered in {"0", "false", "no", "unmounted"}:
            return False
    return None


def press_buff_key_via_key_macro(*, key: str, args, stop_file: Path | None, run_id: str | None) -> dict:
    """Use the proven direct-key sender for live buff refreshes.

    This preserves the same elevated/UAC + robust-focus behavior as the
    control-panel `Press F1/F2 once LIVE` buttons instead of using the older
    in-process tap_key path that can silently miss an elevated client.
    """
    out_path = key_macro_out_path(Path(args.out), key, run_id)
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "key_macro_control.py"),
        "--live",
        "--elevate",
        "--key",
        str(key).lower(),
        "--presses",
        "1",
        "--interval-seconds",
        "0.05",
        "--hold-seconds",
        "0.08",
        "--window-query",
        str(getattr(args, "window_query", None) or "MT2Portugalia"),
        "--out",
        str(out_path),
    ]
    env = os.environ.copy()
    if stop_file:
        env["HERMES_STOP_FILE"] = str(stop_file)
    completed = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90, env=env)
    result = {
        "command": cmd,
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "out": str(out_path),
        "elevated_log": str(out_path.with_suffix(".elevated.log")),
    }
    print("key_macro_child " + json.dumps(result, sort_keys=True), flush=True)
    if completed.returncode != 0:
        raise RuntimeError(f"key_macro_control failed for {key}: exit={completed.returncode} tail={completed.stdout[-600:]}")
    return result




class BuffDamageGuard:
    """Conservative damage-based guard for toggle-style buffs.

    Some Metin2 buffs are toggles: pressing the key while still active can turn
    the buff off.  This guard does not prove a buff icon is up; it only prevents
    early timer refreshes while target HP is still falling at the post-cast
    damage rate.  If evidence is missing, callers fall back to the normal timer.
    """

    def __init__(self, *, guard_keys: set[str] | None = None, baseline_window_seconds: float = 35.0, recent_window_seconds: float = 10.0, active_ratio: float = 0.70):
        self.guard_keys = {str(key).lower() for key in (guard_keys or set()) if str(key).strip()}
        self.baseline_window_seconds = float(baseline_window_seconds)
        self.recent_window_seconds = float(recent_window_seconds)
        self.active_ratio = float(active_ratio)
        self._last_sample: tuple[int | None, float | None, float] | None = None
        self._damage_events: list[dict] = []
        self._pressed_at: dict[str, float] = {}
        self._baseline_rate: dict[str, float] = {}

    @staticmethod
    def target_hp(game) -> float | None:
        value = getattr(game, "target_hp", None)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def note_buff_pressed(self, key: str, *, now: float) -> None:
        key = str(key).lower()
        if key in self.guard_keys:
            self._pressed_at[key] = float(now)
            self._baseline_rate.pop(key, None)

    def observe_game(self, game, *, now: float) -> dict | None:
        target_vid = getattr(game, "target_vid", None)
        hp = self.target_hp(game)
        now = float(now)
        previous = self._last_sample
        self._last_sample = (target_vid, hp, now)
        if previous is None or hp is None or target_vid is None:
            return None
        prev_vid, prev_hp, prev_t = previous
        if prev_vid != target_vid or prev_hp is None:
            return None
        dt = max(0.001, now - float(prev_t))
        damage = float(prev_hp) - float(hp)
        if damage <= 0:
            return None
        event = {"t": now, "target_vid": target_vid, "damage": damage, "dt": dt, "rate": damage / dt}
        self._damage_events.append(event)
        cutoff = now - max(self.baseline_window_seconds, self.recent_window_seconds, 1.0) * 3.0
        self._damage_events = [row for row in self._damage_events if row["t"] >= cutoff]
        for key, pressed_at in list(self._pressed_at.items()):
            if 0.0 <= now - pressed_at <= self.baseline_window_seconds:
                baseline = self._rate_between(pressed_at, now)
                if baseline and baseline > 0:
                    old = self._baseline_rate.get(key)
                    self._baseline_rate[key] = max(old or 0.0, baseline)
        return event

    def _rate_between(self, start: float, end: float) -> float | None:
        window = max(0.001, float(end) - float(start))
        rows = [row for row in self._damage_events if start <= row["t"] <= end and float(row.get("dt", 0.0)) <= window]
        if not rows:
            return None
        total_damage = sum(float(row["damage"]) for row in rows)
        total_dt = sum(float(row["dt"]) for row in rows)
        if total_damage <= 0 or total_dt <= 0:
            return None
        return total_damage / total_dt

    def recent_rate(self, *, now: float) -> float | None:
        return self._rate_between(float(now) - self.recent_window_seconds, float(now))

    def suppress_refresh(self, buff: dict, last_cast: dict[str, float], *, now: float) -> tuple[bool, dict]:
        key = str(buff.get("key") or "").lower()
        if key not in self.guard_keys or key not in last_cast:
            return False, {}
        interval = float(buff.get("interval_seconds", 0.0) or 0.0)
        elapsed = float(now) - float(last_cast[key])
        recent = self.recent_rate(now=now)
        baseline = self._baseline_rate.get(key)
        evidence = {
            "key": key,
            "elapsed_seconds": round(elapsed, 3),
            "interval_seconds": interval,
            "recent_damage_rate": round(recent, 3) if recent is not None else None,
            "baseline_damage_rate": round(baseline, 3) if baseline is not None else None,
        }
        if elapsed < interval:
            evidence["reason"] = "timer_before_full_duration_damage_guard"
            return True, evidence
        if recent is not None and baseline is not None and baseline > 0 and recent >= baseline * self.active_ratio:
            evidence["reason"] = "recent_damage_still_matches_buffed_baseline"
            evidence["active_ratio"] = self.active_ratio
            return True, evidence
        return False, evidence


def parse_buff_damage_guard_keys(spec: str | None) -> set[str]:
    return {key.strip().lower() for key in str(spec or "").split(",") if key.strip()}


def apply_buff_damage_guard(due_buffs: list[dict], last_cast: dict[str, float], *, now: float, guard: BuffDamageGuard | None) -> tuple[list[dict], list[dict]]:
    if guard is None or not guard.guard_keys:
        return due_buffs, []
    due: list[dict] = []
    suppressed: list[dict] = []
    for buff in due_buffs:
        suppress, evidence = guard.suppress_refresh(buff, last_cast, now=now)
        if suppress:
            row = dict(buff)
            row["damage_guard"] = evidence
            suppressed.append(row)
        else:
            due.append(buff)
    return due, suppressed

def read_env_config() -> tuple[dict, dict]:
    buff_path = os.environ.get("METIN2_BUFF_CONFIG")
    combat_path = os.environ.get("METIN2_COMBAT_CONFIG")
    return load_buff_config(Path(buff_path) if buff_path else None), load_combat_config(Path(combat_path) if combat_path else None)


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


def screen_point_from_window_pixel(window, pixel: list[int] | tuple[int, int]) -> tuple[int, int]:
    x = int(pixel[0])
    y = int(pixel[1])
    left, top, right, bottom = window.bbox
    # Client logger pixel_position values are usually window-relative. If they
    # already look like absolute desktop coordinates, leave them unchanged.
    if 0 <= x <= window.width and 0 <= y <= window.height:
        return left + x, top + y
    if left <= x <= right and top <= y <= bottom:
        return x, y
    # Fall back to relative interpretation for small positive points.
    if x >= 0 and y >= 0:
        return left + x, top + y
    raise ValueError(f"invalid target pixel position: {pixel!r}")


def _angle_cross(facing: tuple[float, float], target: tuple[float, float]) -> float:
    """2D cross product sign from facing vector to target vector.

    Screen coordinates have Y downward. Positive means target is visually to the
    right/clockwise of the facing vector; negative means left/counter-clockwise.
    """
    return facing[0] * target[1] - facing[1] * target[0]


def choose_camera_sweep_key_from_vectors(
    facing_tip: tuple[float, float] | None,
    target_dot: tuple[float, float] | None,
    *,
    center: tuple[float, float],
    fallback_key: str,
    deadzone_px: float = 5.0,
) -> tuple[str, dict]:
    """Choose Q/E using minimap target dot relative to facing marker.

    Q/E rotate camera left/right. If the minimap target is already roughly in
    the facing direction or evidence is missing, return the fallback sweep key.
    """
    evidence = {"source": "fallback", "fallback_key": fallback_key}
    if not target_dot:
        evidence["reason"] = "no_minimap_target_dot"
        return fallback_key, evidence
    target_vec = (float(target_dot[0]) - center[0], float(target_dot[1]) - center[1])
    if abs(target_vec[0]) < deadzone_px and abs(target_vec[1]) < deadzone_px:
        evidence.update({"source": "minimap", "reason": "target_near_center", "target_dot": list(target_dot)})
        return fallback_key, evidence
    if facing_tip:
        facing_vec = (float(facing_tip[0]) - center[0], float(facing_tip[1]) - center[1])
    else:
        # If the facing marker cannot be isolated, use a conservative screen-space
        # heuristic: dots left of center -> Q, dots right of center -> E.
        facing_vec = (0.0, -1.0)
    cross = _angle_cross(facing_vec, target_vec)
    if abs(cross) < deadzone_px * 2:
        key = fallback_key
        reason = "target_aligned_or_uncertain"
    else:
        key = "e" if cross > 0 else "q"
        reason = "rotate_toward_minimap_target"
    evidence.update({
        "source": "minimap",
        "reason": reason,
        "target_dot": list(target_dot),
        "facing_tip": list(facing_tip) if facing_tip else None,
        "center": [center[0], center[1]],
        "cross": cross,
        "key": key,
    })
    return key, evidence


def detect_minimap_camera_hint(window, args) -> tuple[str, dict] | None:
    """Try to choose Q/E from the minimap yellow-dot and facing marker.

    This is a hint only: if the yellow target dot or facing marker is not clear,
    callers should keep the existing alternating Q/E sweep.
    """
    if not getattr(args, "minimap_camera_hint", False):
        return None
    try:
        from PIL import Image
    except Exception:
        return None
    out_base = Path(getattr(args, "out", DEFAULT_LOG) or DEFAULT_LOG)
    capture_dir = out_base.parent / "minimap_search"
    capture_dir.mkdir(parents=True, exist_ok=True)
    capture_path = capture_dir / f"minimap_{int(time.time() * 1000)}.jpg"
    try:
        capture_window_image(window, capture_path)
        img = Image.open(capture_path).convert("RGB")
    except Exception:
        return None
    # Learned on MT2Portugalia 1602x1031: minimap circle in top-right.
    left = int(window.width * 0.718)
    top = int(window.height * 0.030)
    right = int(window.width * 0.805)
    bottom = int(window.height * 0.160)
    crop = img.crop((left, top, right, bottom))
    pixels = crop.load()
    w, h = crop.size
    yellow: list[tuple[int, int]] = []
    facing: list[tuple[int, int]] = []
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            # Yellow target/metin hint: bright yellow/orange, not red mob dots.
            if r >= 170 and g >= 135 and b <= 90 and abs(r - g) <= 90:
                yellow.append((x, y))
            # Player/facing marker is typically cyan/green/white in the minimap center.
            if g >= 150 and b >= 110 and r <= 170:
                facing.append((x, y))
    if len(yellow) < int(getattr(args, "minimap_yellow_min_pixels", 3)):
        return None
    center = (w / 2.0, h / 2.0)
    # Prefer yellow blobs away from the center and away from minimap UI labels.
    tx = sum(p[0] for p in yellow) / len(yellow)
    ty = sum(p[1] for p in yellow) / len(yellow)
    facing_tip = None
    if facing:
        # Use the facing pixel farthest from center as a rough tip estimate.
        facing_tip = max(facing, key=lambda p: (p[0] - center[0]) ** 2 + (p[1] - center[1]) ** 2)
    fallback = "q" if int(getattr(args, "_search_cycle", 0)) % 2 == 0 else "e"
    key, evidence = choose_camera_sweep_key_from_vectors(facing_tip, (tx, ty), center=center, fallback_key=fallback)
    evidence.update({"capture": str(capture_path), "crop_box": [left, top, right, bottom], "yellow_pixels": len(yellow), "facing_pixels": len(facing)})
    return key, evidence


def detect_visible_metin_click_point(window, args) -> tuple[int, int, dict] | None:
    """Capture the game window and return a visual Metin click point if YOLO sees one.

    This is only a target-acquisition helper: callers must re-read selected-target
    state before attacking. It prefers visual evidence over blind/random probes.
    """
    if not getattr(args, "visual_target_clicks", False):
        return None
    model_path = Path(getattr(args, "visual_detector_model", DEFAULT_METIN_ONNX) or DEFAULT_METIN_ONNX)
    if not model_path.is_absolute():
        model_path = PROJECT_ROOT / model_path
    if not model_path.exists():
        return None
    out_base = Path(getattr(args, "out", DEFAULT_LOG) or DEFAULT_LOG)
    capture_dir = out_base.parent / "visual_target_search"
    capture_dir.mkdir(parents=True, exist_ok=True)
    capture_path = capture_dir / f"search_{int(time.time() * 1000)}.jpg"
    try:
        capture_window_image(window, capture_path)
        global _ONNX_DETECTOR_CACHE
        if _ONNX_DETECTOR_CACHE is None or getattr(_ONNX_DETECTOR_CACHE, "onnx_path", None) != model_path:
            from metin2_research.onnx_detector import OnnxYoloDetector

            _ONNX_DETECTOR_CACHE = OnnxYoloDetector(model_path, conf_threshold=float(getattr(args, "visual_target_min_confidence", 0.30)))
        detections = _ONNX_DETECTOR_CACHE.detect(capture_path)
    except Exception as exc:
        return None
    detections = [d for d in detections if float(d.get("confidence", 0.0)) >= float(getattr(args, "visual_target_min_confidence", 0.30))]
    if not detections:
        return None
    # Prefer confident, sizeable boxes near the middle/lower-middle of the game view.
    cx = window.width / 2.0
    cy = window.height * 0.52
    def score(det: dict) -> float:
        conf = float(det.get("confidence", 0.0))
        area = float(det.get("width", 0.0)) * float(det.get("height", 0.0))
        dx = abs(float(det.get("x_center", 0.0)) - cx) / max(window.width, 1)
        dy = abs(float(det.get("y_center", 0.0)) - cy) / max(window.height, 1)
        return conf * 10.0 + min(area / 10000.0, 2.0) - dx - dy
    best = max(detections, key=score)
    # Click slightly below center of the detected stone body; this is usually better
    # for selecting the object than the top label/edge.
    rel_x = float(best.get("x_center", 0.0))
    rel_y = float(best.get("ymin", 0.0)) + float(best.get("height", 0.0)) * 0.58
    sx = int(window.bbox[0] + rel_x)
    sy = int(window.bbox[1] + rel_y)
    evidence = {"detection": best, "capture": str(capture_path), "screen_point": [sx, sy], "detections": detections[:5]}
    return sx, sy, evidence


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


def focus_live_window(args) -> None:
    query = getattr(args, "window_query", None) or "MT2Portugalia"
    window = find_window(str(query))
    activate_window(window)


def run_focused_live_command(action, args, navigation_model: dict | None = None) -> float | None:
    focus_live_window(args)
    return run_live_command(action, args, navigation_model)


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
    elif action.command == "search_for_target":
        window = None
        tap_key("tab", 0.06)
        time.sleep(0.05)
        # Prefer seeing the Metin: run screenshot/YOLO target acquisition first.
        # Only fall back to blind click probes if no visual hit is found.
        if getattr(args, "visual_target_clicks", False):
            window = find_window("MT2Portugalia")
            visual = detect_visible_metin_click_point(window, args)
            if visual is not None:
                sx, sy, _evidence = visual
                try:
                    click_at(sx, sy)
                    time.sleep(0.10)
                    return None
                except OSError as exc:
                    # A failed cursor/click should not abort the whole search loop.
                    # Continue into the bounded Tab/blind-probe/patrol sweep so the
                    # operator still gets active target acquisition instead of a
                    # crashed run (observed WinError 0 from SetCursorPos).
                    action.args["visual_click_error"] = str(exc)
        # Operator-approved blind click probes for Yoshy's private-server sandbox:

        # then let the next loop re-read selected-target evidence before attacking.
        if getattr(args, "allow_blind_target_clicks", False):
            if window is None:
                window = find_window("MT2Portugalia")
            points = [
                (0.50, 0.48),
                (0.50, 0.38),
                (0.42, 0.48),
                (0.58, 0.48),
                (0.50, 0.58),
                (0.36, 0.42),
                (0.64, 0.42),
                (0.36, 0.58),
                (0.64, 0.58),
            ]
            idx = int((action.args or {}).get("cycle", 0)) % len(points)
            px, py = points[idx]
            sx = int(window.bbox[0] + window.width * px)
            sy = int(window.bbox[1] + window.height * py)
            try:
                click_at(sx, sy)
                time.sleep(0.08)
            except OSError as exc:
                action.args["blind_click_error"] = str(exc)
        # Bounded local patrol/camera sweep: actively reveal/select nearby mobs/Metins

        hold_key("w", min(float(getattr(args, "target_search_move_seconds", 0.25)), 0.35))
        fallback_sweep_key = "q" if int((action.args or {}).get("cycle", 0)) % 2 == 0 else "e"
        sweep_key = fallback_sweep_key
        minimap_hint = None
        if window is not None:
            setattr(args, "_search_cycle", int((action.args or {}).get("cycle", 0)))
            minimap = detect_minimap_camera_hint(window, args)
            if minimap is not None:
                sweep_key, minimap_hint = minimap
        if minimap_hint is not None:
            action.args["minimap_camera_hint"] = minimap_hint
        hold_key(sweep_key, float(getattr(args, "target_camera_sweep_seconds", 0.16)))
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
    for key in ("space", "w", "a", "s", "d", "q", "e"):
        try:
            key_up(key)
        except Exception:
            pass


def append_state_once(states_visited: list[str], state: str) -> None:
    if not states_visited or states_visited[-1] != state:
        states_visited.append(state)


def attack_nearby_destroy_detected(game, *, locked_metin_vid: int | None, locked_target_alive_cycles: int, min_alive_cycles: int = 4) -> bool:
    """Return True when a tracked Metin likely died rather than merely needing reacquire."""
    return bool(
        locked_metin_vid is not None
        and int(locked_target_alive_cycles) >= int(min_alive_cycles)
        and not getattr(game, "target_vid", None)
        and not (getattr(game, "named_metin_probe", None) or [])
    )


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
    ap.add_argument("--buff-keys", default=None, help="comma-separated buff quickslot keys for buff-only/keeper mode, e.g. f1,f2; overrides config when supplied")
    ap.add_argument("--buff-durations", default=None, help="comma-separated buff durations in seconds; one value applies to all keys")
    ap.add_argument("--buff-refresh-margin-seconds", type=float, default=3.0, help="refresh this many seconds before each configured buff duration")
    ap.add_argument("--assume-mounted", action="store_true", help="Buff-only live override: treat player as mounted even when state detection is flaky; dismount/buff/remount each due buff")
    ap.add_argument("--enable-combat-buffs", action="store_true", help="Opt-in only: allow non-buff-only combat/attack runs to press configured buff keys. Default off so Keep buffs active owns F1/F2.")
    ap.add_argument("--visual-target-clicks", action="store_true", help="attack-nearby live: capture the game window and click YOLO-detected visible Metins before blind click fallback")
    ap.add_argument("--visual-detector-model", default=str(DEFAULT_METIN_ONNX), help="ONNX detector model for visual Metin target clicks")
    ap.add_argument("--visual-target-min-confidence", type=float, default=0.30, help="minimum YOLO confidence for visual target click")
    ap.add_argument("--allow-blind-target-clicks", action="store_true", help="attack-nearby live: operator-approved arbitrary window clicks around the character to try selecting a visible Metin when Tab/state detection fails")
    ap.add_argument("--target-search-move-seconds", type=float, default=0.25, help="attack-nearby live: short bounded patrol step while searching for a target")
    ap.add_argument("--target-click-cooldown-seconds", type=float, default=4.0, help="attack-nearby live: after a left-click target attempt, wait this long before another target click so auto-attack can continue")
    ap.add_argument("--target-camera-sweep-seconds", type=float, default=0.16, help="attack-nearby live: Q/E camera sweep duration while looking for visible Metins")
    ap.add_argument("--minimap-camera-hint", action="store_true", help="attack-nearby live: if a yellow minimap dot is detected, choose Q/E sweep direction from minimap/facing geometry")
    ap.add_argument("--minimap-yellow-min-pixels", type=int, default=3, help="minimum yellow pixels in minimap crop before using minimap camera hint")
    ap.add_argument("--channel-rotate-after-destroy", action="store_true", help="operator-gated workflow: after Metin destroy, spam pickup Z, press X, click next configured channel, then continue searching")
    ap.add_argument("--channel-click-points", default="", help="semicolon-separated channel menu click points, e.g. '0.42,0.35;0.42,0.42' as window fractions or absolute screen pixels")
    ap.add_argument("--pickup-spam-count", type=int, default=12, help="channel-rotate workflow: number of Z pickups after Metin destroy")
    ap.add_argument("--pickup-spam-interval", type=float, default=0.08, help="channel-rotate workflow: seconds between Z pickups")
    ap.add_argument("--channel-menu-delay-seconds", type=float, default=0.35, help="channel-rotate workflow: wait after X opens channel menu before clicking")
    ap.add_argument("--channel-switch-wait-seconds", type=float, default=4.0, help="channel-rotate workflow: wait after clicking channel before searching again")
    ap.add_argument("--potion-key", default="1")
    ap.add_argument("--attack-nearby-mobs", action="store_true", help="Dry-run/live gated option: attack valid nearby mob targets reported by client state when no Metin target is locked")
    ap.add_argument("--buff-only", action="store_true", help="Only run the configured buff keeper; never target, move, or attack")
    ap.add_argument("--buff-damage-guard-keys", default="", help="comma-separated toggle-style buff keys (for example f1) whose timer refresh may be delayed while Metin HP damage still indicates the buff is active")
    ap.add_argument("--live", action="store_true", help="Actually send bounded inputs. Default is dry-run.")
    ap.add_argument("--window-query", default="MT2Portugalia", help="Window title/process query to focus before live keyboard/mouse input")
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

    buff_config, combat_config = read_env_config()
    buff_config = merge_cli_buff_config(
        buff_config,
        buff_config_from_cli(args.buff_keys, args.buff_durations, pre_cast_seconds=args.buff_refresh_margin_seconds),
    )
    args.attack_nearby_mobs = bool(args.attack_nearby_mobs or combat_config.get("attack_nearby_mobs"))
    if args.buff_only:
        args.attack_nearby_mobs = False
        args.session_start_scan = False
    config_buffs_enabled = [row for row in buff_config.get("buffs", []) if row.get("enabled")]
    if config_buffs_enabled and not buff_config.get("use_buff_config"):
        buff_config["use_buff_config"] = True

    args.out.write_text("", encoding="utf-8")
    cfg = CombatConfig()
    last_buff_ts: float | None = None
    last_config_buff_ts: dict[str, float] = {}
    last_auto_target_ts: float = 0.0
    last_target_click_ts: float = 0.0
    locked_target_alive_cycles: int = 0
    reward_seen = False
    channel_rotation_index = 0
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
    buff_damage_guard = BuffDamageGuard(guard_keys=parse_buff_damage_guard_keys(args.buff_damage_guard_keys))

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
        focus_live_window(args)
        time.sleep(0.2)

    try:
        for cycle in itertools.count(1):
            if args.max_cycles > 0 and cycle > args.max_cycles:
                break
            if should_stop(stop_file):
                release_all_keys()
                emit(args.out, {"cycle": cycle, "state": "STOP_REQUESTED", "command": "release_all_keys", "reason": "Graceful stop requested", "run_id": run_id})
                print("[state=STOP_REQUESTED] [action=release_all_keys] [hp=?/?] [sp=?/?] [target=none vid=0 alive=None]", flush=True)
                append_state_once(states_visited, "STOP_REQUESTED")
                return finish("stopped", "stop_file", 0)
            if args.buff_only:
                observed_game = None
                try:
                    observed_game = read_game(args.tsv, json_path=args.json_state, max_age_seconds=args.max_state_age_seconds)
                    last_game = observed_game
                    buff_damage_guard.observe_game(observed_game, now=time.monotonic())
                except RuntimeError:
                    observed_game = None
                now_mono = time.monotonic()
                due_buffs, suppressed_buffs = apply_buff_damage_guard(
                    due_buff_actions(buff_config, last_config_buff_ts, now=now_mono),
                    last_config_buff_ts,
                    now=now_mono,
                    guard=buff_damage_guard,
                )
                for suppressed in suppressed_buffs:
                    emit(args.out, {"cycle": cycle, "dry_run": not args.live, "state": "BUFF_REFRESH_SUPPRESSED_DAMAGE_GUARD", "command": "skip_toggle_buff_refresh", "reason": "damage guard kept a toggle-style buff from being pressed while duration/damage evidence says it may still be active", "buff": suppressed, "run_id": run_id})
                    append_state_once(states_visited, "BUFF_REFRESH_SUPPRESSED_DAMAGE_GUARD")
                if due_buffs:
                    restore_mount_after_buffs = False
                    for buff in due_buffs:
                        mounted_state = None
                        mounted_source = "state"
                        if args.live:
                            if args.assume_mounted:
                                mounted_state = True
                                mounted_source = "assume_mounted"
                            else:
                                try:
                                    game = read_game(args.tsv, json_path=args.json_state, max_age_seconds=args.max_state_age_seconds)
                                    last_game = game
                                    mounted_state = mounted_state_from_game(game)
                                except RuntimeError as exc:
                                    emit(args.out, {"cycle": cycle, "state": "BUFF_MOUNT_STATE_UNKNOWN", "command": "skip_buff_until_fresh_state", "reason": str(exc), "buff": buff, "run_id": run_id})
                                    time.sleep(ATTACK_CYCLE_SLEEP)
                                    continue
                                if mounted_state is None:
                                    emit(args.out, {"cycle": cycle, "state": "BUFF_MOUNT_STATE_UNKNOWN", "command": "skip_buff_until_mounted_field_present", "reason": "fresh state did not include player.mounted; refusing to blindly ctrl+g or buff while horse state is unknown", "buff": buff, "run_id": run_id})
                                    time.sleep(ATTACK_CYCLE_SLEEP)
                                    continue
                            if mounted_state is True:
                                restore_mount_after_buffs = True
                                append_state_once(states_visited, "DISMOUNT_FOR_BUFF")
                                dismount_result = press_buff_key_via_key_macro(key="ctrl+g", args=args, stop_file=stop_file, run_id=run_id)
                                emit(args.out, {"cycle": cycle, "dry_run": False, "state": "DISMOUNT_FOR_BUFF", "command": "ctrl_g_dismount_before_each_buff", "reason": "player treated as mounted immediately before buff; buffs do not apply while mounted", "buff": buff, "mounted_source": mounted_source, "key_macro": dismount_result, "run_id": run_id})
                                time.sleep(1.0)
                        emit(
                            args.out,
                            {
                                "cycle": cycle,
                                "dry_run": not args.live,
                                "state": "BUFF_DUE",
                                "command": "press_buff",
                                "reason": "buff-only mode: configured buff due; client-state freshness is not required for key keepalive",
                                "buff": buff,
                                "mounted_state": mounted_state,
                                "mounted_source": mounted_source,
                                "run_id": run_id,
                            },
                        )
                        print(f"[state=BUFF_DUE] [action=press_buff] [buff={buff['key']}] [mounted={mounted_state}] [dry_run={not args.live}]", flush=True)
                        append_state_once(states_visited, "BUFF_DUE")
                        live_key_result = None
                        if args.live:
                            live_key_result = press_buff_key_via_key_macro(key=buff["key"], args=args, stop_file=stop_file, run_id=run_id)
                        if live_key_result:
                            emit(
                                args.out,
                                {
                                    "cycle": cycle,
                                    "dry_run": False,
                                    "state": "BUFF_KEY_SENT",
                                    "command": "elevated_key_macro_press",
                                    "reason": "buff-only mode used the same elevated robust key sender as Press F1/F2 once LIVE",
                                    "buff": buff,
                                    "mounted_state": mounted_state,
                                    "mounted_source": mounted_source,
                                    "key_macro": live_key_result,
                                    "run_id": run_id,
                                },
                            )
                        pressed_at = time.monotonic()
                        last_config_buff_ts[buff["key"]] = pressed_at
                        buff_damage_guard.note_buff_pressed(buff["key"], now=pressed_at)
                        if args.live and args.assume_mounted:
                            remount_result = press_buff_key_via_key_macro(key="ctrl+g", args=args, stop_file=stop_file, run_id=run_id)
                            emit(args.out, {"cycle": cycle, "dry_run": False, "state": "REMOUNT_AFTER_BUFF", "command": "ctrl_g_remount_after_each_assumed_mounted_buff", "reason": "assume-mounted mode restores horse state after each individual buff so the next buff starts from a known mounted state", "buff": buff, "mounted_source": mounted_source, "key_macro": remount_result, "run_id": run_id})
                            append_state_once(states_visited, "REMOUNT_AFTER_BUFF")
                            time.sleep(0.8)
                        time.sleep(1.0 if args.live else 0.35)
                    if args.live and restore_mount_after_buffs and not args.assume_mounted:
                        mounted_after_buffs = None
                        try:
                            game = read_game(args.tsv, json_path=args.json_state, max_age_seconds=args.max_state_age_seconds)
                            last_game = game
                            mounted_after_buffs = mounted_state_from_game(game)
                        except RuntimeError as exc:
                            emit(args.out, {"cycle": cycle, "state": "REMOUNT_AFTER_BUFF_SKIPPED", "command": "skip_remount_state_stale", "reason": str(exc), "run_id": run_id})
                        if mounted_after_buffs is False:
                            remount_result = press_buff_key_via_key_macro(key="ctrl+g", args=args, stop_file=stop_file, run_id=run_id)
                            emit(args.out, {"cycle": cycle, "dry_run": False, "state": "REMOUNT_AFTER_BUFF", "command": "ctrl_g_remount_after_all_due_buffs", "reason": "restoring horse state after all due buffs", "mounted_after_buffs": mounted_after_buffs, "key_macro": remount_result, "run_id": run_id})
                            append_state_once(states_visited, "REMOUNT_AFTER_BUFF")
                            time.sleep(0.8)
                        else:
                            emit(args.out, {"cycle": cycle, "dry_run": False, "state": "REMOUNT_AFTER_BUFF_SKIPPED", "command": "already_mounted_or_unknown_after_buffs", "reason": "not toggling ctrl+g because player is already mounted or state is unknown after due buffs", "mounted_after_buffs": mounted_after_buffs, "run_id": run_id})
                    time.sleep(ATTACK_CYCLE_SLEEP)
                    continue
                stale_reason = None
                try:
                    game = read_game(args.tsv, json_path=args.json_state, max_age_seconds=args.max_state_age_seconds)
                    last_game = game
                except RuntimeError as exc:
                    game = None
                    stale_reason = str(exc)
                append_state_once(states_visited, "BUFF_KEEPALIVE_IDLE")
                emit(
                    args.out,
                    {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": "BUFF_KEEPALIVE_IDLE",
                        "command": "wait_for_next_buff_due",
                        "reason": "buff-only mode: no configured buff is due; not targeting, moving, or attacking",
                        "state_feed_warning": stale_reason,
                        "hp": getattr(game, "hp", None),
                        "max_hp": getattr(game, "max_hp", None),
                        "sp": getattr(game, "sp", None),
                        "max_sp": getattr(game, "max_sp", None),
                        "target_vid": getattr(game, "target_vid", None),
                        "target_name": getattr(game, "target_name", None),
                        "target_alive": getattr(game, "target_alive", None),
                        "run_id": run_id,
                    },
                )
                print(
                    format_structured_log_line(
                        state="BUFF_KEEPALIVE_IDLE",
                        action="wait_for_next_buff_due",
                        hp=getattr(game, "hp", None),
                        max_hp=getattr(game, "max_hp", None),
                        sp=getattr(game, "sp", None),
                        max_sp=getattr(game, "max_sp", None),
                        target_name=getattr(game, "target_name", None),
                        target_vid=getattr(game, "target_vid", None),
                        target_alive=getattr(game, "target_alive", None),
                    ),
                    flush=True,
                )
                time.sleep(ATTACK_CYCLE_SLEEP)
                continue

            try:
                game = read_game(args.tsv, json_path=args.json_state, max_age_seconds=args.max_state_age_seconds)
                last_game = game
            except RuntimeError as exc:
                emit(args.out, {"cycle": cycle, "state": "WAIT_FRESH_STATE", "command": "pause", "reason": str(exc), "run_id": run_id})
                time.sleep(0.5)
                continue
            buff_damage_guard.observe_game(game, now=time.monotonic())
            # Buff key ownership: buff-only keepalive owns F1/F2 by default.
            # Normal attack/practice runs must not consume configured buffs unless
            # explicitly opted in, so Select/attack nearby can coexist with Keep buffs active.
            if args.buff_only or args.enable_combat_buffs:
                now_mono = time.monotonic()
                due_buffs, suppressed_buffs = apply_buff_damage_guard(
                    due_buff_actions(buff_config, last_config_buff_ts, now=now_mono),
                    last_config_buff_ts,
                    now=now_mono,
                    guard=buff_damage_guard,
                )
            else:
                due_buffs, suppressed_buffs = [], []
            for suppressed in suppressed_buffs:
                emit(args.out, {"cycle": cycle, "dry_run": not args.live, "state": "BUFF_REFRESH_SUPPRESSED_DAMAGE_GUARD", "command": "skip_toggle_buff_refresh", "reason": "damage guard kept a toggle-style buff from being pressed while duration/damage evidence says it may still be active", "buff": suppressed, "run_id": run_id})
                append_state_once(states_visited, "BUFF_REFRESH_SUPPRESSED_DAMAGE_GUARD")
            if due_buffs:
                buff = due_buffs[0]
                emit(
                    args.out,
                    {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": "BUFF_DUE",
                        "command": "press_buff",
                        "reason": "buff config due/overdue; honoring enabled flag, interval_seconds, and pre_cast_seconds",
                        "buff": buff,
                        "run_id": run_id,
                    },
                )
                print(f"[state=BUFF_DUE] [action=press_buff] [buff={buff['key']}] [dry_run={not args.live}]", flush=True)
                append_state_once(states_visited, "BUFF_DUE")
                if args.live:
                    focus_live_window(args)
                    tap_key(buff["key"], 0.08)
                pressed_at = time.monotonic()
                last_config_buff_ts[buff["key"]] = pressed_at
                buff_damage_guard.note_buff_pressed(buff["key"], now=pressed_at)
                time.sleep(ATTACK_CYCLE_SLEEP)
                continue

            if args.buff_only:
                append_state_once(states_visited, "BUFF_KEEPALIVE_IDLE")
                emit(
                    args.out,
                    {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": "BUFF_KEEPALIVE_IDLE",
                        "command": "wait_for_next_buff_due",
                        "reason": "buff-only mode: no configured buff is due; not targeting, moving, or attacking",
                        "hp": game.hp,
                        "max_hp": game.max_hp,
                        "sp": game.sp,
                        "max_sp": game.max_sp,
                        "target_vid": game.target_vid,
                        "target_name": game.target_name,
                        "target_alive": game.target_alive,
                        "run_id": run_id,
                    },
                )
                print(
                    format_structured_log_line(
                        state="BUFF_KEEPALIVE_IDLE",
                        action="wait_for_next_buff_due",
                        hp=game.hp,
                        max_hp=game.max_hp,
                        sp=game.sp,
                        max_sp=game.max_sp,
                        target_name=game.target_name,
                        target_vid=game.target_vid,
                        target_alive=game.target_alive,
                    ),
                    flush=True,
                )
                time.sleep(ATTACK_CYCLE_SLEEP)
                continue

            if args.attack_nearby_mobs and attack_nearby_destroy_detected(
                game,
                locked_metin_vid=locked_metin_vid,
                locked_target_alive_cycles=locked_target_alive_cycles,
            ):
                action = CombatAction(
                    "VERIFY_DESTROYED",
                    "stop_success",
                    "tracked Metin target disappeared after sustained selected-target attack and named probe is gone; treat as destroyed and pick up drops",
                    success=True,
                    args={"locked_target_alive_cycles": locked_target_alive_cycles},
                )
                append_state_once(states_visited, action.state)
                emit(
                    args.out,
                    {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": action.state,
                        "command": action.command,
                        "reason": action.reason,
                        "success": True,
                        "coord": game.player_coord,
                        "hp": game.hp,
                        "max_hp": game.max_hp,
                        "sp": game.sp,
                        "max_sp": game.max_sp,
                        "locked_metin_vid": locked_metin_vid,
                        "locked_metin_name": locked_metin_name,
                        "target_vid": game.target_vid,
                        "target_name": game.target_name,
                        "target_alive": game.target_alive,
                        "args": action.args,
                        "run_id": run_id,
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
                    ),
                    flush=True,
                )
                if args.live:
                    run_pickup_spam_sequence(args)
                emit(
                    args.out,
                    {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": "PICKUP_AFTER_DESTROY",
                        "command": "spam_z_pickup",
                        "reason": "Metin destroyed; pick up drops before searching/rotating",
                        "pickup_spam_count": args.pickup_spam_count,
                        "run_id": run_id,
                    },
                )
                if args.channel_rotate_after_destroy:
                    rotation_event = {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": "CHANNEL_ROTATE_AFTER_DESTROY",
                        "command": "change_channel_after_pickup",
                        "reason": "Metin destroyed and drops picked up; operator workflow now presses X and clicks next channel",
                        "channel_rotation_index": channel_rotation_index,
                        "channel_click_points": args.channel_click_points,
                        "run_id": run_id,
                    }
                    if args.live:
                        try:
                            window = find_window(getattr(args, "window_query", None) or "MT2Portugalia")
                            activate_window(window)
                            tap_key("x", 0.06)
                            time.sleep(max(0.0, float(getattr(args, "channel_menu_delay_seconds", 0.35))))
                            points = parse_channel_click_points(getattr(args, "channel_click_points", None))
                            if not points:
                                raise ValueError("--channel-click-points is required when --channel-rotate-after-destroy is enabled")
                            point = points[channel_rotation_index % len(points)]
                            sx, sy = channel_click_screen_point(window, point)
                            click_at(sx, sy)
                            time.sleep(max(0.0, float(getattr(args, "channel_switch_wait_seconds", 4.0))))
                            rotation_event["result"] = {"channel_index": channel_rotation_index, "point": [point[0], point[1]], "screen_point": [sx, sy]}
                        except Exception as exc:
                            rotation_event["error"] = str(exc)
                            emit(args.out, rotation_event)
                            print(f"[state=CHANNEL_ROTATE_FAILED] [action=change_channel_after_pickup] [reason={exc}]", flush=True)
                            return finish("aborted", "channel_rotate_failed", 2)
                    emit(args.out, rotation_event)
                    print("[state=CHANNEL_ROTATE_AFTER_DESTROY] [action=change_channel_after_pickup] [hp=?/?] [sp=?/?] [target=none vid=0 alive=None]", flush=True)
                    channel_rotation_index += 1
                locked_metin_vid = None
                locked_metin_name = None
                locked_target_alive_cycles = 0
                args.metin_vid = None
                args.metin_name = None
                args.metin_x = None
                args.metin_y = None
                args.metin_coord_source = None
                last_target_click_ts = 0.0
                reward_seen = False
                time.sleep(ATTACK_CYCLE_SLEEP)
                continue

            target_name_text = str(game.target_name or "")
            if args.attack_nearby_mobs and game.target_vid and game.target_alive is not False and target_name_text:
                if "metin" in target_name_text.lower():
                    locked_metin_vid = game.target_vid
                    locked_metin_name = game.target_name or locked_metin_name
                    args.metin_vid = game.target_vid
                    args.metin_name = locked_metin_name
                monitor_action = CombatAction(
                    "AUTO_ATTACKING_TARGET",
                    "hold_space",
                    "attack-nearby-mobs has a selected alive target; send bounded Space pulses to actually start/continue attacking, without repeated mouse clicks",
                    args={"target_vid": game.target_vid, "target_name": game.target_name, "target_alive": game.target_alive},
                )
                append_state_once(states_visited, monitor_action.state)
                locked_target_alive_cycles += 1
                emit(
                    args.out,
                    {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": monitor_action.state,
                        "command": monitor_action.command,
                        "reason": monitor_action.reason,
                        "success": False,
                        "coord": game.player_coord,
                        "hp": game.hp,
                        "max_hp": game.max_hp,
                        "sp": game.sp,
                        "max_sp": game.max_sp,
                        "target_vid": game.target_vid,
                        "target_name": game.target_name,
                        "target_alive": game.target_alive,
                        "run_id": run_id,
                    },
                )
                print(
                    format_structured_log_line(
                        state=monitor_action.state,
                        action=monitor_action.command,
                        hp=game.hp,
                        max_hp=game.max_hp,
                        sp=game.sp,
                        max_sp=game.max_sp,
                        target_name=game.target_name,
                        target_vid=game.target_vid,
                        target_alive=game.target_alive,
                    ),
                    flush=True,
                )
                if args.live:
                    run_focused_live_command(monitor_action, args, navigation_model)
                time.sleep(ATTACK_CYCLE_SLEEP)
                continue

            click_cooldown = max(0.0, float(getattr(args, "target_click_cooldown_seconds", 4.0)))
            if args.attack_nearby_mobs and last_target_click_ts and time.monotonic() - last_target_click_ts < click_cooldown:
                wait_action = CombatAction(
                    "AUTO_ATTACK_OBSERVE",
                    "wait_after_left_click_target",
                    "recent left-click target attempt may have started Metin2 auto-attack; wait before retargeting to avoid repeated clicks",
                    args={"seconds_since_click": round(time.monotonic() - last_target_click_ts, 3), "cooldown_seconds": click_cooldown},
                )
                append_state_once(states_visited, wait_action.state)
                emit(
                    args.out,
                    {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": wait_action.state,
                        "command": wait_action.command,
                        "reason": wait_action.reason,
                        "success": False,
                        "coord": game.player_coord,
                        "hp": game.hp,
                        "max_hp": game.max_hp,
                        "sp": game.sp,
                        "max_sp": game.max_sp,
                        "target_vid": game.target_vid,
                        "target_name": game.target_name,
                        "target_alive": game.target_alive,
                        "args": wait_action.args,
                        "run_id": run_id,
                    },
                )
                print(
                    format_structured_log_line(
                        state=wait_action.state,
                        action=wait_action.command,
                        hp=game.hp,
                        max_hp=game.max_hp,
                        sp=game.sp,
                        max_sp=game.max_sp,
                        target_name=game.target_name,
                        target_vid=game.target_vid,
                        target_alive=game.target_alive,
                    ),
                    flush=True,
                )
                time.sleep(ATTACK_CYCLE_SLEEP)
                continue

            mob_action = choose_nearby_mob_attack(game, enabled=args.attack_nearby_mobs)
            if mob_action is not None:
                append_state_once(states_visited, mob_action.state)
                emit(
                    args.out,
                    {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": mob_action.state,
                        "command": mob_action.command,
                        "reason": mob_action.reason,
                        "success": False,
                        "coord": game.player_coord,
                        "hp": game.hp,
                        "max_hp": game.max_hp,
                        "sp": game.sp,
                        "max_sp": game.max_sp,
                        "target_vid": game.target_vid,
                        "target_name": game.target_name,
                        "target_alive": game.target_alive,
                        "nearby_entities": game.nearby_entities,
                        "args": mob_action.args,
                        "run_id": run_id,
                    },
                )
                print(
                    format_structured_log_line(
                        state=mob_action.state,
                        action=mob_action.command,
                        hp=game.hp,
                        max_hp=game.max_hp,
                        sp=game.sp,
                        max_sp=game.max_sp,
                        target_name=game.target_name,
                        target_vid=game.target_vid,
                        target_alive=game.target_alive,
                    ),
                    flush=True,
                )
                if args.live:
                    run_focused_live_command(mob_action, args, navigation_model)
                time.sleep(ATTACK_CYCLE_SLEEP)
                continue

            now_for_target = time.monotonic()
            auto_target_action = choose_mouse_target_action(game, enabled=args.attack_nearby_mobs)
            if auto_target_action is not None and now_for_target - last_auto_target_ts >= 0.75:
                append_state_once(states_visited, auto_target_action.state)
                last_auto_target_ts = now_for_target
                emit(
                    args.out,
                    {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": auto_target_action.state,
                        "command": auto_target_action.command,
                        "reason": auto_target_action.reason,
                        "success": False,
                        "coord": game.player_coord,
                        "hp": game.hp,
                        "max_hp": game.max_hp,
                        "sp": game.sp,
                        "max_sp": game.max_sp,
                        "target_vid": game.target_vid,
                        "target_name": game.target_name,
                        "target_alive": game.target_alive,
                        "nearby_entities": game.nearby_entities,
                        "args": auto_target_action.args,
                        "run_id": run_id,
                    },
                )
                print(
                    format_structured_log_line(
                        state=auto_target_action.state,
                        action=auto_target_action.command,
                        hp=game.hp,
                        max_hp=game.max_hp,
                        sp=game.sp,
                        max_sp=game.max_sp,
                        target_name=game.target_name,
                        target_vid=game.target_vid,
                        target_alive=game.target_alive,
                    ),
                    flush=True,
                )
                if args.live:
                    window = find_window("MT2Portugalia")
                    activate_window(window)
                    sx, sy = screen_point_from_window_pixel(window, auto_target_action.args["pixel_position"])
                    click_at(sx, sy)
                    last_target_click_ts = time.monotonic()
                time.sleep(ATTACK_CYCLE_SLEEP)
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
                    if args.allow_selected_vid_without_exact_coords or args.attack_nearby_mobs:
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

            if args.attack_nearby_mobs and locked_metin_vid is None and args.metin_x is None and args.metin_y is None:
                search_action = CombatAction(
                    "SEARCH_FOR_TARGET",
                    "search_for_target",
                    "attack-nearby-mobs enabled; no valid mob/Metin target or visible entity yet, so actively press Tab and do a short bounded patrol/camera sweep",
                    success=False,
                    args={"cycle": cycle, "sequence": ["tab", "visual_yolo_click" if args.visual_target_clicks else "no_visual_click", "blind_click_probe" if args.allow_blind_target_clicks else "no_blind_click", "w_short", "q_or_e_sweep"], "visual_target_clicks": bool(args.visual_target_clicks), "allow_blind_target_clicks": bool(args.allow_blind_target_clicks), "max_move_seconds": min(args.target_search_move_seconds, 0.35)},
                )
                emit(
                    args.out,
                    {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": search_action.state,
                        "command": search_action.command,
                        "reason": search_action.reason,
                        "success": False,
                        "coord": game.player_coord,
                        "hp": game.hp,
                        "max_hp": game.max_hp,
                        "sp": game.sp,
                        "max_sp": game.max_sp,
                        "target_vid": game.target_vid,
                        "target_name": game.target_name,
                        "target_alive": game.target_alive,
                        "nearby_entities": game.nearby_entities,
                        "args": search_action.args,
                        "run_id": run_id,
                    },
                )
                print(
                    format_structured_log_line(
                        state=search_action.state,
                        action=search_action.command,
                        hp=game.hp,
                        max_hp=game.max_hp,
                        sp=game.sp,
                        max_sp=game.max_sp,
                        target_name=game.target_name,
                        target_vid=game.target_vid,
                        target_alive=game.target_alive,
                    ),
                    flush=True,
                )
                append_state_once(states_visited, search_action.state)
                if args.live:
                    run_focused_live_command(search_action, args, navigation_model)
                    if args.visual_target_clicks or args.allow_blind_target_clicks:
                        last_target_click_ts = time.monotonic()
                time.sleep(ATTACK_CYCLE_SLEEP)
                continue

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
                if action.success and args.channel_rotate_after_destroy:
                    rotation_event = {
                        "cycle": cycle,
                        "dry_run": not args.live,
                        "state": "CHANNEL_ROTATE_AFTER_DESTROY",
                        "command": "pickup_then_change_channel",
                        "reason": "Metin destroyed; operator workflow is spam Z pickup, press X, left-click next channel, then target another Metin",
                        "channel_rotation_index": channel_rotation_index,
                        "pickup_spam_count": args.pickup_spam_count,
                        "channel_click_points": args.channel_click_points,
                        "run_id": run_id,
                    }
                    if args.live:
                        try:
                            rotation_event["result"] = run_channel_rotation_sequence(args, channel_index=channel_rotation_index)
                        except Exception as exc:
                            rotation_event["error"] = str(exc)
                            emit(args.out, rotation_event)
                            print(f"[state=CHANNEL_ROTATE_FAILED] [action=pickup_then_change_channel] [reason={exc}]", flush=True)
                            return finish("aborted", "channel_rotate_failed", 2)
                    emit(args.out, rotation_event)
                    print("[state=CHANNEL_ROTATE_AFTER_DESTROY] [action=pickup_then_change_channel] [hp=?/?] [sp=?/?] [target=none vid=0 alive=None]", flush=True)
                    channel_rotation_index += 1
                    locked_metin_vid = None
                    locked_metin_name = None
                    args.metin_vid = None
                    args.metin_name = None
                    args.metin_x = None
                    args.metin_y = None
                    args.metin_coord_source = None
                    last_target_click_ts = 0.0
                    reward_seen = False
                    time.sleep(ATTACK_CYCLE_SLEEP)
                    continue
                if action.success:
                    if args.attack_nearby_mobs:
                        pickup_event = {
                            "cycle": cycle,
                            "dry_run": not args.live,
                            "state": "PICKUP_AFTER_DESTROY",
                            "command": "spam_z_pickup",
                            "reason": "Metin destroyed; spam Z pickup before continuing target search",
                            "pickup_spam_count": args.pickup_spam_count,
                            "run_id": run_id,
                        }
                        if args.live:
                            pickup_event["result"] = run_pickup_spam_sequence(args)
                        emit(args.out, pickup_event)
                        print("[state=PICKUP_AFTER_DESTROY] [action=spam_z_pickup] [hp=?/?] [sp=?/?] [target=none vid=0 alive=None]", flush=True)
                        locked_metin_vid = None
                        locked_metin_name = None
                        locked_target_alive_cycles = 0
                        args.metin_vid = None
                        args.metin_name = None
                        args.metin_x = None
                        args.metin_y = None
                        args.metin_coord_source = None
                        last_target_click_ts = 0.0
                        reward_seen = False
                        time.sleep(ATTACK_CYCLE_SLEEP)
                        continue
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
                    new_buff_ts = run_focused_live_command(action, args, navigation_model)
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
        return finish("max_cycles", "max_cycles", max_cycles_exit_code(live=args.live, buff_only=args.buff_only))
    finally:
        mover.release_all()


if __name__ == "__main__":
    raise SystemExit(main())
