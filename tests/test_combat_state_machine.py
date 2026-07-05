from metin2_research.client_state.schema import GameInfo
from metin2_research.client_state.combat import CombatConfig, CombatSnapshot, decide_combat_action
from metin2_research.win_input import SCANCODES


def snapshot(**overrides):
    data = {
        "game": GameInfo(
            hp=1776,
            max_hp=1776,
            sp=1278,
            max_sp=1278,
            target_name="Metin da Batalha",
            target_vid=2752330,
            player_coord=[82213, 70769, 20366],
            buff_active=True,
            nearby_entities=[
                {"vid": 2752330, "name": "Metin da Batalha", "distance": 130, "kind": "metin"}
            ],
        ),
        "metin_vid": 2752330,
        "metin_name": "Metin da Batalha",
        "metin_coord": [82327, 70834],
        "buff_active": True,
        "seconds_since_buff": 5,
    }
    data.update(overrides)
    return CombatSnapshot(**data)


def test_metin_target_switching_to_spawned_mob_enters_add_clear_not_abort():
    snap = snapshot(
        game=GameInfo(
            hp=1500,
            max_hp=1776,
            target_name="Urso Negro",
            target_vid=2784291,
            player_coord=[82213, 70769, 20366],
            buff_active=True,
            nearby_entities=[
                {"vid": 2752330, "name": "Metin da Batalha", "distance": 150, "kind": "metin"},
                {"vid": 2784291, "name": "Urso Negro", "distance": 80, "kind": "mob", "hostile": True},
            ],
        )
    )

    action = decide_combat_action(snap, CombatConfig())

    assert action.state == "KILL_ADDS"
    assert action.command == "attack_target"
    assert "spawned mob" in action.reason


def test_low_hp_potions_before_continuing_combat():
    snap = snapshot(
        game=GameInfo(
            hp=900,
            max_hp=1776,
            target_name="Metin da Batalha",
            target_vid=2752330,
            player_coord=[82213, 70769, 20366],
            buff_active=True,
            nearby_entities=[{"vid": 2752330, "name": "Metin da Batalha", "distance": 130, "kind": "metin"}],
        )
    )

    action = decide_combat_action(snap, CombatConfig(potion_hp_ratio=0.80))

    assert action.state == "RECOVER_HP_SP"
    assert action.command == "press_potion"


def test_missing_or_stale_buff_rebuffs_before_attack():
    action = decide_combat_action(
        snapshot(buff_active=False, seconds_since_buff=999),
        CombatConfig(buff_refresh_seconds=25),
    )

    assert action.state == "ENSURE_BUFF"
    assert action.command == "press_buff"


def test_metin_target_gone_but_nearby_metin_exists_reacquires_not_destroyed():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name=None,
            target_vid=0,
            player_coord=[82213, 70769, 20366],
            buff_active=True,
            nearby_entities=[{"vid": 2752330, "name": "Metin da Batalha", "distance": 140, "kind": "metin"}],
        )
    )

    action = decide_combat_action(snap, CombatConfig())

    assert action.state == "REACQUIRE_METIN"
    assert action.command == "space_probe"
    assert not action.success


def test_target_alive_false_confirms_destroyed_by_hasinstance():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name="Metin da Batalha",
            target_vid=2752330,
            target_alive=False,
            player_coord=[82213, 70769, 20366],
            buff_active=True,
            nearby_entities=[],
        )
    )

    action = decide_combat_action(snap, CombatConfig())

    assert action.state == "VERIFY_DESTROYED"
    assert action.command == "stop_success"
    assert action.success
    assert "HasInstance" in action.reason


def test_selected_tracked_metin_attacks_even_when_table_coordinate_distance_is_large():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name="Metin da Batalha",
            target_vid=2752330,
            target_alive=True,
            player_coord=[81000, 70000, 20366],
            buff_active=True,
            nearby_entities=[],
        ),
        metin_coord=[82327, 70834],
        metin_coord_source="table",
    )

    action = decide_combat_action(snap, CombatConfig(metin_contact_radius=220, reacquire_radius=350))

    assert action.state == "ATTACK_METIN"
    assert action.command == "hold_space"
    assert "selected tracked Metin" in action.reason


