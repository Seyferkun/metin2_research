#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("D:/Games/MT2Portugalia/app/hermes_state.json")


def _summarize_target(target: Any) -> str:
    if not isinstance(target, dict) or not target:
        return str(target)
    parts = []
    for key in ("vid", "name", "alive", "type", "hp_pct", "race_num"):
        if key in target:
            parts.append(f"{key}={target.get(key)}")
    return " ".join(parts) if parts else str(target)


def summarize_state(state: dict[str, Any]) -> str:
    p = state.get("player") if isinstance(state.get("player"), dict) else {}
    return (
        f"[{state['timestamp_ms']}] map={state.get('map')} "
        f"hp={p.get('hp')}/{p.get('max_hp')} pos=({p.get('x')},{p.get('y')}"
        + (f",{p.get('z')}" if p.get("z") is not None else "")
        + ") "
        f"entities={len(state.get('nearby_entities') or [])} "
        f"buffs={len(state.get('buffs') or [])} "
        f"target={_summarize_target(state.get('target'))}"
        + (f" entity_probe={state.get('entity_probe')}" if state.get("entity_probe") is not None else "")
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Watch the client Python hermes_state.json export.")
    ap.add_argument("path", nargs="?", default=str(DEFAULT_PATH))
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    path = Path(args.path)
    while True:
        try:
            print(summarize_state(json.loads(path.read_text(encoding="utf-8"))))
        except Exception as exc:
            print(f"read error: {exc}")
        if args.once:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
