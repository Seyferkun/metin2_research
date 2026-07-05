import json
import subprocess
import sys
from urllib.error import HTTPError

import pytest

from metin2_dashboard.control_panel import (
    ControlPanelApp,
    DashboardApiClient,
    build_combat_payload,
    build_combat_confirmation_message,
    build_quick_start_payload,
    format_nearby_metins_results,
    format_state_card,
    format_combat_summary,
    combat_state_color,
    combat_log_is_stale,
    parse_combat_log_tail,
    state_has_metin_target,
    has_active_live_combat_run,
    has_active_live_control_run,
    load_nearby_metins_artifact,
    build_move_payload_from_metin,
    build_move_confirmation_message,
    option_default_values,
    option_payload_from_vars,
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
        "options": {"max_cycles": "60"},
    }
    assert build_quick_start_payload("find_nearby_metins") == {
        "script": "find_nearby_metins",
        "live": False,
        "confirm_live": False,
        "options": {"radius": "300", "limit": "8"},
    }


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
    assert payload["options"] == {"max_cycles": "60", "metin_vid": "3846"}


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
    assert payload["options"] == {"max_cycles": "60", "metin_vid": "3846"}


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


def test_has_active_live_control_run_includes_movement_runs():
    assert has_active_live_control_run([
        {"script": "move_to_metin_client_state", "mode": "live", "running": True, "run_id": "run-move"}
    ]) is True
    assert has_active_live_control_run([
        {"script": "move_to_metin_client_state", "mode": "dry-run", "running": True, "run_id": "run-move"}
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
