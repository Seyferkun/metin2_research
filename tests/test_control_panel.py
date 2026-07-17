import json
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest

from metin2_dashboard.control_panel import (
    LEARNED_CHANNEL_CLICK_POINTS,
    ControlPanelApp,
    DashboardApiClient,
    GlobalStopHotkey,
    build_combat_payload,
    build_combat_confirmation_message,
    build_quick_start_payload,
    format_nearby_metins_results,
    format_state_card,
    format_truth_dashboard,
    format_combat_summary,
    combat_state_color,
    combat_log_is_stale,
    parse_combat_log_tail,
    state_has_metin_target,
    has_active_live_combat_run,
    has_active_live_control_run,
    find_running_buff_keeper_run,
    find_running_attack_nearby_run,
    load_nearby_metins_artifact,
    build_move_payload_from_metin,
    build_move_confirmation_message,
    option_default_values,
    option_payload_from_vars,
    format_control_config_summary,
    build_control_config_payload,
    build_fixed_sapo_payload,
    build_fixed_sapo_sweep_payload,
    effective_channel_index,
    format_fixed_sapo_gate_details,
    format_fixed_sapo_sweep_summary,
    build_key_macro_payload,
    build_player_training_payload,
    build_boss_farm_tracker_payload,
    build_boss_live_control_payload,
    build_farm_metrics_payload,
    format_farm_metrics_summary,
    build_reroll_recorder_payload,
    build_reroll_config_payload,
    format_reroll_config_summary,
    format_reroll_slot_table,
    build_server_cmd,
    BUFFER_JSON_STATE,
    BUFFER_TSV_STATE,
    build_player_training_manual_command,
    format_player_training_panel_text,
    latest_player_training_summary,
    find_running_player_training_run,
    format_player_training_run_status,
    allow_practice_live_without_metin,
    format_state_bridge_report,
    state_bridge_report,
    normalize_channel_click_points,
    pickup_count_from_seconds,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def close(self):
        pass


class FakeUrlopen:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, request, timeout=0):
        self.calls.append((request, timeout))
        payload = self.responses.pop(0) if self.responses else {"ok": True}
        return FakeResponse(payload)


def test_api_client_get_and_post_json(monkeypatch):
    fake = FakeUrlopen()
    fake.responses = [{"state": "ok"}, {"run_id": "run-1"}]
    monkeypatch.setattr("metin2_dashboard.control_panel.urlopen", fake)

    client = DashboardApiClient("http://127.0.0.1:8767")
    assert client.get("/api/state") == {"state": "ok"}
    assert client.post("/api/start", {"script": "probe_client_state", "live": False}) == {"run_id": "run-1"}

    get_req, get_timeout = fake.calls[0]
    post_req, post_timeout = fake.calls[1]
    assert get_req.full_url == "http://127.0.0.1:8767/api/state"
    assert get_timeout == 3
    assert post_req.full_url == "http://127.0.0.1:8767/api/start"
    assert post_req.get_method() == "POST"
    assert json.loads(post_req.data.decode("utf-8")) == {"script": "probe_client_state", "live": False}
    assert post_req.headers["Content-type"] == "application/json"


def test_api_client_surfaces_http_error_body(monkeypatch):
    def raise_http_error(request, timeout=0):
        raise HTTPError(request.full_url, 400, "Bad Request", {}, FakeResponse({"error": "live start requires confirm_live=true"}))

    monkeypatch.setattr("metin2_dashboard.control_panel.urlopen", raise_http_error)
    client = DashboardApiClient("http://127.0.0.1:8767")
    with pytest.raises(RuntimeError, match="live start requires confirm_live=true"):
        client.post("/api/start", {"script": "combat_metin_client_state", "live": True})


def test_quick_start_payloads_include_safe_buff_presets():
    dry = build_quick_start_payload("buff_only_dry_run")
    live = build_quick_start_payload("buff_only_live")

    assert dry == {
        "script": "combat_metin_client_state",
        "live": False,
        "confirm_live": False,
        "options": {"max_cycles": "4", "buff_only": True, "buff_keys": "f1,f2", "buff_durations": "156,302", "buff_damage_guard_keys": "f1", "assume_mounted": True},
    }
    assert live["live"] is True
    assert live["confirm_live"] is True
    assert live["options"]["max_cycles"] == "0"
    assert live["options"]["buff_only"] is True
    assert "buff_keys" not in live["options"]
    assert "buff_durations" not in live["options"]
    assert live["options"]["assume_mounted"] is True
    assert live["options"]["f1_active_attack_min_min"] == "200"
    assert live["options"]["f2_active_attack_speed_min"] == "130"

    configured = build_quick_start_payload("buff_only_live", buff_keys="f1,f2", buff_durations="124,304", buff_refresh_margin_seconds="3")
    assert configured["options"]["buff_keys"] == "f1,f2"
    assert configured["options"]["buff_durations"] == "124,304"
    assert configured["options"]["buff_refresh_margin_seconds"] == "3"


def test_key_macro_payloads_for_direct_f1_f2_buttons():
    one = build_key_macro_payload(key="F1", interval_seconds="35", presses="1", hold_seconds="0.06", live=True)
    macro = build_key_macro_payload(key="f2", interval_seconds="109", presses="0", hold_seconds="0.07", live=True)

    assert one == {
        "script": "key_macro_control",
        "live": True,
        "confirm_live": True,
        "options": {"key": "f1", "interval_seconds": "35.0", "presses": "1", "hold_seconds": "0.06", "window_query": "MT2Portugalia", "elevate": True},
    }
    assert macro["options"]["key"] == "f2"
    assert macro["options"]["interval_seconds"] == "109.0"
    assert macro["options"]["presses"] == "0"


def test_fixed_sapo_payloads_are_space_only_and_gated():
    dry = build_fixed_sapo_payload(duration="0", live=False, state_json="state.json")
    live = build_fixed_sapo_payload(
        duration="240",
        until_destroyed=True,
        pickup_after_destroy=True,
        hp_stop_threshold="2500",
        min_distance="350",
        live=True,
        state_json="state.json",
    )

    assert dry == {
        "script": "fixed_sapo_space_control",
        "live": False,
        "confirm_live": False,
        "options": {
            "state_json": "state.json",
            "duration": "0.0",
            "hp_stop_threshold": "2500",
            "min_distance": "350.0",
            "window_query": "MT2Portugalia",
            "out": "reports/dashboard_runs/fixed_sapo_0s.jsonl",
            "summary_out": "reports/dashboard_runs/fixed_sapo_0s_summary.json",
        },
    }
    assert live["script"] == "fixed_sapo_space_control"
    assert live["live"] is True
    assert live["confirm_live"] is True
    assert live["options"]["until_destroyed"] is True
    assert live["options"]["pickup_after_destroy"] is True
    assert live["options"]["pickup_count"] == "20"
    assert "click" not in live["options"]
    assert "potion" not in live["options"]

    channel = build_fixed_sapo_payload(
        duration="0",
        pickup_after_destroy=True,
        channel_switch_after_pickup=True,
        channel_click_points="0.4,0.3;0.4,0.34",
        channel_index="1",
        channel_auto_cycle=True,
        skip_first_channel=True,
        pickup_seconds="2.4",
        live=True,
        state_json="state.json",
    )
    assert channel["options"]["channel_switch_after_pickup"] is True
    assert channel["options"]["pickup_after_destroy"] is True
    assert channel["options"]["channel_click_points"] == "0.4,0.34"
    assert channel["options"]["channel_index"] == "0"
    assert channel["options"]["channel_auto_cycle"] is True
    assert channel["options"]["channel_cycle_state"].endswith("fixed_sapo_channel_cycle_state.json")
    assert channel["options"]["pickup_count"] == "30"


def test_learned_channel_click_points_match_latest_live_menu_rows():
    rows = [tuple(map(float, row.split(","))) for row in LEARNED_CHANNEL_CLICK_POINTS.split(";")]

    assert len(rows) == 8
    assert rows == pytest.approx([
        (0.4990, 0.3986),
        (0.4990, 0.4326),
        (0.4990, 0.4665),
        (0.4990, 0.5005),
        (0.4990, 0.5344),
        (0.4990, 0.5684),
        (0.4990, 0.6023),
        (0.4990, 0.6363),
    ], abs=0.0002)
    assert normalize_channel_click_points(LEARNED_CHANNEL_CLICK_POINTS, skip_first=True).split(";")[0] == "0.4990,0.4326"


