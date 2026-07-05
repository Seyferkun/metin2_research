from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any

from .schema import GameInfo


@dataclass(frozen=True)
class CombatConfig:
    potion_hp_ratio: float = 0.80
    emergency_hp_ratio: float = 0.35
    buff_refresh_seconds: float = 35.0
    metin_contact_radius: float = 220.0
    reacquire_radius: float = 350.0


@dataclass(frozen=True)
class CombatSnapshot:
    game: GameInfo
    metin_vid: int | None
    metin_name: str
    metin_coord: list[int] | None = None
    metin_coord_source: str | None = None
    buff_active: bool | None = None
    seconds_since_buff: float | None = None
    reward_seen: bool = False


@dataclass(frozen=True)
class CombatAction:
    state: str
    command: str
    reason: str
    success: bool = False
    args: dict[str, Any] | None = None


def _hp_ratio(game: GameInfo) -> float | None:
    if not game.hp or not game.max_hp or game.max_hp <= 0:
        return None
    return game.hp / game.max_hp


def _is_metin_name(name: str | None, metin_name: str) -> bool:
    return bool(name) and "metin" in name.lower() and metin_name.lower() in name.lower()


def _is_same_metin(entity: dict[str, Any], snap: CombatSnapshot) -> bool:
    vid = entity.get("vid")
    name = str(entity.get("name") or "")
    kind = str(entity.get("kind") or "").lower()
    if snap.metin_vid and vid == snap.metin_vid:
        return True
    if "metin" in kind:
        return True
    return _is_metin_name(name, snap.metin_name)


def _named_probe_metin(snap: CombatSnapshot) -> dict[str, Any] | None:
    for entry in snap.game.named_metin_probe or []:
        name = str(entry.get("name") or "")
        if snap.metin_vid and entry.get("vid") == snap.metin_vid and entry.get("alive") is True:
            return entry
        if _is_metin_name(name, snap.metin_name) and entry.get("alive") is True:
            return entry
    return None


def _metin_entity(snap: CombatSnapshot) -> dict[str, Any] | None:
    if snap.game.target_alive is True and snap.game.target_vid and snap.metin_vid and snap.game.target_vid == snap.metin_vid:
        return {"vid": snap.game.target_vid, "name": snap.game.target_name or snap.metin_name, "kind": "metin"}
    for entity in snap.game.nearby_entities or []:
        if _is_same_metin(entity, snap):
            return entity
    if _is_metin_name(snap.game.target_name, snap.metin_name):
        return {"vid": snap.game.target_vid, "name": snap.game.target_name, "kind": "metin"}
    return None


def _hostile_adds(snap: CombatSnapshot) -> list[dict[str, Any]]:
    adds: list[dict[str, Any]] = []
    for entity in snap.game.nearby_entities or []:
        if _is_same_metin(entity, snap):
            continue
        kind = str(entity.get("kind") or "").lower()
        name = str(entity.get("name") or "")
        hostile = entity.get("hostile") is True or kind in {"mob", "monster", "hostile"}
        if hostile and name:
            adds.append(entity)
    return adds


def _distance_to_metin(snap: CombatSnapshot, metin: dict[str, Any] | None) -> float | None:
    if metin and metin.get("distance") is not None:
        return float(metin["distance"])
    if snap.metin_coord and snap.game.player_coord:
        return hypot(snap.metin_coord[0] - snap.game.player_coord[0], snap.metin_coord[1] - snap.game.player_coord[1])
    return None


def _buff_missing_or_stale(snap: CombatSnapshot, cfg: CombatConfig) -> bool:
    active = snap.game.buff_active if snap.game.buff_active is not None else snap.buff_active
    if active is False:
        return True
    if snap.seconds_since_buff is not None and snap.seconds_since_buff >= cfg.buff_refresh_seconds:
        return True
    return False


def _effective_contact_radius(snap: CombatSnapshot, cfg: CombatConfig) -> float:
    source = str(snap.metin_coord_source or "").lower()
    if source in {"table", "coordinate_table", "known_coordinate_table"}:
        return max(float(cfg.metin_contact_radius), 500.0)
    return float(cfg.metin_contact_radius)


def _reacquire_diagnostic_reason(prefix: str, snap: CombatSnapshot, distance: float | None) -> str:
    named_probe_count = len(snap.game.named_metin_probe or [])
    target_alive = snap.game.target_alive
    if distance is None:
        dist_text = "no_coord"
    else:
        dist_text = f"dist_to_coord={distance:.0f}"
    return f"{prefix} | named_probe_count={named_probe_count} | target_alive={target_alive} | {dist_text}"


