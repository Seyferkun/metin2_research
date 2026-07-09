import json
import subprocess
import sys
from pathlib import Path

from types import SimpleNamespace

import pytest

import scripts.combat_metin_client_state as cmcs
from metin2_research.client_state.combat import CombatAction
from scripts.combat_metin_client_state import DistanceTracker, NavMilestones, ProbeLossGrace, apply_exact_target_evidence, apply_session_start_metin_choice, attack_nearby_destroy_detected, channel_click_screen_point, choose_camera_sweep_key_from_vectors, choose_exact_target_evidence_from_game, choose_movement_key_toward_metin, choose_movement_step_toward_metin, choose_movement_steps_toward_metin, choose_session_start_metin, choose_unstuck_key, movement_stuck, format_structured_log_line, normalize_metin_coord, parse_channel_click_points, probe_best_key, read_game, run_channel_rotation_sequence, run_live_command, run_pickup_spam_sequence, run_session_start_scan, should_decay_after_divergence, should_stop, load_buff_config, load_combat_config, choose_nearby_mob_attack, choose_mouse_target_action, buff_config_from_cli, due_buff_actions, press_buff_key_via_key_macro, mounted_state_from_game


def test_read_game_prefers_fresh_json_state_when_tsv_missing(tmp_path):
    json_path = tmp_path / "hermes_state.json"
    tsv_path = tmp_path / "missing.tsv"
    json_path.write_text(
        json.dumps(
            {
                "timestamp_ms": 1,
                "map": "metin2_map_a1",
                "player": {"name": "Yoshypt", "x": 10, "y": 20, "z": 30, "hp": 111, "max_hp": 222},
                "target": {"vid": 777, "name": "Metin da Batalha", "alive": True},
                "nearby_entities": [],
            }
        ),
        encoding="utf-8",
    )

    game = read_game(tsv_path, json_path=json_path, max_age_seconds=10)

    assert game.map_name == "metin2_map_a1"
    assert game.player_coord == [10, 20, 30]
    assert game.hp == 111
    assert game.target_vid == 777


def test_read_game_error_reports_json_when_missing(tmp_path):
    with pytest.raises(RuntimeError) as excinfo:
        read_game(tmp_path / "ignored.tsv", json_path=tmp_path / "missing.json", max_age_seconds=10)

    message = str(excinfo.value)
    assert "JSON state file not found" in message
    assert "TSV state file not found" not in message


def test_read_game_rejects_stale_json_state(tmp_path):
    json_path = tmp_path / "hermes_state.json"
    json_path.write_text(json.dumps({"map": "metin2_map_a1", "player": {"x": 1, "y": 2, "z": 3}}), encoding="utf-8")
    import os, time
    old = time.time() - 10
    os.utime(json_path, (old, old))

    with pytest.raises(RuntimeError, match="stale"):
        read_game(tmp_path / "ignored.tsv", json_path=json_path, max_age_seconds=2)


def test_should_stop_detects_stop_file(tmp_path):
    stop_file = tmp_path / "run-1.stop"
    assert should_stop(stop_file) is False
    stop_file.touch()
    assert should_stop(stop_file) is True



def test_combat_and_buff_configs_validate_defaults_and_flags(tmp_path):
    combat_path = tmp_path / "combat.json"
    buff_path = tmp_path / "buffs.json"
    combat_path.write_text(json.dumps({"attack_nearby_mobs": True}), encoding="utf-8")
    buff_path.write_text(json.dumps({"buffs": [{"key": "f2", "enabled": True, "interval_seconds": 42, "pre_cast_seconds": 4}]}), encoding="utf-8")

    assert load_combat_config(combat_path)["attack_nearby_mobs"] is True
    buffs = load_buff_config(buff_path)
    assert buffs["buffs"][0]["key"] == "f2"
    assert buffs["buffs"][0]["interval_seconds"] == 42.0
    assert load_combat_config(tmp_path / "missing.json")["attack_nearby_mobs"] is False


def test_choose_nearby_mob_attack_is_conservative_and_config_gated():
    game = SimpleNamespace(
        target_name="Wild Dog",
        target_vid=10,
        target_alive=True,
        nearby_entities=[],
    )

    assert choose_nearby_mob_attack(game, enabled=False) is None
    action = choose_nearby_mob_attack(game, enabled=True)
    assert action.state == "ATTACK_NEARBY_MOBS"
    assert action.command == "attack_target"
    assert action.args["mob"]["name"] == "Wild Dog"

    unselected = SimpleNamespace(target_name=None, target_vid=None, target_alive=None, nearby_entities=[{"vid": 10, "name": "Wild Dog", "kind": "mob", "hostile": True, "distance": 180}])
    assert choose_nearby_mob_attack(unselected, enabled=True) is None

def test_normalize_metin_coord_converts_display_coords_to_raw_client_units():
    assert normalize_metin_coord(846, 442) == [84600, 44200]
    assert normalize_metin_coord(84600, 44200) == [84600, 44200]
    assert normalize_metin_coord(None, 442) is None


def test_choose_session_start_metin_uses_first_trusted_live_exact_coordinate():
    result = {
        "count": 2,
        "metins": [
            {
                "metin_name": "Metin do Combate",
                "location": {"x": 453, "y": 623},
                "source_type": "live_memory_visible_text",
                "vid": None,
                "alive": None,
            },
            {
                "metin_name": "Metin da Batalha",
                "location": {"x": 284, "y": 218},
                "source_type": "table",
                "vid": 3846,
                "alive": True,
            },
        ],
    }

    choice = choose_session_start_metin(result)

    assert choice == {
        "metin_name": "Metin do Combate",
        "metin_x": 453,
        "metin_y": 623,
        "metin_vid": None,
        "metin_coord_source": "live_memory_visible_text",
    }


def test_choose_session_start_metin_rejects_current_position_estimate_rows():
    result = {
        "count": 2,
        "metins": [
            {
                "metin_name": "Metin da Batalha",
                "location": {"x": 305, "y": 189},
                "source_type": "named_metin_probe",
                "vid": 3846,
                "alive": True,
            },
            {
                "metin_name": "Metin stone (visual)",
                "location": {"x": 305, "y": 189},
                "source_type": "visual_detector_current_position_estimate",
            },
        ],
    }

    assert choose_session_start_metin(result) is None


def test_choose_session_start_metin_rejects_table_rows_from_scan_without_exact_live_memory():
    result = {
        "count": 1,
        "metins": [
            {
                "metin_name": "Metin da Batalha",
                "location": {"x": 284, "y": 218},
                "source_type": "table",
                "vid": 3846,
                "alive": True,
            },
        ],
    }

    assert choose_session_start_metin(result) is None