def test_fixed_sapo_channel_helpers_and_formatters():
    assert normalize_channel_click_points("a;b;c", skip_first=True) == "b;c"
    assert effective_channel_index("2", skip_first=True) == "1"
    assert pickup_count_from_seconds("1.6") == "20"
    gates = format_fixed_sapo_gate_details(
        {"_file_mtime": 100.0, "player": {"name": "Yoshypt", "hp": 8296, "max_hp": 8296, "x": 1, "y": 2}, "target": {"name": "Sapo de Pedra", "alive": True}, "named_metin_probe": {"name": "Sapo de Pedra", "alive": True, "vid": 7}},
        [{"run_id": "buff", "script": "combat_metin_client_state", "mode": "live", "running": True, "command": "--buff-only"}],
        {"last_channel_index": 1, "next_channel_index": 2, "points_count": 6},
    )
    assert "buff keeper running: True" in gates
    assert "cycle state: last=1 next=2 points=6" in gates
    summary = format_fixed_sapo_sweep_summary({"run_id": "r1", "outcome": "completed", "reason": "done", "requested_channels": 1, "completed_cycles": 1, "cycles": [{"cycle": 1, "outcome": "destroyed_switched", "reason": "cycle_complete", "pickup_sent": 20, "attack": {"destroyed": True, "hp_min": 8000}, "channel_switch_result": {"channel_index": 2, "screen_point": [807, 410]}}]})
    assert "completed cycles: 1/1" in summary
    assert "channel_index=2" in summary


def test_fixed_sapo_sweep_payload_is_bounded_and_safe():
    sweep = build_fixed_sapo_sweep_payload(
        channels="8",
        cycle_duration="240",
        load_wait_seconds="5",
        hp_stop_threshold="2500",
        min_distance="350",
        channel_click_points="0.4,0.34;0.4,0.37",
        channel_index="1",
        channel_auto_cycle=True,
        skip_first_channel=True,
        pickup_seconds="2.0",
        repeat_while_running=True,
        low_dps_adjust=True,
        low_dps_threshold="0.20",
        low_dps_window_seconds="8",
        low_dps_max_cumulative_steps="3",
        adjust_hold_seconds="0.18",
        live=True,
        state_json="state.json",
    )

    assert sweep["script"] == "fixed_sapo_channel_sweep"
    assert sweep["live"] is True
    assert sweep["confirm_live"] is True
    opts = sweep["options"]
    assert opts["channels"] == "8"
    assert opts["cycle_duration"] == "240.0"
    assert opts["load_wait_seconds"] == "5.0"
    assert opts["hp_stop_threshold"] == "2500"
    assert opts["min_distance"] == "350.0"
    assert opts["pickup_count"] == "25"
    assert opts["channel_click_points"] == "0.4,0.37"
    assert opts["channel_index"] == "0"
    assert opts["channel_auto_cycle"] is True
    assert opts["channel_cycle_state"].endswith("fixed_sapo_channel_cycle_state.json")
    assert opts["repeat_while_running"] is True
    assert opts["low_dps_adjust"] is True
    assert opts["low_dps_threshold"] == "0.2"
    assert opts["low_dps_window_seconds"] == "8.0"
    assert opts["low_dps_max_cumulative_steps"] == "3"
    assert opts["adjust_hold_seconds"] == "0.18"
    assert opts["out"].endswith("fixed_sapo_channel_sweep.jsonl")
    assert "potion" not in opts
    assert "move" not in opts


def test_fixed_sapo_sweep_payload_clamps_zero_max_kept_steps_to_one():
    sweep = build_fixed_sapo_sweep_payload(low_dps_max_cumulative_steps="0")

    assert sweep["options"]["low_dps_max_cumulative_steps"] == "1"


def test_control_panel_source_exposes_direct_key_test_buttons():
    source = Path("metin2_dashboard/control_panel.py").read_text(encoding="utf-8")
    assert "Direct key test + timed macro" in source
    assert "Press F1 once LIVE" in source
    assert "Press F2 once LIVE" in source
    assert "Start F1 timed macro LIVE" in source
    assert "Start F2 timed macro LIVE" in source
    assert "def press_key_once" in source
    assert "def start_key_macro" in source


def test_control_panel_source_exposes_fixed_sapo_test_bench():
    source = Path("metin2_dashboard/control_panel.py").read_text(encoding="utf-8")
    assert "Fixed Sapo" in source
    assert "Run 30s Space test LIVE" in source
    assert "Run until Sapo destroyed LIVE" in source
    assert "Full: destroy + pickup + switch LIVE" in source
    assert "Sweep all channels LIVE" in source
    assert "Keep sweep running LIVE" in source
    assert "low DPS: nudge WASD" in source
    assert "Dry-run sweep preview" in source
    assert "Last sweep summary" in source
    assert "Reset channel cycle" in source
    assert "skip index 0" in source
    assert "pickup seconds" in source
    assert "Test pickup + channel switch LIVE" in source
    assert "auto next channel" in source
    assert "no click / no move / no potion 1" in source
    assert "def sapo_space_short_live" in source
    assert "def sapo_space_until_destroy_live" in source
    assert "def sapo_full_destroy_pickup_switch_live" in source
    assert "def sapo_sweep_all_channels_live" in source
    assert "def toggle_sapo_sweep_keep_running" in source
    assert "def sapo_sweep_dry_run_preview" in source
    assert "def reset_sapo_channel_cycle" in source
    assert "def refresh_sapo_gate_details" in source
    assert "def refresh_sapo_sweep_summary" in source
    assert "def sapo_pickup_channel_switch_live" in source



def test_option_default_values_preserve_editable_defaults():
    script = {
        "options": [
            {"name": "max_cycles", "type": "int", "default": 60},
            {"name": "burst_seconds", "type": "float", "default": 2.5},
            {"name": "metin_name", "type": "str", "default": "Metin da Batalha"},
            {"name": "no_process", "type": "bool", "default": True},
            {"name": "optional", "type": "str", "default": None},
        ]
    }
    assert option_default_values(script) == {
        "max_cycles": "60",
        "burst_seconds": "2.5",
        "metin_name": "Metin da Batalha",
        "no_process": True,
        "optional": "",
    }


def test_option_payload_from_vars_skips_empty_strings_and_unchanged_defaults():
    script = {
        "options": [
            {"name": "max_cycles", "type": "int", "default": 60},
            {"name": "metin_vid", "type": "int", "default": None},
            {"name": "no_process", "type": "bool", "default": True},
            {"name": "safe_probe", "type": "bool", "default": False},
        ]
    }
    values = {"max_cycles": "60", "metin_vid": "", "no_process": True, "safe_probe": True}
    assert option_payload_from_vars(script, values) == {"safe_probe": True}


def test_option_payload_from_vars_can_disable_non_bool_default():
    script = {
        "options": [
            {"name": "max_cycles", "type": "int", "default": 60},
        ]
    }
    assert option_payload_from_vars(script, {"max_cycles": "0"}) == {"max_cycles": "0"}



def test_control_panel_formats_and_builds_buff_mob_config():
    combat = {"attack_nearby_mobs": True}
    buffs = {
        "use_buff_config": True,
        "active_stat_thresholds": {"f1_attack_min_min": 349, "f2_attack_speed_min": 135},
        "buffs": [
            {"key": "f1", "enabled": True, "interval_seconds": 35, "pre_cast_seconds": 3},
            {"key": "f2", "enabled": False, "interval_seconds": 42, "pre_cast_seconds": 4},
        ],
    }

    summary = format_control_config_summary(combat, buffs)
    assert "attack nearby mobs: ON" in summary
    assert "use buff config: ON" in summary
    assert "F1 active threshold: attack_power >= 349" in summary
    assert "F2 active threshold: attack_speed >= 135" in summary
    assert "f1 enabled every 35s pre-cast 3s" in summary
    assert "f2 disabled" in summary

    combat_payload, buff_payload = build_control_config_payload(
        attack_nearby_mobs=False,
        use_buff_config=True,
        f1_enabled=True,
        f1_interval="36",
        f1_pre_cast="2",
        f2_enabled=True,
        f2_interval="44",
        f2_pre_cast="5",
        f1_active_attack_min_min="210",
        f2_active_attack_speed_min="140",
    )
    assert combat_payload == {"attack_nearby_mobs": False}
    assert buff_payload["use_buff_config"] is True
    assert buff_payload["active_stat_thresholds"] == {"f1_attack_min_min": 210.0, "f2_attack_speed_min": 140.0}
    assert buff_payload["buffs"][0] == {"key": "f1", "enabled": True, "interval_seconds": 36.0, "pre_cast_seconds": 2.0}
    assert buff_payload["buffs"][1] == {"key": "f2", "enabled": True, "interval_seconds": 44.0, "pre_cast_seconds": 5.0}