def _decide_reacquire_from_trusted_coord(snap: CombatSnapshot, cfg: CombatConfig) -> CombatAction:
    distance = _distance_to_metin(snap, None)
    if snap.metin_coord and snap.game.player_coord and distance is not None:
        current = snap.game.player_coord[:2]
        if distance > cfg.reacquire_radius:
            return CombatAction(
                "REACQUIRE_METIN",
                "navigate_to_metin",
                _reacquire_diagnostic_reason(
                    "Named probe gone but trusted coord exists and distance > reacquire radius; continuing navigation",
                    snap,
                    distance,
                ),
                args={"distance": distance, "current": current, "target": snap.metin_coord},
            )
        return CombatAction(
            "REACQUIRE_METIN",
            "space_probe",
            _reacquire_diagnostic_reason("Named probe gone, near trusted coord; space probing for contact", snap, distance),
        )
    return CombatAction(
        "REACQUIRE_METIN",
        "space_probe",
        _reacquire_diagnostic_reason("Named probe gone, no trusted coord; space probing", snap, distance),
    )


def decide_combat_action(snap: CombatSnapshot, cfg: CombatConfig) -> CombatAction:
    """Choose one safe next action for a Metin combat state machine.

    This is intentionally symbolic: live scripts translate commands to bounded
    inputs. The policy never treats a mob target as failure; spawned adds are a
    combat phase, and success requires Metin entity absence.
    """

    if snap.game.target_alive is False and snap.game.target_vid and snap.metin_vid and snap.game.target_vid == snap.metin_vid:
        return CombatAction("VERIFY_DESTROYED", "stop_success", "Metin VID confirmed gone by HasInstance", success=True)

    ratio = _hp_ratio(snap.game)
    if ratio is not None and ratio <= cfg.emergency_hp_ratio:
        return CombatAction("ABORT_SAFE", "press_potion_and_disengage", "HP is below emergency threshold")
    if ratio is not None and ratio < cfg.potion_hp_ratio:
        return CombatAction("RECOVER_HP_SP", "press_potion", "HP is below potion threshold")

    if _buff_missing_or_stale(snap, cfg):
        return CombatAction("ENSURE_BUFF", "press_buff", "F1 buff is missing or stale")

    metin = _metin_entity(snap)
    adds = _hostile_adds(snap)
    named_probe = _named_probe_metin(snap)
    target_is_metin = _is_metin_name(snap.game.target_name, snap.metin_name)
    target_switched_from_tracked_metin = bool(
        snap.metin_vid
        and snap.game.target_vid
        and snap.game.target_vid != snap.metin_vid
        and not target_is_metin
    )
    target_is_add = bool(snap.game.target_name) and not target_is_metin

    if target_switched_from_tracked_metin:
        return CombatAction("KILL_ADDS", "attack_target", "target switched away from tracked Metin VID to non-Metin spawned mob target")

    if snap.game.target_alive is True and snap.game.target_vid and snap.metin_vid and snap.game.target_vid == snap.metin_vid:
        return CombatAction("ATTACK_METIN", "hold_space", "selected tracked Metin is alive; game engine target is ground truth")

    effective_contact_radius = _effective_contact_radius(snap, cfg)

    if metin is None:
        if named_probe is not None:
            distance = _distance_to_metin(snap, None)
            if distance is not None and distance > cfg.reacquire_radius:
                return CombatAction(
                    "RETURN_TO_METIN",
                    "navigate_to_metin",
                    "Metin is alive in named Metin probe; navigating to trusted coordinate before reacquire probe",
                    args={"distance": distance, "current": snap.game.player_coord[:2] if snap.game.player_coord else None, "target": snap.metin_coord},
                )
            return CombatAction("REACQUIRE_METIN", "space_probe", "Metin is alive in named Metin probe but not currently selected", args={"vid": named_probe.get("vid")})
        if snap.reward_seen:
            return CombatAction("VERIFY_DESTROYED", "stop_success", "Metin entity is absent after reward evidence", success=True)
        if snap.metin_vid is None and snap.metin_coord is None and not snap.game.target_name:
            return CombatAction(
                "NEED_METIN_TARGET",
                "stop_need_metin_target",
                "Select a Metin or provide metin_vid/metin_x/metin_y before starting live combat",
            )
        return _decide_reacquire_from_trusted_coord(snap, cfg)

    distance = _distance_to_metin(snap, metin)
    if distance is not None and distance > cfg.reacquire_radius and distance > effective_contact_radius:
        return CombatAction(
            "RETURN_TO_METIN",
            "navigate_to_metin",
            "Metin still exists but player is too far",
            args={"distance": distance, "current": snap.game.player_coord[:2] if snap.game.player_coord else None, "target": snap.metin_coord},
        )

    if target_is_add or adds:
        return CombatAction("KILL_ADDS", "attack_target", "target switched to spawned mob while Metin still exists")

    if not target_is_metin:
        reason = "Metin exists within approximate table coordinate contact radius but is not selected" if distance is not None and distance <= effective_contact_radius and effective_contact_radius > cfg.metin_contact_radius else "Metin exists nearby but is not selected"
        return CombatAction("REACQUIRE_METIN", "space_probe", reason)

    if distance is not None and distance > effective_contact_radius:
        return CombatAction(
            "VERIFY_METIN_CONTACT",
            "micro_position",
            "Metin target exists but contact distance is too high",
            args={"distance": distance, "current": snap.game.player_coord[:2] if snap.game.player_coord else None, "target": snap.metin_coord},
        )

    return CombatAction("ATTACK_METIN", "hold_space", "Metin is selected, buffed, and HP is safe")
