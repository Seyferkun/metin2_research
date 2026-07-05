from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from metin2_research.live_navigation import screen_to_relative_point
from metin2_research.spawn_memory import SpawnMemory


def load_events(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def extract_destroyed_spawn_clicks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the last Metin-body click before each destroy event.

    This intentionally uses only confirmed `count_destroyed` events, not every
    detector click, so spawn memory is seeded from successful kills rather than
    historical false positives.
    """
    destroyed: list[dict[str, Any]] = []
    last_click: dict[str, Any] | None = None
    for event in events:
        if event.get("action") == "click_world_metin_body" and event.get("xy"):
            last_click = event
        if event.get("action") == "count_destroyed" and last_click is not None:
            destroyed.append(
                {
                    "xy": [int(last_click["xy"][0]), int(last_click["xy"][1])],
                    "click_step": last_click.get("step"),
                    "click_t": last_click.get("t"),
                    "destroy_step": event.get("step"),
                    "destroy_t": event.get("t"),
                    "destroyed_count": event.get("destroyed_count"),
                }
            )
            last_click = None
    return destroyed


def seed_spawn_memory(
    event_paths: list[str | Path],
    output_path: str | Path,
    *,
    window_bbox: tuple[int, int, int, int],
    confidence: float = 1.0,
    append: bool = False,
) -> SpawnMemory:
    output_path = Path(output_path)
    if output_path.exists() and not append:
        output_path.unlink()
    memory = SpawnMemory(output_path)
    for event_path in event_paths:
        for item in extract_destroyed_spawn_clicks(load_events(event_path)):
            rel = screen_to_relative_point(item["xy"], window_bbox)
            spawn = memory.record_observation(
                rel,
                screen_xy=item["xy"],
                confidence=confidence,
                now=float(item.get("destroy_t") or 0.0),
            )
            memory.mark_destroyed(spawn["id"], now=float(item.get("destroy_t") or 0.0))
            # Historical event timestamps are run-relative and not comparable to
            # a future controller run. Mark seeded spawns as old enough to patrol
            # immediately while preserving last_seen/last_destroyed evidence.
            spawn["last_visited_ts"] = -1_000_000.0
    memory.save()
    return memory


def parse_bbox(text: str) -> tuple[int, int, int, int]:
    parts = [int(p.strip()) for p in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be left,top,right,bottom")
    return (parts[0], parts[1], parts[2], parts[3])


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Metin spawn memory from confirmed historical destroy event logs.")
    parser.add_argument("events", nargs="+", help="events.jsonl files from successful runs")
    parser.add_argument("--output", default="reports/metin_spawn_memory_seeded.json")
    parser.add_argument("--append", action="store_true", help="Append/merge into existing output instead of overwriting it.")
    parser.add_argument("--window-bbox", type=parse_bbox, default=(0, 0, 1922, 1031), help="left,top,right,bottom used to convert screen clicks to relative coords")
    args = parser.parse_args()

    memory = seed_spawn_memory(args.events, args.output, window_bbox=args.window_bbox, append=args.append)
    summary = {
        "output": str(Path(args.output)),
        "spawn_count": len(memory.spawns),
        "total_destroyed": sum(int(s.get("destroyed_count", 0)) for s in memory.spawns),
        "spawns": memory.spawns,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