def test_render_control_config_preserves_dirty_operator_edits():
    class FakeVar:
        def __init__(self, value):
            self.value = value
        def set(self, value):
            self.value = value
        def get(self):
            return self.value
    class FakeLabel:
        def __init__(self):
            self.text = ""
        def configure(self, **kwargs):
            self.text = kwargs.get("text", self.text)

    app = object.__new__(ControlPanelApp)
    app._control_config_dirty = True
    app.attack_nearby_mobs_var = FakeVar(True)
    app.use_buff_config_var = FakeVar(True)
    app.f1_enabled_var = FakeVar(True)
    app.f2_enabled_var = FakeVar(False)
    app.f1_interval_var = FakeVar("99")
    app.f2_interval_var = FakeVar("88")
    app.f1_pre_cast_var = FakeVar("7")
    app.f2_pre_cast_var = FakeVar("6")
    app.control_config_label = FakeLabel()

    app.render_control_config(
        {"attack_nearby_mobs": False},
        {"use_buff_config": False, "buffs": [{"key": "f1", "enabled": False, "interval_seconds": 35, "pre_cast_seconds": 3}]},
    )

    assert app.attack_nearby_mobs_var.get() is True
    assert app.use_buff_config_var.get() is True
    assert app.f1_enabled_var.get() is True
    assert app.f1_interval_var.get() == "99"
    assert "editing; not overwritten" in app.control_config_label.text


def test_practice_live_allows_no_metin_when_attack_nearby_mobs_enabled():
    assert allow_practice_live_without_metin({}, {"attack_nearby_mobs": True}) is True
    assert allow_practice_live_without_metin({}, {"attack_nearby_mobs": False}) is False


def test_control_panel_formats_state_bridge_trust_report():
    report = state_bridge_report(
        {
            "_file_mtime": 100.0,
            "target": {"vid": 321, "name": "Metin da Batalha", "alive": True, "pixel_position": [1, 2, 3]},
        },
        now=100.5,
    )
    text = format_state_bridge_report(report)

    assert report["has_trusted_target"] is True
    assert "State bridge trust" in text
    assert "Dry-run DRY_RUN_IDLE" in text
    assert "Live ENGAGE_TARGET" in text




def test_format_truth_dashboard_shows_honest_uiux_statuses():
    state = {
        "_file_mtime": 100.0,
        "target": {
            "vid": 1558008,
            "name": "Metin da Alma",
            "type": 2,
            "alive": True,
            "pixel_position": [95920.0, 25729.0],
            "target_hp_now": 103614,
            "target_hp_max": 119700,
            "target_hp_pct": 86.5614,
            "target_project_position_error": "project_error",
        },
    }

    text = format_truth_dashboard(
        state,
        now=100.2,
        combat_config={"attack_nearby_mobs": True},
        buff_config={"use_buff_config": True, "buffs": [{"key": "f1", "enabled": True, "interval_seconds": 109}]},
        runs=[],
    )

    assert "[TARGET PROVEN] Metin da Alma" in text
    assert "pixel_position=(95920, 25729)" in text
    assert "project_position=not proven" in text
    assert "z=not proven" in text
    assert "HP 86.6%" in text
    assert "[SAFETY LOCKED]" in text
    assert "manual buffs observed; automation not proven" in text or "no active dispatcher proof" in text
    assert "DRY-RUN ONLY" in text
    assert "generic mobs need a separate hostile-mob gate" in text


def test_control_panel_source_exposes_operator_enabled_attack_live_toggle():
    source = Path("metin2_dashboard/control_panel.py").read_text(encoding="utf-8")
    assert "Select/attack nearby LIVE (operator enabled)" in source
    assert "toggle_attack_nearby_live" in source
    assert "confirm_live" in source
    assert "Automation truth dashboard" in source
    assert "ribbon_left_var" in source
    assert "LIVE ATTACK ENABLED" in source

def test_control_panel_log_tail_has_manual_refresh_and_follow_toggle():
    source = Path("metin2_dashboard/control_panel.py").read_text(encoding="utf-8")
    assert "Refresh log tail" in source
    assert "follow log tail" in source
    assert "Log tail: paused" in source
    assert "def refresh_selected_log" in source


def test_control_panel_uses_notebook_pages_to_organize_operator_surface():
    source = Path("metin2_dashboard/control_panel.py").read_text(encoding="utf-8")
    assert "ttk.Notebook" in source
    assert "text=\"State\"" in source
    assert "F1/F2 + buffs" in source
    assert "Scripts" in source
    assert "Targets + runs" in source
    assert "Player training" in source
    assert "Boss farm" in source
    assert "Farm metrics" in source
    assert "Start DPS/item tracker" in source
    assert "farm_metrics_tracker" in source
    assert "DPS meter + item farm history" in source
    assert "Start boss farm tracker" in source
    assert "Start gated Chefe Orc LIVE" in source
    assert "chefe_orc_live_control" in source
    assert "boss_farm_tracker" in source
    assert "alterar personagem" in source
    assert "boss_kills_confirmed" in source
    assert "Cofre do Chefe Orc" in source
    assert "loot vnum" in source
    assert "Reroll Items" in source
    assert "possible rolls for selected equipment slot" in source
    assert "reroll_slot_notebook" in source
    assert "build_reroll_slot_tabs" in source
    assert "self.reroll_slot_combo" not in source
    assert "Best stat #1" in source
    assert "Best stat #4" in source
    assert "Start reroll recorder" in source
    assert "reroll_recorder" in source
    assert "reports/reroll_recordings" in source
    assert "reroll_desired_stat_vars" in source
    assert "comma-separated" not in source



def test_reroll_panel_formats_slot_tables_and_builds_desired_stat_payload():
    config = {
        "equip_slots": [
            {
                "slot": "weapon",
                "label": "Weapon",
                "possible_rolls": [
                    {"attr_type": 71, "name": "Dano Médio", "observed_values": [-49, 23], "observed_min": -49, "observed_max": 23},
                    {"attr_type": 72, "name": "Dano de Habilidade", "observed_values": [-29, 12], "observed_min": -29, "observed_max": 12},
                ],
            }
        ],
        "desired_stats": {"weapon": [{"attr_type": 71, "target_value": 50, "priority": 1}]},
    }

    table = format_reroll_slot_table(config, "weapon")
    assert "Weapon" in table
    assert "Dano Médio" in table
    assert "desired priority 1 target 50" in table

    summary = format_reroll_config_summary(config)
    assert "Reroll Items" in summary
    assert "weapon: #1 attr 71 >= 50" in summary

    payload = build_reroll_config_payload(
        "weapon",
        [
            {"attr_type": "71", "target_value": "50"},
            {"attr_type": "72", "target_value": "20"},
            {"attr_type": "", "target_value": ""},
            {"attr_type": "15", "target_value": "10"},
        ],
        config,
    )
    assert payload["desired_stats"]["weapon"] == [
        {"attr_type": 71, "target_value": 50, "priority": 1},
        {"attr_type": 72, "target_value": 20, "priority": 2},
        {"attr_type": 15, "target_value": 10, "priority": 4},
    ]


def test_build_boss_farm_tracker_payload_is_observation_only():
    payload = build_boss_farm_tracker_payload(
        duration="7200",
        interval="1",
        boss_name="Boss Foo",
        spawn_interval_minutes="30",
        channels="8",
        wait_menu="alterar personagem",
        state_json=BUFFER_JSON_STATE,
    )

    assert payload["script"] == "boss_farm_tracker"
    assert payload["live"] is False
    assert payload["confirm_live"] is False
    assert payload["options"] == {
        "duration": "7200.0",
        "interval": "1.0",
        "state_json": BUFFER_JSON_STATE,
        "boss_name": "Boss Foo",
        "spawn_interval_minutes": "30.0",
        "channels": "8",
        "wait_menu": "alterar personagem",
        "loot_name": "Cofre do Chefe Orc",
        "loot_vnum": "50070",
    }


def test_build_boss_live_control_payload_is_gated_live_control():
    payload = build_boss_live_control_payload(
        duration="600",
        max_kills="4",
        channel_rotate=True,
        channel_click_points="0.4039,0.4559;0.4039,0.4830",
        pickup_spam_count="12",
        state_json=BUFFER_JSON_STATE,
    )

    assert payload["script"] == "chefe_orc_live_control"
    assert payload["live"] is True
    assert payload["confirm_live"] is True
    assert payload["options"]["state_json"] == BUFFER_JSON_STATE
    assert payload["options"]["boss_name"] == "Chefe Orc"
    assert payload["options"]["loot_vnum"] == "50070"
    assert payload["options"]["channel_rotate"] is True
    assert payload["options"]["channel_click_points"] == "0.4039,0.4559;0.4039,0.4830"
    assert payload["options"]["pickup_spam_count"] == "12"


def test_build_farm_metrics_payload_and_summary_are_read_only():
    payload = build_farm_metrics_payload(duration="120", interval="0.25", history_window="15", state_json=BUFFER_JSON_STATE)

    assert payload["script"] == "farm_metrics_tracker"
    assert payload["live"] is False
    assert payload["confirm_live"] is False
    assert payload["options"]["duration"] == "120.0"
    assert payload["options"]["interval"] == "0.25"
    assert payload["options"]["history_window"] == "15.0"
    assert payload["options"]["state_json"] == BUFFER_JSON_STATE
    assert "potion" not in payload["options"]
    assert "click" not in payload["options"]

    text = format_farm_metrics_summary({
        "run_id": "farm1",
        "outcome": "completed",
        "samples": 5,
        "duration_seconds": 2.5,
        "dps": {"last": 10, "best": 12, "average": 8, "total_damage": 20, "kills_estimated": 1},
        "items": {"inventory_available": True, "farmed_total_count": 3, "farmed": [{"name": "Livro", "vnum": 1, "count": 2}], "loot_events": []},
        "log": "reports/dashboard_runs/farm_metrics_tracker.jsonl",
    })
    assert "DPS last=10" in text
    assert "2x Livro" in text
    assert "items farmed total: 3" in text


