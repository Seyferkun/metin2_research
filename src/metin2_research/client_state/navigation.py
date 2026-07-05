from __future__ import annotations

import math
from typing import Any

from .schema import ClientState

MOVEMENT_KEYS = ("w", "a", "s", "d")


def _coord_from_state(state: ClientState) -> list[int]:
    if state.game is None or state.game.player_coord is None:
        raise ValueError("ClientState has no game.player_coord from client TSV state")
    if len(state.game.player_coord) < 3:
        raise ValueError("ClientState.game.player_coord must contain x, y, z")
    return [int(state.game.player_coord[0]), int(state.game.player_coord[1]), int(state.game.player_coord[2])]


def movement_delta(before: ClientState, after: ClientState, *, key: str, seconds: float, moved_threshold: float = 20.0) -> dict[str, Any]:
    """Return a movement observation derived only from client TSV game coordinates."""

    if key not in MOVEMENT_KEYS:
        raise ValueError(f"unsupported movement key: {key}")
    start = _coord_from_state(before)
    end = _coord_from_state(after)
    delta = [end[0] - start[0], end[1] - start[1], end[2] - start[2]]
    distance_xy = round(math.hypot(delta[0], delta[1]), 3)
    return {
        "key": key,
        "seconds": float(seconds),
        "map_name": after.game.map_name if after.game else None,
        "from": start,
        "to": end,
        "delta": delta,
        "distance_xy": distance_xy,
        "moved": distance_xy >= moved_threshold,
        "hp": after.game.hp if after.game else None,
        "max_hp": after.game.max_hp if after.game else None,
        "target_vid": after.game.target_vid if after.game else None,
        "target_name": after.game.target_name if after.game else None,
        "source": "client_python_tsv",
    }


