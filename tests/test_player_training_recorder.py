import json
from pathlib import Path

from scripts.player_training_recorder import (
    build_training_summary,
    key_edge_events,
    mouse_edge_events,
    sanitize_state_snapshot,
)
from metin2_research.window_capture import WindowInfo, is_plausible_game_window


def test_key_edge_events_reports_pressed_and_released_transitions():
    assert key_edge_events(previous=set(), current={"w", "space"}, now=10.0) == [
        {"type": "key_down", "key": "space", "t": 10.0},
        {"type": "key_down", "key": "w", "t": 10.0},
    ]
    assert key_edge_events(previous={"w", "space"}, current={"z"}, now=11.5) == [
        {"type": "key_down", "key": "z", "t": 11.5},
        {"type": "key_up", "key": "space", "t": 11.5},
        {"type": "key_up", "key": "w", "t": 11.5},
    ]


def test_mouse_edge_events_reports_button_edges_and_window_relative_position():
    previous = {"left"}
    current = {"right"}
    pos = {"screen_x": 150, "screen_y": 260, "window_x": 50, "window_y": 60, "window_w": 800, "window_h": 600, "inside_window": False}

    events = mouse_edge_events(previous=previous, current=current, now=12.3456, position=pos)

    assert events == [
        {"type": "mouse_down", "button": "right", "t": 12.346, "position": pos, "pos": [150, 260], "window_pos": [50, 60], "window_relative": [0.0625, 0.1], "inside_window": False},
        {"type": "mouse_up", "button": "left", "t": 12.346, "position": pos, "pos": [150, 260], "window_pos": [50, 60], "window_relative": [0.0625, 0.1], "inside_window": False},
    ]


def test_recorder_rejects_implausible_window_geometry_that_breaks_mouse_learning():
    good = WindowInfo(hwnd=1, pid=2, process_name="", title="MT2Portugalia", bbox=(160, -5, 1762, 1026))
    bad = WindowInfo(hwnd=1, pid=2, process_name="", title="MT2Portugalia", bbox=(-39991, -40000, -39791, -39900))

    assert is_plausible_game_window(good)
    assert not is_plausible_game_window(bad)


def test_sanitize_state_snapshot_keeps_learning_fields_without_secrets():
    raw = {
        "map": "metin2_map_n_desert_01",
        "player": {"name": "Yoshypt", "x": 100, "y": 200, "hp": 300, "max_hp": 500, "sp": 20, "max_sp": 40, "password": "secret"},
        "target": {"vid": 123, "name": "Metin da Batalha", "alive": True, "hp_pct": 75},
        "nearby_entities": [{"vid": 1, "name": "mob", "distance": 12, "password": "secret"}],
        "inventory": [{"slot": 0, "vnum": 2849, "name": "Lança Fénix+9", "attrs": [{"index": 0, "type": 72, "value": 23, "secret_token": "x"}], "sockets": [1, 2, 3], "password": "secret"}],
        "equipped_weapon": {"slot": 0, "vnum": 2849, "name": "Lança Fénix+9", "attrs": [{"index": 0, "type": 72, "value": 23}], "sockets": [1, 2, 3]},
        "session_token": "secret",
    }

    snapshot = sanitize_state_snapshot(raw)

    assert snapshot["map"] == "metin2_map_n_desert_01"
    assert snapshot["player"]["name"] == "Yoshypt"
    assert snapshot["player"]["x"] == 100
    assert snapshot["target"]["name"] == "Metin da Batalha"
    assert snapshot["nearby_entities"][0] == {"vid": 1, "name": "mob", "distance": 12}
    assert snapshot["inventory"][0]["name"] == "Lança Fénix+9"
    assert snapshot["inventory"][0]["attrs"] == [{"index": 0, "type": 72, "value": 23}]
    assert snapshot["equipped_weapon"]["attrs"] == [{"index": 0, "type": 72, "value": 23}]
    assert "password" not in json.dumps(snapshot).lower()
    assert "token" not in json.dumps(snapshot).lower()


def test_build_training_summary_finds_target_attack_destroy_pickup_timing(tmp_path):
    events_path = tmp_path / "events.jsonl"
    rows = [
        {"type": "sample", "t": 0.0, "state": {"target": None, "player": {"x": 0, "y": 0}}},
        {"type": "key_down", "t": 1.0, "key": "tab"},
        {"type": "sample", "t": 1.4, "state": {"target": {"vid": 7, "name": "Metin da Batalha", "alive": True}, "player": {"x": 10, "y": 10}}},
        {"type": "mouse_down", "t": 1.8, "button": "left", "position": {"screen_x": 1320, "screen_y": 740, "window_x": 320, "window_y": 240, "window_w": 800, "window_h": 600, "inside_window": True}},
        {"type": "mouse_up", "t": 1.9, "button": "left", "position": {"screen_x": 1320, "screen_y": 740, "window_x": 320, "window_y": 240, "window_w": 800, "window_h": 600, "inside_window": True}},
        {"type": "key_down", "t": 2.0, "key": "space"},
        {"type": "key_down", "t": 5.0, "key": "1"},
        {"type": "sample", "t": 8.5, "state": {"target": None, "player": {"x": 20, "y": 20}}},
        {"type": "key_down", "t": 9.1, "key": "z"},
        {"type": "key_down", "t": 9.3, "key": "z"},
        {"type": "key_down", "t": 10.0, "key": "x"},
        {"type": "mouse_down", "t": 11.0, "button": "left", "pos": [600, 350], "window_pos": [300, 180], "window_relative": [0.375, 0.3]},
    ]
    events_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    summary = build_training_summary(events_path)

    assert summary["samples"] == 3
    assert summary["key_down_counts"] == {"1": 1, "space": 1, "tab": 1, "x": 1, "z": 2}
    assert summary["mouse_down_counts"] == {"left": 2}
    assert summary["avg_seconds_target_to_left_click"] == 0.4
    assert summary["target_lock_count"] == 1
    assert summary["destroy_candidates"] == 1
    assert summary["avg_seconds_target_to_attack"] == 0.6
    assert summary["avg_seconds_destroy_to_pickup"] == 0.6
    assert summary["target_left_clicks"] == [
        {"t": 1.8, "button": "left", "pos": [1320.0, 740.0], "window_pos": [320.0, 240.0], "window_relative": [0.4, 0.4], "inside_window": True, "seconds_after_target_lock": 0.4}
    ]
    assert summary["target_left_click_avg_window_relative"] == [0.4, 0.4]
    assert summary["channel_followup_clicks"] == [
        {"t": 11.0, "button": "left", "pos": [600.0, 350.0], "window_pos": [300.0, 180.0], "window_relative": [0.375, 0.3], "seconds_after_x": 1.0}
    ]
    assert summary["channel_followup_avg_window_relative"] == [0.375, 0.3]
    assert summary["recommendations"][0]["topic"] == "attack_start"