def test_table_coordinate_source_uses_wider_contact_radius_for_approximate_spawn_point():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name=None,
            target_vid=0,
            player_coord=[27950, 21800, 20366],
            buff_active=True,
            nearby_entities=[{"vid": 2752330, "name": "Metin da Batalha", "distance": 450, "kind": "metin"}],
        ),
        metin_coord=[28400, 21800],
        metin_coord_source="table",
    )

    action = decide_combat_action(snap, CombatConfig(metin_contact_radius=220, reacquire_radius=350))

    assert action.state == "REACQUIRE_METIN"
    assert action.command == "space_probe"
    assert "approximate table coordinate contact radius" in action.reason


def test_unselected_tracked_metin_moves_into_range_before_reacquire_when_coord_known():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name=None,
            target_vid=0,
            target_alive=None,
            player_coord=[81000, 70000, 20366],
            buff_active=True,
            nearby_entities=[],
            named_metin_probe=[{"name": "Metin da Batalha", "vid": 2752330, "alive": True}],
        ),
        metin_coord=[82327, 70834],
    )

    action = decide_combat_action(snap, CombatConfig(metin_contact_radius=220, reacquire_radius=350))

    assert action.state == "RETURN_TO_METIN"
    assert action.command == "navigate_to_metin"
    assert action.args["distance"] > 350


def test_selected_tracked_metin_attacks_when_near_even_if_coordinate_distance_exceeds_contact():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name="Metin da Batalha",
            target_vid=2752330,
            target_alive=True,
            player_coord=[82100, 70800, 20366],
            buff_active=True,
            nearby_entities=[],
        ),
        metin_coord=[82327, 70834],
    )

    action = decide_combat_action(snap, CombatConfig(metin_contact_radius=220, reacquire_radius=350))

    assert action.state == "ATTACK_METIN"
    assert action.command == "hold_space"


def test_target_alive_true_keeps_attacking_tracked_metin_without_entity_list():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name="Metin da Batalha",
            target_vid=2752330,
            target_alive=True,
            player_coord=[82213, 70769, 20366],
            buff_active=True,
            nearby_entities=[],
        )
    )

    action = decide_combat_action(snap, CombatConfig())

    assert action.state == "ATTACK_METIN"
    assert action.command == "hold_space"


def test_named_metin_probe_reacquires_when_target_lost_but_vid_alive():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name=None,
            target_vid=0,
            player_coord=[82213, 70769, 20366],
            buff_active=True,
            nearby_entities=[],
            named_metin_probe=[{"name": "Metin da Batalha", "vid": 2752330, "alive": True}],
        )
    )

    action = decide_combat_action(snap, CombatConfig())

    assert action.state == "REACQUIRE_METIN"
    assert action.command == "space_probe"
    assert "named Metin probe" in action.reason


def test_named_metin_probe_with_far_trusted_coord_navigates_before_reacquire_probe():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name=None,
            target_vid=0,
            player_coord=[54157, 20025, 17966],
            buff_active=True,
            nearby_entities=[],
            named_metin_probe=[{"name": "Metin da Batalha", "vid": 3846, "alive": True}],
        ),
        metin_vid=3846,
        metin_coord=[28400, 21800],
    )

    action = decide_combat_action(snap, CombatConfig(reacquire_radius=350))

    assert action.state == "RETURN_TO_METIN"
    assert action.command == "navigate_to_metin"
    assert action.args["target"] == [28400, 21800]
    assert action.args["distance"] > 25000
    assert "trusted coordinate" in action.reason