def test_run_session_start_scan_invokes_hybrid_find_and_returns_choice(tmp_path):
    calls = []

    def fake_run(command, *, cwd, capture_output, text, timeout):
        calls.append((command, cwd, capture_output, text, timeout))
        payload = {
            "count": 1,
            "metins": [
                {
                    "metin_name": "Metin do Combate",
                    "location": {"x": 453, "y": 623},
                    "source_type": "live_memory_visible_text",
                    "vid": None,
                    "alive": None,
                }
            ],
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    choice, result = run_session_start_scan(radius=600, limit=8, runner=fake_run, project_root=tmp_path)

    assert choice["metin_x"] == 453
    assert choice["metin_y"] == 623
    assert choice["metin_coord_source"] == "live_memory_visible_text"
    command = calls[0][0]
    assert command[1:4] == ["scripts/find_nearby_metins.py", "--source", "hybrid"]
    assert "--radius" in command and "600" in command
    assert result["count"] == 1


def test_apply_session_start_metin_choice_overrides_stale_target_args():
    args = SimpleNamespace(metin_name="Metin da Batalha", metin_vid=3846, metin_x=284, metin_y=218, metin_coord_source="table")
    choice = {
        "metin_name": "Metin do Combate",
        "metin_vid": None,
        "metin_x": 453,
        "metin_y": 623,
        "metin_coord_source": "live_memory_visible_text",
    }

    apply_session_start_metin_choice(args, choice)

    assert args.metin_name == "Metin do Combate"
    assert args.metin_vid is None
    assert args.metin_x == 453
    assert args.metin_y == 623
    assert args.metin_coord_source == "live_memory_visible_text"


def test_choose_exact_target_evidence_accepts_manual_selected_metin_with_project_position():
    game = SimpleNamespace(
        target_name="Metin da Batalha",
        target_vid=2752330,
        target_alive=True,
        target_pixel_position=[641.5, 392.0],
        target_project_position=[82213.0, 70769.0, 20366.0],
        target_liveness_source="has_instance",
        target_type=None,
        target_race_num=8001,
    )

    evidence = choose_exact_target_evidence_from_game(game, "Metin")

    assert evidence == {
        "metin_name": "Metin da Batalha",
        "metin_vid": 2752330,
        "metin_x": 82213,
        "metin_y": 70769,
        "metin_coord_source": "target_selected_project_position",
        "evidence_source": "manual_selected_target",
        "target_pixel_position": [641.5, 392.0],
        "target_project_position": [82213.0, 70769.0, 20366.0],
        "target_liveness_source": "has_instance",
    }


def test_choose_exact_target_evidence_rejects_selected_metin_without_exact_position():
    game = SimpleNamespace(
        target_name="Metin da Batalha",
        target_vid=2752330,
        target_alive=True,
        target_pixel_position=None,
        target_project_position=None,
        target_liveness_source="has_instance",
        target_type=None,
        target_race_num=8001,
    )

    assert choose_exact_target_evidence_from_game(game, "Metin") is None


def test_apply_exact_target_evidence_locks_manual_selected_target():
    args = SimpleNamespace(metin_name="Metin", metin_vid=None, metin_x=None, metin_y=None, metin_coord_source=None)
    evidence = {
        "metin_name": "Metin da Batalha",
        "metin_vid": 2752330,
        "metin_x": 82213,
        "metin_y": 70769,
        "metin_coord_source": "target_selected_project_position",
    }

    assert apply_exact_target_evidence(args, evidence) is True
    assert args.metin_name == "Metin da Batalha"
    assert args.metin_vid == 2752330
    assert args.metin_x == 82213
    assert args.metin_y == 70769
    assert args.metin_coord_source == "target_selected_project_position"


def test_choose_movement_key_toward_metin_uses_learned_navigation_model():
    action_args = {"target": [1000, 0], "current": [0, 0], "distance": 1000.0}
    model = {
        "w": {"mean_delta_xy": [100.0, 0.0]},
        "s": {"mean_delta_xy": [-100.0, 0.0]},
        "a": {"mean_delta_xy": [0.0, -100.0]},
        "d": {"mean_delta_xy": [0.0, 100.0]},
    }

    assert choose_movement_key_toward_metin(action_args, model) == "w"


def test_choose_movement_step_toward_metin_uses_proportional_hold_and_cap():
    model = {
        "w": {"mean_delta_xy": [437.0, -49.0], "mean_distance_xy": 439.739},
        "a": {"mean_delta_xy": [-102.0, -427.0], "mean_distance_xy": 439.014},
        "s": {"mean_delta_xy": [-155.0, 86.0], "mean_distance_xy": 177.26},
        "d": {"mean_delta_xy": [101.0, 422.0], "mean_distance_xy": 433.918},
    }

    key, hold = choose_movement_step_toward_metin(
        {"target": [1000, 0], "current": [0, 0], "distance": 1000.0},
        model,
        calibrated_hold_seconds=0.6,
        max_hold=0.45,
    )

    assert key == "w"
    assert hold == 0.45


def test_choose_movement_step_toward_metin_scales_down_near_target():
    model = {"w": {"mean_delta_xy": [437.0, 0.0], "mean_distance_xy": 437.0}}

    key, hold = choose_movement_step_toward_metin(
        {"target": [100, 0], "current": [0, 0], "distance": 100.0},
        model,
        calibrated_hold_seconds=0.6,
        max_hold=0.45,
    )

    assert key == "w"
    assert 0.08 <= hold < 0.2


def test_choose_movement_steps_toward_metin_uses_two_key_decomposition_for_diagonal_target():
    model = {
        "w": {"mean_delta_xy": [-89.2, 255.0], "mean_distance_xy": 270.152},
        "a": {"mean_delta_xy": [263.6, 60.2], "mean_distance_xy": 270.387},
        "s": {"mean_delta_xy": [30.8, -267.2], "mean_distance_xy": 268.97},
        "d": {"mean_delta_xy": [-263.6, -61.0], "mean_distance_xy": 270.567},
    }

    steps = choose_movement_steps_toward_metin(
        {"target": [28400, 21800], "current": [54157, 20025], "distance": 25818.0},
        model,
        max_hold=0.45,
    )

    assert len(steps) == 2
    assert steps[0][0] == {"d"}
    assert steps[1][0] == {"w"}
    assert all(0.08 <= hold <= 0.45 for _, hold in steps)


def test_distance_tracker_detects_stuck_diverging_and_trend():
    tracker = DistanceTracker(window=4)
    for distance in [1000, 980, 970, 965]:
        tracker.update(distance)
    assert tracker.is_stuck(threshold=80) is True
    assert tracker.recent_trend() == -35
    assert tracker.best_distance() == 965

    diverging = DistanceTracker(window=4)
    for distance in [900, 920, 945]:
        diverging.update(distance)
    assert diverging.is_diverging() is True


def test_should_decay_after_divergence_requires_real_prior_progress():
    tracker = DistanceTracker(window=6)
    for distance in [15000, 12000, 11500, 11800, 12200, 12600]:
        tracker.update(distance)
    assert tracker.is_diverging() is True
    assert should_decay_after_divergence(tracker) is True

    no_progress = DistanceTracker(window=6)
    for distance in [15000, 15100, 15200]:
        no_progress.update(distance)
    assert no_progress.is_diverging() is True
    assert should_decay_after_divergence(no_progress) is False


def test_nav_milestones_emit_each_threshold_once_when_crossed():
    milestones = NavMilestones([15000, 10000, 5000])

    first = milestones.update(previous_distance=16000, current_distance=9400, cycle=12, elapsed_seconds=3.4)
    second = milestones.update(previous_distance=9400, current_distance=4300, cycle=13, elapsed_seconds=4.1)
    repeat = milestones.update(previous_distance=4300, current_distance=4200, cycle=14, elapsed_seconds=5.0)

    assert first == [
        {"milestone_raw": 15000, "milestone_display": 150, "cycle": 12, "elapsed_s": 3.4},
        {"milestone_raw": 10000, "milestone_display": 100, "cycle": 12, "elapsed_s": 3.4},
    ]
    assert second == [{"milestone_raw": 5000, "milestone_display": 50, "cycle": 13, "elapsed_s": 4.1}]
    assert repeat == []


def test_probe_best_key_measures_each_key_and_updates_online_model():
    from metin2_research.client_state.navigation import OnlineNavModel

    positions = {
        "w": ((1000, 1000), (1100, 1000)),
        "a": ((1000, 1000), (1000, 1100)),
        "s": ((1000, 1000), (700, 1000)),
        "d": ((1000, 1000), (1000, 700)),
    }
    current_key = {"value": None}
    calls = []

    def read_pos():
        key = current_key["value"]
        if key is None:
            return (1000, 1000)
        before, after = positions[key]
        if calls and calls[-1] == ("held", key):
            return after
        return before

    def hold(key, seconds):
        current_key["value"] = key
        calls.append(("held", key))

    model = OnlineNavModel({k: {"mean_delta_xy": [0, 0], "mean_distance_xy": 100} for k in "wasd"})
    best_key, best_dist = probe_best_key(
        read_player_pos=read_pos,
        target=(500, 1000),
        online_model=model,
        hold_func=hold,
        sleep_func=lambda _seconds: None,
        hold=0.3,
        keys=("w", "a", "s", "d"),
    )

    assert best_key == "s"
    assert best_dist == 200
    assert all(len(model.live_obs[key]) == 1 for key in "wasd")


def test_movement_stuck_detects_small_position_delta():
    assert movement_stuck([1000, 1000, 0], [1020, 1030, 0], threshold=50.0) is True
    assert movement_stuck([1000, 1000, 0], [1060, 1000, 0], threshold=50.0) is False
    assert movement_stuck(None, [1000, 1000, 0], threshold=50.0) is False


def test_choose_unstuck_key_uses_perpendicular_cycle():
    assert choose_unstuck_key("w") == "a"
    assert choose_unstuck_key("a") == "s"
    assert choose_unstuck_key("s") == "d"
    assert choose_unstuck_key("d") == "w"
    assert choose_unstuck_key(None) == "a"


def test_format_structured_log_line_contains_state_action_hp_target():
    line = format_structured_log_line(
        state="ATTACK_METIN",
        action="hold_space",
        hp=1820,
        max_hp=1820,
        sp=1200,
        max_sp=1300,
        target_name="Metin da Batalha",
        target_vid=4821,
        target_alive=True,
    )
    assert "[state=ATTACK_METIN]" in line
    assert "[action=hold_space]" in line
    assert "[hp=1820/1820]" in line
    assert "[sp=1200/1300]" in line
    assert "[target=Metin da Batalha vid=4821 alive=True]" in line


def test_combat_script_writes_structured_report_on_max_cycles(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(
        json.dumps(
            {
                "timestamp_ms": 1,
                "map": "metin2_map_a1",
                "player": {"name": "Yoshypt", "x": 10, "y": 20, "z": 30, "hp": 222, "max_hp": 222, "sp": 80, "max_sp": 80},
                "target": {"vid": 777, "name": "Metin da Batalha", "alive": True, "alive_source": "chr.HasInstance", "type": 2, "pixel_position": [640.0, 390.0], "project_position": [10.0, 20.0, 30.0]},
                "nearby_entities": [],
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "combat.jsonl"
    run_id = "run-report-test"
    report_path = tmp_path / "reports" / "dashboard_runs" / f"{run_id}_report.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/combat_metin_client_state.py",
            "--json-state",
            str(state_path),
            "--max-cycles",
            "1",
            "--run-id",
            run_id,
            "--out",
            str(out_path),
            "--report-dir",
            str(report_path.parent),
            "--metin-name",
            "Metin da Batalha",
        ],
        cwd=".",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert events[0]["state"] == "EXACT_TARGET_LOCKED"
    assert events[0]["exact_target_evidence"]["evidence_source"] == "manual_selected_target"
    assert events[0]["exact_target_evidence"]["metin_vid"] == 777
    assert events[1]["state"] == "ATTACK_METIN"
    assert report["run_id"] == run_id
    assert report["outcome"] == "max_cycles"
    assert report["metin_name"] == "Metin da Batalha"
    assert report["metin_vid"] == 777
    assert report["hp_at_end"] == 222
    assert report["max_hp"] == 222
    assert report["potions_used"] == 0
    assert report["stop_reason"] == "max_cycles"
    assert report["states_visited"] == ["ATTACK_METIN"]
    assert report["duration_seconds"] >= 0


def test_combat_script_allows_explicit_selected_vid_without_projection_when_flagged(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(
        json.dumps(
            {
                "timestamp_ms": 1,
                "map": "metin2_map_a1",
                "player": {"name": "Yoshypt", "x": 10, "y": 20, "z": 30, "hp": 222, "max_hp": 222, "sp": 80, "max_sp": 80},
                "target": {"vid": 777, "name": "Metin da Batalha", "alive": True, "alive_source": "chr.HasInstance", "type": 2},
                "nearby_entities": [],
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "combat.jsonl"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/combat_metin_client_state.py",
            "--json-state",
            str(state_path),
            "--max-cycles",
            "1",
            "--out",
            str(out_path),
            "--metin-name",
            "Metin da Batalha",
            "--allow-selected-vid-without-exact-coords",
        ],
        cwd=".",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert events[0]["state"] == "TRACKED_VID_ATTACK_WITHOUT_EXACT_COORDS"
    assert events[0]["target_vid"] == 777
    assert events[0]["movement_allowed"] is False
    assert events[1]["state"] == "ATTACK_METIN"
    assert "selected tracked Metin" in events[1]["reason"]


def test_combat_script_navigation_event_logs_online_model_summary(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(
        json.dumps(
            {
                "timestamp_ms": 1,
                "map": "metin2_map_a1",
                "player": {"name": "Yoshypt", "x": 1000, "y": 1000, "z": 30, "hp": 222, "max_hp": 222, "sp": 80, "max_sp": 80},
                "target": None,
                "named_metin_probe": [{"name": "Metin da Batalha", "vid": 777, "alive": True, "type": 2}],
                "nearby_entities": [],
            }
        ),
        encoding="utf-8",
    )
    nav_path = tmp_path / "nav.json"
    nav_path.write_text(
        json.dumps(
            {
                "navigation_model": {
                    "w": {"mean_delta_xy": [100.0, 0.0], "mean_distance_xy": 100.0},
                    "a": {"mean_delta_xy": [0.0, 100.0], "mean_distance_xy": 100.0},
                    "s": {"mean_delta_xy": [-100.0, 0.0], "mean_distance_xy": 100.0},
                    "d": {"mean_delta_xy": [0.0, -100.0], "mean_distance_xy": 100.0},
                }
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "combat.jsonl"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/combat_metin_client_state.py",
            "--json-state",
            str(state_path),
            "--navigation-model",
            str(nav_path),
            "--max-cycles",
            "1",
            "--out",
            str(out_path),
            "--metin-name",
            "Metin da Batalha",
            "--metin-vid",
            "777",
            "--metin-x",
            "5000",
            "--metin-y",
            "1000",
            "--metin-coord-source",
            "table",
        ],
        cwd=".",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(out_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["command"] == "navigate_to_metin"
    assert event["args"]["nav_steps"]
    assert event["args"]["nav_model_summary"]["w"] == {"n_live": 0, "cal": [100.0, 0.0], "current": [100.0, 0.0]}


def test_probe_loss_grace_keeps_attacking_for_two_missing_probe_cycles_after_tracked_attack():
    tracker = ProbeLossGrace(grace_cycles=2)
    attacking_game = SimpleNamespace(
        target_vid=3846,
        target_alive=True,
        target_name="Metin da Batalha",
        named_metin_probe=[{"name": "Metin da Batalha", "vid": 3846, "alive": True}],
    )
    missing_game = SimpleNamespace(target_vid=0, target_alive=None, target_name=None, named_metin_probe=[])
    attack = CombatAction("ATTACK_METIN", "hold_space", "selected tracked Metin")
    reacquire = CombatAction("REACQUIRE_METIN", "navigate_to_metin", "Named probe gone", args={"target": [28400, 21800]})

    assert tracker.apply(attack, attacking_game, 3846, "Metin da Batalha") is attack
    grace1 = tracker.apply(reacquire, missing_game, 3846, "Metin da Batalha")
    grace2 = tracker.apply(reacquire, missing_game, 3846, "Metin da Batalha")
    grace3 = tracker.apply(reacquire, missing_game, 3846, "Metin da Batalha")

    assert grace1.command == "hold_space"
    assert grace2.command == "hold_space"
    assert "probe loss grace" in grace1.reason
    assert grace3 is reacquire


def test_return_to_last_metin_coord_without_args_space_probes_instead_of_noop(monkeypatch):
    calls = []
    monkeypatch.setattr("scripts.combat_metin_client_state.key_down", lambda key: calls.append(("down", key)))
    monkeypatch.setattr("scripts.combat_metin_client_state.key_up", lambda key: calls.append(("up", key)))
    monkeypatch.setattr("scripts.combat_metin_client_state.time.sleep", lambda seconds: calls.append(("sleep", round(seconds, 3))))

    run_live_command(
        CombatAction("REACQUIRE_METIN", "return_to_last_metin_coord", "no coord", args=None),
        SimpleNamespace(burst_seconds=2.5, move_step_seconds=0.35, micro_move_seconds=0.12, potion_key="1", buff_key="f1"),
        {},
    )

    assert calls == [("down", "space"), ("sleep", 0.8), ("up", "space")]




def test_choose_mouse_target_action_clicks_visible_metin_or_mob_when_enabled_without_target():
    game = SimpleNamespace(target_name=None, target_vid=0, target_alive=None, nearby_entities=[
        {"vid": 10, "name": "Wild Dog", "kind": "mob", "hostile": True, "distance": 180, "pixel_position": [510, 330]},
        {"vid": 20, "name": "Metin da Batalha", "kind": "metin", "distance": 240, "pixel_position": [640, 360]},
    ])

    assert choose_mouse_target_action(game, enabled=False) is None
    action = choose_mouse_target_action(game, enabled=True)
    assert action.state == "ACQUIRE_TARGET"
    assert action.command == "mouse_target_entity"
    assert action.args["pixel_position"] == [640, 360]
    assert action.args["entity"]["name"] == "Metin da Batalha"


def test_combat_script_dry_run_mouse_targets_when_visible_entity_has_pixel_position(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "timestamp_ms": 1,
        "map": "metin2_map_a1",
        "player": {"name": "Yoshypt", "x": 10, "y": 20, "z": 30, "hp": 222, "max_hp": 222, "sp": 80, "max_sp": 80},
        "target": {"vid": 0, "name": "", "alive": None},
        "nearby_entities": [{"vid": 888, "name": "Wild Dog", "kind": "mob", "hostile": True, "distance": 120, "pixel_position": [510, 330]}],
        "buffs": [],
    }), encoding="utf-8")
    combat_config = tmp_path / "combat.json"
    combat_config.write_text(json.dumps({"attack_nearby_mobs": True}), encoding="utf-8")
    out_path = tmp_path / "combat.jsonl"

    env = dict(**__import__('os').environ, METIN2_COMBAT_CONFIG=str(combat_config))
    result = subprocess.run([
        sys.executable,
        "scripts/combat_metin_client_state.py",
        "--json-state", str(state_path),
        "--max-cycles", "1",
        "--out", str(out_path),
        "--no-session-start-scan",
    ], cwd=".", env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)

    assert result.returncode == 0, result.stderr + result.stdout
    events = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert events[0]["state"] == "ACQUIRE_TARGET"
    assert events[0]["command"] == "mouse_target_entity"
    assert events[0]["args"]["pixel_position"] == [510, 330]


def test_attack_nearby_mobs_monitors_selected_metin_auto_attack_without_exact_coords(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "timestamp_ms": 1,
        "map": "metin2_map_a1",
        "player": {"name": "Yoshypt", "x": 10, "y": 20, "z": 30, "hp": 222, "max_hp": 222, "sp": 80, "max_sp": 80},
        "target": {"vid": 777, "name": "Metin da Batalha", "alive": True},
        "nearby_entities": [],
        "buffs": [],
    }), encoding="utf-8")
    combat_config = tmp_path / "combat.json"
    combat_config.write_text(json.dumps({"attack_nearby_mobs": True}), encoding="utf-8")
    out_path = tmp_path / "combat.jsonl"

    env = dict(**__import__('os').environ, METIN2_COMBAT_CONFIG=str(combat_config))
    result = subprocess.run([
        sys.executable,
        "scripts/combat_metin_client_state.py",
        "--json-state", str(state_path),
        "--max-cycles", "1",
        "--out", str(out_path),
        "--no-session-start-scan",
    ], cwd=".", env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)

    assert result.returncode == 0, result.stderr + result.stdout
    events = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(event["state"] == "AUTO_ATTACKING_TARGET" and event["command"] == "hold_space" for event in events)
    assert not any(event["state"] == "NEED_EXACT_TARGET" for event in events)



def test_search_for_target_prefers_visual_metin_click_over_blind_probe(monkeypatch):
    calls = []
    fake_window = SimpleNamespace(bbox=(100, 200, 900, 800), width=800, height=600)
    monkeypatch.setattr(cmcs, "find_window", lambda query: fake_window)
    monkeypatch.setattr(cmcs, "detect_visible_metin_click_point", lambda window, args: (333, 444, {"detection": {"confidence": 0.9}}))
    monkeypatch.setattr(cmcs, "tap_key", lambda key, hold=0.0: calls.append(("tap", key, hold)))
    monkeypatch.setattr(cmcs, "hold_key", lambda key, hold=0.0: calls.append(("hold", key, hold)))
    monkeypatch.setattr(cmcs, "click_at", lambda x, y: calls.append(("click", x, y)))
    monkeypatch.setattr(cmcs.time, "sleep", lambda _seconds: None)
    action = CombatAction("SEARCH_FOR_TARGET", "search_for_target", "test", args={"cycle": 0})
    args = SimpleNamespace(visual_target_clicks=True, allow_blind_target_clicks=True, target_search_move_seconds=0.25, burst_seconds=0.8)

    run_live_command(action, args, {})

    assert ("tap", "tab", 0.06) in calls
    assert ("click", 333, 444) in calls
    assert not any(call[0] == "hold" and call[1] == "w" for call in calls)


def test_search_for_target_visual_click_failure_continues_to_patrol(monkeypatch):
    calls = []
    fake_window = SimpleNamespace(bbox=(100, 200, 900, 800), width=800, height=600)
    monkeypatch.setattr(cmcs, "find_window", lambda query: fake_window)
    monkeypatch.setattr(cmcs, "detect_visible_metin_click_point", lambda window, args: (333, 444, {"detection": {"confidence": 0.9}}))
    monkeypatch.setattr(cmcs, "tap_key", lambda key, hold=0.0: calls.append(("tap", key, hold)))
    monkeypatch.setattr(cmcs, "hold_key", lambda key, hold=0.0: calls.append(("hold", key, hold)))
    monkeypatch.setattr(cmcs, "click_at", lambda x, y: (_ for _ in ()).throw(OSError("SetCursorPos failed")))
    monkeypatch.setattr(cmcs.time, "sleep", lambda _seconds: None)
    action = CombatAction("SEARCH_FOR_TARGET", "search_for_target", "test", args={"cycle": 0})
    args = SimpleNamespace(visual_target_clicks=True, allow_blind_target_clicks=False, target_search_move_seconds=0.25, burst_seconds=0.8)

    run_live_command(action, args, {})

    assert ("tap", "tab", 0.06) in calls
    assert ("hold", "w", 0.25) in calls
    assert any(call[0] == "hold" and call[1] in {"q", "e"} for call in calls)
    assert "visual_click_error" in action.args


def test_search_for_target_live_uses_operator_approved_blind_clicks(monkeypatch):
    calls = []
    fake_window = SimpleNamespace(bbox=(100, 200, 900, 800), width=800, height=600)
    monkeypatch.setattr(cmcs, "find_window", lambda query: fake_window)
    monkeypatch.setattr(cmcs, "tap_key", lambda key, hold=0.0: calls.append(("tap", key, hold)))
    monkeypatch.setattr(cmcs, "hold_key", lambda key, hold=0.0: calls.append(("hold", key, hold)))
    monkeypatch.setattr(cmcs, "click_at", lambda x, y: calls.append(("click", x, y)))
    monkeypatch.setattr(cmcs.time, "sleep", lambda _seconds: None)
    action = CombatAction("SEARCH_FOR_TARGET", "search_for_target", "test", args={"cycle": 0})
    args = SimpleNamespace(allow_blind_target_clicks=True, target_search_move_seconds=0.25, burst_seconds=0.8)

    run_live_command(action, args, {})

    assert ("tap", "tab", 0.06) in calls
    assert ("click", 500, 488) in calls
    assert ("hold", "w", 0.25) in calls
    assert any(call[0] == "hold" and call[1] in {"q", "e"} for call in calls)

def test_attack_nearby_mobs_searches_for_targets_instead_of_aborting_without_metin(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "timestamp_ms": 1,
        "map": "metin2_map_a1",
        "player": {"name": "Yoshypt", "x": 10, "y": 20, "z": 30, "hp": 222, "max_hp": 222, "sp": 80, "max_sp": 80},
        "target": {"vid": 0, "name": "", "alive": None},
        "nearby_entities": [],
        "buffs": [],
    }), encoding="utf-8")
    combat_config = tmp_path / "combat.json"
    combat_config.write_text(json.dumps({"attack_nearby_mobs": True}), encoding="utf-8")
    out_path = tmp_path / "combat.jsonl"

    env = dict(**__import__('os').environ, METIN2_COMBAT_CONFIG=str(combat_config))
    result = subprocess.run([
        sys.executable,
        "scripts/combat_metin_client_state.py",
        "--json-state", str(state_path),
        "--max-cycles", "2",
        "--out", str(out_path),
        "--no-session-start-scan",
    ], cwd=".", env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)

    assert result.returncode == 0, result.stderr + result.stdout
    events = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(event["state"] == "SEARCH_FOR_TARGET" and event["command"] == "search_for_target" for event in events)
    assert not any(event["state"] == "NEED_METIN_TARGET" for event in events)






def test_choose_camera_sweep_key_uses_minimap_target_relative_to_facing():
    center = (50.0, 50.0)
    # Facing up; target is screen-right/clockwise, so rotate E.
    key, evidence = choose_camera_sweep_key_from_vectors((50, 35), (75, 50), center=center, fallback_key="q")
    assert key == "e"
    assert evidence["reason"] == "rotate_toward_minimap_target"
    # Facing up; target is screen-left/counter-clockwise, so rotate Q.
    key, evidence = choose_camera_sweep_key_from_vectors((50, 35), (25, 50), center=center, fallback_key="e")
    assert key == "q"
    assert evidence["source"] == "minimap"


def test_choose_camera_sweep_key_falls_back_without_minimap_target():
    key, evidence = choose_camera_sweep_key_from_vectors((50, 35), None, center=(50, 50), fallback_key="e")
    assert key == "e"
    assert evidence["reason"] == "no_minimap_target_dot"


def test_channel_click_points_parse_relative_and_absolute():
    assert parse_channel_click_points("0.4,0.3;640,420") == [(0.4, 0.3), (640.0, 420.0)]
    window = SimpleNamespace(bbox=(100, 200, 900, 800), width=800, height=600)
    assert channel_click_screen_point(window, (0.5, 0.25)) == (500, 350)
    assert channel_click_screen_point(window, (640.0, 420.0)) == (640, 420)


def test_channel_rotation_sequence_spams_z_presses_x_and_left_clicks(monkeypatch):
    calls = []
    window = SimpleNamespace(bbox=(100, 200, 900, 800), width=800, height=600)
    monkeypatch.setattr(cmcs, "tap_key", lambda key, hold=0.0: calls.append(("tap", key, hold)))
    monkeypatch.setattr(cmcs, "find_window", lambda query: calls.append(("find", query)) or window)
    monkeypatch.setattr(cmcs, "activate_window", lambda found: calls.append(("activate", found is window)))
    monkeypatch.setattr(cmcs, "click_at", lambda x, y: calls.append(("click", x, y)))
    monkeypatch.setattr(cmcs.time, "sleep", lambda seconds: calls.append(("sleep", round(float(seconds), 2))))
    args = SimpleNamespace(
        channel_click_points="0.50,0.25;0.60,0.25",
        pickup_spam_count=3,
        pickup_spam_interval=0.01,
        channel_menu_delay_seconds=0.05,
        channel_switch_wait_seconds=0.0,
        window_query="MT2Portugalia",
    )

    result = run_channel_rotation_sequence(args, channel_index=1)

    assert [call for call in calls if call[:2] == ("tap", "z")] == [("tap", "z", 0.03)] * 3
    assert ("tap", "x", 0.06) in calls
    assert ("click", 580, 350) in calls
    assert result["channel_index"] == 1
    assert result["pickup_count"] == 3


def test_pickup_spam_sequence_only_presses_z(monkeypatch):
    calls = []
    monkeypatch.setattr(cmcs, "tap_key", lambda key, hold=0.0: calls.append(("tap", key, hold)))
    monkeypatch.setattr(cmcs.time, "sleep", lambda seconds: calls.append(("sleep", round(float(seconds), 2))))
    args = SimpleNamespace(pickup_spam_count=4, pickup_spam_interval=0.02)

    result = run_pickup_spam_sequence(args)

    assert result == {"pickup_count": 4}
    assert [call for call in calls if call[:2] == ("tap", "z")] == [("tap", "z", 0.03)] * 4
    assert not any(call[:2] == ("tap", "x") for call in calls)


def test_attack_nearby_destroy_detected_requires_sustained_attack_and_probe_absence():
    gone = SimpleNamespace(target_vid=None, named_metin_probe=[])
    still_selected = SimpleNamespace(target_vid=171151, named_metin_probe=[])
    still_probed = SimpleNamespace(target_vid=None, named_metin_probe=[{"vid": 171151, "alive": True}])

    assert attack_nearby_destroy_detected(gone, locked_metin_vid=171151, locked_target_alive_cycles=4)
    assert not attack_nearby_destroy_detected(gone, locked_metin_vid=171151, locked_target_alive_cycles=3)
    assert not attack_nearby_destroy_detected(gone, locked_metin_vid=None, locked_target_alive_cycles=10)
    assert not attack_nearby_destroy_detected(still_selected, locked_metin_vid=171151, locked_target_alive_cycles=10)
    assert not attack_nearby_destroy_detected(still_probed, locked_metin_vid=171151, locked_target_alive_cycles=10)


def test_cli_buff_config_builds_independent_f1_f2_schedules():
    cfg = buff_config_from_cli("f1,f2", "109,301", pre_cast_seconds=3)
    assert cfg == {
        "use_buff_config": True,
        "buffs": [
            {"key": "f1", "enabled": True, "interval_seconds": 109.0, "pre_cast_seconds": 3.0},
            {"key": "f2", "enabled": True, "interval_seconds": 301.0, "pre_cast_seconds": 3.0},
        ],
    }
    due = due_buff_actions(cfg, {"f1": 100.0, "f2": 100.0}, now=206.0)
    assert [item["key"] for item in due] == ["f1"]
    due = due_buff_actions(cfg, {"f1": 100.0, "f2": 100.0}, now=398.0)
    assert [item["key"] for item in due] == ["f1", "f2"]


def test_attack_nearby_does_not_press_configured_buffs_without_explicit_opt_in(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "timestamp_ms": 1,
        "map": "metin2_map_a1",
        "player": {"name": "Yoshypt", "x": 10, "y": 20, "z": 30, "hp": 222, "max_hp": 222, "sp": 80, "max_sp": 80},
        "target": {"vid": 0, "name": "", "alive": None},
        "nearby_entities": [],
        "buffs": [],
    }), encoding="utf-8")
    combat_config = tmp_path / "combat.json"
    combat_config.write_text(json.dumps({"attack_nearby_mobs": True}), encoding="utf-8")
    buff_config = tmp_path / "buffs.json"
    buff_config.write_text(json.dumps({
        "use_buff_config": True,
        "buffs": [{"key": "f1", "enabled": True, "interval_seconds": 109, "pre_cast_seconds": 3}],
    }), encoding="utf-8")
    out_path = tmp_path / "combat.jsonl"

    env = dict(**__import__('os').environ, METIN2_COMBAT_CONFIG=str(combat_config), METIN2_BUFF_CONFIG=str(buff_config))
    result = subprocess.run([
        sys.executable,
        "scripts/combat_metin_client_state.py",
        "--json-state", str(state_path),
        "--max-cycles", "4",
        "--out", str(out_path),
        "--no-session-start-scan",
    ], cwd=".", env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)

    assert result.returncode == 0, result.stderr + result.stdout
    events = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [event["state"] for event in events].count("BUFF_DUE") == 0
    assert any(event["state"] == "SEARCH_FOR_TARGET" for event in events)

def test_combat_script_help_runs_from_project_root_without_pythonpath():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/combat_metin_client_state.py", "--help"],
        cwd=".",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--json-state" in result.stdout
    assert "--metin-coord-source" in result.stdout


def test_probe_script_help_runs_from_project_root_without_pythonpath():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/probe_client_state.py", "--help"],
        cwd=".",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--json-state" in result.stdout


def test_combat_script_dry_run_attack_nearby_mobs_logs_decision_without_live_input(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "timestamp_ms": 1,
        "map": "metin2_map_a1",
        "player": {"name": "Yoshypt", "x": 10, "y": 20, "z": 30, "hp": 222, "max_hp": 222, "sp": 80, "max_sp": 80},
        "target": {"vid": 888, "name": "Wild Dog", "alive": True, "type": 0},
        "nearby_entities": [{"vid": 888, "name": "Wild Dog", "kind": "mob", "hostile": True, "distance": 120}],
        "buffs": [{"key": "f1", "active": True}],
    }), encoding="utf-8")
    combat_config = tmp_path / "combat.json"
    combat_config.write_text(json.dumps({"attack_nearby_mobs": True}), encoding="utf-8")
    out_path = tmp_path / "combat.jsonl"

    env = dict(**__import__('os').environ, METIN2_COMBAT_CONFIG=str(combat_config))
    result = subprocess.run([
        sys.executable,
        "scripts/combat_metin_client_state.py",
        "--json-state", str(state_path),
        "--max-cycles", "1",
        "--out", str(out_path),
        "--no-session-start-scan",
    ], cwd=".", env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)

    assert result.returncode == 0, result.stderr + result.stdout
    events = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert events[0]["state"] == "AUTO_ATTACKING_TARGET"
    assert events[0]["dry_run"] is True
    assert events[0]["command"] == "hold_space"
    assert "LIVE_INPUT" not in out_path.read_text(encoding="utf-8")


def test_focus_live_window_activates_configured_target_window(monkeypatch):
    from scripts import combat_metin_client_state as script
    calls = []
    window = SimpleNamespace(title="MT2Portugalia")
    monkeypatch.setattr(script, "find_window", lambda query: calls.append(("find", query)) or window)
    monkeypatch.setattr(script, "activate_window", lambda found: calls.append(("activate", found.title)))

    script.focus_live_window(SimpleNamespace(window_query="MT2Portugalia"))

    assert calls == [("find", "MT2Portugalia"), ("activate", "MT2Portugalia")]


def test_live_selected_mob_focuses_game_before_sending_space(monkeypatch):
    from scripts import combat_metin_client_state as script
    calls = []
    monkeypatch.setattr(script, "key_down", lambda key: calls.append(("down", key)))
    monkeypatch.setattr(script, "key_up", lambda key: calls.append(("up", key)))
    monkeypatch.setattr(script.time, "sleep", lambda seconds: calls.append(("sleep", round(seconds, 2))))
    monkeypatch.setattr(script, "focus_live_window", lambda args: calls.append(("focus", args.window_query)))
    action = CombatAction("ATTACK_NEARBY_MOBS", "attack_target", "selected mob", args={})

    script.run_focused_live_command(action, SimpleNamespace(burst_seconds=0.4, window_query="MT2Portugalia"), {})

    assert calls[:2] == [("focus", "MT2Portugalia"), ("down", "space")]
    assert calls[-1] == ("up", "space")



def test_live_buff_key_uses_elevated_key_macro_sender(monkeypatch, tmp_path):
    calls = []

    class Done:
        returncode = 0
        stdout = "relaunched_elevated_for_live_key_macro\nelevated_child_exit_code 0\n"

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Done()

    monkeypatch.setattr(cmcs.subprocess, "run", fake_run)
    args = SimpleNamespace(out=tmp_path / "buff_keepalive.jsonl", window_query="MT2Portugalia")
    result = press_buff_key_via_key_macro(key="f1", args=args, stop_file=tmp_path / "stop.flag", run_id="run-test")

    cmd, kwargs = calls[0]
    assert "scripts\\key_macro_control.py" in " ".join(cmd) or "scripts/key_macro_control.py" in " ".join(cmd)
    assert "--live" in cmd
    assert "--elevate" in cmd
    assert cmd[cmd.index("--key") + 1] == "f1"
    assert cmd[cmd.index("--presses") + 1] == "1"
    assert kwargs["env"]["HERMES_STOP_FILE"].endswith("stop.flag")
    assert result["exit_code"] == 0
    assert result["elevated_log"].endswith(".elevated.log")

def test_buff_only_mode_logs_idle_and_never_attacks_selected_mob(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "timestamp_ms": 1,
        "player": {"x": 100, "y": 200, "z": 0, "hp": 100, "max_hp": 100, "sp": 50, "max_sp": 50},
        "target": {"vid": 888, "name": "Wild Dog", "alive": True, "type": 0},
        "nearby_entities": [{"vid": 888, "name": "Wild Dog", "kind": "mob", "hostile": True, "distance": 120}],
        "buffs": [],
    }), encoding="utf-8")
    combat_config = tmp_path / "combat.json"
    combat_config.write_text(json.dumps({"attack_nearby_mobs": True}), encoding="utf-8")
    buff_config = tmp_path / "buffs.json"
    buff_config.write_text(json.dumps({"use_buff_config": True, "buffs": []}), encoding="utf-8")
    out_path = tmp_path / "combat.jsonl"
    env = dict(**__import__('os').environ, METIN2_COMBAT_CONFIG=str(combat_config), METIN2_BUFF_CONFIG=str(buff_config))

    result = subprocess.run([
        sys.executable,
        "scripts/combat_metin_client_state.py",
        "--json-state", str(state_path),
        "--buff-only",
        "--max-cycles", "1",
        "--out", str(out_path),
        "--no-session-start-scan",
    ], cwd=Path(__file__).resolve().parents[1], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in out_path.read_text().splitlines()]
    assert events[0]["state"] == "BUFF_KEEPALIVE_IDLE"
    assert events[0]["command"] == "wait_for_next_buff_due"
    assert "ATTACK_NEARBY_MOBS" not in out_path.read_text()






