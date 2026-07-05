from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from .schema import ClientState, GameInfo

SOURCE_NAME = "client_python_tsv"
SOURCE_DESCRIPTION = "local_read_only_client_python_state_logger"
DEFAULT_TSV_PATH = Path("D:/Games/MT2Portugalia/app/hermes_state.tsv")


def _to_int(value: str) -> int | None:
    value = value.strip()
    if value == "":
        return None
    return int(float(value))


def _to_float(value: str) -> float | None:
    value = value.strip()
    if value == "":
        return None
    return float(value)


def _to_bool(value: str) -> bool | None:
    value = value.strip().lower()
    if value == "":
        return None
    return value in {"1", "true", "yes", "on"}


def _to_entities(value: str) -> list[dict]:
    value = value.strip()
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("nearby_entities JSON must be a list")
    return [entity for entity in parsed if isinstance(entity, dict)]


def parse_tsv_state_line(line: str) -> ClientState:
    """Parse one line from hermes_state.tsv into normalized ClientState.

    Format written by the client Python logger:
    timestamp_ms, map_name, x, y, z, hp, max_hp, sp, max_sp, target_vid,
    player_name, target_name.
    """

    parts = line.rstrip("\r\n").split("\t")
    if len(parts) < 11:
        raise ValueError(f"Expected at least 11 TSV columns, got {len(parts)}")
    # Keep target_name optional and allow embedded tabs to preserve names if that ever happens.
    if len(parts) == 11:
        parts.append("")
    timestamp_ms, map_name, x, y, z, hp, max_hp, sp, max_sp, target_vid, player_name = parts[:11]
    target_name = parts[11].strip() or None
    buff_active = _to_bool(parts[12]) if len(parts) > 12 else None
    buff_remaining_seconds = _to_float(parts[13]) if len(parts) > 13 else None
    nearby_entities = _to_entities("\t".join(parts[14:])) if len(parts) > 14 else []
    ix = _to_int(x)
    iy = _to_int(y)
    iz = _to_int(z)
    game = GameInfo(
        client_timestamp_ms=_to_int(timestamp_ms),
        map_name=map_name.strip() or None,
        player_coord=[ix, iy, iz] if ix is not None and iy is not None and iz is not None else None,
        z=iz,
        hp=_to_int(hp),
        max_hp=_to_int(max_hp),
        sp=_to_int(sp),
        max_sp=_to_int(max_sp),
        target_vid=_to_int(target_vid),
        player_name=player_name.strip() or None,
        target_name=target_name,
        buff_active=buff_active,
        buff_remaining_seconds=buff_remaining_seconds,
        nearby_entities=nearby_entities,
    )
    return ClientState(game=game, sources={SOURCE_NAME: SOURCE_DESCRIPTION})


class TsvClientStateSource:
    """Read the latest valid line from the local client Python TSV state logger."""

    name = SOURCE_NAME

    def __init__(
        self,
        path: str | Path = DEFAULT_TSV_PATH,
        *,
        max_age_seconds: float | None = 2.0,
        now: Callable[[], float] | None = None,
    ):
        self.path = Path(path)
        self.max_age_seconds = max_age_seconds
        self.now = now or time.time

    def read(self) -> ClientState:
        if not self.path.exists():
            return ClientState(
                sources={SOURCE_NAME: "missing"},
                warnings=[f"Client Python TSV state file not found: {self.path}"],
            )

        warnings: list[str] = []
        if self.max_age_seconds is not None:
            age = self.now() - self.path.stat().st_mtime
            if age > self.max_age_seconds:
                warnings.append(f"Client Python TSV state is stale: age={age:.2f}s path={self.path}")

        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        parse_errors = 0
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                state = parse_tsv_state_line(line)
            except (TypeError, ValueError):
                parse_errors += 1
                continue
            state.warnings.extend(warnings)
            if parse_errors:
                state.warnings.append(f"Skipped {parse_errors} malformed trailing/client TSV line(s).")
            return state

        return ClientState(
            sources={SOURCE_NAME: "empty"},
            warnings=[f"Client Python TSV state file has no valid state lines: {self.path}", *warnings],
        )
