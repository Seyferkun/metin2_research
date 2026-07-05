#!/usr/bin/env python
"""Rank known Metin coordinates around the current character position.

Read-only helper for the MT2Portugalia private sandbox. It reads the live
client-state JSON written by the local client logger and the curated
``data/metin_coordinates.csv`` table, then writes JSON/CSV artifacts that list
nearby known Metin locations. It does not send game input.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for _path in (PROJECT_ROOT, SRC_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

DEFAULT_STATE_JSON = Path("D:/Games/MT2Portugalia/app/hermes_state.json")
DEFAULT_COORDS_CSV = Path("data/metin_coordinates.csv")
DEFAULT_OUT_JSON = Path("reports/nearby_metins/latest_nearby_metins.json")
DEFAULT_OUT_CSV = Path("reports/nearby_metins/latest_nearby_metins.csv")
DEFAULT_LEARNED_SPAWNS_JSON = Path("data/metin_learned_spawns.json")
DEFAULT_BAD_LOCATIONS_JSON = Path("data/metin_bad_locations.json")
MAP_ALIASES = {
    "metin2_map_a1": "Yongan",
    "map_a1": "Yongan",
    "yongan": "Yongan",
}
METIN_RE = re.compile(
    r"(?P<name>Metin\s+(?:da|do|de|dos|das)?\s*[A-Za-zÀ-ÿ0-9_ '\-]+)\(\s*(?P<x>\d{1,4})\s*,\s*(?P<y>\d{1,4})\s*\)",
    re.IGNORECASE,
)
EVIDENCE_META = {
    "target_selected": (1, "Selected target (live VID)"),
    "live_memory_visible_text": (2, "Live memory label (exact coordinate)"),
    "live_memory_name_only": (3, "Live memory name match (no coordinate)"),
    "named_metin_probe": (3, "Named Metin probe (live VID, current-position estimate)"),
    "visual_detector_current_position_estimate": (4, "Visual detector (current-position estimate)"),
    "learned_live_memory": (5, "Learned live-memory spawn (previously observed, not destroyed proof)"),
    "destroyed_live_memory": (5, "Historic destroyed Metin spawn (confirmed/marked destroyed)"),
}
TABLE_EVIDENCE = (6, "Known coordinate table (not live proof)")


@dataclass(frozen=True)
class LiveMetinHit:
    address: int
    metin_name: str
    x: int
    y: int
    raw: str


@dataclass(frozen=True)
class VisualMetinHit:
    confidence: float
    screen_x: float
    screen_y: float
    width: float
    height: float


@dataclass(frozen=True)
class LiveMetinNameHit:
    address: int
    metin_name: str
    raw: str


def printable(raw: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 or 160 <= b <= 255 else " " for b in raw)


def scan_live_metin_memory(process_name: str = "pgclient.app", *, max_region_mb: int = 128, context: int = 240) -> list[LiveMetinHit]:
    """Read-only scan for currently materialized Metin label text in the local client process."""
    try:
        from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory
    except ModuleNotFoundError:
        from scripts.refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory

    pid = find_pid(process_name) or find_pid("MT2Portugalia")
    if not pid:
        raise RuntimeError(f"process not found: {process_name}")
    handle = open_process(pid)
    if not handle:
        raise RuntimeError(f"OpenProcess failed for pid {pid}")
    hits: list[LiveMetinHit] = []
    seen: set[tuple[str, int, int]] = set()
    try:
        for region in enumerate_regions(handle, max_region_mb=max_region_mb):
            base = int(region["base"])
            size = int(region["size"])
            step = 1024 * 1024
            overlap = context + 128
            prev = b""
            prev_base = base
            for offset in range(0, size, step):
                chunk = read_memory(handle, base + offset, min(step, size - offset))
                if not chunk:
                    continue
                data = prev + chunk
                data_base = prev_base
                start = 0
                while True:
                    idx = data.find(b"Metin")
                    if idx < 0:
                        break
                    lo = max(0, idx - context)
                    hi = min(len(data), idx + context)
                    text = printable(data[lo:hi])
                    for match in METIN_RE.finditer(text):
                        name = " ".join(match.group("name").strip().rstrip(" ,.;:").split())
                        x = int(match.group("x"))
                        y = int(match.group("y"))
                        key = (name.casefold(), x, y)
                        if key in seen:
                            continue
                        seen.add(key)
                        hits.append(LiveMetinHit(data_base + lo + match.start(), name, x, y, text[:300]))
                    start = idx + 1
                    data = data[start:]
                    data_base += start
                    start = 0
                if len(chunk) > overlap:
                    prev = chunk[-overlap:]
                    prev_base = base + offset + len(chunk) - overlap
                else:
                    prev = chunk
                    prev_base = base + offset
    finally:
        close_handle(handle)
    return hits


COMMON_METIN_NAMES = (
    "Metin da Batalha",
    "Metin do Combate",
    "Metin Negra",
    "Metin da Sombra",
    "Metin da Dureza",
    "Metin da Alma",
    "Metin do Ciúme",
    "Metin do Ciume",
    "Metin da Escuridão",
    "Metin da Escuridao",
    "Metin da Morte",
    "Metin da Queda",
    "Metin Pung-Ma",
    "Metin Tu-Young",
)


def scan_live_metin_name_memory(
    process_name: str = "pgclient.app",
    *,
    anchor_names: list[str] | None = None,
    metin_names: list[str] | None = None,
    max_region_mb: int = 128,
    context: int = 180,
) -> list[LiveMetinNameHit]:
    """Read-only fallback for live Metin names when no coordinate label is materialized."""
    try:
        from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory
    except ModuleNotFoundError:
        from scripts.refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory

    anchors = [str(name or "").strip() for name in (anchor_names or []) if str(name or "").strip()]
    names = list(dict.fromkeys([*(metin_names or []), *COMMON_METIN_NAMES]))
    encoded_names: list[tuple[str, bytes]] = []
    for name in names:
        for encoding in ("utf-8", "latin1"):
            raw = str(name).encode(encoding, errors="ignore")
            if raw:
                encoded_names.append((str(name), raw))
    encoded_anchors: list[bytes] = []
    for name in anchors:
        for encoding in ("utf-8", "latin1"):
            raw = name.encode(encoding, errors="ignore")
            if raw:
                encoded_anchors.append(raw)
    if not encoded_names or not encoded_anchors:
        return []
    pid = find_pid(process_name) or find_pid("MT2Portugalia")
    if not pid:
        raise RuntimeError(f"process not found: {process_name}")
    handle = open_process(pid)
    if not handle:
        raise RuntimeError(f"OpenProcess failed for pid {pid}")
    hits: list[LiveMetinNameHit] = []
    seen: set[str] = set()
    try:
        for region in enumerate_regions(handle, max_region_mb=max_region_mb):
            base = int(region["base"])
            size = int(region["size"])
            chunks: list[bytes] = []
            for offset in range(0, size, 1024 * 1024):
                chunk = read_memory(handle, base + offset, min(1024 * 1024, size - offset))
                if chunk:
                    chunks.append(chunk)
            data = b"".join(chunks)
            if not data:
                continue
            # With a current-target anchor, require the Metin name to appear in
            # the same dynamic region. This filters out static quest/wiki/proto text.
            if encoded_anchors and not any(anchor in data for anchor in encoded_anchors):
                continue
            for name, needle in encoded_names:
                idx = data.find(needle)
                if idx < 0:
                    continue
                key = name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                lo = max(0, idx - context)
                hi = min(len(data), idx + context)
                raw = printable(data[lo:hi])[:360]
                # Monster proto/quest/wiki/catalog tables contain many Metin names
                # but are not live nearby entities. They commonly include ???
                # placeholders or quest/UI text; never surface those as live hits.
                if "???" in raw or "[ENTER]" in raw or "WIKI_" in raw or "uiAttachMetin" in raw:
                    continue
                hits.append(LiveMetinNameHit(base + idx, name, raw))
    finally:
        close_handle(handle)
    return hits


def evidence_meta(source_type: str) -> tuple[int, str]:
    return EVIDENCE_META.get(str(source_type), TABLE_EVIDENCE)


def is_metin_name(name: Any) -> bool:
    text = str(name or "").strip().casefold()
    return "metin" in text or "stone" in text or "pedra" in text


def target_to_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    target = state.get("target") if isinstance(state.get("target"), dict) else None
    if not target:
        return []
    vid = target.get("vid")
    name = str(target.get("name") or "").strip()
    if not vid or not name or not is_metin_name(name):
        return []
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    coord = display_coord_from_raw(float(player["x"]), float(player["y"]))
    map_name = normalize_map_name(state.get("map") or state.get("map_name"))
    alive = target.get("alive")
    return [
        {
            "id": f"target_{map_name.lower()}_{vid}",
            "map_name": map_name,
            "metin_name": name,
            "x": int(coord["x"]),
            "y": int(coord["y"]),
            "confidence": 1.0,
            "source_type": "target_selected",
            "source_path": f"hermes_state.json target.vid={vid}",
            "evidence": f"selected target vid={vid} alive={alive}",
            "notes": "The client logger reports this Metin as the currently selected target; coordinate is the current player position estimate.",
            "vid": vid,
            "alive": alive,
        }
    ]


def named_probe_to_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    coord = display_coord_from_raw(float(player["x"]), float(player["y"]))
    map_name = normalize_map_name(state.get("map") or state.get("map_name"))
    probe = state.get("named_metin_probe") if isinstance(state.get("named_metin_probe"), list) else []
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in probe:
        if not isinstance(item, dict):
            continue
        try:
            vid = int(item.get("vid") or 0)
        except Exception:
            continue
        name = str(item.get("name") or "").strip()
        if vid <= 0 or vid in seen or not is_metin_name(name):
            continue
        seen.add(vid)
        alive = item.get("alive")
        type_value = item.get("type")
        evidence = f"GetVIDByName resolved vid={vid} alive={alive}"
        if type_value is not None:
            evidence += f" type={type_value}"
        rows.append(
            {
                "id": f"named_probe_{map_name.lower()}_{vid}",
                "map_name": map_name,
                "metin_name": name,
                "x": int(coord["x"]),
                "y": int(coord["y"]),
                "confidence": 0.92,
                "source_type": "named_metin_probe",
                "source_path": f"hermes_state.json named_metin_probe vid={vid}",
                "evidence": evidence,
                "notes": "The client logger resolved this known Metin name to a live VID; coordinate is the current player position estimate.",
                "vid": vid,
                "alive": alive,
            }
        )
    return rows


def _slug(text: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().casefold()).strip("_")
    return slug or "unknown"


def _bad_location_keys(path: str | Path = DEFAULT_BAD_LOCATIONS_JSON) -> set[tuple[str, str, int, int]]:
    path = Path(path)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return set()
    locations = data.get("locations") if isinstance(data, dict) else data
    keys: set[tuple[str, str, int, int]] = set()
    for item in locations or []:
        if not isinstance(item, dict):
            continue
        try:
            keys.add((normalize_map_name(item.get("map_name")), str(item.get("metin_name") or "").strip().casefold(), int(item["x"]), int(item["y"])))
        except Exception:
            continue
    return keys


def is_bad_location(row: dict[str, Any], *, bad_locations_path: str | Path = DEFAULT_BAD_LOCATIONS_JSON) -> bool:
    keys = _bad_location_keys(bad_locations_path)
    if not keys:
        return False
    try:
        key = (normalize_map_name(row.get("map_name")), str(row.get("metin_name") or "").strip().casefold(), int(row["x"]), int(row["y"]))
    except Exception:
        return False
    return key in keys


def _is_destroyed_or_marked(item: dict[str, Any]) -> bool:
    return bool(
        item.get("destroyed") is True
        or item.get("marked_destroyed") is True
        or int(item.get("destroyed_count") or 0) > 0
        or item.get("last_destroyed_ts") is not None
    )


def load_learned_spawn_rows(path: str | Path = DEFAULT_LEARNED_SPAWNS_JSON) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    spawns = data.get("spawns") if isinstance(data, dict) else data
    rows: list[dict[str, Any]] = []
    for item in spawns or []:
        if not isinstance(item, dict):
            continue
        try:
            destroyed_or_marked = _is_destroyed_or_marked(item)
            source_type = "destroyed_live_memory" if destroyed_or_marked else "learned_live_memory"
            note_prefix = "Historic destroyed/marked Metin coordinate" if destroyed_or_marked else "Previously observed live-memory coordinate (not destroyed proof)"
            rows.append(
                {
                    "id": item.get("id") or f"learned_{_slug(item.get('map_name'))}_{_slug(item.get('metin_name'))}_{int(item['x'])}_{int(item['y'])}",
                    "map_name": normalize_map_name(item.get("map_name")),
                    "metin_name": item.get("metin_name") or "Metin",
                    "x": int(item["x"]),
                    "y": int(item["y"]),
                    "confidence": float(item.get("confidence") or 0.88),
                    "source_type": source_type,
                    "source_path": str(path),
                    "evidence": item.get("last_evidence") or "learned from previous live_memory_visible_text hit",
                    "notes": f"{note_prefix}; observations={int(item.get('observations') or 1)} last_seen_ts={item.get('last_seen_ts')}",
                    "observations": int(item.get("observations") or 1),
                    "first_seen_ts": item.get("first_seen_ts"),
                    "last_seen_ts": item.get("last_seen_ts"),
                    "destroyed": destroyed_or_marked,
                    "destroyed_count": int(item.get("destroyed_count") or 0),
                    "last_destroyed_ts": item.get("last_destroyed_ts"),
                }
            )
        except Exception:
            continue
    return rows


def record_learned_spawns(rows: list[dict[str, Any]], *, path: str | Path = DEFAULT_LEARNED_SPAWNS_JSON, now: float | None = None) -> list[dict[str, Any]]:
    now = time.time() if now is None else float(now)
    path = Path(path)
    existing = {row["id"]: dict(row) for row in load_learned_spawn_rows(path)}
    written: list[dict[str, Any]] = []
    for row in rows:
        if row.get("source_type") != "live_memory_visible_text":
            continue
        try:
            map_name = normalize_map_name(row.get("map_name"))
            name = str(row.get("metin_name") or "Metin").strip()
            x = int(row["x"])
            y = int(row["y"])
        except Exception:
            continue
        spawn_id = f"learned_{_slug(map_name)}_{_slug(name)}_{x}_{y}"
        old = existing.get(spawn_id, {})
        observations = int(old.get("observations") or 0) + 1
        item = {
            "id": spawn_id,
            "map_name": map_name,
            "metin_name": name,
            "x": x,
            "y": y,
            "confidence": min(0.99, max(float(old.get("confidence") or 0.0), 0.90 + min(observations, 9) * 0.01)),
            "source_type": "learned_live_memory",
            "observations": observations,
            "first_seen_ts": old.get("first_seen_ts") or now,
            "last_seen_ts": now,
            "last_evidence": row.get("evidence") or "live_memory_visible_text",
            "destroyed": bool(old.get("destroyed") is True or old.get("marked_destroyed") is True or int(old.get("destroyed_count") or 0) > 0),
            "destroyed_count": int(old.get("destroyed_count") or 0),
            "last_destroyed_ts": old.get("last_destroyed_ts"),
        }
        existing[spawn_id] = item
        written.append(item)
    if written:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "updated_at": now, "spawns": sorted(existing.values(), key=lambda item: (item.get("map_name", ""), item.get("metin_name", ""), item.get("x", 0), item.get("y", 0)))}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return written


def enrich_named_probe_rows_with_table_coords(
    named_rows: list[dict[str, Any]],
    table_rows: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    radius: float,
    bad_locations_path: str | Path = DEFAULT_BAD_LOCATIONS_JSON,
) -> list[dict[str, Any]]:
    """Turn a live named-VID proof into a trusted table destination when a nearby table row matches.

    The named probe proves a Metin with this name is alive in the current client, but its own
    x/y are only the current player position. This helper pairs that live proof with the nearest
    known coordinate-table row of the same name/map within the operator radius.
    """
    if not named_rows or not table_rows:
        return []
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    coord = display_coord_from_raw(float(player["x"]), float(player["y"]))
    current_map = normalize_map_name(state.get("map") or state.get("map_name"))
    enriched: list[dict[str, Any]] = []
    used_table_ids: set[str] = set()
    for named in named_rows:
        if named.get("alive") is False:
            continue
        name = str(named.get("metin_name") or "").strip().casefold()
        if not name:
            continue
        candidates: list[tuple[float, dict[str, Any]]] = []
        for table in table_rows:
            if is_bad_location(table, bad_locations_path=bad_locations_path):
                continue
            if normalize_map_name(table.get("map_name")) != current_map:
                continue
            if str(table.get("metin_name") or "").strip().casefold() != name:
                continue
            dx = float(table["x"]) - float(coord["x"])
            dy = float(table["y"]) - float(coord["y"])
            distance = math.hypot(dx, dy)
            if distance <= radius:
                candidates.append((distance, table))
        if not candidates:
            continue
        _distance, best = min(candidates, key=lambda item: (-float(item[1].get("confidence") or 0), item[0]))
        table_id = str(best.get("id") or f"{best.get('x')}_{best.get('y')}")
        if table_id in used_table_ids:
            continue
        used_table_ids.add(table_id)
        vid = named.get("vid")
        enriched.append(
            {
                "id": f"named_table_{current_map.lower()}_{vid}_{table_id}",
                "map_name": current_map,
                "metin_name": best.get("metin_name") or named.get("metin_name"),
                "x": int(best["x"]),
                "y": int(best["y"]),
                "confidence": min(0.97, max(float(best.get("confidence") or 0.0), 0.90)),
                "source_type": "table",
                "location_source_type": best.get("source_type"),
                "source_path": best.get("source_path") or "data/metin_coordinates.csv",
                "evidence": f"{named.get('evidence', '')}; table={best.get('evidence', '')}".strip("; "),
                "notes": f"Named probe confirmed alive; coordinate from table lookup for '{best.get('metin_name') or named.get('metin_name')}'. Table row: {best.get('notes') or table_id}",
                "vid": vid,
                "alive": named.get("alive"),
            }
        )
    return enriched


def matching_learned_spawn_rows(live_rows: list[dict[str, Any]], *, path: str | Path = DEFAULT_LEARNED_SPAWNS_JSON) -> list[dict[str, Any]]:
    if not live_rows:
        return []
    live_names = {str(row.get("metin_name") or "").strip().casefold() for row in live_rows if row.get("alive") is not False}
    if not live_names:
        return []
    return [row for row in load_learned_spawn_rows(path) if row.get("destroyed") is True and str(row.get("metin_name") or "").strip().casefold() in live_names]


def memory_hits_to_rows(hits: list[LiveMetinHit], *, map_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hit in hits:
        rows.append(
            {
                "id": f"live_{map_name.lower()}_{hit.metin_name.lower().replace(' ', '_')}_{hit.x}_{hit.y}",
                "map_name": normalize_map_name(map_name),
                "metin_name": hit.metin_name,
                "x": int(hit.x),
                "y": int(hit.y),
                "confidence": 0.99,
                "source_type": "live_memory_visible_text",
                "source_path": f"ReadProcessMemory address=0x{hit.address:x}",
                "notes": "Live visible Metin label text found in current client memory; address is session-local.",
                "evidence": hit.raw,
            }
        )
    return rows


def memory_name_hits_to_rows(hits: list[LiveMetinNameHit], state: dict[str, Any]) -> list[dict[str, Any]]:
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    coord = display_coord_from_raw(float(player["x"]), float(player["y"]))
    map_name = normalize_map_name(state.get("map") or state.get("map_name"))
    rows: list[dict[str, Any]] = []
    for hit in hits:
        rows.append(
            {
                "id": f"memory_name_{map_name.lower()}_{_slug(hit.metin_name)}_{hit.address:x}",
                "map_name": map_name,
                "metin_name": hit.metin_name,
                "x": int(coord["x"]),
                "y": int(coord["y"]),
                "confidence": 0.70,
                "source_type": "live_memory_name_only",
                "source_path": f"ReadProcessMemory address=0x{hit.address:x}",
                "notes": "A Metin name appeared in the same live memory region as the current target label, but no coordinate-bearing Metin label was found; coordinate is current player position estimate.",
                "evidence": hit.raw,
            }
        )
    return rows


def visual_hits_to_rows(hits: list[VisualMetinHit], state: dict[str, Any]) -> list[dict[str, Any]]:
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    coord = display_coord_from_raw(float(player["x"]), float(player["y"]))
    map_name = normalize_map_name(state.get("map") or state.get("map_name"))
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    target_name = target.get("name") if isinstance(target, dict) else None
    if not hits:
        return []
    hit = max(hits, key=lambda item: item.confidence)
    name = str(target_name).strip() if target_name and is_metin_name(target_name) else "Metin stone (visual)"
    return [
        {
            "id": f"visual_{map_name.lower()}_1_{coord['x']}_{coord['y']}",
            "map_name": map_name,
            "metin_name": name,
            "x": int(coord["x"]),
            "y": int(coord["y"]),
            "confidence": round(float(hit.confidence), 6),
            "source_type": "visual_detector_current_position_estimate",
            "source_path": "ONNX detector on MT2Portugalia window capture",
            "notes": "Detector saw a Metin on screen, but no coordinate-bearing live text was found; map coordinate is the current character position estimate.",
            "evidence": f"screen=({hit.screen_x:.1f},{hit.screen_y:.1f}) size=({hit.width:.1f}x{hit.height:.1f}) confidence={hit.confidence:.3f}",
        }
    ]


def capture_window_screenshot(
    window: Any,
    screenshot_path: str | Path,
    *,
    foreground: bool = True,
    restore_foreground: bool = True,
    settle_seconds: float = 0.35,
    user32: Any | None = None,
    image_grab: Any | None = None,
) -> Path:
    """Capture a DirectX game window via desktop pixels, foregrounding it briefly if needed.

    PIL ImageGrab captures the composed desktop, not an occluded HWND surface. Bringing the
    game to the front prevents the dashboard/WhatsApp/etc. from being captured inside the
    game's rectangle and making visual fallback falsely report no Metin.
    """
    if user32 is None:
        import ctypes

        user32 = ctypes.windll.user32
    if image_grab is None:
        from PIL import ImageGrab as image_grab

    screenshot_path = Path(screenshot_path)
    previous = None
    if foreground:
        try:
            previous = user32.GetForegroundWindow()
            user32.ShowWindow(window.hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(window.hwnd)
            time.sleep(settle_seconds)
        except Exception:
            previous = None
    image_grab.grab(bbox=window.bbox).save(screenshot_path)
    if foreground and restore_foreground and previous and previous != getattr(window, "hwnd", None):
        try:
            user32.SetForegroundWindow(previous)
        except Exception:
            pass
    return screenshot_path


def scan_visual_metins(
    *,
    window_query: str = "MT2Portugalia",
    onnx_path: str | Path = "reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.onnx",
    min_confidence: float = 0.15,
    out_dir: str | Path = "reports/nearby_metins/live_detector_probe",
) -> list[VisualMetinHit]:
    sys.path.insert(0, str(SRC_ROOT))
    from metin2_research.onnx_detector import OnnxYoloDetector
    from metin2_research.predict import build_prediction_report
    from metin2_research.screenshot_state import save_annotated_preview
    from metin2_research.window_capture import find_window

    window = find_window(window_query)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    screenshot = out / "current_window.jpg"
    capture_window_screenshot(window, screenshot)
    detector = OnnxYoloDetector(onnx_path, conf_threshold=min_confidence)
    detections = detector.detect(screenshot)
    report = build_prediction_report(screenshot, detections, min_confidence=min_confidence)
    (out / "current_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    save_annotated_preview(screenshot, report["state"], out / "current_preview.jpg")
    hits: list[VisualMetinHit] = []
    for box in report["state"].get("boxes", []):
        conf = float(box.get("confidence", 0.0))
        if conf < min_confidence:
            continue
        hits.append(
            VisualMetinHit(
                confidence=conf,
                screen_x=float(box.get("x_center", 0.0)),
                screen_y=float(box.get("y_center", 0.0)),
                width=float(box.get("width", 0.0)),
                height=float(box.get("height", 0.0)),
            )
        )
    # De-duplicate near-identical overlapping boxes from low-confidence NMS edge cases.
    deduped: list[VisualMetinHit] = []
    for hit in sorted(hits, key=lambda h: h.confidence, reverse=True):
        if any(abs(hit.screen_x - old.screen_x) < 15 and abs(hit.screen_y - old.screen_y) < 15 for old in deduped):
            continue
        deduped.append(hit)
    return deduped


def normalize_map_name(value: str | None) -> str:
    if not value:
        return "unknown_current_map"
    return MAP_ALIASES.get(str(value).strip().casefold(), str(value).strip())


def load_player_state(path: str | Path = DEFAULT_STATE_JSON) -> dict[str, Any]:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError(f"state JSON root must be an object: {path}")
    player = data.get("player")
    if not isinstance(player, dict):
        raise ValueError(f"state JSON is missing player object: {path}")
    if player.get("x") is None or player.get("y") is None:
        raise ValueError(f"state JSON is missing player x/y: {path}")
    return data


def apply_display_offset(coord: dict[str, Any], offset_x: int = 0, offset_y: int = 0) -> dict[str, int]:
    return {
        "x": int(coord["x"]) + int(offset_x),
        "y": int(coord["y"]) + int(offset_y),
        "offset_x": int(offset_x),
        "offset_y": int(offset_y),
    }


def display_coord_from_raw(raw_x: float, raw_y: float) -> dict[str, float | int]:
    return {
        "x": int(round(float(raw_x) / 100.0)),
        "y": int(round(float(raw_y) / 100.0)),
        "raw_x": float(raw_x),
        "raw_y": float(raw_y),
    }


def load_coordinate_rows(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                rows.append(
                    {
                        "id": row.get("id") or "",
                        "map_name": normalize_map_name(row.get("map_name")),
                        "metin_name": row.get("metin_name") or "Metin",
                        "x": int(float(row.get("x") or 0)),
                        "y": int(float(row.get("y") or 0)),
                        "confidence": float(row.get("confidence") or 0),
                        "source_type": row.get("source_type") or "",
                        "source_path": row.get("source_path") or "",
                        "evidence": row.get("evidence") or "",
                        "notes": row.get("notes") or "",
                    }
                )
            except Exception:
                continue
    return rows


def find_nearby_metins(
    state: dict[str, Any],
    coords_csv: str | Path = DEFAULT_COORDS_CSV,
    *,
    radius: float = 300.0,
    limit: int = 8,
    include_unknown_map: bool = False,
    rows: list[dict[str, Any]] | None = None,
    display_offset_x: int = 0,
    display_offset_y: int = 0,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    coord = display_coord_from_raw(float(player["x"]), float(player["y"]))
    map_name = normalize_map_name(state.get("map") or state.get("map_name"))
    rows = rows if rows is not None else load_coordinate_rows(coords_csv)
    if rows:
        source_types = {str(row.get("source_type")) for row in rows}
        ranked_types = sorted(source_types, key=lambda item: evidence_meta(item)[0])
        if len(ranked_types) == 1:
            source = ranked_types[0]
        else:
            source = "mixed_live_evidence"
    else:
        source = "no_candidate_rows" if rows is not None else "coordinate_table"
    metins: list[dict[str, Any]] = []
    for row in rows:
        if is_bad_location(row):
            continue
        row_map = row["map_name"]
        if row_map != map_name and not (include_unknown_map and row_map == "unknown_current_map"):
            continue
        dx = float(row["x"]) - float(coord["x"])
        dy = float(row["y"]) - float(coord["y"])
        dist = math.hypot(dx, dy)
        if dist > radius:
            continue
        rank, label = evidence_meta(str(row.get("source_type")))
        metins.append(
            {
                "id": row["id"],
                "map_name": row_map,
                "metin_name": row["metin_name"],
                "location": {"x": row["x"], "y": row["y"]},
                "display_location": apply_display_offset({"x": row["x"], "y": row["y"]}, display_offset_x, display_offset_y),
                "distance": round(dist, 1),
                "delta": {"dx": int(round(dx)), "dy": int(round(dy))},
                "confidence": row["confidence"],
                "source_type": row["source_type"],
                "evidence_rank": rank,
                "evidence_label": label,
                "source_path": row["source_path"],
                "evidence": row.get("evidence", ""),
                "notes": row["notes"],
                "vid": row.get("vid"),
                "alive": row.get("alive"),
            }
        )
    exact_memory_names = {
        str(item.get("metin_name") or "").casefold()
        for item in metins
        if item.get("source_type") == "live_memory_visible_text"
    }
    selected_target_vids = {
        str(item.get("vid"))
        for item in metins
        if item.get("source_type") == "target_selected" and item.get("vid") not in {None, "", 0, "0"}
    }
    position_estimate_types = {"target_selected", "named_metin_probe", "live_memory_name_only", "visual_detector_current_position_estimate"}
    suppressed_position_estimate_rows = 0
    live_indicators: list[dict[str, Any]] = []
    if metins:
        filtered_metins = []
        for item in metins:
            is_position_estimate = item.get("source_type") in position_estimate_types
            same_exact_memory_name = str(item.get("metin_name") or "").casefold() in exact_memory_names
            same_selected_vid = item.get("source_type") == "named_metin_probe" and str(item.get("vid")) in selected_target_vids
            if is_position_estimate:
                suppressed_position_estimate_rows += 1
                live_indicators.append(item)
                continue
            if (same_exact_memory_name or same_selected_vid) and item.get("source_type") in {"named_metin_probe", "visual_detector_current_position_estimate"}:
                suppressed_position_estimate_rows += 1
                live_indicators.append(item)
                continue
            filtered_metins.append(item)
        metins = filtered_metins
    metins.sort(key=lambda item: (int(item.get("evidence_rank") or 99), float(item["distance"]), -float(item.get("confidence") or 0)))
    if limit > 0:
        metins = metins[:limit]
    result = {
        "player": {
            "map_raw": state.get("map") or state.get("map_name"),
            "map_name": map_name,
            "coord": coord,
            "display_coord": apply_display_offset(coord, display_offset_x, display_offset_y),
            "hp": player.get("hp"),
            "max_hp": player.get("max_hp"),
        },
        "radius": radius,
        "source": source,
        "count": len(metins),
        "metins": metins,
    }
    if live_indicators:
        result["live_indicators"] = live_indicators
    if diagnostics is not None:
        result["diagnostics"] = {
            **diagnostics,
            "candidate_rows_before_radius_filter": len(rows),
            "rows_after_radius_filter": len(metins),
        }
        if suppressed_position_estimate_rows:
            result["diagnostics"]["suppressed_position_estimate_rows"] = suppressed_position_estimate_rows
    return result


def write_results(result: dict[str, Any], json_path: str | Path = DEFAULT_OUT_JSON, csv_path: str | Path = DEFAULT_OUT_CSV) -> dict[str, str]:
    json_path = Path(json_path)
    csv_path = Path(csv_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "rank",
            "metin_name",
            "x",
            "y",
            "display_x",
            "display_y",
            "display_offset_x",
            "display_offset_y",
            "distance",
            "dx",
            "dy",
            "confidence",
            "evidence_rank",
            "evidence_label",
            "vid",
            "alive",
            "map_name",
            "id",
            "source_type",
            "source_path",
            "evidence",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rank, metin in enumerate(result.get("metins", []), start=1):
            writer.writerow(
                {
                    "rank": rank,
                    "metin_name": metin.get("metin_name"),
                    "x": metin.get("location", {}).get("x"),
                    "y": metin.get("location", {}).get("y"),
                    "display_x": metin.get("display_location", {}).get("x"),
                    "display_y": metin.get("display_location", {}).get("y"),
                    "display_offset_x": metin.get("display_location", {}).get("offset_x"),
                    "display_offset_y": metin.get("display_location", {}).get("offset_y"),
                    "distance": metin.get("distance"),
                    "dx": metin.get("delta", {}).get("dx"),
                    "dy": metin.get("delta", {}).get("dy"),
                    "confidence": metin.get("confidence"),
                    "evidence_rank": metin.get("evidence_rank"),
                    "evidence_label": metin.get("evidence_label"),
                    "vid": metin.get("vid"),
                    "alive": metin.get("alive"),
                    "map_name": metin.get("map_name"),
                    "id": metin.get("id"),
                    "source_type": metin.get("source_type"),
                    "source_path": metin.get("source_path"),
                    "evidence": metin.get("evidence"),
                }
            )
    return {"json": str(json_path), "csv": str(csv_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find known Metin coordinates around the current character from live client-state JSON.")
    parser.add_argument("--state-json", default=str(DEFAULT_STATE_JSON))
    parser.add_argument("--coords-csv", default=str(DEFAULT_COORDS_CSV))
    parser.add_argument("--radius", type=float, default=300.0, help="search radius in displayed map-coordinate units")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--out-csv", default=str(DEFAULT_OUT_CSV))
    parser.add_argument("--include-unknown-map", action="store_true")
    parser.add_argument("--source", choices=("hybrid", "live-memory", "visual", "table", "both"), default="hybrid", help="hybrid tries live memory, then visual detector, then table; table uses saved coordinates")
    parser.add_argument("--process-name", default="pgclient.app")
    parser.add_argument("--window-query", default="MT2Portugalia")
    parser.add_argument("--onnx", default="reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.onnx")
    parser.add_argument("--visual-min-confidence", type=float, default=0.15)
    parser.add_argument("--display-offset-x", type=int, default=0, help="add this x offset when showing user/display coordinates")
    parser.add_argument("--display-offset-y", type=int, default=0, help="add this y offset when showing user/display coordinates")
    args = parser.parse_args(argv)

    state = load_player_state(args.state_json)
    map_name = normalize_map_name(state.get("map") or state.get("map_name"))
    rows = None
    target_rows: list[dict[str, Any]] = []
    named_rows: list[dict[str, Any]] = []
    named_table_rows: list[dict[str, Any]] = []
    live_rows: list[dict[str, Any]] = []
    memory_name_rows: list[dict[str, Any]] = []
    visual_rows: list[dict[str, Any]] = []
    learned_rows: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {"target_rows": 0, "named_probe_rows": 0, "memory_raw_hits": 0, "live_memory_rows": 0, "memory_name_rows": 0, "visual_raw_hits": 0, "visual_rows": 0}
    if args.source in {"hybrid", "both"}:
        target_rows = target_to_rows(state)
        named_rows = named_probe_to_rows(state)
        named_table_rows = enrich_named_probe_rows_with_table_coords(named_rows, load_coordinate_rows(args.coords_csv), state, radius=args.radius)
        learned_rows = matching_learned_spawn_rows(target_rows + named_rows)
        diagnostics["target_rows"] = len(target_rows)
        diagnostics["named_probe_rows"] = len(named_rows)
        diagnostics["named_table_rows"] = len(named_table_rows)
        diagnostics["matching_learned_spawn_rows"] = len(learned_rows)
    if args.source in {"hybrid", "live-memory", "both"}:
        try:
            raw_memory_hits = scan_live_metin_memory(args.process_name)
        except Exception as exc:
            raw_memory_hits = []
            diagnostics["memory_scan_error"] = str(exc)
        diagnostics["memory_raw_hits"] = len(raw_memory_hits)
        live_rows = memory_hits_to_rows(raw_memory_hits, map_name=map_name)
        record_learned_spawns(live_rows)
        diagnostics["live_memory_rows"] = len(live_rows)
        if not live_rows:
            target = state.get("target") if isinstance(state.get("target"), dict) else {}
            anchor_names = [str(target.get("name") or "").strip()] if target else []
            try:
                memory_name_hits = scan_live_metin_name_memory(args.process_name, anchor_names=anchor_names)
            except Exception as exc:
                memory_name_hits = []
                diagnostics["memory_name_scan_error"] = str(exc)
            memory_name_rows = memory_name_hits_to_rows(memory_name_hits, state)
            diagnostics["memory_name_rows"] = len(memory_name_rows)
    if args.source == "live-memory":
        rows = live_rows + memory_name_rows
    elif args.source == "visual":
        visual_hits = scan_visual_metins(window_query=args.window_query, onnx_path=args.onnx, min_confidence=args.visual_min_confidence)
        diagnostics["visual_raw_hits"] = len(visual_hits)
        visual_rows = visual_hits_to_rows(
            visual_hits,
            state,
        )
        diagnostics["visual_rows"] = len(visual_rows)
        rows = visual_rows
    elif args.source == "hybrid":
        live_candidate_rows = target_rows + live_rows + memory_name_rows + named_rows + named_table_rows + learned_rows
        live_candidate_result = find_nearby_metins(
            state,
            args.coords_csv,
            radius=args.radius,
            limit=args.limit,
            include_unknown_map=args.include_unknown_map,
            rows=live_candidate_rows,
            display_offset_x=args.display_offset_x,
            display_offset_y=args.display_offset_y,
            diagnostics={**diagnostics, "live_candidate_rows": len(live_candidate_rows)},
        )
        if live_candidate_result["metins"] or live_candidate_result.get("live_indicators"):
            rows = live_candidate_rows
        else:
            visual_hits = scan_visual_metins(window_query=args.window_query, onnx_path=args.onnx, min_confidence=args.visual_min_confidence)
            diagnostics["visual_raw_hits"] = len(visual_hits)
            visual_rows = visual_hits_to_rows(
                visual_hits,
                state,
            )
            diagnostics["visual_rows"] = len(visual_rows)
            if visual_rows:
                rows = visual_rows
            else:
                rows = []
    elif args.source == "both":
        visual_hits = scan_visual_metins(window_query=args.window_query, onnx_path=args.onnx, min_confidence=args.visual_min_confidence)
        diagnostics["visual_raw_hits"] = len(visual_hits)
        visual_rows = visual_hits_to_rows(
            visual_hits,
            state,
        )
        diagnostics["visual_rows"] = len(visual_rows)
        rows = target_rows + live_rows + memory_name_rows + named_rows + named_table_rows + visual_rows + load_learned_spawn_rows() + load_coordinate_rows(args.coords_csv)
    elif args.source == "table":
        rows = load_learned_spawn_rows() + load_coordinate_rows(args.coords_csv)
    result = find_nearby_metins(
        state,
        args.coords_csv,
        radius=args.radius,
        limit=args.limit,
        include_unknown_map=args.include_unknown_map,
        rows=rows,
        display_offset_x=args.display_offset_x,
        display_offset_y=args.display_offset_y,
        diagnostics={**diagnostics, "live_candidate_rows": len(target_rows + live_rows + memory_name_rows + named_rows + named_table_rows + learned_rows)},
    )
    paths = write_results(result, args.out_json, args.out_csv)
    print(json.dumps(result | {"artifacts": paths}, indent=2, ensure_ascii=False))
    if result["metins"]:
        for idx, metin in enumerate(result["metins"], start=1):
            loc = metin["location"]
            delta = metin["delta"]
            display_loc = metin.get("display_location") or loc
            print(
                f"#{idx} [{metin.get('evidence_label')}] {metin['metin_name']} at client=({loc['x']},{loc['y']}) "
                f"display=({display_loc['x']},{display_loc['y']}) distance={metin['distance']} delta=({delta['dx']},{delta['dy']})"
            )
    else:
        if result.get("live_indicators"):
            names = ", ".join(str(item.get("metin_name") or "Metin") for item in result.get("live_indicators", [])[:5])
            print(f"Live Metin evidence found near current position, but no exact coordinate label was available: {names}")
        elif result.get("source") == "no_candidate_rows":
            print(f"No live Metin evidence within radius={args.radius} around {result['player']['coord']} on {result['player']['map_name']} (table fallback is disabled in hybrid mode)")
        else:
            print(f"No known Metin coordinates within radius={args.radius} around {result['player']['coord']} on {result['player']['map_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