def test_target_switch_to_non_metin_vid_infers_add_without_entity_list():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name="Urso Negro",
            target_vid=999,
            player_coord=[82213, 70769, 20366],
            buff_active=True,
            nearby_entities=[],
        )
    )

    action = decide_combat_action(snap, CombatConfig())

    assert action.state == "KILL_ADDS"
    assert action.command == "attack_target"
    assert "target switched" in action.reason


def test_only_entity_absence_confirms_destroyed():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name=None,
            target_vid=0,
            player_coord=[82213, 70769, 20366],
            buff_active=True,
            nearby_entities=[],
        ),
        reward_seen=True,
    )

    action = decide_combat_action(snap, CombatConfig())

    assert action.state == "VERIFY_DESTROYED"
    assert action.command == "stop_success"
    assert action.success


def test_no_metin_evidence_stops_instead_of_reacquire_loop():
    snap = CombatSnapshot(
        game=GameInfo(
            hp=1820,
            max_hp=1820,
            target_name=None,
            target_vid=None,
            player_coord=[81043, 52342, 20346],
            buff_active=None,
            nearby_entities=[],
        ),
        metin_vid=None,
        metin_name="Metin da Batalha",
        metin_coord=None,
    )

    action = decide_combat_action(snap, CombatConfig())

    assert action.state == "NEED_METIN_TARGET"
    assert action.command == "stop_need_metin_target"
    assert not action.success
    assert "Select a Metin" in action.reason


def test_named_probe_missing_with_far_trusted_coord_continues_navigation_instead_of_noop_reacquire():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name=None,
            target_vid=0,
            target_alive=None,
            player_coord=[43634, 20594, 17314],
            buff_active=True,
            nearby_entities=[],
            named_metin_probe=[],
        ),
        metin_vid=3846,
        metin_coord=[28400, 21800],
        metin_coord_source="table",
    )

    action = decide_combat_action(snap, CombatConfig(reacquire_radius=350))

    assert action.state == "REACQUIRE_METIN"
    assert action.command == "navigate_to_metin"
    assert action.args["target"] == [28400, 21800]
    assert action.args["current"] == [43634, 20594]
    assert action.args["distance"] > 15000
    assert "Named probe gone" in action.reason
    assert "named_probe_count=0" in action.reason
    assert "dist_to_coord=" in action.reason


def test_named_probe_missing_near_trusted_coord_space_probes_instead_of_noop_reacquire():
    snap = snapshot(
        game=GameInfo(
            hp=1776,
            max_hp=1776,
            target_name=None,
            target_vid=0,
            target_alive=None,
            player_coord=[28300, 21790, 17314],
            buff_active=True,
            nearby_entities=[],
            named_metin_probe=[],
        ),
        metin_vid=3846,
        metin_coord=[28400, 21800],
        metin_coord_source="table",
    )

    action = decide_combat_action(snap, CombatConfig(reacquire_radius=350))

    assert action.state == "REACQUIRE_METIN"
    assert action.command == "space_probe"
    assert action.args is None
    assert "near trusted coord" in action.reason
    assert "named_probe_count=0" in action.reason
    assert "dist_to_coord=100" in action.reason


def test_win_input_knows_f1_buff_key_scan_code():
    assert SCANCODES["f1"] == 0x3B


def test_tsv_v2_parses_buff_and_nearby_entities():
    from metin2_research.client_state.tsv_state import parse_tsv_state_line

    state = parse_tsv_state_line(
        '178\tmetin2_map_a1\t10\t20\t30\t100\t200\t50\t60\t2752330\tYoshypt\tMetin da Batalha\t1\t22.5\t'
        '[{"vid":2752330,"name":"Metin da Batalha","kind":"metin","distance":120},'
        '{"vid":278,"name":"Urso Negro","kind":"mob","hostile":true,"distance":80}]\n'
    )

    assert state.game.buff_active is True
    assert state.game.buff_remaining_seconds == 22.5
    assert state.game.nearby_entities[0]["kind"] == "metin"
    assert state.game.nearby_entities[1]["hostile"] is True