def test_build_reroll_recorder_payload_is_observation_only():
    payload = build_reroll_recorder_payload(duration="120", interval="0.2", target_slot="12", target_vnum="2849", state_json=BUFFER_JSON_STATE)

    assert payload["script"] == "reroll_recorder"
    assert payload["live"] is False
    assert payload["confirm_live"] is False
    assert payload["options"] == {
        "duration": "120.0",
        "interval": "0.2",
        "target_slot": "12",
        "target_vnum": "2849",
        "state_json": BUFFER_JSON_STATE,
    }



def test_render_reroll_config_preserves_dirty_operator_edits():
    class FakeVar:
        def __init__(self, value):
            self.value = value
        def set(self, value):
            self.value = value
        def get(self):
            return self.value
    class FakeLabel:
        def __init__(self):
            self.value = ""
        def set(self, value):
            self.value = value

    app = object.__new__(ControlPanelApp)
    app._reroll_config_dirty = True
    app.reroll_slot_var = FakeVar("weapon")
    app.reroll_desired_stat_vars = [FakeVar("71 - Dano Médio"), FakeVar("72 - Dano de Habilidade"), FakeVar(""), FakeVar("")]
    app.reroll_desired_target_vars = [FakeVar("50"), FakeVar("20"), FakeVar(""), FakeVar("")]
    app.reroll_desired_combos = []
    app.reroll_status_var = FakeLabel()

    app.render_reroll_config({"equip_slots": [{"slot": "weapon", "label": "Weapon", "possible_rolls": []}], "desired_stats": {"weapon": []}})

    assert app.reroll_desired_stat_vars[0].get() == "71 - Dano Médio"
    assert app.reroll_desired_target_vars[0].get() == "50"
    assert "editing; not overwritten" in app.reroll_status_var.value



def test_control_panel_build_server_cmd_can_target_buffer_client():
    cmd = build_server_cmd(port="8768", json_state=BUFFER_JSON_STATE, tsv_state=BUFFER_TSV_STATE)

    assert "--json-state" in cmd
    assert BUFFER_JSON_STATE in cmd
    assert "--tsv" in cmd
    assert BUFFER_TSV_STATE in cmd
    assert cmd[cmd.index("--port") + 1] == "8768"



def test_open_login_payload_can_target_buffer_account_and_app_dir():
    payload = build_quick_start_payload("open_login_game", login_username="buffer", login_app_dir="D:/Games/MT2PortugaliaBuffer/app")

    assert payload["script"] == "login_mt2_local"
    assert payload["live"] is True
    assert payload["confirm_live"] is True
    assert payload["options"] == {"username": "buffer", "app_dir": "D:/Games/MT2PortugaliaBuffer/app"}


def test_control_panel_source_exposes_login_account_config_fields():
    source = Path("metin2_dashboard/control_panel.py").read_text(encoding="utf-8")

    assert "Login accounts" in source
    assert "Save login config" in source
    assert "Open MAIN + login" in source
    assert "Open BUFFER + login" in source
    assert "Open FARMER + login" in source
    assert "D:/Games/MT2PortugaliaFarmer/app" in source
    assert "This keeps the other client open" in source
    assert "/api/login_config" in source

    assert "open_login_profile" in source
    assert "login_buffer_user_var" in source
    assert "login_buffer_password_var" in source
    assert "login_farmer_user_var" in source
    assert "login_farmer_password_var" in source
    assert "_login_config_dirty" in source
    assert "Login config editing; auto-refresh will not overwrite fields" in source

def test_player_training_payload_preserves_buffer_state_json():
    payload = build_player_training_payload(duration="2", interval="0.1", state_json=BUFFER_JSON_STATE)

    assert payload["script"] == "player_training_recorder"
    assert payload["options"]["state_json"] == BUFFER_JSON_STATE

def test_control_panel_script_runs_from_project_root_without_pythonpath():
    result = subprocess.run(
        [sys.executable, "scripts/metin2_control_panel.py", "--help"],
        cwd=".",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--base-url" in result.stdout




def test_global_stop_hotkey_poll_once_fires_on_ctrl_alt_s(monkeypatch):
    calls = []

    class FakeRoot:
        def after(self, _delay, callback):
            callback()

    hotkey = GlobalStopHotkey(FakeRoot(), lambda: calls.append("stop"))
    pressed = {hotkey.VK_CONTROL, hotkey.VK_MENU, hotkey.VK_S}
    hotkey._user32 = object()
    monkeypatch.setattr(hotkey, "_down", lambda vk: vk in pressed)

    assert hotkey.poll_once() is True
    assert calls == ["stop"]


def test_global_stop_hotkey_poll_once_requires_full_chord(monkeypatch):
    calls = []

    class FakeRoot:
        def after(self, _delay, callback):
            callback()

    hotkey = GlobalStopHotkey(FakeRoot(), lambda: calls.append("stop"))
    pressed = {hotkey.VK_CONTROL, hotkey.VK_MENU}
    hotkey._user32 = object()
    monkeypatch.setattr(hotkey, "_down", lambda vk: vk in pressed)

    assert hotkey.poll_once() is False
    assert calls == []

def test_stop_all_now_posts_stop_all_without_confirmation():
    calls = []

    class FakeVar:
        def __init__(self):
            self.value = None
        def set(self, value):
            self.value = value

    app = object.__new__(ControlPanelApp)
    app.action_status = FakeVar()
    app._post_async = lambda path, payload: calls.append((path, payload))

    app.stop_all_now()

    assert calls == [("/api/stop_all", {})]
    assert "Ctrl+Alt+S" in app.action_status.value


def test_stop_all_button_uses_immediate_stop_after_confirmation(monkeypatch):
    calls = []
    app = object.__new__(ControlPanelApp)
    app.stop_all_now = lambda source="hotkey": calls.append(source)
    monkeypatch.setattr("metin2_dashboard.control_panel.messagebox.askokcancel", lambda *args, **kwargs: True)

    app.stop_all()

    assert calls == ["button"]


def test_buff_only_live_payload_can_start_on_ground_without_ctrl_g():
    mounted = build_quick_start_payload("buff_only_live", assume_mounted=True)
    ground = build_quick_start_payload("buff_only_live", assume_mounted=False)

    assert mounted["options"]["assume_mounted"] is True
    assert ground["options"]["assume_mounted"] is False


def test_toggle_buff_only_live_uses_starting_mount_toggle(monkeypatch):
    calls = []

    class FakeVar:
        def __init__(self, value):
            self.value = value
        def get(self):
            return self.value

    app = object.__new__(ControlPanelApp)
    app.buff_start_mounted_var = FakeVar(False)
    app.f1_enabled_var = FakeVar(True)
    app.f2_enabled_var = FakeVar(True)
    app.f1_interval_var = FakeVar("124")
    app.f2_interval_var = FakeVar("304")
    app.f1_pre_cast_var = FakeVar("3")
    app.f2_pre_cast_var = FakeVar("3")
    app.f1_active_attack_min_min_var = FakeVar("215")
    app.f2_active_attack_speed_min_var = FakeVar("145")
    app._toggle_existing_run_or_none = lambda **kwargs: False
    app._post_after_api_ready = lambda payload: calls.append(payload)
    monkeypatch.setattr("metin2_dashboard.control_panel.messagebox.askokcancel", lambda *args, **kwargs: True)

    app.toggle_buff_only_live()

    assert calls
    assert calls[0]["options"]["buff_only"] is True
    assert calls[0]["options"]["assume_mounted"] is False
    assert calls[0]["options"]["buff_keys"] == "f1,f2"
    assert calls[0]["options"]["buff_durations"] == "124,304"
    assert calls[0]["options"]["buff_refresh_margin_seconds"] == "3"
    assert calls[0]["options"]["f1_active_attack_min_min"] == "215"
    assert calls[0]["options"]["f2_active_attack_speed_min"] == "145"

def test_quick_start_payloads_cover_login_and_practice_buttons():
    assert build_quick_start_payload("open_login_game") == {
        "script": "login_mt2_local",
        "live": True,
        "confirm_live": True,
        "options": {},
    }
    assert build_quick_start_payload("integrate_client") == {
        "script": "patch_mt2_root_state_logger",
        "live": False,
        "confirm_live": False,
        "options": {},
    }
    assert build_quick_start_payload("practice_dry_run") == {
        "script": "combat_metin_client_state",
        "live": False,
        "confirm_live": False,
        "options": {"max_cycles": "5"},
    }
    assert build_quick_start_payload("practice_live") == {
        "script": "combat_metin_client_state",
        "live": True,
        "confirm_live": True,
        "options": {"max_cycles": "0"},
    }
    assert build_quick_start_payload("attack_nearby_live") == {
        "script": "combat_metin_client_state",
        "live": True,
        "confirm_live": True,
        "options": {"max_cycles": "0", "attack_nearby_mobs": True, "visual_target_clicks": True, "allow_blind_target_clicks": True, "minimap_camera_hint": True},
    }
    assert build_quick_start_payload("buff_only_live") == {
        "script": "combat_metin_client_state",
        "live": True,
        "confirm_live": True,
        "options": {"max_cycles": "0", "buff_only": True, "assume_mounted": True, "buff_damage_guard_keys": "f1", "f1_active_attack_min_min": "200", "f2_active_attack_speed_min": "130"},
    }
    assert build_quick_start_payload("find_nearby_metins") == {
        "script": "find_nearby_metins",
        "live": False,
        "confirm_live": False,
        "options": {"radius": "300", "limit": "8"},
    }







def test_player_training_payload_is_observation_only_dry_run():
    payload = build_player_training_payload(duration="180", interval="0.25", capture_screenshots=True)

    assert payload == {
        "script": "player_training_recorder",
        "live": False,
        "confirm_live": False,
        "options": {"duration": "180.0", "interval": "0.25", "capture_screenshots": True, "screenshot_backend": "screen", "record_mouse": True, "refresh_window_every": "1.0", "window_query": "MT2Portugalia", "state_json": "D:/Games/MT2Portugalia/app/hermes_state.json"},
    }


def test_player_training_panel_text_includes_start_command_outputs_and_safety():
    text = format_player_training_panel_text(duration="300", interval="0.25", capture_screenshots=True)

    assert "observation-only" in text
    assert "sends no keys/clicks" in text
    assert "mouse clicks" in text
    assert "reports/player_training_runs/<run-id>/events.jsonl" in text
    assert "summary.json" in text
    assert "recommendations.md" in text
    assert "python scripts/player_training_recorder.py --duration 300" in text
    assert "--window-query MT2Portugalia" in text
    assert "--screenshot-backend screen" in text
    assert "--refresh-window-every 1.0" in text


def test_player_training_manual_command_omits_screenshot_flag_when_disabled():
    command = build_player_training_manual_command(duration="60", interval="0.5", capture_screenshots=False)

    assert "--duration 60" in command
    assert "--interval 0.5" in command
    assert "--capture-screenshots" not in command
    assert "--record-mouse" in command
    assert "PYTHONPATH='src;.'" in command


def test_latest_player_training_summary_reads_newest_summary(tmp_path):
    old = tmp_path / "reports" / "player_training_runs" / "old"
    new = tmp_path / "reports" / "player_training_runs" / "new"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "summary.json").write_text(json.dumps({"samples": 1, "recommendations": []}), encoding="utf-8")
    (new / "summary.json").write_text(json.dumps({"samples": 9, "recommendations": [{"topic": "pickup_after_destroy", "suggestion": "spam Z"}]}), encoding="utf-8")

    summary = latest_player_training_summary(tmp_path)

    assert summary["run_dir"].endswith("new")
    assert "samples: 9" in summary["text"]
    assert "pickup_after_destroy" in summary["text"]


