from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_TSV_PATH = Path(r"D:/Games/MT2Portugalia/app/hermes_state.tsv")


def _with_file_mtime(state: dict[str, Any], path: Path) -> dict[str, Any]:
    try:
        state["_file_mtime"] = path.stat().st_mtime
    except Exception:
        pass
    return state


def read_client_state(path: str | Path = DEFAULT_TSV_PATH) -> dict[str, Any]:
    """Read the newest client state from a TSV file.

    Supports either a normal header row TSV or simple key/value rows.
    Missing files return a visible unavailable state instead of raising.
    """
    p = Path(path)
    if not p.exists():
        return {"available": False, "path": str(p), "error": "state file not found"}

    text = p.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return {"available": False, "path": str(p), "error": "state file empty"}

    lines = [line for line in text.splitlines() if line.strip()]
    rows = [line.split("\t") for line in lines]
    state: dict[str, Any] = {"available": True, "path": str(p)}

    first_cell = rows[0][0].strip().lower() if rows and rows[0] else ""
    has_header = len(rows) >= 2 and len(rows[0]) > 1 and not first_cell.replace(".", "", 1).isdigit()
    if has_header:
        headers = [h.strip() for h in rows[0]]
        values = rows[-1]
        for idx, key in enumerate(headers):
            if not key:
                continue
            state[key] = values[idx].strip() if idx < len(values) else ""
        return _with_file_mtime(state, p)

    latest = rows[-1]
    if len(latest) >= 11:
        keys = [
            "timestamp_ms",
            "map",
            "x",
            "y",
            "z",
            "hp",
            "max_hp",
            "sp",
            "max_sp",
            "target_vid",
            "player_name",
            "target_name",
            "buff_active",
            "buff_remaining_seconds",
            "nearby_entities",
        ]
        for idx, key in enumerate(keys):
            if idx < len(latest):
                state[key] = latest[idx].strip()
        return _with_file_mtime(state, p)

    for row in rows:
        if len(row) >= 2:
            state[row[0].strip()] = row[1].strip()
    return _with_file_mtime(state, p)
