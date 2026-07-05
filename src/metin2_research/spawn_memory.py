from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


class SpawnMemory:
    def __init__(self, path: str | Path, *, merge_radius: float = 0.06) -> None:
        self.path = Path(path)
        self.merge_radius = merge_radius
        self.version = 1
        self.spawns: list[dict[str, Any]] = []
        if self.path.exists():
            self.load()

    def load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.version = int(data.get("version", 1))
        self.spawns = list(data.get("spawns", []))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": self.version, "spawns": self.spawns}
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _next_id(self) -> str:
        return f"spawn_{len(self.spawns) + 1:03d}"

    def _distance(self, a: list[float], b: list[float]) -> float:
        return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))

    def find_nearby(self, relative_xy: list[float]) -> dict[str, Any] | None:
        best: tuple[float, dict[str, Any]] | None = None
        for spawn in self.spawns:
            dist = self._distance(relative_xy, spawn["window_relative_xy"])
            if dist <= self.merge_radius and (best is None or dist < best[0]):
                best = (dist, spawn)
        return best[1] if best else None

    def record_observation(
        self,
        relative_xy: list[float],
        *,
        screen_xy: list[int] | None = None,
        confidence: float = 0.0,
        now: float,
    ) -> dict[str, Any]:
        relative_xy = [float(relative_xy[0]), float(relative_xy[1])]
        spawn = self.find_nearby(relative_xy)
        if spawn is None:
            spawn = {
                "id": self._next_id(),
                "screen_xy": screen_xy,
                "window_relative_xy": relative_xy,
                "observations": 0,
                "destroyed_count": 0,
                "last_seen_ts": None,
                "last_destroyed_ts": None,
                "last_visited_ts": 0.0,
                "confidence": 0.0,
            }
            self.spawns.append(spawn)

        old_n = int(spawn.get("observations", 0))
        new_n = old_n + 1
        old_xy = spawn.get("window_relative_xy", relative_xy)
        spawn["window_relative_xy"] = [
            (float(old_xy[0]) * old_n + relative_xy[0]) / new_n,
            (float(old_xy[1]) * old_n + relative_xy[1]) / new_n,
        ]
        if screen_xy is not None:
            spawn["screen_xy"] = [int(screen_xy[0]), int(screen_xy[1])]
        spawn["observations"] = new_n
        spawn["last_seen_ts"] = float(now)
        spawn["last_visited_ts"] = float(now)
        spawn["confidence"] = max(float(spawn.get("confidence", 0.0)), float(confidence))
        return spawn

    def mark_destroyed(self, spawn_id: str, *, now: float) -> dict[str, Any] | None:
        for spawn in self.spawns:
            if spawn.get("id") == spawn_id:
                spawn["destroyed_count"] = int(spawn.get("destroyed_count", 0)) + 1
                spawn["last_destroyed_ts"] = float(now)
                spawn["last_visited_ts"] = float(now)
                return spawn
        return None
