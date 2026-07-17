import json
import sys
import time
from pathlib import Path

import pytest

from metin2_dashboard.registry import ProcessRegistry
from metin2_dashboard.server import HTML, DashboardHandler, make_server
from metin2_dashboard.state import read_client_state
from metin2_dashboard.state_bridge import state_bridge_report


def make_script(root: Path, rel: str, body: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_tsv_header_latest_row(tmp_path):
    p = tmp_path / "state.tsv"
    p.write_text("map\tx\ty\thp\ttarget_name\nmap1\t10\t20\t99\tmetin\n")
    s = read_client_state(p)
    assert s["available"] is True
    assert s["map"] == "map1"
    assert s["hp"] == "99"
    assert s["target_name"] == "metin"
    assert s["_file_mtime"] == p.stat().st_mtime


def test_api_state_prefers_json_state_with_file_mtime(tmp_path):
    from metin2_dashboard.server import read_api_state

    json_path = tmp_path / "hermes_state.json"
    tsv_path = tmp_path / "hermes_state.tsv"
    json_path.write_text(json.dumps({"map": "metin2_map_a1", "player": {"name": "Yoshypt", "hp": 1864}}), encoding="utf-8")
    tsv_path.write_text("1787450544\told_map\t1\t2\t3\t4\t5\t6\t7\t0\tOldName\n", encoding="utf-8")

    state = read_api_state(tsv_path, json_path=json_path)

    assert state["available"] is True
    assert state["map"] == "metin2_map_a1"
    assert state["player"]["name"] == "Yoshypt"
    assert state["_file_mtime"] == json_path.stat().st_mtime


def test_equipment_from_state_extracts_weapon_attrs(monkeypatch):
    from metin2_dashboard.server import equipment_from_state

    monkeypatch.setattr("metin2_dashboard.server.time.time", lambda: 105.5)
    weapon = {
        "slot": 3,
        "vnum": 2849,
        "name": "Lança Fénix+9",
        "attrs": [
            {"index": 0, "type": 72, "value": 23},
            {"index": 1, "type": 71, "value": -5},
        ],
    }
    state = {"_file_mtime": 100.0, "inventory": [{"slot": 1, "vnum": 1, "name": "Potion", "attrs": []}, weapon], "equipped_weapon": weapon}

    out = equipment_from_state(state)

    assert out["available"] is True
    assert out["equipped_weapon"]["name"] == "Lança Fénix+9"
    assert out["equipped_weapon"]["attrs"][0]["value"] == 23
    assert out["inventory_with_attrs"] == [weapon]
    assert out["inventory_count"] == 2
    assert out["state_age_seconds"] == 5.5
    assert out["stale"] is True
    assert "stale" in out["warning"]



def test_make_server_uses_configured_json_state_and_exposes_client_endpoint(tmp_path):
    json_path = tmp_path / "buffer" / "hermes_state.json"
    tsv_path = tmp_path / "buffer" / "hermes_state.tsv"
    json_path.parent.mkdir()
    json_path.write_text(json.dumps({"map": "buffer_map", "player": {"name": "Alt"}}), encoding="utf-8")

    srv = make_server("127.0.0.1", 0, tmp_path, tsv_path, json_path=json_path)
    try:
        assert DashboardHandler.json_path == json_path
        assert DashboardHandler.tsv_path == tsv_path
        assert DashboardHandler.registry.default_state_json == str(json_path)
        read_api_state = __import__("metin2_dashboard.server", fromlist=["read_api_state"]).read_api_state
        assert read_api_state(tsv_path, json_path=json_path)["map"] == "buffer_map"
    finally:
        srv.server_close()


def test_registry_injects_configured_state_json_for_scripts(tmp_path):
    from metin2_dashboard.registry import ProcessRegistry

    state_json = tmp_path / "buffer_state.json"
    reg = ProcessRegistry(tmp_path, default_state_json=state_json)
    scripts = {row["name"]: row for row in reg.list_scripts()}

    assert next(opt for opt in scripts["combat_metin_client_state"]["options"] if opt["name"] == "state_json")["default"] == str(state_json)
    assert next(opt for opt in scripts["boss_farm_tracker"]["options"] if opt["name"] == "state_json")["default"] == str(state_json)
    assert scripts["boss_farm_tracker"]["force_dry_run"] is True
    args = reg.build_option_args(__import__("metin2_dashboard.registry", fromlist=["DEFAULT_SCRIPTS"]).DEFAULT_SCRIPTS["combat_metin_client_state"], {})
    assert args[args.index("--json-state") + 1] == str(state_json)


def test_login_config_roundtrip_is_redacted_and_can_update_usernames(tmp_path, monkeypatch):
    from metin2_dashboard.config import read_login_config, write_login_config
    from scripts import login_mt2_local

    stored = []
    monkeypatch.setattr(login_mt2_local, "credential_exists", lambda username: username in {"buf"})
    monkeypatch.setattr(login_mt2_local, "write_credential", lambda username, password: stored.append((username, password)))

    out = write_login_config(tmp_path, {
        "active_profile": "buffer",
        "profiles": {
            "main": {"username": "mainuser", "password": ""},
            "buffer": {"username": "buf", "password": "secret"},
        },
    })

    assert out["active_profile"] == "buffer"
    assert out["profiles"]["buffer"]["username"] == "buf"
    assert out["profiles"]["buffer"]["has_password"] is True
    assert out["profiles"]["buffer"]["password_updated"] is True
    assert stored == [("buf", "secret")]
    raw = (tmp_path / "config" / "login.json").read_text(encoding="utf-8")
    assert "secret" not in raw
    assert read_login_config(tmp_path)["profiles"]["main"]["username"] == "mainuser"



def test_reroll_config_defaults_include_equip_slots_and_desired_stats(tmp_path):
    from metin2_dashboard.config import read_reroll_config, write_reroll_config

    defaults = read_reroll_config(tmp_path)
    slots = {row["slot"]: row for row in defaults["equip_slots"]}

    assert "weapon" in slots
    assert "armor" in slots
    assert any(stat["attr_type"] == 71 for stat in slots["weapon"]["possible_rolls"])
    assert any(stat["attr_type"] == 1 for stat in slots["armor"]["possible_rolls"])
    assert defaults["desired_stats"]["weapon"]

    updated = write_reroll_config(
        tmp_path,
        {"desired_stats": {"weapon": [{"attr_type": 71, "target_value": 50, "priority": 1}, {"attr_type": 72, "target_value": 20, "priority": 2}]}},
    )
    assert updated["desired_stats"]["weapon"][0] == {"attr_type": 71, "target_value": 50, "priority": 1}
    raw = json.loads((tmp_path / "config" / "reroll.json").read_text(encoding="utf-8"))
    assert raw["desired_stats"]["weapon"][1]["attr_type"] == 72



def test_login_config_raises_if_credential_write_does_not_persist(tmp_path, monkeypatch):
    from metin2_dashboard.config import write_login_config
    from scripts import login_mt2_local

    monkeypatch.setattr(login_mt2_local, "credential_exists", lambda username: False)
    monkeypatch.setattr(login_mt2_local, "write_credential", lambda username, password: None)

    with pytest.raises(RuntimeError, match="Credential Manager write did not persist"):
        write_login_config(tmp_path, {"profiles": {"buffer": {"username": "buf", "password": "secret"}}})


def test_dashboard_exposes_login_config_endpoints_and_registry_defaults(tmp_path, monkeypatch):
    from metin2_dashboard.config import write_login_config
    from scripts import login_mt2_local

    monkeypatch.setattr(login_mt2_local, "credential_exists", lambda username: False)
    write_login_config(tmp_path, {"profiles": {"buffer": {"username": "buf", "app_dir": "D:/Games/MT2PortugaliaBuffer/app"}}})
    srv = make_server("127.0.0.1", 0, tmp_path, tmp_path / "state.tsv", login_profile="buffer")
    try:
        scripts = {row["name"]: row for row in DashboardHandler.registry.list_scripts()}
        opts = {opt["name"]: opt for opt in scripts["login_mt2_local"]["options"]}
        assert opts["username"]["default"] == "buf"
        assert opts["app_dir"]["default"] == "D:/Games/MT2PortugaliaBuffer/app"
    finally:
        srv.server_close()


def test_login_script_defaults_launch_without_restart_to_keep_both_clients_open(tmp_path):
    reg = ProcessRegistry(tmp_path)
    scripts = {row["name"]: row for row in reg.list_scripts()}
    login = scripts["login_mt2_local"]
    opts = {opt["name"]: opt for opt in login["options"]}

    assert "--launch" in login["live_args"]
    assert "--restart" not in login["live_args"]
    assert opts["restart"]["default"] is False


def test_api_state_falls_back_to_tsv_when_json_is_invalid(tmp_path):
    from metin2_dashboard.server import read_api_state

    json_path = tmp_path / "hermes_state.json"
    tsv_path = tmp_path / "hermes_state.tsv"
    json_path.write_text("{not-json", encoding="utf-8")
    tsv_path.write_text("1787450544\tmetin2_map_a1\t62784\t61534\t3\t1500\t1864\t1380\t1422\t0\tYoshypt\n", encoding="utf-8")

    state = read_api_state(tsv_path, json_path=json_path)

    assert state["available"] is True
    assert state["map"] == "metin2_map_a1"
    assert state["player_name"] == "Yoshypt"
    assert state["_file_mtime"] == tsv_path.stat().st_mtime


def test_real_client_tsv_without_header_uses_latest_row(tmp_path):
    p = tmp_path / "state.tsv"
    p.write_text(
        "1787450000\tmetin2_map_a1\t1\t2\t3\t4\t5\t6\t7\t0\tYoshypt\n"
        "1787450544\tmetin2_map_a1\t83888\t71063\t20358\t1776\t1776\t1024\t1278\t2752330\tYoshypt\tMetin da Batalha\n"
    )
    s = read_client_state(p)
    assert s["available"] is True
    assert s["timestamp_ms"] == "1787450544"
    assert s["map"] == "metin2_map_a1"
    assert s["x"] == "83888"
    assert s["y"] == "71063"
    assert s["hp"] == "1776"
    assert s["target_vid"] == "2752330"
    assert s["target_name"] == "Metin da Batalha"


def test_dashboard_html_does_not_refresh_script_form_while_editing():
    assert "autoRefresh" in HTML
    assert "scriptsDirty" in HTML
    assert "renderScripts" in HTML
    assert "oninput=\"scriptsDirty=true\"" in HTML
    assert "if(!scriptsDirty && document.activeElement" in HTML


def test_dashboard_html_exposes_direct_f1_f2_key_macro_controls():
    assert "direct F1/F2 key test + timed macro" in HTML
    assert "Press F1 once LIVE" in HTML
    assert "Press F2 once LIVE" in HTML
    assert "Start F1 timed macro LIVE" in HTML
    assert "Start F2 timed macro LIVE" in HTML
    assert "startKeyMacro" in HTML
    assert "key_macro_control" in HTML




def test_dashboard_html_exposes_truth_cards_and_honest_locked_statuses():
    assert "automation truth dashboard" in HTML
    assert "statusRibbon" in HTML
    assert "renderTruthCards" in HTML
    assert "renderRibbon" in HTML
    assert "project_position/screen/z" in HTML
    assert "Buff Automation" in HTML
    assert "Buff automation not proven" in HTML
    assert "Attack Mobs" in HTML
    assert "LIVE ENABLED WITH EXPLICIT CONFIRMATION" in HTML
    assert "start live attack" in HTML
    assert "MOVEMENT_LIVE_LOCKED" in HTML
    assert "copy /api/state" in HTML
    assert "copy /api/state_bridge" in HTML
    assert "copy /api/runs summary" in HTML
    assert "copy latest reason" in HTML
    assert "VERIFIED" in HTML
    assert "UNVERIFIED" in HTML
    assert "BLOCKED" in HTML
    assert "last 20 failure/block reasons" in HTML
    assert "runs newest-first compact table" in HTML
    assert "Live engage enabled: confirm LIVE in controls" in HTML
    assert "NO_BUFFS_PROVEN" in HTML

def test_dashboard_html_exposes_state_bridge_trust_panel():
    assert "state bridge trust" in HTML
    assert "bridgeTrust" in HTML
    assert "/api/state_bridge" in HTML


def test_dashboard_state_bridge_report_uses_json_file_mtime_freshness(tmp_path):
    json_path = tmp_path / "hermes_state.json"
    tsv_path = tmp_path / "hermes_state.tsv"
    json_path.write_text(json.dumps({"target": {"vid": 42, "name": "Metin da Batalha", "alive": True}}), encoding="utf-8")

    from metin2_dashboard.server import read_api_state

    api_state = read_api_state(tsv_path, json_path=json_path)
    report = state_bridge_report(api_state, now=json_path.stat().st_mtime + 0.5)

    assert report["has_trusted_target"] is True
    assert report["dry_run_action"] == "DRY_RUN_IDLE"
    assert report["live_action"] == "ENGAGE_TARGET"


def test_dashboard_html_exposes_archive_run_button():
    assert "archiveRun" in HTML
    assert "/api/archive_run" in HTML
    assert "/api/archive_all" in HTML
    assert "archiveAll" in HTML
    assert "archived=1" in HTML


def test_dashboard_html_collect_options_skips_unchanged_defaults():
    assert "data-default" in HTML
    assert "el.value !== el.dataset.default" in HTML
    assert "String(val) !== el.dataset.default" in HTML


def test_dashboard_log_message_ignores_broken_parent_stream(monkeypatch):
    class BrokenStream:
        def write(self, _text):
            raise OSError("parent console is gone")

        def flush(self):
            raise OSError("parent console is gone")

    handler = object.__new__(DashboardHandler)
    monkeypatch.setattr(handler, "address_string", lambda: "127.0.0.1")
    monkeypatch.setattr(sys, "stderr", BrokenStream())

    handler.log_message('\"GET /api/state HTTP/1.1\" 200 -')


def test_registry_default_args_match_real_scripts(tmp_path):
    make_script(tmp_path, "scripts/learn_runaround_client_tsv.py", "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--cycles'); ap.add_argument('--no-focus', action='store_true'); ap.add_argument('--seconds'); ap.add_argument('--pause'); ap.add_argument('--out-jsonl'); ap.add_argument('--model-out'); ap.parse_args(); print('learn ok', flush=True)\n")
    make_script(tmp_path, "scripts/combat_metin_client_state.py", "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--max-cycles'); ap.add_argument('--out'); ap.add_argument('--live', action='store_true'); ap.parse_args(); print('combat ok', flush=True)\n")
    make_script(tmp_path, "scripts/probe_client_state.py", "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--no-process', action='store_true'); ap.add_argument('--out'); ap.parse_args(); print('probe ok', flush=True)\n")
    reg = ProcessRegistry(tmp_path)
    runs = [reg.start(name) for name in ["learn_runaround_client_tsv", "combat_metin_client_state", "probe_client_state"]]
    deadline = time.time() + 3
    while time.time() < deadline and any(reg.status(r["run_id"])["running"] for r in runs):
        time.sleep(0.05)
    statuses = [reg.status(r["run_id"]) for r in runs]
    assert [s["exit_code"] for s in statuses] == [0, 0, 0]
    assert all("--dry-run" not in s["command"] for s in statuses)


def test_learn_runaround_script_runs_directly_without_pythonpath():
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "scripts/learn_runaround_client_tsv.py",
            "--cycles",
            "0",
            "--no-focus",
            "--seconds",
            "0.05",
            "--pause",
            "0.05",
            "--out-jsonl",
            "reports/dashboard_runs/test_direct_learn.jsonl",
            "--model-out",
            "reports/dashboard_runs/test_direct_learn_model.json",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_registry_learning_dry_run_ignores_movement_options(tmp_path):
    make_script(
        tmp_path,
        "scripts/learn_runaround_client_tsv.py",
        "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--cycles'); ap.add_argument('--no-focus', action='store_true'); ap.add_argument('--seconds'); ap.add_argument('--pause'); ap.add_argument('--out-jsonl'); ap.add_argument('--model-out'); ns=ap.parse_args(); print(ns.cycles, ns.no_focus, ns.seconds, ns.pause, flush=True)\n",
    )
    reg = ProcessRegistry(tmp_path)

    run = reg.start("learn_runaround_client_tsv", options={"cycles": 9, "seconds": 9, "pause": 9})
    deadline = time.time() + 3
    while time.time() < deadline and reg.status(run["run_id"])["running"]:
        time.sleep(0.05)
    status = reg.status(run["run_id"])

    assert status["exit_code"] == 0
    assert status["command"].count("--cycles") == 1
    assert "9" not in status["command"][2:]
    assert "0 True 0.05 0.05" in status["log_tail"]


def test_registry_child_scripts_can_import_src_package(tmp_path):
    package = tmp_path / "src" / "metin2_research" / "client_state"
    package.mkdir(parents=True)
    (tmp_path / "src" / "metin2_research" / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "process_probe.py").write_text("VALUE = 'import ok'\n")
    make_script(tmp_path, "scripts/probe_client_state.py", "from metin2_research.client_state.process_probe import VALUE\nprint(VALUE, flush=True)\n")
    reg = ProcessRegistry(tmp_path)

    run = reg.start("probe_client_state")
    deadline = time.time() + 3
    while time.time() < deadline and reg.status(run["run_id"])["running"]:
        time.sleep(0.05)
    status = reg.status(run["run_id"])

    assert status["exit_code"] == 0
    assert "import ok" in status["log_tail"]


def test_registry_exposes_safe_tunable_options():
    reg = ProcessRegistry(Path.cwd())
    scripts = {s["name"]: s for s in reg.list_scripts()}
    combat_options = {o["name"]: o for o in scripts["combat_metin_client_state"]["options"]}
    learn_options = {o["name"]: o for o in scripts["learn_runaround_client_tsv"]["options"]}
    login_options = {o["name"]: o for o in scripts["login_mt2_local"]["options"]}
    nearby_options = {o["name"]: o for o in scripts["find_nearby_metins"]["options"]}
    move_options = {o["name"]: o for o in scripts["move_to_metin_client_state"]["options"]}
    assert combat_options["max_cycles"]["flag"] == "--max-cycles"
    assert combat_options["burst_seconds"]["type"] == "float"
    assert combat_options["allow_selected_vid_without_exact_coords"]["flag"] == "--allow-selected-vid-without-exact-coords"
    assert combat_options["allow_selected_vid_without_exact_coords"]["type"] == "bool"
    assert learn_options["cycles"]["flag"] == "--cycles"
    assert login_options["username"]["flag"] == "--username"
    assert login_options["enter_game_count"]["type"] == "int"
    assert nearby_options["radius"]["flag"] == "--radius"
    assert nearby_options["limit"]["type"] == "int"
    assert nearby_options["display_offset_x"]["default"] == 0
    assert nearby_options["display_offset_y"]["default"] == 0
    assert move_options["metin_x"]["flag"] == "--metin-x"
    assert move_options["metin_coord_source"]["default"] is None
    assert "named_metin_probe" in move_options["metin_coord_source"]["description"]
    assert "no travel movement" in scripts["fixed_sapo_channel_sweep"]["description"]
    assert scripts["find_nearby_metins"]["force_dry_run"] is True
    assert scripts["move_to_metin_client_state"]["exclusive_live_group"] == "combat"



def test_move_to_metin_allows_named_probe_coordinates_for_live_private_sandbox():
    from scripts.move_to_metin_client_state import TRUSTED_MOVE_COORD_SOURCES

    assert "named_metin_probe" in TRUSTED_MOVE_COORD_SOURCES



def test_registry_exposes_key_macro_control_options():
    reg = ProcessRegistry(Path.cwd())
    scripts = {s["name"]: s for s in reg.list_scripts()}
    assert scripts["key_macro_control"]["exclusive_live_group"] == "combat"
    options = {o["name"]: o for o in scripts["key_macro_control"]["options"]}
    assert options["key"]["flag"] == "--key"
    assert options["key"]["default"] == "f1"
    assert options["interval_seconds"]["flag"] == "--interval-seconds"
    assert options["interval_seconds"]["type"] == "float"
    assert options["presses"]["flag"] == "--presses"
    assert options["presses"]["type"] == "int"
    assert options["elevate"]["flag"] == "--elevate"
    assert options["elevate"]["type"] == "bool"
    assert options["elevate"]["default"] is True


def test_registry_key_macro_dry_run_defaults_match_script(tmp_path):
    make_script(
        tmp_path,
        "scripts/key_macro_control.py",
        "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--live', action='store_true'); ap.add_argument('--key', default='f1'); ap.add_argument('--interval-seconds'); ap.add_argument('--presses'); ap.add_argument('--hold-seconds'); ap.add_argument('--window-query'); ap.add_argument('--out'); ap.add_argument('--elevate', action='store_true'); ns=ap.parse_args(); print(ns.live, ns.key, ns.out, ns.elevate, flush=True)\n",
    )
    reg = ProcessRegistry(tmp_path)
    run = reg.start("key_macro_control", options={"key": "f2", "presses": 1, "interval_seconds": 0.5})
    deadline = time.time() + 3
    while time.time() < deadline and reg.status(run["run_id"])["running"]:
        time.sleep(0.05)
    status = reg.status(run["run_id"])
    assert status["exit_code"] == 0
    assert status["mode"] == "dry-run"
    assert "False f2 reports/dashboard_runs/key_macro_dryrun.json False" in status["log_tail"]


def test_registry_exposes_attack_nearby_mobs_option_default_off():
    reg = ProcessRegistry(Path.cwd())
    scripts = {s["name"]: s for s in reg.list_scripts()}
    combat_options = {o["name"]: o for o in scripts["combat_metin_client_state"]["options"]}

    assert combat_options["attack_nearby_mobs"]["flag"] == "--attack-nearby-mobs"
    assert combat_options["attack_nearby_mobs"]["type"] == "bool"
    assert combat_options["attack_nearby_mobs"]["default"] is False
    assert combat_options["buff_only"]["flag"] == "--buff-only"
    assert combat_options["buff_only"]["type"] == "bool"
    assert combat_options["buff_only"]["default"] is False
    assert combat_options["buff_keys"]["flag"] == "--buff-keys"
    assert combat_options["buff_keys"]["default"] == "f1,f2"
    assert combat_options["buff_durations"]["flag"] == "--buff-durations"
    assert combat_options["buff_durations"]["default"] == "156,302"
    assert combat_options["assume_mounted"]["flag"] == "--assume-mounted"
    assert combat_options["assume_mounted"]["type"] == "bool"
    assert combat_options["assume_mounted"]["default"] is False
    assert combat_options["enable_combat_buffs"]["flag"] == "--enable-combat-buffs"
    assert combat_options["enable_combat_buffs"]["default"] is False
    assert combat_options["visual_target_clicks"]["flag"] == "--visual-target-clicks"
    assert combat_options["visual_target_clicks"]["default"] is False
    assert combat_options["visual_detector_model"]["flag"] == "--visual-detector-model"
    assert combat_options["visual_target_min_confidence"]["flag"] == "--visual-target-min-confidence"
    assert combat_options["allow_blind_target_clicks"]["flag"] == "--allow-blind-target-clicks"
    assert combat_options["allow_blind_target_clicks"]["default"] is False
    assert combat_options["target_search_move_seconds"]["flag"] == "--target-search-move-seconds"
    assert combat_options["target_search_move_seconds"]["default"] == 0.25
    assert combat_options["target_click_cooldown_seconds"]["flag"] == "--target-click-cooldown-seconds"
    assert combat_options["target_click_cooldown_seconds"]["default"] == 4.0
    assert combat_options["target_camera_sweep_seconds"]["flag"] == "--target-camera-sweep-seconds"
    assert combat_options["target_camera_sweep_seconds"]["default"] == 0.16
    assert combat_options["minimap_camera_hint"]["flag"] == "--minimap-camera-hint"
    assert combat_options["minimap_camera_hint"]["default"] is False
    assert combat_options["channel_rotate_after_destroy"]["flag"] == "--channel-rotate-after-destroy"
    assert combat_options["channel_rotate_after_destroy"]["default"] is False
    assert combat_options["channel_click_points"]["flag"] == "--channel-click-points"
    assert combat_options["pickup_spam_count"]["flag"] == "--pickup-spam-count"
    assert "attack nearby mobs" in HTML


def test_registry_exposes_fixed_sapo_live_scripts_with_elevated_input_and_new_channel_points():
    reg = ProcessRegistry(Path.cwd())
    scripts = {s["name"]: s for s in reg.list_scripts()}

    space = scripts["fixed_sapo_space_control"]
    assert space["exclusive_live_group"] == "combat"
    assert space["live_args"] == ["--live", "--elevate"]

    sweep = scripts["fixed_sapo_channel_sweep"]
    options = {o["name"]: o for o in sweep["options"]}
    assert sweep["exclusive_live_group"] == "combat"
    assert sweep["live_args"] == ["--live", "--elevate"]
    assert options["channel_click_points"]["default"].split(";")[:4] == [
        "0.4990,0.3986",
        "0.4990,0.4326",
        "0.4990,0.4665",
        "0.4990,0.5005",
    ]
    assert options["repeat_while_running"]["flag"] == "--repeat-while-running"
    assert options["low_dps_adjust"]["flag"] == "--low-dps-adjust"
    assert options["low_dps_max_cumulative_steps"]["flag"] == "--low-dps-max-cumulative-steps"
    assert options["low_dps_max_cumulative_steps"]["default"] == 3
    assert "potion" not in {name for name in options}


def test_registry_exposes_player_training_recorder_observation_only():
    reg = ProcessRegistry(Path.cwd())
    scripts = {s["name"]: s for s in reg.list_scripts()}
    spec = scripts["player_training_recorder"]
    options = {o["name"]: o for o in spec["options"]}

    assert spec["force_dry_run"] is True
    assert spec["live_args"] == []
    assert options["duration"]["flag"] == "--duration"
    assert options["interval"]["flag"] == "--interval"
    assert options["capture_screenshots"]["flag"] == "--capture-screenshots"
    assert options["window_query"]["default"] == "MT2Portugalia"


def test_registry_exposes_reroll_recorder_observation_only():
    reg = ProcessRegistry(Path.cwd())
    scripts = {s["name"]: s for s in reg.list_scripts()}
    spec = scripts["reroll_recorder"]
    options = {o["name"]: o for o in spec["options"]}

    assert spec["force_dry_run"] is True
    assert spec["live_args"] == []
    assert "observation-only" in spec["description"]
    assert options["duration"]["flag"] == "--duration"
    assert options["interval"]["flag"] == "--interval"
    assert options["state_json"]["flag"] == "--state-json"
    assert options["target_slot"]["flag"] == "--target-slot"
    assert options["target_vnum"]["flag"] == "--target-vnum"


def test_registry_passes_buff_and_combat_config_env_to_managed_scripts(tmp_path):
    make_script(
        tmp_path,
        "scripts/combat_metin_client_state.py",
        "import os\nprint('buff=' + os.environ.get('METIN2_BUFF_CONFIG', ''), flush=True)\nprint('combat=' + os.environ.get('METIN2_COMBAT_CONFIG', ''), flush=True)\n",
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "buffs.json").write_text('{"buffs": []}', encoding="utf-8")
    (tmp_path / "config" / "combat.json").write_text('{"attack_nearby_mobs": false}', encoding="utf-8")
    reg = ProcessRegistry(tmp_path)

    run = reg.start("combat_metin_client_state")
    deadline = time.time() + 3
    while time.time() < deadline and reg.status(run["run_id"])["running"]:
        time.sleep(0.05)
    status = reg.status(run["run_id"])

    assert status["exit_code"] == 0
    assert "METIN2_BUFF_CONFIG" not in status["command"]
    assert "config" in status["log_tail"] and "buffs.json" in status["log_tail"]
    assert "combat.json" in status["log_tail"]


def test_dashboard_config_apis_persist_buffs_and_combat_defaults(tmp_path):
    from metin2_dashboard.config import read_buff_config, read_combat_config, write_buff_config, write_combat_config

    assert read_combat_config(tmp_path)["attack_nearby_mobs"] is False
    combat = write_combat_config(tmp_path, {"attack_nearby_mobs": True})
    buffs = write_buff_config(tmp_path, {"use_buff_config": True, "buffs": [{"key": "f1", "enabled": True, "interval_seconds": 35, "pre_cast_seconds": 3}]})

    assert combat["attack_nearby_mobs"] is True
    assert read_combat_config(tmp_path)["attack_nearby_mobs"] is True
    assert buffs["use_buff_config"] is True
    assert read_buff_config(tmp_path)["buffs"][0]["key"] == "f1"

def test_registry_forces_find_nearby_metins_to_dry_run_even_when_live_requested(tmp_path):
    make_script(
        tmp_path,
        "scripts/find_nearby_metins.py",
        "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--radius'); ap.add_argument('--source'); ns=ap.parse_args(); print(ns.radius, ns.source, flush=True)\n",
    )
    reg = ProcessRegistry(tmp_path)

    run = reg.start("find_nearby_metins", live=True, confirm_live=True, options={"radius": 300, "source": "hybrid"})
    deadline = time.time() + 3
    while time.time() < deadline and reg.status(run["run_id"])["running"]:
        time.sleep(0.05)
    status = reg.status(run["run_id"])

    assert status["mode"] == "dry-run"
    assert status["exit_code"] == 0
    assert "300.0 hybrid" in status["log_tail"]


def test_registry_dry_run_commands_are_noop_for_side_effect_scripts(tmp_path):
    make_script(
        tmp_path,
        "scripts/patch_mt2_root_state_logger.py",
        "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--dry-run', action='store_true'); ns=ap.parse_args(); print('patch dry', ns.dry_run, flush=True)\n",
    )
    make_script(
        tmp_path,
        "scripts/login_mt2_local.py",
        "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--help-only', action='store_true'); ap.add_argument('--version', action='store_true'); ap.add_argument('args', nargs='*'); ns=ap.parse_args(); print('login noop', ns.args, flush=True)\n",
    )
    reg = ProcessRegistry(tmp_path)

    patch_run = reg.start("patch_mt2_root_state_logger")
    login_run = reg.start("login_mt2_local")
    deadline = time.time() + 3
    while time.time() < deadline and any(reg.status(r["run_id"])["running"] for r in [patch_run, login_run]):
        time.sleep(0.05)
    patch_status = reg.status(patch_run["run_id"])
    login_status = reg.status(login_run["run_id"])

    assert patch_status["exit_code"] == 0
    assert "--dry-run" in patch_status["command"]
    assert "patch dry True" in patch_status["log_tail"]
    assert login_status["exit_code"] == 0
    login_args = login_status["command"][2:]
    assert "login" not in login_args
    assert "--launch" not in login_args
    assert "--restart" not in login_args


def test_registry_login_script_live_args_match_helper(tmp_path):
    make_script(tmp_path, "scripts/login_mt2_local.py", "import argparse\nap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd', required=True); login=sub.add_parser('login'); login.add_argument('--username'); login.add_argument('--launch', action='store_true'); login.add_argument('--restart', action='store_true'); login.add_argument('--click-fields', action='store_true'); login.add_argument('--enter-game', action='store_true'); login.add_argument('--enter-game-count'); login.add_argument('--delay'); login.add_argument('--window-timeout'); ns=ap.parse_args(); print(ns.cmd, ns.username, ns.launch, ns.restart, ns.click_fields, ns.enter_game, ns.enter_game_count, ns.delay, ns.window_timeout, flush=True)\n")
    reg = ProcessRegistry(tmp_path)
    run = reg.start("login_mt2_local", live=True, confirm_live=True, options={"username": "yoshy", "enter_game_count": 3, "delay": 5})
    deadline = time.time() + 3
    while time.time() < deadline and reg.status(run["run_id"])["running"]:
        time.sleep(0.05)
    status = reg.status(run["run_id"])
    assert status["exit_code"] == 0
    assert "login yoshy True False True True 3 5.0 90" in status["log_tail"]


def test_registry_converts_safe_options_to_script_args(tmp_path):
    make_script(tmp_path, "scripts/combat_metin_client_state.py", "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--max-cycles'); ap.add_argument('--burst-seconds'); ap.add_argument('--out'); ap.add_argument('--live', action='store_true'); ns=ap.parse_args(); print(ns.max_cycles, ns.burst_seconds, flush=True)\n")
    reg = ProcessRegistry(tmp_path)
    run = reg.start("combat_metin_client_state", options={"max_cycles": 7, "burst_seconds": 0.4})
    deadline = time.time() + 3
    while time.time() < deadline and reg.status(run["run_id"])["running"]:
        time.sleep(0.05)
    status = reg.status(run["run_id"])
    assert status["exit_code"] == 0
    assert "--max-cycles" in status["command"]
    assert "7" in status["command"]
    assert "--burst-seconds" in status["command"]
    assert "0.4" in status["command"]
    assert "7 0.4" in status["log_tail"]


def test_registry_rejects_unknown_options(tmp_path):
    make_script(tmp_path, "scripts/probe_client_state.py", "print('ok')\n")
    reg = ProcessRegistry(tmp_path)
    with pytest.raises(ValueError, match="unknown option"):
        reg.start("probe_client_state", options={"shell": "rm -rf"})


def test_registry_live_args_match_real_scripts(tmp_path):
    make_script(tmp_path, "scripts/learn_runaround_client_tsv.py", "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--cycles'); ap.add_argument('--seconds'); ap.add_argument('--pause'); ap.add_argument('--out-jsonl'); ap.add_argument('--model-out'); ap.parse_args(); print('learn live ok', flush=True)\n")
    make_script(tmp_path, "scripts/probe_client_state.py", "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--out'); ap.parse_args(); print('probe live ok', flush=True)\n")
    reg = ProcessRegistry(tmp_path)
    runs = [reg.start(name, live=True, confirm_live=True) for name in ["learn_runaround_client_tsv", "probe_client_state"]]
    deadline = time.time() + 3
    while time.time() < deadline and any(reg.status(r["run_id"])["running"] for r in runs):
        time.sleep(0.05)
    statuses = [reg.status(r["run_id"]) for r in runs]
    assert [s["exit_code"] for s in statuses] == [0, 0]
    assert all("--live" not in s["command"] for s in statuses)


def test_lan_bind_requires_explicit_allow_flag(tmp_path):
    with pytest.raises(SystemExit, match="refusing non-local bind"):
        make_server("0.0.0.0", 0, tmp_path, tmp_path / "missing.tsv")
    srv = make_server("0.0.0.0", 0, tmp_path, tmp_path / "missing.tsv", allow_lan=True)
    try:
        assert srv.server_address[0] == "0.0.0.0"
    finally:
        srv.server_close()



def test_registry_archive_all_archives_completed_but_not_running_runs(tmp_path):
    make_script(tmp_path, "scripts/probe_client_state.py", "print('done', flush=True)\n")
    make_script(tmp_path, "scripts/combat_metin_client_state.py", "import time, argparse\nap=argparse.ArgumentParser(); ap.add_argument('--max-cycles'); ap.add_argument('--out'); ap.parse_args(); print('running', flush=True); time.sleep(20)\n")
    reg = ProcessRegistry(tmp_path)
    completed = reg.start("probe_client_state")
    running = reg.start("combat_metin_client_state")
    deadline = time.time() + 3
    while time.time() < deadline and reg.status(completed["run_id"])["running"]:
        time.sleep(0.05)

    result = {run["run_id"]: run for run in reg.archive_all()}

    assert result[completed["run_id"]]["archived"] is True
    assert result[running["run_id"]]["archived"] is False
    visible = {run["run_id"] for run in reg.status()}
    assert completed["run_id"] not in visible
    assert running["run_id"] in visible
    reg.stop(running["run_id"])

def test_registry_start_stop_managed_process_only(tmp_path):
    make_script(tmp_path, "scripts/probe_client_state.py", "import time, argparse\nap=argparse.ArgumentParser(); ap.add_argument('--no-process', action='store_true'); ap.add_argument('--out'); ap.parse_args(); print('hello', flush=True); time.sleep(20)\n")
    reg = ProcessRegistry(tmp_path)
    run = reg.start("probe_client_state")
    assert run["running"] is True
    assert run["pid"] > 0
    deadline = time.time() + 3
    while time.time() < deadline:
        if "hello" in reg.status(run["run_id"])["log_tail"]:
            break
        time.sleep(0.05)
    stopped = reg.stop(run["run_id"], timeout=1)
    assert stopped["running"] is False
    assert "hello" in stopped["log_tail"]


def test_registry_stop_writes_stop_file_before_terminating(tmp_path):
    make_script(
        tmp_path,
        "scripts/combat_metin_client_state.py",
        "import os, time\nfrom pathlib import Path\np=Path(os.environ['HERMES_STOP_FILE'])\nfor _ in range(50):\n    if p.exists(): print('saw stop file', flush=True); raise SystemExit(0)\n    time.sleep(0.05)\nraise SystemExit(3)\n",
    )
    reg = ProcessRegistry(tmp_path)
    run = reg.start("combat_metin_client_state", live=True, confirm_live=True)
    status = reg.stop(run["run_id"], timeout=3)

    stop_path = tmp_path / "reports" / "dashboard_runs" / f"{run['run_id']}.stop"
    assert stop_path.exists()
    assert status["exit_code"] == 0
    assert "saw stop file" in status["log_tail"]


def test_registry_archives_completed_runs_and_hides_them_by_default(tmp_path):
    make_script(tmp_path, "scripts/probe_client_state.py", "print('archive me', flush=True)\n")
    reg = ProcessRegistry(tmp_path)
    run = reg.start("probe_client_state")
    deadline = time.time() + 3
    while time.time() < deadline and reg.status(run["run_id"])["running"]:
        time.sleep(0.05)

    archived = reg.archive(run["run_id"])

    assert archived["archived"] is True
    assert archived["archived_at"]
    assert reg.status() == []
    all_runs = reg.status(include_archived=True)
    assert [r["run_id"] for r in all_runs] == [run["run_id"]]
    assert all_runs[0]["archived"] is True


def test_registry_refuses_to_archive_running_runs(tmp_path):
    make_script(tmp_path, "scripts/probe_client_state.py", "import time\ntime.sleep(20)\n")
    reg = ProcessRegistry(tmp_path)
    run = reg.start("probe_client_state")
    try:
        with pytest.raises(RuntimeError, match="cannot archive running run"):
            reg.archive(run["run_id"])
    finally:
        reg.stop(run["run_id"], timeout=1)


def test_live_requires_confirmation(tmp_path):
    make_script(tmp_path, "scripts/combat_metin_client_state.py", "import time, argparse\nap=argparse.ArgumentParser(); ap.add_argument('--live', action='store_true'); ap.parse_args(); time.sleep(1)\n")
    reg = ProcessRegistry(tmp_path)
    with pytest.raises(PermissionError):
        reg.start("combat_metin_client_state", live=True, confirm_live=False)


def test_duplicate_live_combat_blocked(tmp_path):
    make_script(tmp_path, "scripts/combat_metin_client_state.py", "import time, argparse\nap=argparse.ArgumentParser(); ap.add_argument('--live', action='store_true'); ap.parse_args(); time.sleep(20)\n")
    reg = ProcessRegistry(tmp_path)
    first = reg.start("combat_metin_client_state", live=True, confirm_live=True)
    try:
        with pytest.raises(RuntimeError):
            reg.start("combat_metin_client_state", live=True, confirm_live=True)
    finally:
        reg.stop(first["run_id"], timeout=1)


def test_live_move_to_metin_shares_combat_exclusive_group(tmp_path):
    make_script(tmp_path, "scripts/combat_metin_client_state.py", "import time, argparse\nap=argparse.ArgumentParser(); ap.add_argument('--live', action='store_true'); ap.parse_args(); time.sleep(20)\n")
    make_script(
        tmp_path,
        "scripts/move_to_metin_client_state.py",
        "import argparse\nap=argparse.ArgumentParser(); ap.add_argument('--live', action='store_true'); ap.add_argument('--metin-x'); ap.add_argument('--metin-y'); ap.add_argument('--metin-coord-source'); ap.add_argument('--max-cycles'); ns=ap.parse_args(); print(ns.metin_x, ns.metin_y, ns.metin_coord_source, flush=True)\n",
    )
    reg = ProcessRegistry(tmp_path)
    first = reg.start("combat_metin_client_state", live=True, confirm_live=True)
    try:
        with pytest.raises(RuntimeError, match="duplicate live combat run blocked"):
            reg.start("move_to_metin_client_state", live=True, confirm_live=True, options={"metin_x": 54200, "metin_y": 21900, "metin_coord_source": "live_memory_visible_text"})
    finally:
        reg.stop(first["run_id"], timeout=1)


def test_live_buff_keeper_can_run_alongside_attack_nearby_but_duplicates_block(tmp_path):
    make_script(
        tmp_path,
        "scripts/combat_metin_client_state.py",
        "import argparse, time\n"
        "ap=argparse.ArgumentParser(); ap.add_argument('--live', action='store_true'); ap.add_argument('--max-cycles'); ap.add_argument('--buff-only', action='store_true'); ap.add_argument('--buff-keys'); ap.add_argument('--buff-durations'); ap.add_argument('--buff-refresh-margin-seconds'); ap.add_argument('--assume-mounted', action='store_true'); ap.add_argument('--attack-nearby-mobs', action='store_true'); ns=ap.parse_args(); print('buff', ns.buff_only, 'attack', ns.attack_nearby_mobs, flush=True); time.sleep(20)\n",
    )
    reg = ProcessRegistry(tmp_path)
    buff = reg.start(
        "combat_metin_client_state",
        live=True,
        confirm_live=True,
        options={"max_cycles": 0, "buff_only": True, "buff_keys": "f1,f2", "buff_durations": "109,301", "assume_mounted": True},
    )
    attack = None
    try:
        attack = reg.start(
            "combat_metin_client_state",
            live=True,
            confirm_live=True,
            options={"max_cycles": 0, "attack_nearby_mobs": True},
        )
        assert buff["run_id"] != attack["run_id"]
        with pytest.raises(RuntimeError, match="duplicate live buff run blocked"):
            reg.start("combat_metin_client_state", live=True, confirm_live=True, options={"max_cycles": 0, "buff_only": True})
        with pytest.raises(RuntimeError, match="duplicate live combat run blocked"):
            reg.start("combat_metin_client_state", live=True, confirm_live=True, options={"max_cycles": 0, "attack_nearby_mobs": True})
    finally:
        reg.stop(buff["run_id"], timeout=1)
        if attack:
            reg.stop(attack["run_id"], timeout=1)