def test_buff_damage_guard_suppresses_f1_timer_before_full_duration():
    guard = cmcs.BuffDamageGuard(guard_keys={"f1"})
    last_cast = {"f1": 100.0}
    buff = {"key": "f1", "interval_seconds": 109.0, "pre_cast_seconds": 3.0}

    suppress, evidence = guard.suppress_refresh(buff, last_cast, now=206.0)

    assert suppress is True
    assert evidence["reason"] == "timer_before_full_duration_damage_guard"


def test_buff_damage_guard_uses_recent_damage_baseline_to_delay_toggle_refresh():
    guard = cmcs.BuffDamageGuard(guard_keys={"f1"}, baseline_window_seconds=30, recent_window_seconds=10, active_ratio=0.7)
    guard.note_buff_pressed("f1", now=0.0)
    guard.observe_game(SimpleNamespace(target_vid=7, target_hp=1000), now=1.0)
    guard.observe_game(SimpleNamespace(target_vid=7, target_hp=900), now=3.0)
    guard.observe_game(SimpleNamespace(target_vid=7, target_hp=800), now=5.0)
    guard.observe_game(SimpleNamespace(target_vid=7, target_hp=700), now=111.0)
    guard.observe_game(SimpleNamespace(target_vid=7, target_hp=600), now=113.0)

    buff = {"key": "f1", "interval_seconds": 109.0, "pre_cast_seconds": 3.0}
    due, suppressed = cmcs.apply_buff_damage_guard([buff], {"f1": 0.0}, now=113.0, guard=guard)

    assert due == []
    assert suppressed[0]["key"] == "f1"
    assert suppressed[0]["damage_guard"]["reason"] == "recent_damage_still_matches_buffed_baseline"


