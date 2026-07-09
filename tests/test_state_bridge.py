from __future__ import annotations

from metin2_dashboard.state_bridge import (
    CombatAction,
    combat_trust_state_from_dashboard_state,
    evaluate_combat_transition,
    format_state_bridge_report,
    state_bridge_report,
)


def _fresh_metin_state(now: float = 100.0) -> dict:
    return {
        "_file_mtime": now - 0.5,
        "target": {
            "vid": 1234,
            "name": "Metin da Batalha",
            "alive": True,
            "hp_pct": 100.0,
            "pixel_position": [321.0, 222.0, 0.0],
        },
    }


def test_state_bridge_trusts_only_fresh_high_exact_alive_metin():
    trust_state = combat_trust_state_from_dashboard_state(_fresh_metin_state(), now=100.0)

    assert trust_state.trust_level == "HIGH_EXACT"
    assert trust_state.has_trusted_target is True
    assert trust_state.target_age_seconds == 0.5


def test_state_bridge_dry_run_blocks_engage_until_explicit_live():
    trust_state = combat_trust_state_from_dashboard_state(_fresh_metin_state(), now=100.0)

    assert evaluate_combat_transition(trust_state, dry_run=True) == CombatAction.DRY_RUN_IDLE
    assert evaluate_combat_transition(trust_state, dry_run=False) == CombatAction.ENGAGE_TARGET


def test_state_bridge_rejects_stale_non_metin_and_dead_targets():
    stale = combat_trust_state_from_dashboard_state(_fresh_metin_state(now=90.0), now=100.0)
    assert stale.has_trusted_target is False
    assert evaluate_combat_transition(stale, dry_run=False) == CombatAction.NEED_METIN_TARGET

    non_metin = _fresh_metin_state()
    non_metin["target"]["name"] = "Lobo Alfa"
    assert combat_trust_state_from_dashboard_state(non_metin, now=100.0).has_trusted_target is False

    dead = _fresh_metin_state()
    dead["target"]["alive"] = False
    assert combat_trust_state_from_dashboard_state(dead, now=100.0).has_trusted_target is False


def test_state_bridge_report_is_control_panel_friendly():
    report = state_bridge_report(_fresh_metin_state(), now=100.0)
    text = format_state_bridge_report(report)

    assert report["has_trusted_target"] is True
    assert report["dry_run_action"] == "DRY_RUN_IDLE"
    assert report["live_action"] == "ENGAGE_TARGET"
    assert "State bridge trust" in text
    assert "Dry-run DRY_RUN_IDLE" in text