def test_player_training_run_status_shows_recording_when_active(tmp_path):
    run_dir = tmp_path / "reports" / "player_training_runs" / "run-live"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        "{}\n" + json.dumps({"type": "sample", "t": 0.1}) + "\n" + json.dumps({"type": "key_down", "key": "w", "t": 0.2}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "screenshots").mkdir()
    (run_dir / "screenshots" / "frame-00000.jpg").write_bytes(b"fake")
    runs = [{"run_id": "run-live", "script": "player_training_recorder", "running": True, "exit_code": None}]

    assert find_running_player_training_run(runs)["run_id"] == "run-live"
    text = format_player_training_run_status(tmp_path, runs)["text"]

    assert "RECORDING" in text
    assert "run-live" in text
    assert "samples so far: 1" in text
    assert "key events so far: 1" in text
    assert "screenshots so far: 1" in text


def test_player_training_run_status_shows_latest_analysis_when_not_running(tmp_path):
    run_dir = tmp_path / "reports" / "player_training_runs" / "done"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({"samples": 7, "target_lock_count": 2, "destroy_candidates": 1, "recommendations": [{"topic": "attack_start", "suggestion": "pulse Space"}]}), encoding="utf-8")

    status = format_player_training_run_status(tmp_path, [])

    assert status["recording"] is False
    assert "Latest analysis" in status["text"]
    assert "samples: 7" in status["text"]
    assert "attack_start" in status["text"]


def test_learned_channel_click_points_match_visible_ch1_to_ch8_rows():
    points = LEARNED_CHANNEL_CLICK_POINTS.split(";")
    assert len(points) == 8
    assert points[0] == "0.4990,0.3986"  # CH1 row, physical-pixel center
    assert points[1] == "0.4990,0.4326"  # CH2
    assert points[2] == "0.4990,0.4665"  # CH3
    assert points[3] == "0.4990,0.5005"  # CH4
    assert points[-1] == "0.4990,0.6363"  # CH8


def test_attack_nearby_live_payload_can_enable_channel_rotation():
    payload = build_quick_start_payload(
        "attack_nearby_live",
        channel_rotate_after_destroy=True,
        channel_click_points="0.42,0.35;0.42,0.45",
        pickup_spam_count="15",
    )

    assert payload["options"]["channel_rotate_after_destroy"] is True
    assert payload["options"]["channel_click_points"] == "0.42,0.35;0.42,0.45"
    assert payload["options"]["pickup_spam_count"] == "15"


def test_live_toggle_run_detection_distinguishes_buff_keeper_and_attack_nearby():
    runs = [
        {"run_id": "buff", "script": "combat_metin_client_state", "mode": "live", "running": True, "command": ["python", "scripts/combat_metin_client_state.py", "--live", "--buff-only", "--max-cycles", "0"]},
        {"run_id": "attack", "script": "combat_metin_client_state", "mode": "live", "running": True, "command": "python scripts/combat_metin_client_state.py --live --attack-nearby-mobs --max-cycles 0"},
        {"run_id": "dry", "script": "combat_metin_client_state", "mode": "dry-run", "running": True, "command": "--buff-only"},
    ]

    assert find_running_buff_keeper_run(runs)["run_id"] == "buff"
    assert find_running_attack_nearby_run(runs)["run_id"] == "attack"