def test_buff_damage_guard_does_not_suppress_when_damage_falls_below_baseline():
    guard = cmcs.BuffDamageGuard(guard_keys={"f1"}, baseline_window_seconds=30, recent_window_seconds=10, active_ratio=0.7)
    guard.note_buff_pressed("f1", now=0.0)
    guard.observe_game(SimpleNamespace(target_vid=7, target_hp=1000), now=1.0)
    guard.observe_game(SimpleNamespace(target_vid=7, target_hp=900), now=3.0)
    guard.observe_game(SimpleNamespace(target_vid=7, target_hp=800), now=5.0)
    guard.observe_game(SimpleNamespace(target_vid=7, target_hp=799), now=111.0)
    guard.observe_game(SimpleNamespace(target_vid=7, target_hp=798), now=113.0)

    buff = {"key": "f1", "interval_seconds": 109.0, "pre_cast_seconds": 3.0}
    due, suppressed = cmcs.apply_buff_damage_guard([buff], {"f1": 0.0}, now=113.0, guard=guard)

    assert due == [buff]
    assert suppressed == []

def test_json_state_exposes_mounted_flag_for_buff_keeper(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "player": {"x": 1, "y": 2, "z": 3, "mounted": True},
        "target": None,
    }), encoding="utf-8")

    game = read_game(tmp_path / "missing.tsv", json_path=state_path, max_age_seconds=10)

    assert mounted_state_from_game(game) is True


