from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


WindowBbox = tuple[int, int, int, int]
RelativePoint = list[float]
RelativeRegion = list[float]


@dataclass
class NavigationConfig:
    reference_size: tuple[int, int]
    ui_regions: dict[str, RelativeRegion] = field(default_factory=dict)
    patrol_points: dict[str, RelativePoint] = field(default_factory=dict)
    search: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NavigationConfig":
        ref = data.get("reference_size", [1922, 1031])
        return cls(
            reference_size=(int(ref[0]), int(ref[1])),
            ui_regions={str(k): [float(vv) for vv in v] for k, v in data.get("ui_regions", {}).items()},
            patrol_points={str(k): [float(vv) for vv in v] for k, v in data.get("patrol_points", {}).items()},
            search=dict(data.get("search", {})),
        )

    def point_to_screen(self, name: str, window_bbox: WindowBbox) -> list[int]:
        return relative_point_to_screen(self.patrol_points[name], window_bbox)

    def region_to_screen(self, name: str, window_bbox: WindowBbox) -> list[int]:
        return relative_region_to_screen(self.ui_regions[name], window_bbox)


def load_navigation_config(path: str | Path) -> NavigationConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return NavigationConfig.from_dict(data)


def relative_point_to_screen(point: RelativePoint, window_bbox: WindowBbox) -> list[int]:
    left, top, right, bottom = window_bbox
    width = right - left
    height = bottom - top
    x = left + width * float(point[0])
    y = top + height * float(point[1])
    return [int(round(x)), int(round(y))]


def relative_region_to_screen(region: RelativeRegion, window_bbox: WindowBbox) -> list[int]:
    left, top, right, bottom = window_bbox
    width = right - left
    height = bottom - top
    x1, y1, x2, y2 = [float(v) for v in region]
    return [
        int(round(left + width * x1)),
        int(round(top + height * y1)),
        int(round(left + width * x2)),
        int(round(top + height * y2)),
    ]


def screen_to_relative_point(screen_xy: list[int] | tuple[int, int], window_bbox: WindowBbox) -> list[float]:
    left, top, right, bottom = window_bbox
    width = max(1, right - left)
    height = max(1, bottom - top)
    return [
        max(0.0, min(1.0, (float(screen_xy[0]) - left) / width)),
        max(0.0, min(1.0, (float(screen_xy[1]) - top) / height)),
    ]


def choose_next_patrol_point(
    spawns: list[dict[str, Any]],
    config: NavigationConfig,
    *,
    now: float,
    cooldown_seconds: float = 120.0,
    window_bbox: WindowBbox | None = None,
) -> dict[str, Any]:
    eligible = []
    for spawn in spawns:
        last_visited = float(spawn.get("last_visited_ts", spawn.get("last_destroyed_ts", 0.0)) or 0.0)
        if now - last_visited >= cooldown_seconds:
            eligible.append(spawn)

    if eligible:
        eligible.sort(key=lambda s: float(s.get("last_visited_ts", s.get("last_destroyed_ts", 0.0)) or 0.0))
        spawn = eligible[0]
        target = {
            "kind": "known_spawn",
            "spawn_id": spawn["id"],
            "relative_xy": [float(spawn["window_relative_xy"][0]), float(spawn["window_relative_xy"][1])],
            "reason": "oldest known spawn outside cooldown",
        }
    else:
        if not config.patrol_points:
            target = {"kind": "none", "relative_xy": None, "reason": "no known spawns or fallback patrol points"}
        else:
            name, point = next(iter(config.patrol_points.items()))
            target = {
                "kind": "fallback_patrol",
                "name": name,
                "relative_xy": [float(point[0]), float(point[1])],
                "reason": "all known spawns are recent or no known spawns",
            }

    if window_bbox is not None and target.get("relative_xy") is not None:
        target["screen_xy"] = relative_point_to_screen(target["relative_xy"], window_bbox)
    return target