def test_build_combat_payload_uses_live_memory_coordinate(tmp_path):
    artifact = tmp_path / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "metins": [
                    {
                        "metin_name": "Metin da Batalha",
                        "source_type": "live_memory_visible_text",
                        "location": {"x": 930, "y": 837},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    state = {"target": {"name": "Metin da Batalha", "vid": 3846, "alive": True}}

    payload, coord = build_combat_payload(tmp_path, state)

    assert payload["script"] == "combat_metin_client_state"
    assert payload["live"] is True
    assert payload["confirm_live"] is True
    assert payload["options"]["metin_name"] == "Metin da Batalha"
    assert payload["options"]["metin_x"] == "93000"
    assert payload["options"]["metin_y"] == "83700"
    assert payload["options"]["metin_vid"] == "3846"
    assert payload["options"]["metin_coord_source"] == "live_memory_visible_text"
    assert coord == {"source_type": "live_memory_visible_text", "label": "Live memory label", "x": 930, "y": 837, "raw_x": 93000, "raw_y": 83700}


def test_build_combat_payload_rejects_table_rows_from_auto_scan(tmp_path):
    artifact = tmp_path / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "metins": [
                    {
                        "metin_name": "Metin da Batalha",
                        "source_type": "table",
                        "location": {"x": 284, "y": 218},
                        "vid": 3846,
                        "alive": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload, coord = build_combat_payload(tmp_path, {"target": {"name": "Metin da Batalha", "vid": 3846}})

    assert coord is None
    assert payload["options"] == {"max_cycles": "0", "metin_vid": "3846"}


def test_build_combat_payload_rejects_current_position_estimate_for_navigation(tmp_path):
    artifact = tmp_path / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "metins": [
                    {
                        "metin_name": "Metin da Batalha",
                        "source_type": "target_selected",
                        "location": {"x": 548, "y": 183},
                    },
                    {
                        "metin_name": "Metin da Batalha",
                        "source_type": "visual_detector_current_position_estimate",
                        "location": {"x": 549, "y": 184},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    payload, coord = build_combat_payload(tmp_path, {"target": {"name": "Metin da Batalha", "vid": 3846}})

    assert coord is None
    assert payload["options"] == {"max_cycles": "0", "metin_vid": "3846"}


def test_build_combat_confirmation_message_shows_navigation_status():
    state = {
        "map": "metin2_map_a1",
        "player": {"hp": 2452, "max_hp": 2452},
        "target": {"name": "Metin da Batalha", "vid": 3846, "alive": True},
    }

    with_coord = build_combat_confirmation_message(
        state,
        {"label": "Live memory label", "x": 930, "y": 837, "raw_x": 93000, "raw_y": 83700},
    )
    without_coord = build_combat_confirmation_message(state, None)

    assert "Coord     (930, 837)  [Live memory label]" in with_coord
    assert "Navigation enabled" in with_coord
    assert "Do not rotate the camera during this run" in with_coord
    assert "Coord     none  player position estimate only" in without_coord
    assert "Navigation disabled" in without_coord


def test_build_move_payload_from_selected_live_memory_metin():
    payload, detail = build_move_payload_from_metin(
        {
            "metin_name": "Metin da Batalha",
            "source_type": "live_memory_visible_text",
            "location": {"x": 542, "y": 219},
        }
    )

    assert payload == {
        "script": "move_to_metin_client_state",
        "live": True,
        "confirm_live": True,
        "options": {
            "max_cycles": "90",
            "metin_name": "Metin da Batalha",
            "metin_x": "54200",
            "metin_y": "21900",
            "metin_coord_source": "live_memory_visible_text",
        },
    }
    assert detail == "(542, 219) [Live memory label]"
    assert "movement only" in build_move_confirmation_message({"metin_name": "Metin da Batalha"}, detail)


def test_build_move_payload_rejects_estimate_only_metin():
    payload, detail = build_move_payload_from_metin(
        {
            "metin_name": "Metin da Batalha",
            "source_type": "visual_detector_current_position_estimate",
            "location": {"x": 542, "y": 219},
        }
    )

    assert payload is None
    assert "not trusted" in detail


def test_load_nearby_metins_artifact_reads_selectable_rows(tmp_path):
    artifact = tmp_path / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"metins": [{"metin_name": "A"}, {"bad": True}]}), encoding="utf-8")

    rows = load_nearby_metins_artifact(tmp_path)

    assert len(rows) == 2
    assert rows[0]["metin_name"] == "A"


def test_find_then_fight_starts_combat_after_trusted_scan_result(tmp_path, monkeypatch):
    artifact = tmp_path / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "metins": [
                    {
                        "metin_name": "Metin da Batalha",
                        "source_type": "live_memory_visible_text",
                        "location": {"x": 930, "y": 837},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class Client:
        def __init__(self):
            self.posts = []
        def post(self, path, payload):
            self.posts.append((path, payload))
            return {"run_id": "run-find"}
        def get(self, path):
            if path == "/api/runs":
                return [{"run_id": "run-find", "script": "find_nearby_metins", "running": False, "exit_code": 0}]
            if path == "/api/state":
                return {"map": "metin2_map_a1", "player": {"hp": 2452, "max_hp": 2452}, "target": None}
            raise AssertionError(path)

    app = object.__new__(ControlPanelApp)
    app.client = Client()
    app.project_root = tmp_path
    app.refresh_all = lambda: None
    app.root = type("Root", (), {"after": lambda _self, delay, func: func()})()
    statuses = []
    posted = []
    app.set_status = statuses.append
    app._post_after_api_ready = lambda payload: posted.append(payload)
    monkeypatch.setattr("metin2_dashboard.control_panel.messagebox.askokcancel", lambda title, msg: "Navigation enabled" in msg)

    app._find_then_fight_worker()

    assert app.client.posts == [("/api/start", build_quick_start_payload("find_nearby_metins"))]
    assert posted[0]["script"] == "combat_metin_client_state"
    assert posted[0]["live"] is True
    assert posted[0]["confirm_live"] is True
    assert posted[0]["options"]["metin_x"] == "93000"
    assert posted[0]["options"]["metin_y"] == "83700"
    assert posted[0]["options"]["metin_coord_source"] == "live_memory_visible_text"
    assert posted[0]["options"]["max_cycles"] == "120"
    assert any("trusted coordinate" in status for status in statuses)


def test_find_then_fight_refuses_estimate_only_scan_result(tmp_path, monkeypatch):
    artifact = tmp_path / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({"metins": [{"metin_name": "Metin stone", "source_type": "visual_detector_current_position_estimate", "location": {"x": 540, "y": 216}}]}),
        encoding="utf-8",
    )

    class Client:
        def post(self, path, payload):
            return {"run_id": "run-find"}
        def get(self, path):
            if path == "/api/runs":
                return [{"run_id": "run-find", "script": "find_nearby_metins", "running": False, "exit_code": 0}]
            if path == "/api/state":
                return {"map": "metin2_map_a1", "player": {"hp": 2452, "max_hp": 2452}, "target": None}
            raise AssertionError(path)

    app = object.__new__(ControlPanelApp)
    app.client = Client()
    app.project_root = tmp_path
    app.refresh_all = lambda: None
    app.root = type("Root", (), {"after": lambda _self, delay, func: func()})()
    statuses = []
    posted = []
    app.set_status = statuses.append
    app._post_after_api_ready = lambda payload: posted.append(payload)
    monkeypatch.setattr("metin2_dashboard.control_panel.messagebox.askokcancel", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not confirm")))

    app._find_then_fight_worker()

    assert posted == []
    assert any("estimate only" in status for status in statuses)


def test_quick_action_starts_api_before_posting_when_needed(monkeypatch):
    scheduled = []
    app = object.__new__(ControlPanelApp)
    app.server_proc = None
    app.root = type("FakeRoot", (), {"after": lambda _self, delay, func: scheduled.append((delay, func))})()
    monkeypatch.setattr(app, "start_server", lambda: scheduled.append(("start_server", None)))
    monkeypatch.setattr(app, "_post_async", lambda path, payload: scheduled.append((path, payload)))

    payload = build_quick_start_payload("practice_dry_run")
    app._post_after_api_ready(payload)

    assert scheduled[0] == ("start_server", None)
    assert scheduled[1][0] == 1500
    scheduled[1][1]()
    assert scheduled[2] == ("/api/start", payload)


def test_archive_selected_posts_archive_run_endpoint(monkeypatch):
    posted = []
    app = object.__new__(ControlPanelApp)
    app.selected_run = type("Selected", (), {"get": lambda _self: "run-1"})()
    monkeypatch.setattr(app, "_post_async", lambda path, payload: posted.append((path, payload)))

    app.archive_selected()

    assert posted == [("/api/archive_run", {"run_id": "run-1"})]


def test_post_async_freezes_exception_message_for_tk_callback(monkeypatch):
    shown = []
    app = object.__new__(ControlPanelApp)
    app.client = type("FailingClient", (), {"post": lambda _self, path, payload: (_ for _ in ()).throw(RuntimeError("boom"))})()
    app.root = type("FakeRoot", (), {"after": lambda _self, delay, func: func()})()
    monkeypatch.setattr("metin2_dashboard.control_panel.messagebox.showerror", lambda title, message: shown.append((title, message)))

    app._post_async("/api/start", {"script": "probe_client_state"})

    assert shown == [("API error", "boom")]


def test_start_selected_treats_force_dry_run_scripts_as_dry_run(monkeypatch):
    app = object.__new__(ControlPanelApp)
    app.script_vars = {"find_nearby_metins": {}}
    app.selected_script = type("Selected", (), {"get": lambda _self: "find_nearby_metins"})()
    app.scripts = [{"name": "find_nearby_metins", "force_dry_run": True, "options": []}]
    posted = []
    monkeypatch.setattr(app, "_post_async", lambda path, payload: posted.append((path, payload)))
    monkeypatch.setattr("metin2_dashboard.control_panel.messagebox.askokcancel", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not prompt for read-only script")))

    app.start_selected(True)

    assert posted == [("/api/start", {"script": "find_nearby_metins", "live": False, "confirm_live": False, "options": {}})]


def test_has_active_live_combat_run_detects_running_exclusive_practice():
    assert has_active_live_combat_run([
        {"script": "combat_metin_client_state", "mode": "live", "running": True, "run_id": "run-1"}
    ]) is True
    assert has_active_live_combat_run([
        {"script": "combat_metin_client_state", "mode": "live", "running": False, "run_id": "run-1"}
    ]) is False
    assert has_active_live_combat_run([
        {"script": "combat_metin_client_state", "mode": "dry-run", "running": True, "run_id": "run-1"}
    ]) is False
    assert has_active_live_combat_run([
        {"script": "combat_metin_client_state", "mode": "live", "running": True, "run_id": "run-buff", "command": ["python", "script", "--live", "--buff-only"]}
    ]) is False


def test_has_active_live_control_run_includes_movement_runs():
    assert has_active_live_control_run([
        {"script": "move_to_metin_client_state", "mode": "live", "running": True, "run_id": "run-move"}
    ]) is True
    assert has_active_live_control_run([
        {"script": "move_to_metin_client_state", "mode": "dry-run", "running": True, "run_id": "run-move"}
    ]) is False
    assert has_active_live_control_run([
        {"script": "combat_metin_client_state", "mode": "live", "running": True, "run_id": "run-buff", "command": "--buff-only"}
    ]) is False


def test_practice_live_blocks_before_confirmation_when_combat_already_running(monkeypatch):
    app = object.__new__(ControlPanelApp)
    app.client = type(
        "Client",
        (),
        {"get": lambda _self, path: [{"script": "combat_metin_client_state", "mode": "live", "running": True, "run_id": "run-1"}] if path == "/api/runs" else {}},
    )()
    shown = []
    app.refresh_all = lambda: None
    monkeypatch.setattr("metin2_dashboard.control_panel.messagebox.showinfo", lambda title, message: shown.append((title, message)))
    monkeypatch.setattr("metin2_dashboard.control_panel.messagebox.askokcancel", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not prompt")))

    app.practice_live()

    assert shown and "already running" in shown[0][1]


def test_post_async_blocks_second_start_while_first_start_is_in_flight(monkeypatch):
    calls = []
    app = object.__new__(ControlPanelApp)
    app._start_in_flight = False
    app.client = type("Client", (), {"post": lambda _self, path, payload: calls.append((path, payload)) or {"run_id": "run-1"}})()
    app.root = type("Root", (), {"after": lambda _self, delay, func: None})()
    app.action_status = type("Status", (), {"set": lambda _self, text: None})()
    monkeypatch.setattr(app, "refresh_all", lambda: None)

    app._post_async("/api/start", {"script": "combat_metin_client_state", "live": True})
    app._post_async("/api/start", {"script": "combat_metin_client_state", "live": True})

    assert len(calls) == 1


def test_format_state_card_shows_operator_summary_with_target_and_age():
    state = {
        "timestamp_ms": 1_000_000,
        "map": "metin2_map_a1",
        "player": {
            "name": "Yoshypt",
            "x": 62784.0,
            "y": 61534.0,
            "hp": 1500,
            "max_hp": 1864,
            "sp": 1380,
            "max_sp": 1422,
        },
        "target": {"name": "Metin da Batalha", "vid": 4821, "alive": True},
    }

    text, hp_ratio = format_state_card(state, now_ms=1_000_300)

    assert "Yoshypt  |  metin2_map_a1  |  connected" in text
    assert "HP    1500/1864" in text
    assert "SP    1380/1422" in text
    assert "Pos   (628, 615)" in text
    assert "Target  Metin da Batalha  VID:4821  alive" in text
    assert "Last update 0.3s ago" in text
    assert round(hp_ratio, 2) == 0.8


def test_format_state_card_supports_flat_tsv_shape_and_file_mtime():
    state = {
        "available": True,
        "timestamp_ms": "1787450544",
        "map": "metin2_map_a1",
        "x": "62784",
        "y": "61534",
        "hp": "1500",
        "max_hp": "1864",
        "sp": "1380",
        "max_sp": "1422",
        "target_vid": "4821",
        "player_name": "Yoshypt",
        "target_name": "Metin da Batalha",
        "_file_mtime": 100.0,
    }

    text, hp_ratio = format_state_card(state, now=103.5)

    assert "Yoshypt  |  metin2_map_a1  |  connected" in text
    assert "HP    1500/1864" in text
    assert "SP    1380/1422" in text
    assert "Pos   (628, 615)" in text
    assert "Target  Metin da Batalha  VID:4821" in text
    assert "Last update  3.5s ago" in text
    assert round(hp_ratio, 2) == 0.8


def test_format_state_card_uses_json_file_mtime_not_client_runtime_timestamp():
    state = {
        "timestamp_ms": 123,
        "_file_mtime": 100.0,
        "map": "metin2_map_a1",
        "player": {"name": "Yoshypt", "x": 62784, "y": 61534, "hp": 1864, "max_hp": 1864, "sp": 1380, "max_sp": 1422},
    }

    text, _hp_ratio = format_state_card(state, now=101.25)

    assert "Last update  1.2s ago" in text
    assert "178" not in text


def test_parse_combat_log_tail_reads_last_structured_state_line():
    parsed = parse_combat_log_tail(
        "noise\n[state=ENSURE_BUFF] [action=press_buff] [hp=1800/1900] [sp=1300/1400] [target=Metin da Batalha vid=4821 alive=True]\n"
        "{\"json\": true}\n[state=ATTACK_METIN] [action=hold_space] [hp=1820/1900] [sp=1200/1400] [target=Metin da Batalha vid=4821 alive=True]\n"
    )

    assert parsed == {
        "state": "ATTACK_METIN",
        "action": "hold_space",
        "hp": "1820/1900",
        "sp": "1200/1400",
        "target": "Metin da Batalha vid=4821 alive=True",
    }


def test_format_combat_summary_reads_report_json(tmp_path):
    report_dir = tmp_path / "reports" / "dashboard_runs"
    report_dir.mkdir(parents=True)
    (report_dir / "run-1_report.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "outcome": "destroyed",
                "metin_name": "Metin da Batalha",
                "metin_vid": 3846,
                "duration_seconds": 47.2,
                "hp_at_end": 2452,
                "max_hp": 2452,
                "potions_used": 0,
                "states_visited": ["ENSURE_BUFF", "ATTACK_METIN", "VERIFY_DESTROYED"],
            }
        ),
        encoding="utf-8",
    )

    text = format_combat_summary(tmp_path, "run-1")

    assert "Last combat run" in text
    assert "Outcome    destroyed" in text
    assert "Target     Metin da Batalha  VID:3846" in text
    assert "Duration   47.2s" in text
    assert "HP at end  2452/2452" in text
    assert "Potions    0" in text
    assert "States     ENSURE_BUFF  ATTACK_METIN  VERIFY_DESTROYED" in text


def test_combat_state_color_maps_important_states():
    assert combat_state_color("ATTACK_METIN") == "green"
    assert combat_state_color("RECOVER_HP_SP") == "goldenrod"
    assert combat_state_color("ENSURE_BUFF") == "goldenrod"
    assert combat_state_color("KILL_ADDS") == "darkorange"
    assert combat_state_color("REACQUIRE_METIN") == "darkorange"
    assert combat_state_color("VERIFY_DESTROYED") == "royalblue"
    assert combat_state_color("ABORT_SAFE") == "red"
    assert combat_state_color("STOP_REQUESTED") == "red"
    assert combat_state_color("idle") == "black"


def test_combat_log_is_stale_uses_log_mtime_for_running_runs(tmp_path):
    log_path = tmp_path / "run.log"
    log_path.write_text("[state=ATTACK_METIN] [action=hold_space]\n", encoding="utf-8")
    import os
    os.utime(log_path, (100.0, 100.0))

    assert combat_log_is_stale({"running": True, "log_path": str(log_path)}, now=106.1, max_age=5.0) is True
    assert combat_log_is_stale({"running": True, "log_path": str(log_path)}, now=104.9, max_age=5.0) is False
    assert combat_log_is_stale({"running": False, "log_path": str(log_path)}, now=106.1, max_age=5.0) is False


def test_state_has_metin_target_requires_metin_name_and_vid():
    assert state_has_metin_target({"target": {"name": "Metin da Batalha", "vid": 4821}}) is True
    assert state_has_metin_target({"target": {"name": "Urso Negro", "vid": 10}}) is False
    assert state_has_metin_target({"target": {"name": "Metin da Batalha", "vid": 0}}) is False


def test_format_nearby_metins_results_reads_latest_artifact(tmp_path):
    artifact = tmp_path / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "metins": [
                    {
                        "metin_name": "Metin da Batalha",
                        "location": {"x": 930, "y": 837},
                        "display_location": {"x": 930, "y": 837},
                        "distance": 7.1,
                        "evidence_label": "Live memory label (exact coordinate)",
                    },
                    {
                        "metin_name": "Metin stone (visual)",
                        "location": {"x": 929, "y": 844},
                        "distance": 0.0,
                        "evidence_label": "Visual detector (current-position estimate)",
                    },
                ],
                "diagnostics": {"memory_raw_hits": 1, "visual_raw_hits": 2},
            }
        ),
        encoding="utf-8",
    )

    text = format_nearby_metins_results(tmp_path, now=artifact.stat().st_mtime + 3)

    assert "Nearby Metins (last scan: 3.0s ago)" in text
    assert "#1  Metin da Batalha  (930, 837)  7.1u  [Live memory]" in text
    assert "#2  Metin stone (visual)  (929, 844)  0.0u  [Visual detector]" in text
    assert "diagnostics: memory_raw_hits=1 visual_raw_hits=2" in text


def test_format_nearby_metins_results_handles_no_live_evidence(tmp_path):
    artifact = tmp_path / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"source": "no_candidate_rows", "metins": [], "diagnostics": {"memory_raw_hits": 0, "visual_raw_hits": 0}}), encoding="utf-8")

    text = format_nearby_metins_results(tmp_path, now=artifact.stat().st_mtime)

    assert "No live Metin evidence in last scan" in text
    assert "memory_raw_hits=0 visual_raw_hits=0" in text


