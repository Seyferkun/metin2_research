from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .schema import ClientState, GameInfo

SOURCE_NAME = "client_python_json"
SOURCE_DESCRIPTION = "local_read_only_client_python_json_state_logger"
DEFAULT_JSON_PATH = Path("D:/Games/MT2Portugalia/app/hermes_state.json")


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _list_of_floats(value: Any) -> list[float] | None:
    if not isinstance(value, list):
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def parse_json_state(data: dict[str, Any]) -> ClientState:
    player = data.get("player") if isinstance(data.get("player"), dict) else {}
    target = data.get("target") if isinstance(data.get("target"), dict) else {}

    x = _to_int(player.get("x"))
    y = _to_int(player.get("y"))
    z = _to_int(player.get("z"))
    api_probe = data.get("_api_probe") if isinstance(data.get("_api_probe"), dict) else {}
    if data.get("entity_probe") is not None:
        api_probe["entity_probe"] = data.get("entity_probe")
    if isinstance(data.get("chr_probe"), str):
        api_probe["chr_probe"] = [item for item in data.get("chr_probe", "").split(";") if item]
    elif isinstance(data.get("chr_probe"), list):
        api_probe["chr_probe"] = [str(item) for item in data.get("chr_probe", []) if item]
    for key in ("source", "target_source", "player_target_vid", "target_board_vid", "target_board_available", "target_hp_cache_vid", "target_hp_cache_age_ms", "target_errors", "target_board_error", "target_pixel_position_error", "target_project_position_error"):
        if key in target:
            api_probe[key] = target[key]

    game = GameInfo(
        map_name=data.get("map") or data.get("map_name"),
        player_coord=[x, y, z] if x is not None and y is not None and z is not None else None,
        z=z,
        hp=_to_int(player.get("hp")),
        max_hp=_to_int(player.get("max_hp")),
        sp=_to_int(player.get("sp")),
        max_sp=_to_int(player.get("max_sp")),
        player_name=player.get("name"),
        target_vid=_to_int(target.get("vid")),
        target_name=target.get("name"),
        target_alive=target.get("alive") if isinstance(target.get("alive"), bool) else None,
        target_type=_to_int(target.get("type")),
        target_hp=_to_int(target.get("hp")),
        target_max_hp=_to_int(target.get("max_hp")),
        target_hp_percent=_to_float(target.get("hp_pct")),
        target_pixel_position=_list_of_floats(target.get("pixel_position")),
        target_project_position=_list_of_floats(target.get("project_position")),
        target_race_num=_to_int(target.get("race_num")),
        target_liveness_source=target.get("alive_source"),
        nearby_entities=_list_of_dicts(data.get("nearby_entities")),
        named_metin_probe=_list_of_dicts(data.get("named_metin_probe")),
        buffs=_list_of_dicts(data.get("buffs")),
        skills=_list_of_dicts(data.get("skills")),
        quickslots=_list_of_dicts(data.get("quickslots")),
        player_stats=player.get("stats") if isinstance(player.get("stats"), dict) else (data.get("player_stats") if isinstance(data.get("player_stats"), dict) else {}),
        player_flags={
            key: player[key]
            for key in ("is_dead", "is_stunned", "is_moving", "facing_deg", "mounted")
            if key in player
        },
        api_probe=api_probe,
        client_timestamp_ms=_to_int(data.get("timestamp_ms")),
    )
    return ClientState(game=game, sources={SOURCE_NAME: SOURCE_DESCRIPTION})


class JsonClientStateSource:
    """Read atomic hermes_state.json written by the client Python logger."""

    name = SOURCE_NAME

    def __init__(
        self,
        path: str | Path = DEFAULT_JSON_PATH,
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
                warnings=[f"Client Python JSON state file not found: {self.path}"],
            )

        warnings: list[str] = []
        if self.max_age_seconds is not None:
            age = self.now() - self.path.stat().st_mtime
            if age > self.max_age_seconds:
                warnings.append(f"Client Python JSON state is stale: age={age:.2f}s path={self.path}")

        try:
            data = json.loads(self.path.read_text(encoding="utf-8", errors="replace"))
            if not isinstance(data, dict):
                raise ValueError("JSON state root must be an object")
            state = parse_json_state(data)
            state.warnings.extend(warnings)
            return state
        except Exception as exc:
            return ClientState(
                sources={SOURCE_NAME: "parse_error"},
                warnings=warnings + [f"Failed to parse Client Python JSON state {self.path}: {exc}"],
            )
