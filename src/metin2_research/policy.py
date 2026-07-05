from __future__ import annotations

from typing import Any


def decide_next_action(state: dict[str, Any]) -> dict[str, Any]:
    """Return a safe symbolic action from a structured Metin2 state.

    This policy is intentionally advisory. It produces action names, not keypresses/clicks.
    """
    if state.get("player_dead"):
        return {"action": "WAIT_FOR_RESPAWN_OR_MANUAL", "reason": "Player appears dead"}

    hp_percent = state.get("hp_percent")
    if hp_percent is not None and hp_percent < state.get("heal_threshold", 35):
        return {"action": "USE_HEAL", "reason": "HP below safe threshold"}

    if state.get("inventory_full"):
        return {"action": "RETURN_TO_TOWN", "reason": "Inventory appears full"}

    target_visible = bool(state.get("target_visible"))
    target_confirmed = bool(state.get("target_confirmed"))
    target_confidence = float(state.get("target_confidence", 0.0) or 0.0)
    min_confidence = float(state.get("min_target_confidence", 0.60))
    candidate_confidence = float(state.get("candidate_target_confidence", 0.35))

    if target_visible and target_confirmed and target_confidence >= min_confidence:
        return {
            "action": "APPROACH_OR_ATTACK",
            "reason": "Confirmed target above confidence threshold",
            "confidence": target_confidence,
            "target_xy": state.get("target_xy"),
        }

    if target_visible and target_confirmed and target_confidence >= candidate_confidence:
        return {
            "action": "INVESTIGATE_TARGET",
            "reason": "Candidate target visible below attack threshold",
            "confidence": target_confidence,
            "target_xy": state.get("target_xy"),
        }

    if state.get("no_target_seconds", 0) >= state.get("rotate_after_seconds", 5):
        return {"action": "ROTATE_CAMERA", "reason": "No target visible for too long"}

    return {"action": "SEARCH", "reason": "No high-confidence confirmed target"}