def test_format_nearby_metins_results_surfaces_selected_target_live_indicator(tmp_path):
    artifact = tmp_path / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "source": "mixed_live_evidence",
                "metins": [],
                "live_indicators": [
                    {
                        "metin_name": "Metin da Dor",
                        "display_location": {"x": 820, "y": 403},
                        "vid": 203982,
                        "evidence_label": "Selected target (live VID)",
                    }
                ],
                "diagnostics": {"target_rows": 1, "memory_raw_hits": 0},
            }
        ),
        encoding="utf-8",
    )

    text = format_nearby_metins_results(tmp_path, now=artifact.stat().st_mtime)

    assert "Live evidence near you (not exact move coords):" in text
    assert "* Metin da Dor VID:203982  near (820, 403)  [Selected target]" in text
    assert "target_rows=1" in text


def test_format_nearby_metins_results_explains_indicator_only_scan_and_memory_error(tmp_path):
    artifact = tmp_path / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "source": "named_metin_probe",
                "metins": [],
                "live_indicators": [
                    {
                        "metin_name": "Metin da Sombra",
                        "display_location": {"x": 720, "y": 668},
                        "vid": 12879,
                        "evidence_label": "Named Metin probe (live VID, current-position estimate)",
                    }
                ],
                "diagnostics": {
                    "named_probe_rows": 1,
                    "memory_raw_hits": 0,
                    "memory_scan_error": "OpenProcess failed for pid 40612",
                    "suppressed_position_estimate_rows": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    text = format_nearby_metins_results(tmp_path, now=artifact.stat().st_mtime)

    assert "No exact move-safe Metin coordinates found." in text
    assert "Live evidence near you (not exact move coords):" in text
    assert "* Metin da Sombra VID:12879  near (720, 668)  [Named VID]" in text
    assert "exact memory scan blocked: OpenProcess failed for pid 40612" in text


def test_load_nearby_metins_artifact_includes_indicator_rows_for_visibility(tmp_path):
    artifact = tmp_path / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "metins": [{"metin_name": "Exact", "source_type": "live_memory_visible_text"}],
                "live_indicators": [{"metin_name": "Indicator", "source_type": "named_metin_probe"}],
            }
        ),
        encoding="utf-8",
    )

    rows = load_nearby_metins_artifact(tmp_path)

    assert [row["metin_name"] for row in rows] == ["Exact", "Indicator"]
    assert rows[0]["_move_safe"] is True
    assert rows[1]["_move_safe"] is False


