#!/usr/bin/env python
"""Patrol Yongan and record Metin coordinates found in client memory.

This is for the private MT2Portugalia sandbox. It uses normal keyboard scan-code
input to walk and read-only memory scanning to collect visible/nearby Metin text
coordinates such as `Metin da Batalha(284, 218)`.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from ctypes import wintypes

from metin2_research.window_capture import activate_window, find_window
try:
    from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory
    from update_metin_coordinate_table import coord_id, load_existing, write_outputs, MetinCoord
except ModuleNotFoundError:  # Imported as scripts.explore_yongan_metin_coords under pytest.
    from scripts.refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory
    from scripts.update_metin_coordinate_table import coord_id, load_existing, write_outputs, MetinCoord

METIN_RE = re.compile(
    r"(?P<name>Metin\s+(?:da|do|de|dos|das)?\s*[A-Za-zÀ-ÿ0-9_ '\-]+)\(\s*(?P<x>\d{1,4})\s*,\s*(?P<y>\d{1,4})\s*\)",
    re.IGNORECASE,
)
PLAYER_COORD_RE = re.compile(r"(?:Yoshypt)?\(?\s*(?P<x>\d{2,4})\s*,\s*(?P<y>\d{2,4})\s*\)?")

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


SCANCODES = {"w": 0x11, "a": 0x1E, "s": 0x1F, "d": 0x20, "1": 0x02}
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002


@dataclass
class ScanHit:
    address: int
    raw: str
    metin_name: str
    x: int
    y: int


def printable(raw: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 or 160 <= b <= 255 else " " for b in raw)


def sanitize_name(name: str) -> str:
    return " ".join(name.strip().rstrip(" ,.;:").split())


def send_scan(scancode: int, keyup: bool = False) -> None:
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if keyup else 0)
    inp = INPUT(type=INPUT_KEYBOARD, union=INPUTUNION(ki=KEYBDINPUT(0, scancode, flags, 0, 0)))
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    if sent != 1:
        raise ctypes.WinError()


def hold_key(key: str, seconds: float) -> None:
    scancode = SCANCODES[key]
    send_scan(scancode, False)
    time.sleep(seconds)
    send_scan(scancode, True)


def read_text_window(h_process, address: int, before: int = 48, size: int = 220) -> str:
    raw = read_memory(h_process, max(0, address - before), size) or b""
    return printable(raw)


def current_player_coord(h_process, addresses: list[int]) -> tuple[int, int] | None:
    for address in addresses:
        text = read_text_window(h_process, address)
        # Prefer explicit Yoshypt(...) if present, otherwise first coordinate.
        explicit = re.search(r"Yoshypt\(\s*(\d{2,4})\s*,\s*(\d{2,4})\s*\)", text)
        if explicit:
            return int(explicit.group(1)), int(explicit.group(2))
        match = PLAYER_COORD_RE.search(text)
        if match:
            return int(match.group("x")), int(match.group("y"))
    return None


def scan_metin_text(h_process, *, max_region_mb: int = 64, context: int = 128) -> list[ScanHit]:
    pattern = b"Metin "
    hits: list[ScanHit] = []
    seen: set[tuple[str, int, int]] = set()
    for region in enumerate_regions(h_process, max_region_mb=max_region_mb):
        if region.get("type") == "image":
            continue
        base = int(region["base"])
        size = min(int(region["size"]), max_region_mb * 1024 * 1024)
        step = 1024 * 1024
        overlap = context + 128
        prev = b""
        prev_base = base
        for offset in range(0, size, step):
            chunk = read_memory(h_process, base + offset, min(step, size - offset))
            if not chunk:
                continue
            data = prev + chunk
            data_base = prev_base
            start = 0
            while True:
                idx = data.find(pattern, start)
                if idx < 0:
                    break
                lo = max(0, idx - context)
                hi = min(len(data), idx + context)
                text = printable(data[lo:hi])
                for match in METIN_RE.finditer(text):
                    name = sanitize_name(match.group("name"))
                    x = int(match.group("x"))
                    y = int(match.group("y"))
                    key = (name.lower(), x, y)
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(ScanHit(data_base + lo + match.start(), text, name, x, y))
                start = idx + 1
            if len(chunk) > overlap:
                prev = chunk[-overlap:]
                prev_base = base + offset + len(chunk) - overlap
            else:
                prev = chunk
                prev_base = base + offset
    return hits


def merge_hits_into_table(hits: list[ScanHit], *, map_name: str, csv_path: Path, json_path: Path, source_path: Path) -> int:
    rows = load_existing(csv_path)
    before = len(rows)
    # Also dedupe against same map/name/x/y even if an older id format exists.
    by_tuple = {(r.map_name, r.metin_name.lower(), r.x, r.y): key for key, r in rows.items()}
    for hit in hits:
        row_id = coord_id(map_name, hit.metin_name, hit.x, hit.y)
        row = MetinCoord(
            id=row_id,
            map_name=map_name,
            metin_name=hit.metin_name,
            x=hit.x,
            y=hit.y,
            source_type="live_memory_patrol",
            source_path=str(source_path),
            evidence=hit.raw[:240],
            confidence=0.96,
            notes="Recorded during Yongan patrol memory scan.",
        )
        tuple_key = (map_name, hit.metin_name.lower(), hit.x, hit.y)
        old_key = by_tuple.get(tuple_key)
        if old_key and old_key != row_id:
            rows.pop(old_key, None)
        rows[row_id] = row
        by_tuple[tuple_key] = row_id
    write_outputs(rows, csv_path, json_path)
    return len(rows) - before


def parse_patrol(spec: str) -> list[tuple[str, float]]:
    legs: list[tuple[str, float]] = []
    for part in spec.split(","):
        if not part.strip():
            continue
        key, seconds = part.split(":", 1)
        key = key.strip().lower()
        if key not in SCANCODES or key == "1":
            raise ValueError(f"unsupported patrol key: {key}")
        legs.append((key, float(seconds)))
    return legs


def coord_to_cell(coord: tuple[int, int], bounds: tuple[int, int, int, int], cell_size: int) -> tuple[int, int]:
    min_x, min_y, _max_x, _max_y = bounds
    return (math.floor((coord[0] - min_x) / cell_size), math.floor((coord[1] - min_y) / cell_size))


def cell_center(cell: tuple[int, int], bounds: tuple[int, int, int, int], cell_size: int) -> tuple[int, int]:
    min_x, min_y, _max_x, _max_y = bounds
    return (min_x + cell[0] * cell_size + cell_size // 2, min_y + cell[1] * cell_size + cell_size // 2)


def choose_unvisited_target(
    *,
    current: tuple[int, int],
    visited_cells: set[tuple[int, int]],
    bounds: tuple[int, int, int, int],
    cell_size: int,
    blocked_cells: set[tuple[int, int]] | None = None,
) -> tuple[int, int] | None:
    blocked_cells = blocked_cells or set()
    min_x, min_y, max_x, max_y = bounds
    x_cells = max(1, math.ceil((max_x - min_x) / cell_size))
    y_cells = max(1, math.ceil((max_y - min_y) / cell_size))
    candidates: list[tuple[float, int, int, tuple[int, int]]] = []
    for cy in range(y_cells):
        for cx in range(x_cells):
            cell = (cx, cy)
            if cell in visited_cells or cell in blocked_cells:
                continue
            center = cell_center(cell, bounds, cell_size)
            dist = math.hypot(center[0] - current[0], center[1] - current[1])
            # Tie-break toward lower Y first, then lower X, so coverage expands in
            # deterministic horizontal sweeps instead of jittering locally.
            candidates.append((dist, center[1], center[0], center))
    if not candidates:
        return None
    return min(candidates)[3]


def should_block_target(
    recent_coords: list[tuple[int, int]],
    *,
    stall_limit: int = 4,
    oscillation_limit: int = 6,
) -> bool:
    if len(recent_coords) >= stall_limit:
        tail = recent_coords[-stall_limit:]
        if len(set(tail)) == 1:
            return True
    if len(recent_coords) >= oscillation_limit:
        tail = recent_coords[-oscillation_limit:]
        if len(set(tail)) == 2 and all(tail[i] == tail[i % 2] for i in range(len(tail))):
            return True
    return False


def fallback_key(recent_keys: list[str]) -> str:
    tail = recent_keys[-4:]
    counts = {key: tail.count(key) for key in ("w", "a", "s", "d")}
    # Prefer a perpendicular escape when oscillating on a common axis.
    if counts["w"] + counts["s"] >= 3:
        return "a" if counts["a"] <= counts["d"] else "d"
    if counts["a"] + counts["d"] >= 3:
        return "w" if counts["w"] <= counts["s"] else "s"
    return min(("w", "a", "s", "d"), key=lambda key: (counts[key], key))


def mean_delta(values: list[tuple[int, int]]) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    return (sum(v[0] for v in values) / len(values), sum(v[1] for v in values) / len(values))


def choose_key_toward_target(
    current: tuple[int, int],
    target: tuple[int, int],
    observations: dict[str, list[tuple[int, int]]],
) -> str:
    if not observations:
        observations = {
            "w": [(-12, 8)],
            "a": [(12, 8)],
            "s": [(8, -12)],
            "d": [(-12, -8)],
        }
    best: tuple[float, str] | None = None
    start_dist = math.hypot(target[0] - current[0], target[1] - current[1])
    for key in ("w", "a", "s", "d"):
        dx, dy = mean_delta(observations.get(key, []))
        projected = (current[0] + dx, current[1] + dy)
        dist = math.hypot(target[0] - projected[0], target[1] - projected[1])
        # Prefer keys that reduce distance; if all are bad, still return the least bad.
        score = dist - start_dist
        candidate = (score, key)
        if best is None or candidate < best:
            best = candidate
    assert best is not None
    return best[1]


def build_navigation_model(observations: list[dict]) -> dict[str, dict]:
    by_key: dict[str, list[tuple[int, int]]] = {"w": [], "a": [], "s": [], "d": []}
    for obs in observations:
        key = obs.get("key")
        if key in by_key:
            by_key[key].append((int(obs.get("delta_x", 0)), int(obs.get("delta_y", 0))))
    model: dict[str, dict] = {}
    for key, values in by_key.items():
        if not values:
            continue
        dx, dy = mean_delta(values)
        model[key] = {"samples": len(values), "mean_delta": [round(dx, 3), round(dy, 3)]}
    return model


def build_map_reference(
    *,
    map_name: str,
    metins: list[dict],
    visited_cells: set[tuple[int, int]],
    observations: list[dict],
    bounds: tuple[int, int, int, int],
    cell_size: int,
    blocked_cells: set[tuple[int, int]] | None = None,
) -> dict:
    blocked_cells = blocked_cells or set()
    min_x, min_y, max_x, max_y = bounds
    total_cells = max(1, math.ceil((max_x - min_x) / cell_size)) * max(1, math.ceil((max_y - min_y) / cell_size))
    known_metins = sorted(
        [
            {
                "metin_name": m["metin_name"],
                "coord": [int(m["x"]), int(m["y"])],
                "source_type": m.get("source_type", ""),
                "confidence": float(m.get("confidence", 0) or 0),
            }
            for m in metins
        ],
        key=lambda item: (item["metin_name"], item["coord"]),
    )
    return {
        "version": 1,
        "map_name": map_name,
        "bounds": [min_x, min_y, max_x, max_y],
        "cell_size": cell_size,
        "coverage": {
            "visited_cells": len(visited_cells),
            "blocked_cells": len(blocked_cells),
            "total_cells": total_cells,
            "coverage_ratio": round(len(visited_cells) / total_cells, 4),
            "visited_cell_ids": [[x, y] for x, y in sorted(visited_cells)],
            "blocked_cell_ids": [[x, y] for x, y in sorted(blocked_cells)],
        },
        "known_metins": known_metins,
        "navigation_model": build_navigation_model(observations),
    }


def parse_bounds(spec: str) -> tuple[int, int, int, int]:
    parts = [int(p.strip()) for p in spec.split(",")]
    if len(parts) != 4:
        raise ValueError("bounds must be min_x,min_y,max_x,max_y")
    return (parts[0], parts[1], parts[2], parts[3])


def load_metin_rows_for_map(csv_path: Path, map_name: str) -> list[dict]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        return [row for row in csv.DictReader(f) if row.get("map_name") == map_name]


def load_blocked_cells_from_reference(path: Path) -> set[tuple[int, int]]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    cells = data.get("coverage", {}).get("blocked_cell_ids", []) if isinstance(data, dict) else []
    result: set[tuple[int, int]] = set()
    for cell in cells:
        try:
            if len(cell) == 2:
                result.add((int(cell[0]), int(cell[1])))
        except Exception:
            continue
    return result


def load_existing_observations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        return list(data.get("navigation_observations", []))
    return []


def observations_to_deltas(observations: list[dict]) -> dict[str, list[tuple[int, int]]]:
    result: dict[str, list[tuple[int, int]]] = {"w": [], "a": [], "s": [], "d": []}
    for obs in observations:
        key = obs.get("key")
        if key in result:
            result[key].append((int(obs.get("delta_x", 0)), int(obs.get("delta_y", 0))))
    return result


def write_navigation_observations(path: Path, observations: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "navigation_observations": observations}, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", default="Yongan")
    parser.add_argument("--process", default="pgclient.app")
    parser.add_argument("--window", default="MT2Portugalia")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--patrol", default="w:2.0,a:2.0,s:2.0,d:2.0")
    parser.add_argument("--pause", type=float, default=0.5)
    parser.add_argument("--csv", default="data/metin_coordinates.csv")
    parser.add_argument("--json", default="data/metin_coordinates.json")
    parser.add_argument("--log", default="reports/yongan_exploration_log.json")
    parser.add_argument("--player-address", action="append", default=["0x0b73d584", "0x0b73d60c"])
    parser.add_argument("--heal-each-leg", action="store_true")
    parser.add_argument("--smart", action="store_true", help="Use memory feedback to choose nearest uncovered map cells instead of a fixed patrol.")
    parser.add_argument("--smart-steps", type=int, default=20)
    parser.add_argument("--smart-step-seconds", type=float, default=1.5)
    parser.add_argument("--bounds", default="0,0,500,500", help="Exploration bounds as min_x,min_y,max_x,max_y")
    parser.add_argument("--cell-size", type=int, default=25)
    parser.add_argument("--navigation-observations", default="data/yongan_navigation_observations.json")
    parser.add_argument("--map-reference", default="data/yongan_map_reference.json")
    args = parser.parse_args()

    pid = find_pid(args.process)
    if pid is None:
        print(f"process not found: {args.process}")
        return 1
    window = find_window(args.window)
    activate_window(window)
    h_process = open_process(pid)
    if not h_process:
        print(f"OpenProcess failed for pid={pid}")
        return 1

    csv_path = Path(args.csv)
    json_path = Path(args.json)
    log_path = Path(args.log)
    nav_obs_path = Path(args.navigation_observations)
    map_reference_path = Path(args.map_reference)
    bounds = parse_bounds(args.bounds)
    player_addresses = [int(a, 0) for a in args.player_address]
    patrol = parse_patrol(args.patrol)
    log: list[dict] = []
    navigation_observations = load_existing_observations(nav_obs_path)
    visited_cells: set[tuple[int, int]] = set()
    blocked_cells: set[tuple[int, int]] = load_blocked_cells_from_reference(map_reference_path)
    if blocked_cells:
        print(f"loaded_blocked_cells={len(blocked_cells)} from {map_reference_path}")
    recent_coords: list[tuple[int, int]] = []
    recent_keys: list[str] = []
    pending_escape_key: str | None = None

    try:
        def scan_and_record(label: str) -> dict:
            player = current_player_coord(h_process, player_addresses)
            if player is not None:
                visited_cells.add(coord_to_cell(player, bounds, args.cell_size))
            hits = scan_metin_text(h_process)
            snapshot_path = log_path.with_name(log_path.stem + f"_{label}.json")
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(json.dumps({"label": label, "player_coord": player, "hits": [asdict(h) for h in hits]}, indent=2, ensure_ascii=False), encoding="utf-8")
            added = merge_hits_into_table(hits, map_name=args.map, csv_path=csv_path, json_path=json_path, source_path=snapshot_path)
            entry = {"label": label, "player_coord": player, "metin_hits": len(hits), "new_rows_delta": added, "snapshot": str(snapshot_path)}
            log.append(entry)
            print(f"{label}: player={player} metin_hits={len(hits)} new_rows_delta={added}")
            for hit in hits:
                print(f"  {hit.metin_name}({hit.x},{hit.y}) addr=0x{hit.address:08x}")
            return entry

        first = scan_and_record("start")
        current = tuple(first["player_coord"]) if first.get("player_coord") else None
        if args.smart:
            for step in range(1, args.smart_steps + 1):
                if current is None:
                    print("smart: no current memory coordinate; falling back to fixed patrol leg")
                    key = patrol[(step - 1) % len(patrol)][0]
                    target = None
                elif pending_escape_key is not None:
                    key = pending_escape_key
                    pending_escape_key = None
                    target = None
                    print(f"smart: executing escape key={key}")
                else:
                    target = choose_unvisited_target(
                        current=(int(current[0]), int(current[1])),
                        visited_cells=visited_cells,
                        blocked_cells=blocked_cells,
                        bounds=bounds,
                        cell_size=args.cell_size,
                    )
                    if target is None:
                        print("smart: all configured cells visited; stopping")
                        break
                    key = choose_key_toward_target(
                        (int(current[0]), int(current[1])),
                        target,
                        observations_to_deltas(navigation_observations),
                    )
                if args.heal_each_leg:
                    hold_key("1", 0.05)
                    time.sleep(0.1)
                before = current
                print(f"smart step={step} target={target} key={key} seconds={args.smart_step_seconds}")
                hold_key(key, args.smart_step_seconds)
                time.sleep(args.pause)
                entry = scan_and_record(f"smart_{step:03d}_{key}")
                after = tuple(entry["player_coord"]) if entry.get("player_coord") else None
                if before is not None and after is not None:
                    obs = {
                        "map_name": args.map,
                        "source_log": str(log_path),
                        "label": entry["label"],
                        "key": key,
                        "from_x": int(before[0]),
                        "from_y": int(before[1]),
                        "to_x": int(after[0]),
                        "to_y": int(after[1]),
                        "delta_x": int(after[0]) - int(before[0]),
                        "delta_y": int(after[1]) - int(before[1]),
                        "target": list(target) if target else None,
                        "notes": "Autonomous smart exploration memory-feedback step.",
                    }
                    navigation_observations.append(obs)
                    write_navigation_observations(nav_obs_path, navigation_observations)
                    recent_coords.append((int(after[0]), int(after[1])))
                    recent_keys.append(key)
                    if len(recent_coords) > 8:
                        recent_coords[:] = recent_coords[-8:]
                    if len(recent_keys) > 8:
                        recent_keys[:] = recent_keys[-8:]
                    if target and should_block_target(recent_coords):
                        blocked = coord_to_cell((int(target[0]), int(target[1])), bounds, args.cell_size)
                        blocked_cells.add(blocked)
                        escape = fallback_key(recent_keys)
                        print(f"smart: blocking target_cell={blocked} after stall/oscillation; escape_next={escape}")
                        pending_escape_key = escape
                current = after
        else:
            for cycle in range(args.cycles):
                for leg_idx, (key, seconds) in enumerate(patrol, start=1):
                    if args.heal_each_leg:
                        hold_key("1", 0.05)
                        time.sleep(0.1)
                    before = current
                    print(f"move cycle={cycle + 1} leg={leg_idx} key={key} seconds={seconds}")
                    hold_key(key, seconds)
                    time.sleep(args.pause)
                    entry = scan_and_record(f"c{cycle + 1}_l{leg_idx}_{key}")
                    after = tuple(entry["player_coord"]) if entry.get("player_coord") else None
                    if before is not None and after is not None:
                        navigation_observations.append({
                            "map_name": args.map,
                            "source_log": str(log_path),
                            "label": entry["label"],
                            "key": key,
                            "from_x": int(before[0]),
                            "from_y": int(before[1]),
                            "to_x": int(after[0]),
                            "to_y": int(after[1]),
                            "delta_x": int(after[0]) - int(before[0]),
                            "delta_y": int(after[1]) - int(before[1]),
                            "notes": "Fixed patrol memory-feedback step.",
                        })
                        write_navigation_observations(nav_obs_path, navigation_observations)
                    current = after
    finally:
        close_handle(h_process)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({"map": args.map, "pid": pid, "window": str(window), "bounds": list(bounds), "cell_size": args.cell_size, "log": log}, indent=2, ensure_ascii=False), encoding="utf-8")
    reference = build_map_reference(
        map_name=args.map,
        metins=load_metin_rows_for_map(csv_path, args.map),
        visited_cells=visited_cells,
        observations=navigation_observations,
        bounds=bounds,
        cell_size=args.cell_size,
        blocked_cells=blocked_cells,
    )
    map_reference_path.parent.mkdir(parents=True, exist_ok=True)
    map_reference_path.write_text(json.dumps(reference, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved log={log_path} table={csv_path} json={json_path} map_reference={map_reference_path} navigation_observations={nav_obs_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
