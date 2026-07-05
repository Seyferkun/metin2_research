import json
import subprocess
import sys

from types import SimpleNamespace

import pytest

from metin2_research.client_state.combat import CombatAction
from scripts.combat_metin_client_state import DistanceTracker, NavMilestones, ProbeLossGrace, apply_exact_target_evidence, apply_session_start_metin_choice, choose_exact_target_evidence_from_game, choose_movement_key_toward_metin, choose_movement_step_toward_metin, choose_movement_steps_toward_metin, choose_session_start_metin, choose_unstuck_key, movement_stuck, format_structured_log_line, normalize_metin_coord, probe_best_key, read_game, run_live_command, run_session_start_scan, should_decay_after_divergence, should_stop


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