class FakeBoolVar:
    def __init__(self, value=False):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = bool(value)


class FakeStringVar:
    def __init__(self, value=""):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = str(value)


class FakeListbox:
    def __init__(self):
        self.items = []
        self.selected = []
    def delete(self, start, end):
        self.items = []
    def insert(self, index, label):
        self.items.append(label)
    def selection_set(self, index):
        if index == "end":
            self.selected = [len(self.items) - 1]
        else:
            self.selected = [index]
    def curselection(self):
        return tuple(self.selected)
    def get(self, index):
        return self.items[index]


class FakeText:
    def __init__(self, yview=(1.0, 1.0)):
        self._yview = yview
        self.calls = []
        self.content = ""

    def yview(self):
        return self._yview

    def delete(self, start, end):
        self.calls.append(("delete", start, end))
        self.content = ""

    def insert(self, index, text):
        self.calls.append(("insert", index, text))
        self.content += text

    def see(self, index):
        self.calls.append(("see", index))
        self._yview = (1.0, 1.0)

    def yview_moveto(self, fraction):
        self.calls.append(("yview_moveto", fraction))
        self._yview = (fraction, fraction + 0.1)


def test_show_run_log_preserves_scroll_position_when_operator_reading_log():
    app = object.__new__(ControlPanelApp)
    app.log_text = FakeText(yview=(0.25, 0.55))

    app.show_run_log({"log_tail": "new log text"})

    assert app.log_text.content == "new log text"
    assert ("yview_moveto", 0.25) in app.log_text.calls
    assert ("see", "end") not in app.log_text.calls


def test_show_run_log_autoscrolls_when_already_at_bottom():
    app = object.__new__(ControlPanelApp)
    app.log_text = FakeText(yview=(0.92, 1.0))

    app.show_run_log({"log_tail": "new log text"})

    assert ("see", "end") in app.log_text.calls


def test_render_runs_does_not_overwrite_log_tail_when_follow_disabled(monkeypatch):
    app = object.__new__(ControlPanelApp)
    app.selected_run = FakeStringVar("run-1")
    app.follow_log_tail = FakeBoolVar(False)
    app.log_tail_status = FakeStringVar()
    app.runs_list = FakeListbox()
    app.log_text = FakeText()
    app.log_text.content = "operator selected text should stay"
    monkeypatch.setattr(app, "render_nearby_metins", lambda: None)

    app.render_runs([
        {"run_id": "run-1", "script": "combat_metin_client_state", "mode": "live", "running": True, "exit_code": None, "log_tail": "new tail"}
    ])

    assert app.log_text.content == "operator selected text should stay"
    assert "paused for run-1" in app.log_tail_status.get()


def test_render_runs_updates_log_tail_when_follow_enabled(monkeypatch):
    app = object.__new__(ControlPanelApp)
    app.selected_run = FakeStringVar("run-1")
    app.follow_log_tail = FakeBoolVar(True)
    app.log_tail_status = FakeStringVar()
    app.runs_list = FakeListbox()
    app.log_text = FakeText()
    monkeypatch.setattr(app, "render_nearby_metins", lambda: None)

    app.render_runs([
        {"run_id": "run-1", "script": "combat_metin_client_state", "mode": "live", "running": True, "exit_code": None, "log_tail": "fresh follow tail"}
    ])

    assert app.log_text.content == "fresh follow tail"
    assert "showing run-1" in app.log_tail_status.get()


def test_refresh_selected_log_updates_log_tail_manually():
    app = object.__new__(ControlPanelApp)
    app.selected_run = FakeStringVar("run-1")
    app._runs_by_id = {"run-1": {"run_id": "run-1", "log_tail": "manual tail"}}
    app.log_tail_status = FakeStringVar()
    app.log_text = FakeText()

    app.refresh_selected_log()

    assert app.log_text.content == "manual tail"
    assert "showing run-1" in app.log_tail_status.get()


def test_render_runs_refreshes_nearby_panel_after_completed_find_run(monkeypatch):
    app = object.__new__(ControlPanelApp)
    app.selected_run = type("Selected", (), {"get": lambda _self: ""})()
    app.runs_list = type(
        "FakeListbox",
        (),
        {
            "delete": lambda _self, start, end: None,
            "insert": lambda _self, index, label: None,
            "selection_set": lambda _self, index: None,
        },
    )()
    calls = []
    monkeypatch.setattr(app, "render_nearby_metins", lambda: calls.append("nearby"))

    app.render_runs([{"run_id": "run-1", "script": "find_nearby_metins", "mode": "dry-run", "running": False, "exit_code": 0}])

    assert calls == ["nearby"]


def test_start_server_reuses_existing_api_instead_of_spawning_duplicate(monkeypatch):
    app = object.__new__(ControlPanelApp)
    app.server_proc = None
    app.project_root = "."
    app.client = type("WorkingClient", (), {"get": lambda _self, path: {"ok": True}})()
    app.root = type("FakeRoot", (), {"after": lambda _self, delay, func: None})()
    statuses = []
    monkeypatch.setattr(app, "set_status", statuses.append)
    spawned = []
    monkeypatch.setattr("metin2_dashboard.control_panel.subprocess.Popen", lambda *args, **kwargs: spawned.append((args, kwargs)))

    app.start_server()

    assert spawned == []
    assert statuses == ["local API already running"]


def test_archive_all_posts_archive_all_endpoint(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.calls = []
        def post(self, path, payload):
            self.calls.append((path, payload))
            return []
    class FakeRoot:
        def after(self, _delay, func):
            func()
    app = object.__new__(ControlPanelApp)
    app.client = FakeClient()
    app.root = FakeRoot()
    app._start_in_flight = False
    app.action_status = type("Status", (), {"set": lambda self, value: setattr(self, "value", value)})()
    monkeypatch.setattr("metin2_dashboard.control_panel.messagebox.askokcancel", lambda *args, **kwargs: True)
    monkeypatch.setattr("metin2_dashboard.control_panel.messagebox.showerror", lambda *args, **kwargs: None)

    app.archive_all()

    assert app.client.calls == [("/api/archive_all", {})]