def test_mounted_state_from_game_normalizes_missing_and_false():
    assert mounted_state_from_game(SimpleNamespace(player_flags={})) is None
    assert mounted_state_from_game(SimpleNamespace(player_flags={"mounted": "false"})) is False
    assert mounted_state_from_game(SimpleNamespace(player_flags={"mounted": "1"})) is True


def test_buff_only_live_dismounts_and_remounts_around_due_buffs(monkeypatch, tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "player": {"x": 1, "y": 2, "z": 3, "hp": 10, "max_hp": 10, "mounted": True},
        "target": None,
    }), encoding="utf-8")
    out = tmp_path / "buff_mount.jsonl"
    sent = []

    def fake_press(*, key, args, stop_file, run_id):
        sent.append(key)
        return {"key": key, "exit_code": 0}

    monkeypatch.setattr(cmcs, "press_buff_key_via_key_macro", fake_press)
    monkeypatch.setattr(cmcs, "focus_live_window", lambda args: None)
    monkeypatch.setattr(cmcs.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(sys, "argv", [
        "combat_metin_client_state.py",
        "--live",
        "--buff-only",
        "--buff-keys", "f1,f2",
        "--buff-durations", "1,1",
        "--max-cycles", "1",
        "--json-state", str(state_path),
        "--out", str(out),
    ])
    rc = cmcs.main()

    assert rc == 0
    assert sent == ["ctrl+g", "f1", "ctrl+g", "f2"]
    events = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert sum(1 for event in events if event.get("state") == "DISMOUNT_FOR_BUFF") == 2
    assert any(event.get("state") == "REMOUNT_AFTER_BUFF_SKIPPED" for event in events)


def test_buff_only_assume_mounted_does_not_need_mount_detection_and_remounts_each_buff(monkeypatch, tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "player": {"x": 1, "y": 2, "z": 3, "hp": 10, "max_hp": 10},
        "target": None,
    }), encoding="utf-8")
    out = tmp_path / "buff_assume_mounted.jsonl"
    sent = []

    def fake_press(*, key, args, stop_file, run_id):
        sent.append(key)
        return {"key": key, "exit_code": 0}

    monkeypatch.setattr(cmcs, "press_buff_key_via_key_macro", fake_press)
    monkeypatch.setattr(cmcs, "focus_live_window", lambda args: None)
    monkeypatch.setattr(cmcs.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(sys, "argv", [
        "combat_metin_client_state.py",
        "--live",
        "--buff-only",
        "--assume-mounted",
        "--buff-keys", "f1,f2",
        "--buff-durations", "1,1",
        "--max-cycles", "1",
        "--json-state", str(state_path),
        "--out", str(out),
    ])

    rc = cmcs.main()

    assert rc == 0
    assert sent == ["ctrl+g", "f1", "ctrl+g", "ctrl+g", "f2", "ctrl+g"]
    events = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    due_events = [event for event in events if event.get("state") == "BUFF_DUE"]
    assert [event.get("mounted_source") for event in due_events] == ["assume_mounted", "assume_mounted"]
    assert not any(event.get("state") == "BUFF_MOUNT_STATE_UNKNOWN" for event in events)

def test_buff_only_live_max_cycles_exit_code_is_success():
    from scripts.combat_metin_client_state import max_cycles_exit_code

    assert max_cycles_exit_code(live=True, buff_only=True) == 0
    assert max_cycles_exit_code(live=True, buff_only=False) == 1
    assert max_cycles_exit_code(live=False, buff_only=True) == 0


def test_buff_only_cli_f1_f2_preset_presses_each_key_once_in_dry_run(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({"player": {"x": 1, "y": 2, "z": 3}, "target": None}), encoding="utf-8")
    out_path = tmp_path / "buffs.jsonl"

    result = subprocess.run([
        sys.executable,
        "scripts/combat_metin_client_state.py",
        "--json-state", str(state_path),
        "--buff-only",
        "--buff-keys", "f1,f2",
        "--buff-durations", "109,301",
        "--max-cycles", "2",
        "--out", str(out_path),
        "--no-session-start-scan",
    ], cwd=Path(__file__).resolve().parents[1], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in out_path.read_text().splitlines()]
    states = [event["state"] for event in events]
    assert states[:2] == ["BUFF_DUE", "BUFF_DUE"]
    assert states[-1] == "BUFF_KEEPALIVE_IDLE"
    due_events = [event for event in events if event["state"] == "BUFF_DUE"]
    assert [event["buff"]["key"] for event in due_events] == ["f1", "f2"]
    assert all(event["dry_run"] is True for event in events)


def test_buff_only_does_not_require_fresh_client_state(tmp_path):
    import os
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "timestamp_ms": 1,
        "player": {"x": 100, "y": 200, "z": 0, "hp": 100, "max_hp": 100, "sp": 50, "max_sp": 50},
        "target": None,
        "nearby_entities": [],
    }), encoding="utf-8")
    old = 1_700_000_000
    os.utime(state_path, (old, old))
    buff_config = tmp_path / "buffs.json"
    buff_config.write_text(json.dumps({"use_buff_config": True, "buffs": [{"key": "f1", "enabled": True, "interval_seconds": 109, "pre_cast_seconds": 3}]}), encoding="utf-8")
    combat_config = tmp_path / "combat.json"
    combat_config.write_text(json.dumps({"attack_nearby_mobs": True}), encoding="utf-8")
    out_path = tmp_path / "combat.jsonl"
    env = dict(os.environ, METIN2_BUFF_CONFIG=str(buff_config), METIN2_COMBAT_CONFIG=str(combat_config))

    result = subprocess.run([
        sys.executable,
        "scripts/combat_metin_client_state.py",
        "--json-state", str(state_path),
        "--buff-only",
        "--max-cycles", "1",
        "--out", str(out_path),
        "--max-state-age-seconds", "0.01",
    ], cwd=Path(__file__).resolve().parents[1], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    assert result.returncode == 0, result.stdout + result.stderr
    text = out_path.read_text()
    assert "BUFF_DUE" in text
    assert "press_buff" in text
    assert "WAIT_FRESH_STATE" not in text
