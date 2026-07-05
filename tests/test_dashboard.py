import json
import sys
import time
from pathlib import Path

import pytest

from metin2_dashboard.registry import ProcessRegistry
from metin2_dashboard.server import HTML, DashboardHandler, make_server
from metin2_dashboard.state import read_client_state


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


def test_dashboard_html_exposes_archive_run_button():
    assert "archiveRun" in HTML
    assert "/api/archive_run" in HTML
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
    assert scripts["find_nearby_metins"]["force_dry_run"] is True
    assert scripts["move_to_metin_client_state"]["exclusive_live_group"] == "combat"


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
    make_script(tmp_path, "scripts/login_mt2_local.py", "import argparse\nap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd', required=True); login=sub.add_parser('login'); login.add_argument('--username'); login.add_argument('--restart', action='store_true'); login.add_argument('--click-fields', action='store_true'); login.add_argument('--enter-game', action='store_true'); login.add_argument('--enter-game-count'); login.add_argument('--delay'); ns=ap.parse_args(); print(ns.cmd, ns.username, ns.restart, ns.click_fields, ns.enter_game, ns.enter_game_count, ns.delay, flush=True)\n")
    reg = ProcessRegistry(tmp_path)
    run = reg.start("login_mt2_local", live=True, confirm_live=True, options={"username": "yoshy", "enter_game_count": 3, "delay": 5})
    deadline = time.time() + 3
    while time.time() < deadline and reg.status(run["run_id"])["running"]:
        time.sleep(0.05)
    status = reg.status(run["run_id"])
    assert status["exit_code"] == 0
    assert "login yoshy True True True 3 5.0" in status["log_tail"]


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