def summarize_movement_observations(observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a compact per-key navigation model from TSV movement observations."""

    model: dict[str, dict[str, Any]] = {}
    for key in MOVEMENT_KEYS:
        rows = [obs for obs in observations if obs.get("key") == key]
        if not rows:
            continue
        moved_rows = [obs for obs in rows if obs.get("moved")]
        dxs = [float(obs.get("delta", [0, 0, 0])[0]) for obs in rows]
        dys = [float(obs.get("delta", [0, 0, 0])[1]) for obs in rows]
        distances = [float(obs.get("distance_xy", 0.0)) for obs in rows]
        model[key] = {
            "samples": len(rows),
            "moved_samples": len(moved_rows),
            "mean_delta_xy": [round(sum(dxs) / len(dxs), 3), round(sum(dys) / len(dys), 3)],
            "mean_distance_xy": round(sum(distances) / len(distances), 3),
        }
    return model


def _proportional_hold(distance: float, key_data: dict[str, Any], *, calibrated_hold_seconds: float = 0.6, max_hold: float = 0.45, min_hold: float = 0.08) -> float:
    mean_distance = float(key_data.get("mean_distance_xy") or 0.0)
    if mean_distance <= 0 or calibrated_hold_seconds <= 0:
        return round(max(min_hold, min(max_hold, 0.12)), 3)
    units_per_second = mean_distance / calibrated_hold_seconds
    desired = (float(distance) / units_per_second) * 0.7
    return round(max(float(min_hold), min(float(max_hold), desired)), 3)


def choose_movement_steps(
    current: tuple[int, int],
    target: tuple[int, int],
    model: dict[str, dict[str, Any]],
    *,
    max_hold: float = 0.45,
    prefer_secondary: bool = False,
) -> list[tuple[str, float]]:
    """Choose one or two bounded movement steps toward target.

    WASD deltas are often diagonal in client coordinates. Use the best key for
    the full displacement, then optionally add a second key that meaningfully
    improves the residual vector after the primary step.
    """

    if not model:
        raise ValueError("navigation model is empty; calibrate movement first")
    dx = float(target[0] - current[0])
    dy = float(target[1] - current[1])
    distance = math.hypot(dx, dy)
    if distance < 50:
        return []
    ranked: list[tuple[float, str]] = []
    for key in MOVEMENT_KEYS:
        entry = model.get(key)
        if not entry:
            continue
        mdx, mdy = entry.get("mean_delta_xy", [0.0, 0.0])[:2]
        projected = math.hypot(dx - float(mdx), dy - float(mdy))
        gain = distance - projected
        ranked.append((gain, key))
    if not ranked:
        raise ValueError("navigation model has no usable movement keys")
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if prefer_secondary and len(ranked) > 1:
        ranked[0], ranked[1] = ranked[1], ranked[0]
    primary_gain, primary_key = ranked[0]
    primary_entry = model[primary_key]
    primary_delta = primary_entry.get("mean_delta_xy", [0.0, 0.0])[:2]
    residual_dx = dx - float(primary_delta[0])
    residual_dy = dy - float(primary_delta[1])
    residual_dist = math.hypot(residual_dx, residual_dy)
    steps: list[tuple[str, float]] = [(primary_key, _proportional_hold(distance, primary_entry, max_hold=max_hold))]
    for _gain, secondary_key in ranked[1:]:
        secondary_entry = model[secondary_key]
        sec_dx, sec_dy = secondary_entry.get("mean_delta_xy", [0.0, 0.0])[:2]
        after_secondary = math.hypot(residual_dx - float(sec_dx), residual_dy - float(sec_dy))
        if after_secondary < residual_dist * 0.85 or residual_dist - after_secondary >= 50.0:
            steps.append((secondary_key, _proportional_hold(residual_dist, secondary_entry, max_hold=max_hold)))
            break
    return steps


def choose_waypoint(player_pos: tuple[int, int] | list[int], target_pos: tuple[int, int] | list[int], _nav_model_summary: dict[str, Any] | None = None) -> tuple[int, int]:
    """Return an intermediate axis-alignment waypoint to avoid diagonal local minima."""
    dx = int(target_pos[0]) - int(player_pos[0])
    dy = int(target_pos[1]) - int(player_pos[1])
    if abs(dx) > abs(dy) * 1.5:
        return int(target_pos[0]), int(player_pos[1])
    if abs(dy) > abs(dx) * 1.5:
        return int(player_pos[0]), int(target_pos[1])
    return int(target_pos[0]), int(target_pos[1])


class OnlineNavModel:
    """Calibrated navigation model with live per-key adaptation.

    The calibrated WASD transform is camera/facing/session-sensitive. This
    wrapper starts with the calibrated estimates, then records observed
    before/after deltas from live combat steps and switches toward those live
    estimates once enough samples exist.
    """

    LIVE_WEIGHT = 0.7
    MIN_LIVE_SAMPLES = 3
    SIGN_FLIP_THRESHOLD = 0.0

    def __init__(self, calibrated_model: dict[str, dict[str, Any]] | None):
        self.calibrated: dict[str, dict[str, Any]] = calibrated_model or {}
        self.live_obs: dict[str, list[tuple[float, float, float]]] = {key: [] for key in self.calibrated}

    def record_step(self, key: str, before: tuple[int, int] | list[int], after: tuple[int, int] | list[int]) -> None:
        if key not in self.live_obs or len(before) < 2 or len(after) < 2:
            return
        dx = float(after[0]) - float(before[0])
        dy = float(after[1]) - float(before[1])
        dist = math.hypot(dx, dy)
        if dist < 30.0:
            return
        self.live_obs[key].append((dx, dy, dist))
        if len(self.live_obs[key]) > 8:
            self.live_obs[key].pop(0)

    def decay_suspect_key(self, key: str, factor: float = 0.5) -> None:
        obs = self.live_obs.get(key)
        if not obs:
            return
        keep = max(1, int(len(obs) * float(factor)))
        self.live_obs[key] = obs[-keep:]

    def reset_key(self, key: str) -> None:
        if key in self.live_obs:
            self.live_obs[key] = []

    def get_delta(self, key: str) -> tuple[float, float]:
        obs = self.live_obs.get(key, [])
        cal = self.calibrated.get(key, {})
        cal_dx, cal_dy = cal.get("mean_delta_xy", [0.0, 0.0])[:2]
        cal_dx = float(cal_dx)
        cal_dy = float(cal_dy)
        if len(obs) < self.MIN_LIVE_SAMPLES:
            return cal_dx, cal_dy
        live_dx = sum(row[0] for row in obs) / len(obs)
        live_dy = sum(row[1] for row in obs) / len(obs)
        dot = cal_dx * live_dx + cal_dy * live_dy
        if dot < self.SIGN_FLIP_THRESHOLD:
            return live_dx, live_dy
        w = self.LIVE_WEIGHT
        return (1.0 - w) * cal_dx + w * live_dx, (1.0 - w) * cal_dy + w * live_dy

    def get_distance_per_sec(self, key: str) -> float:
        obs = self.live_obs.get(key, [])
        if len(obs) >= self.MIN_LIVE_SAMPLES:
            return (sum(row[2] for row in obs) / len(obs)) / 0.6
        return float(self.calibrated.get(key, {}).get("mean_distance_xy", 270.0)) / 0.6

    def _hold(self, distance: float, key: str, max_hold: float) -> float:
        ups = self.get_distance_per_sec(key)
        if ups <= 0:
            return round(max(0.08, min(max_hold, 0.12)), 3)
        return round(max(0.08, min(float(distance) / ups * 0.7, max_hold)), 3)

    def choose_steps(
        self,
        player_pos: tuple[int, int] | list[int],
        target_pos: tuple[int, int] | list[int],
        *,
        max_hold: float = 0.45,
        prefer_secondary: bool = False,
    ) -> list[tuple[set[str], float]]:
        if not self.calibrated:
            raise ValueError("navigation model is empty; calibrate movement first")
        dx = float(target_pos[0]) - float(player_pos[0])
        dy = float(target_pos[1]) - float(player_pos[1])
        distance = math.hypot(dx, dy)
        if distance < 50.0:
            return []
        ranked: list[tuple[float, str]] = []
        for key in self.calibrated:
            mdx, mdy = self.get_delta(key)
            gain = distance - math.hypot(dx - mdx, dy - mdy)
            ranked.append((gain, key))
        if not ranked:
            raise ValueError("navigation model has no usable movement keys")
        ranked.sort(key=lambda item: (-item[0], item[1]))
        if prefer_secondary and len(ranked) > 1:
            ranked[0], ranked[1] = ranked[1], ranked[0]
        primary_gain, primary_key = ranked[0]
        opposing = {frozenset({"w", "s"}), frozenset({"a", "d"})}
        if len(ranked) >= 2:
            secondary_gain, secondary_key = ranked[1]
            pair = frozenset({primary_key, secondary_key})
            if pair not in opposing and secondary_gain > 0:
                return [(set(pair), self._hold(distance, primary_key, max_hold))]
        return [({primary_key}, self._hold(distance, primary_key, max_hold))]

    def choose_steps_with_waypoint(
        self,
        player_pos: tuple[int, int] | list[int],
        target_pos: tuple[int, int] | list[int],
        *,
        use_waypoint: bool = False,
        max_hold: float = 0.45,
        prefer_secondary: bool = False,
    ) -> list[tuple[set[str], float]]:
        effective_target = choose_waypoint(player_pos, target_pos, self.summary()) if use_waypoint else (target_pos[0], target_pos[1])
        return self.choose_steps(player_pos, effective_target, max_hold=max_hold, prefer_secondary=prefer_secondary)

    def summary(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for key in self.calibrated:
            current_dx, current_dy = self.get_delta(key)
            cal_dx, cal_dy = self.calibrated[key].get("mean_delta_xy", [0.0, 0.0])[:2]
            result[key] = {
                "n_live": len(self.live_obs.get(key, [])),
                "cal": [round(float(cal_dx), 1), round(float(cal_dy), 1)],
                "current": [round(float(current_dx), 1), round(float(current_dy), 1)],
            }
        return result


def choose_key_for_direction(current: tuple[int, int], target: tuple[int, int], model: dict[str, dict[str, Any]]) -> str:
    """Choose the observed movement key whose mean delta best projects toward target."""

    if not model:
        raise ValueError("navigation model is empty; calibrate movement first")
    best: tuple[float, str] | None = None
    for key in MOVEMENT_KEYS:
        entry = model.get(key)
        if not entry:
            continue
        dx, dy = entry.get("mean_delta_xy", [0.0, 0.0])[:2]
        projected = (current[0] + float(dx), current[1] + float(dy))
        dist = math.hypot(target[0] - projected[0], target[1] - projected[1])
        candidate = (dist, key)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        raise ValueError("navigation model has no usable movement keys")
    return best[1]
