from __future__ import annotations

import time
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class TargetEvidence(BaseModel):
    """Selected-target evidence normalized from the control-panel state feed."""

    vid: int
    name: str = ""
    is_metin: bool = False
    is_alive: bool = True
    x: float | None = None
    y: float | None = None
    z: float | None = None
    hp_percent: float | None = None
    last_seen_timestamp: float = Field(default_factory=time.time)


class CombatTrustState(BaseModel):
    """Pydantic trust/freshness gate for the dashboard combat bridge."""

    FRESHNESS_WINDOW_SECONDS: ClassVar[float] = 2.0

    current_action: str = "IDLE"
    selected_target: TargetEvidence | None = None
    trust_level: str = "NONE"
    now: float = Field(default_factory=time.time)

    @property
    def target_age_seconds(self) -> float | None:
        if not self.selected_target:
            return None
        return max(0.0, float(self.now) - float(self.selected_target.last_seen_timestamp))

    @property
    def has_trusted_target(self) -> bool:
        age = self.target_age_seconds
        if age is None or not self.selected_target:
            return False
        return (
            self.trust_level == "HIGH_EXACT"
            and self.selected_target.is_metin
            and self.selected_target.is_alive
            and age < self.FRESHNESS_WINDOW_SECONDS
        )


class CombatAction(str, Enum):
    NEED_METIN_TARGET = "NEED_METIN_TARGET"
    DRY_RUN_IDLE = "DRY_RUN_IDLE"
    ENGAGE_TARGET = "ENGAGE_TARGET"


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    value_float = _to_float(value)
    if value_float is None:
        return None
    return int(value_float)


def _target_coord(target: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    for key in ("pixel_position", "project_position"):
        value = target.get(key)
        if isinstance(value, list) and len(value) >= 2:
            return _to_float(value[0]), _to_float(value[1]), _to_float(value[2]) if len(value) > 2 else None
    return _to_float(target.get("x")), _to_float(target.get("y")), _to_float(target.get("z"))


def combat_trust_state_from_dashboard_state(state: dict[str, Any], *, now: float | None = None) -> CombatTrustState:
    """Build the trust gate from /api/state output.

    Freshness is based on the state file mtime because the client-side
    timestamp_ms is a game/runtime clock, not Unix epoch time.
    """

    now = time.time() if now is None else float(now)
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    vid = _to_int(target.get("vid") or state.get("target_vid"))
    name = str(target.get("name") or state.get("target_name") or "")
    file_mtime = _to_float(state.get("_file_mtime"))
    last_seen = file_mtime if file_mtime is not None else _to_float(target.get("last_seen_timestamp")) or 0.0
    alive_value = target.get("alive") if "alive" in target else state.get("target_alive")
    if isinstance(alive_value, bool):
        is_alive = alive_value
    elif alive_value in (None, ""):
        is_alive = True
    else:
        is_alive = str(alive_value).strip().lower() not in {"0", "false", "dead", "none"}
    x, y, z = _target_coord(target)
    evidence = None
    if vid and vid > 0:
        evidence = TargetEvidence(
            vid=vid,
            name=name,
            is_metin="metin" in name.casefold(),
            is_alive=is_alive,
            x=x,
            y=y,
            z=z,
            hp_percent=_to_float(target.get("hp_pct") or target.get("hp_percent") or state.get("target_hp_percent")),
            last_seen_timestamp=last_seen,
        )
    trust_level = "HIGH_EXACT" if evidence and evidence.is_metin and evidence.is_alive else "NONE"
    return CombatTrustState(
        current_action=str(state.get("current_action") or "TARGETING" if evidence else "IDLE"),
        selected_target=evidence,
        trust_level=trust_level,
        now=now,
    )


def evaluate_combat_transition(state: CombatTrustState, *, dry_run: bool = True) -> CombatAction:
    if not state.has_trusted_target:
        return CombatAction.NEED_METIN_TARGET
    if dry_run:
        return CombatAction.DRY_RUN_IDLE
    return CombatAction.ENGAGE_TARGET


def state_bridge_report(state: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    trust_state = combat_trust_state_from_dashboard_state(state, now=now)
    target = trust_state.selected_target
    age = trust_state.target_age_seconds
    dry_run_action = evaluate_combat_transition(trust_state, dry_run=True).value
    live_action = evaluate_combat_transition(trust_state, dry_run=False).value
    if target is None:
        reason = "no selected target evidence"
    elif trust_state.trust_level != "HIGH_EXACT":
        reason = "target is not HIGH_EXACT trusted Metin evidence"
    elif not target.is_metin:
        reason = "selected target is not Metin-like"
    elif not target.is_alive:
        reason = "selected target is not alive"
    elif age is not None and age >= trust_state.FRESHNESS_WINDOW_SECONDS:
        reason = f"selected target evidence is stale: age={age:.2f}s"
    else:
        reason = "fresh HIGH_EXACT alive Metin target; dry-run still blocks engagement"
    return {
        "available": target is not None,
        "current_action": trust_state.current_action,
        "trust_level": trust_state.trust_level,
        "freshness_window_seconds": trust_state.FRESHNESS_WINDOW_SECONDS,
        "age_seconds": None if age is None else round(age, 3),
        "has_trusted_target": trust_state.has_trusted_target,
        "dry_run_action": dry_run_action,
        "live_action": live_action,
        "reason": reason,
        "target": None if target is None else target.model_dump(exclude={"last_seen_timestamp"}),
    }


def format_state_bridge_report(report: dict[str, Any]) -> str:
    target = report.get("target") if isinstance(report.get("target"), dict) else None
    if target:
        target_line = f"Target  {target.get('name') or 'unknown'}  VID:{target.get('vid') or '?'}  alive={target.get('is_alive')}"
    else:
        target_line = "Target  none"
    trusted = "YES" if report.get("has_trusted_target") else "NO"
    age = report.get("age_seconds")
    age_text = "unknown" if age is None else f"{float(age):.1f}s"
    return "\n".join(
        [
            "State bridge trust",
            target_line,
            f"Trusted {trusted}  level={report.get('trust_level') or 'NONE'}  age={age_text}",
            f"Dry-run {report.get('dry_run_action')}  Live {report.get('live_action')}",
            f"Reason  {report.get('reason')}",
        ]
    )
