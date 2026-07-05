from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://127.0.0.1:8767"
DEFAULT_SERVER_CMD = [
    sys.executable,
    "-m",
    "metin2_dashboard.server",
    "--host",
    "127.0.0.1",
    "--port",
    "8767",
    "--project-root",
    ".",
    "--tsv",
    "D:/Games/MT2Portugalia/app/hermes_state.tsv",
]


class DashboardApiClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str) -> Any:
        req = Request(self.base_url + path, method="GET")
        return self._open_json(req)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        raw = json.dumps(payload or {}).encode("utf-8")
        req = Request(
            self.base_url + path,
            data=raw,
            headers={"content-type": "application/json"},
            method="POST",
        )
        return self._open_json(req)

    def _open_json(self, request: Request) -> Any:
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.reason
            try:
                body = json.loads(exc.fp.read().decode("utf-8"))
                detail = body.get("error") or body.get("detail") or detail
            except Exception:
                pass
            raise RuntimeError(str(detail)) from exc
        except URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc


def option_default_values(script: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for opt in script.get("options", []):
        default = opt.get("default")
        if opt.get("type") == "bool":
            values[opt["name"]] = bool(default)
        else:
            values[opt["name"]] = "" if default is None else str(default)
    return values


def option_payload_from_vars(script: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for opt in script.get("options", []):
        name = opt["name"]
        value = values.get(name)
        default = opt.get("default")
        if opt.get("type") == "bool":
            bool_value = bool(value)
            if bool_value != bool(default):
                payload[name] = bool_value
        elif value not in (None, ""):
            if default is not None and str(value) == str(default):
                continue
            payload[name] = str(value)
    return payload


def build_quick_start_payload(action: str) -> dict[str, Any]:
    """Return allowlisted payloads for native one-click operator buttons."""
    if action == "open_login_game":
        return {"script": "login_mt2_local", "live": True, "confirm_live": True, "options": {}}
    if action == "integrate_client":
        return {"script": "patch_mt2_root_state_logger", "live": False, "confirm_live": False, "options": {}}
    if action == "practice_dry_run":
        return {"script": "combat_metin_client_state", "live": False, "confirm_live": False, "options": {"max_cycles": "5"}}
    if action == "practice_live":
        return {"script": "combat_metin_client_state", "live": True, "confirm_live": True, "options": {"max_cycles": "60"}}
    if action == "find_nearby_metins":
        return {"script": "find_nearby_metins", "live": False, "confirm_live": False, "options": {"radius": "300", "limit": "8"}}
    raise ValueError(f"unknown quick action: {action}")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _short_evidence_label(label: str) -> str:
    if "name match" in label:
        return "Memory name"
    if "Live memory" in label:
        return "Live memory"
    if "Visual detector" in label:
        return "Visual detector"
    if "Named Metin" in label:
        return "Named VID"
    if "Selected target" in label:
        return "Selected target"
    if "Known coordinate" in label:
        return "Table"
    return label or "unknown"


def format_state_card(state: dict[str, Any], *, now: float | None = None, now_ms: float | None = None) -> tuple[str, float]:
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    if player:
        name = player.get("name") or "unknown"
        hp = _safe_int(player.get("hp"))
        max_hp = _safe_int(player.get("max_hp"), 1) or 1
        sp = _safe_int(player.get("sp"))
        max_sp = _safe_int(player.get("max_sp"))
        x = _safe_int(float(player.get("x") or 0) / 100.0)
        y = _safe_int(float(player.get("y") or 0) / 100.0)
    else:
        name = state.get("player_name") or "unknown"
        hp = _safe_int(state.get("hp"))
        max_hp = _safe_int(state.get("max_hp"), 1) or 1
        sp = _safe_int(state.get("sp"))
        max_sp = _safe_int(state.get("max_sp"))
        x = _safe_int(float(state.get("x") or 0) / 100.0)
        y = _safe_int(float(state.get("y") or 0) / 100.0)
    map_name = state.get("map") or state.get("map_name") or "unknown_map"
    target = state.get("target") if isinstance(state.get("target"), dict) else None
    target_line = "Target  none"
    if target:
        alive = "alive" if target.get("alive") is True else "dead" if target.get("alive") is False else "?"
        target_line = f"Target  {target.get('name') or 'unknown'}  VID:{target.get('vid') or '?'}  {alive}"
    elif state.get("target_vid") and str(state.get("target_vid", "0")) != "0":
        target_line = f"Target  {state.get('target_name') or 'unknown'}  VID:{state['target_vid']}"
    file_mtime = state.get("_file_mtime")
    if file_mtime is not None:
        try:
            if now is None:
                now = time.time()
            age_seconds = max(0.0, float(now) - float(file_mtime))
            age_line = f"Last update  {age_seconds:.1f}s ago"
        except Exception:
            age_line = "Last update  unknown"
    elif now_ms is not None and state.get("timestamp_ms") is not None:
        try:
            age_seconds = max(0.0, (float(now_ms) - float(state.get("timestamp_ms"))) / 1000.0)
            age_line = f"Last update {age_seconds:.1f}s ago"
        except Exception:
            age_line = "Last update  unknown"
    else:
        age_line = "Last update  unknown"
    hp_ratio = max(0.0, min(1.0, hp / float(max_hp)))
    text = "\n".join(
        [
            f"{name}  |  {map_name}  |  connected",
            f"HP    {hp}/{max_hp}",
            f"SP    {sp}/{max_sp}",
            f"Pos   ({x}, {y})",
            target_line,
            age_line,
        ]
    )
    return text, hp_ratio


def format_nearby_metins_results(project_root: Path, *, now: float | None = None) -> str:
    path = Path(project_root) / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    if not path.exists():
        return "Nearby Metins\nNo scan result yet."
    if now is None:
        now = time.time()
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return f"Nearby Metins\nCould not read latest result: {exc}"
    age = max(0.0, float(now) - path.stat().st_mtime)
    lines = [f"Nearby Metins (last scan: {age:.1f}s ago)"]
    metins = data.get("metins") if isinstance(data.get("metins"), list) else []
    live_indicators = data.get("live_indicators") if isinstance(data.get("live_indicators"), list) else []
    if not metins:
        if live_indicators:
            lines.append("No exact move-safe Metin coordinates found.")
        elif data.get("source") == "no_candidate_rows":
            lines.append("No live Metin evidence in last scan.")
        else:
            lines.append("No nearby Metins in last scan.")
    for idx, metin in enumerate(metins, start=1):
        loc = metin.get("display_location") or metin.get("location") or {}
        evidence = _short_evidence_label(str(metin.get("evidence_label") or metin.get("source_type") or ""))
        lines.append(
            f"#{idx}  {metin.get('metin_name') or 'Metin'}  "
            f"({loc.get('x', '?')}, {loc.get('y', '?')})  "
            f"{metin.get('distance', '?')}u  [{evidence}]"
        )
    if live_indicators:
        lines.append("Live evidence near you (not exact move coords):")
        for idx, metin in enumerate(live_indicators, start=1):
            loc = metin.get("display_location") or metin.get("location") or {}
            evidence = _short_evidence_label(str(metin.get("evidence_label") or metin.get("source_type") or ""))
            vid = metin.get("vid")
            vid_text = f" VID:{vid}" if vid else ""
            lines.append(
                f"* {metin.get('metin_name') or 'Metin'}{vid_text}  "
                f"near ({loc.get('x', '?')}, {loc.get('y', '?')})  "
                f"[{evidence}]"
            )
    diagnostics = data.get("diagnostics") if isinstance(data.get("diagnostics"), dict) else {}
    if diagnostics:
        keys = ["memory_raw_hits", "live_memory_rows", "memory_name_rows", "visual_raw_hits", "named_probe_rows", "target_rows"]
        parts = [f"{key}={diagnostics[key]}" for key in keys if key in diagnostics]
        if parts:
            lines.append("diagnostics: " + " ".join(parts))
        memory_error = diagnostics.get("memory_scan_error") or diagnostics.get("memory_name_scan_error")
        if memory_error:
            lines.append(f"exact memory scan blocked: {memory_error}")
    return "\n".join(lines)


def format_combat_summary(project_root: Path, run_id: str | None) -> str:
    if not run_id:
        return "Last combat run\nNo completed combat report yet."
    path = Path(project_root) / "reports" / "dashboard_runs" / f"{run_id}_report.json"
    if not path.exists():
        return "Last combat run\nNo completed combat report yet."
    try:
        report = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return f"Last combat run\nCould not read report: {exc}"
    states = report.get("states_visited") if isinstance(report.get("states_visited"), list) else []
    return "\n".join(
        [
            "Last combat run",
            f"Outcome    {report.get('outcome') or 'unknown'}",
            f"Target     {report.get('metin_name') or 'unknown'}  VID:{report.get('metin_vid') or '?'}",
            f"Duration   {report.get('duration_seconds', '?')}s",
            f"HP at end  {report.get('hp_at_end', '?')}/{report.get('max_hp', '?')}",
            f"Potions    {report.get('potions_used', '?')}",
            "States     " + "  ".join(str(state) for state in states),
        ]
    )


def combat_state_color(state: str | None) -> str:
    state = str(state or "idle")
    if state == "ATTACK_METIN":
        return "green"
    if state in {"RECOVER_HP_SP", "ENSURE_BUFF"}:
        return "goldenrod"
    if state in {"KILL_ADDS", "REACQUIRE_METIN"}:
        return "darkorange"
    if state == "VERIFY_DESTROYED":
        return "royalblue"
    if state in {"ABORT_SAFE", "STOP_REQUESTED"}:
        return "red"
    return "black"


def combat_log_is_stale(run: dict[str, Any] | None, *, now: float | None = None, max_age: float = 5.0) -> bool:
    if not run or not run.get("running"):
        return False
    log_path = run.get("log_path")
    if not log_path:
        return False
    try:
        mtime = Path(log_path).stat().st_mtime
    except OSError:
        return False
    if now is None:
        now = time.time()
    return now - mtime > max_age


COMBAT_LINE_RE = re.compile(
    r"\[state=(?P<state>[^\]]+)\]\s+\[action=(?P<action>[^\]]+)\]\s+\[hp=(?P<hp>[^\]]+)\]\s+\[sp=(?P<sp>[^\]]+)\]\s+\[target=(?P<target>[^\]]+)\]"
)


def parse_combat_log_tail(log_tail: str) -> dict[str, str] | None:
    latest: dict[str, str] | None = None
    for line in str(log_tail or "").splitlines():
        match = COMBAT_LINE_RE.search(line)
        if match:
            latest = match.groupdict()
    return latest


def state_has_metin_target(state: dict[str, Any]) -> bool:
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    name = str(target.get("name") or "")
    return bool(target.get("vid")) and "metin" in name.casefold()


def has_active_live_combat_run(runs: list[dict[str, Any]]) -> bool:
    return any(
        run.get("script") == "combat_metin_client_state"
        and run.get("mode") == "live"
        and bool(run.get("running"))
        for run in runs
    )


def has_active_live_control_run(runs: list[dict[str, Any]]) -> bool:
    return any(
        run.get("script") in {"combat_metin_client_state", "move_to_metin_client_state"}
        and run.get("mode") == "live"
        and bool(run.get("running"))
        for run in runs
    )


TRUSTED_COMBAT_COORD_SOURCES = {"live_memory_visible_text"}
TRUSTED_MOVE_COORD_SOURCES = {"live_memory_visible_text"}
COMBAT_COORD_LABELS = {
    "live_memory_visible_text": "Live memory label",
}


def _coord_raw_from_location(loc: dict[str, Any]) -> tuple[int, int, int, int] | None:
    try:
        display_x = int(round(float(loc.get("x"))))
        display_y = int(round(float(loc.get("y"))))
        raw_x = int(round(float(loc.get("raw_x", display_x * 100))))
        raw_y = int(round(float(loc.get("raw_y", display_y * 100))))
        return display_x, display_y, raw_x, raw_y
    except Exception:
        return None


def build_combat_payload(project_root: Path, state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    payload = build_quick_start_payload("practice_live")
    options = payload["options"]
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    if target.get("vid"):
        options["metin_vid"] = str(int(target["vid"]))
    result_path = Path(project_root) / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    if not result_path.exists():
        return payload, None
    try:
        data = json.loads(result_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return payload, None
    metins = data.get("metins") if isinstance(data.get("metins"), list) else []
    for metin in metins:
        if not isinstance(metin, dict):
            continue
        source_type = str(metin.get("source_type") or "")
        if source_type not in TRUSTED_COMBAT_COORD_SOURCES:
            continue
        loc = metin.get("location") if isinstance(metin.get("location"), dict) else {}
        coord = _coord_raw_from_location(loc)
        if coord is None:
            continue
        display_x, display_y, raw_x, raw_y = coord
        name = str(metin.get("metin_name") or target.get("name") or "Metin da Batalha")
        options["metin_name"] = name
        options["metin_x"] = str(raw_x)
        options["metin_y"] = str(raw_y)
        options["metin_coord_source"] = source_type
        if metin.get("vid") and "metin_vid" not in options:
            options["metin_vid"] = str(int(metin["vid"]))
        return payload, {
            "source_type": source_type,
            "label": COMBAT_COORD_LABELS.get(source_type, source_type),
            "x": display_x,
            "y": display_y,
            "raw_x": raw_x,
            "raw_y": raw_y,
        }
    return payload, None


def load_nearby_metins_artifact(project_root: Path) -> list[dict[str, Any]]:
    path = Path(project_root) / "reports" / "nearby_metins" / "latest_nearby_metins.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    metins = data.get("metins") if isinstance(data.get("metins"), list) else []
    indicators = data.get("live_indicators") if isinstance(data.get("live_indicators"), list) else []
    rows: list[dict[str, Any]] = []
    for metin in metins:
        if isinstance(metin, dict):
            item = dict(metin)
            item["_move_safe"] = str(item.get("source_type") or "") in TRUSTED_MOVE_COORD_SOURCES
            rows.append(item)
    for metin in indicators:
        if isinstance(metin, dict):
            item = dict(metin)
            item["_move_safe"] = False
            rows.append(item)
    return rows


def build_move_payload_from_metin(metin: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    source_type = str(metin.get("source_type") or "")
    if source_type not in TRUSTED_MOVE_COORD_SOURCES:
        return None, f"selected Metin coordinate source is not trusted for live movement: {source_type or 'unknown'}"
    loc = metin.get("location") if isinstance(metin.get("location"), dict) else {}
    coord = _coord_raw_from_location(loc)
    if coord is None:
        return None, "selected Metin has no usable coordinate"
    display_x, display_y, raw_x, raw_y = coord
    payload = {
        "script": "move_to_metin_client_state",
        "live": True,
        "confirm_live": True,
        "options": {
            "max_cycles": "90",
            "metin_name": str(metin.get("metin_name") or "Metin da Batalha"),
            "metin_x": str(raw_x),
            "metin_y": str(raw_y),
            "metin_coord_source": source_type,
        },
    }
    label = COMBAT_COORD_LABELS.get(source_type, source_type)
    return payload, f"({display_x}, {display_y}) [{label}]"


def build_move_confirmation_message(metin: dict[str, Any], coord_label: str) -> str:
    return "\n".join(
        [
            "Move directly to the selected Metin?",
            "",
            f"Target    {metin.get('metin_name') or 'Metin'}",
            f"Coord     {coord_label}",
            "Action    movement only: WASD pathing, no attack/click/skill input",
            "Path      online navigation model will choose smooth key holds and adapt from client-state feedback",
            "",
            "Starting LIVE movement. Confirm?",
        ]
    )


def build_combat_confirmation_message(state: dict[str, Any], coord: dict[str, Any] | None) -> str:
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    lines = [
        "Start the live bounded Metin practice loop?",
        "",
        f"Target    {target.get('name')}  VID:{target.get('vid')}  alive={target.get('alive')}",
        f"HP        {player.get('hp')}/{player.get('max_hp')}",
        f"Map       {state.get('map') or state.get('map_name')}",
    ]
    if coord:
        lines.append(f"Coord     ({coord['x']}, {coord['y']})  [{coord['label']}]")
        lines.append(f"Raw coord ({coord['raw_x']}, {coord['raw_y']})")
        lines.append("Navigation enabled: bot will move toward this coordinate before attacking.")
        lines.append("Do not rotate the camera during this run — movement calibration is camera/facing-dependent.")
    else:
        lines.append("Coord     none  player position estimate only")
        lines.append("Navigation disabled. Bot will attack from current position.")
        lines.append("Move adjacent to the Metin manually before starting.")
    lines.extend(["", "Starting LIVE combat. Confirm?"])
    return "\n".join(lines)


class ControlPanelApp:
    def __init__(self, root: tk.Tk, client: DashboardApiClient, *, project_root: Path):
        self.root = root
        self.client = client
        self.project_root = project_root
        self.server_proc: subprocess.Popen | None = None
        self.scripts: list[dict[str, Any]] = []
        self.script_vars: dict[str, dict[str, tk.Variable]] = {}
        self.selected_script = tk.StringVar()
        self.selected_run = tk.StringVar()
        self.auto_refresh = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="ready")
        self.action_status = tk.StringVar(value="Last action: none")
        self.combat_state_var = tk.StringVar(value="State   idle")
        self.hp_var = tk.DoubleVar(value=0.0)
        self.nearby_metins: list[dict[str, Any]] = []
        self._refreshing = False
        self._start_in_flight = False
        self._build_ui()
        self.refresh_all()
        self._schedule_refresh()

    def _build_ui(self) -> None:
        self.root.title("Metin2 Control Panel")
        self.root.geometry("1080x760")
        self.style = ttk.Style(self.root)
        self.style.configure("HpGreen.Horizontal.TProgressbar", background="#2e8b57")
        self.style.configure("HpYellow.Horizontal.TProgressbar", background="#d4a017")
        self.style.configure("HpRed.Horizontal.TProgressbar", background="#b22222")

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Start local API", command=self.start_server).pack(side="left", padx=3)
        ttk.Button(top, text="Refresh state", command=self.refresh_all).pack(side="left", padx=3)
        ttk.Button(top, text="Patch/integrate client", command=self.integrate_client).pack(side="left", padx=3)
        ttk.Button(top, text="Open game + login", command=self.open_login_game).pack(side="left", padx=3)
        ttk.Button(top, text="Find nearby Metins", command=self.find_nearby_metins).pack(side="left", padx=3)
        ttk.Button(top, text="Find then Fight", command=self.find_then_fight).pack(side="left", padx=3)
        ttk.Button(top, text="Practice dry-run", command=self.practice_dry_run).pack(side="left", padx=3)
        ttk.Button(top, text="Start practicing LIVE", command=self.practice_live).pack(side="left", padx=3)
        ttk.Checkbutton(top, text="auto refresh state/runs", variable=self.auto_refresh).pack(side="left", padx=12)
        ttk.Label(top, textvariable=self.status).pack(side="right")

        panes = ttk.PanedWindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(panes, padding=6)
        right = ttk.Frame(panes, padding=6)
        panes.add(left, weight=1)
        panes.add(right, weight=1)

        ttk.Label(left, text="Client state").pack(anchor="w")
        self.state_text = tk.Text(left, height=7, wrap="none", font=("Consolas", 10))
        self.state_text.pack(fill="x", pady=(0, 4))
        self.hp_bar = ttk.Progressbar(left, variable=self.hp_var, maximum=100)
        self.hp_bar.pack(fill="x", pady=(0, 8))
        ttk.Label(left, textvariable=self.action_status).pack(anchor="w", pady=(0, 8))
        ttk.Label(left, text="Combat").pack(anchor="w")
        self.combat_state_label = tk.Label(left, textvariable=self.combat_state_var, anchor="w", fg="black", font=("Consolas", 10, "bold"))
        self.combat_state_label.pack(fill="x")
        self.combat_text = tk.Text(left, height=10, wrap="none", font=("Consolas", 9))
        self.combat_text.pack(fill="x", pady=(0, 8))

        ttk.Label(left, text="Script").pack(anchor="w")
        self.script_combo = ttk.Combobox(left, textvariable=self.selected_script, state="readonly")
        self.script_combo.pack(fill="x")
        self.script_combo.bind("<<ComboboxSelected>>", lambda _e: self.render_selected_script())

        self.options_frame = ttk.LabelFrame(left, text="Options", padding=8)
        self.options_frame.pack(fill="both", expand=True, pady=8)

        actions = ttk.Frame(left)
        actions.pack(fill="x")
        ttk.Button(actions, text="Start dry-run", command=lambda: self.start_selected(False)).pack(side="left", padx=3)
        ttk.Button(actions, text="Start live", command=lambda: self.start_selected(True)).pack(side="left", padx=3)

        ttk.Label(right, text="Nearby Metins").pack(anchor="w")
        self.nearby_text = tk.Text(right, height=7, wrap="none", font=("Consolas", 9))
        self.nearby_text.pack(fill="x", pady=(0, 4))
        self.nearby_list = tk.Listbox(right, height=5, exportselection=False)
        self.nearby_list.pack(fill="x", pady=(0, 4))
        ttk.Button(right, text="Move to selected Metin", command=self.move_to_selected_metin).pack(anchor="w", pady=(0, 8))

        ttk.Label(right, text="Runs").pack(anchor="w")
        self.runs_list = tk.Listbox(right, height=7)
        self.runs_list.pack(fill="x")
        self.runs_list.bind("<<ListboxSelect>>", lambda _e: self.on_run_selected())
        run_buttons = ttk.Frame(right)
        run_buttons.pack(fill="x", pady=4)
        ttk.Button(run_buttons, text="Stop selected", command=self.stop_selected).pack(side="left", padx=3)
        ttk.Button(run_buttons, text="Archive selected", command=self.archive_selected).pack(side="left", padx=3)
        ttk.Button(run_buttons, text="Emergency stop all", command=self.stop_all).pack(side="left", padx=3)
        ttk.Button(run_buttons, text="Open full log", command=self.open_selected_log).pack(side="left", padx=3)

        ttk.Label(right, text="Log tail").pack(anchor="w")
        self.log_text = tk.Text(right, height=22, wrap="none")
        self.log_text.pack(fill="both", expand=True)

    def start_server(self) -> None:
        if self.server_proc and self.server_proc.poll() is None:
            self.set_status("local API already started by this app")
            return
        if self._api_available():
            self.set_status("local API already running")
            return
        log_dir = Path(self.project_root) / "reports" / "dashboard_runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        server_log = (log_dir / "dashboard_api.log").open("ab", buffering=0)
        self.server_proc = subprocess.Popen(
            DEFAULT_SERVER_CMD,
            cwd=str(self.project_root),
            stdout=server_log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        self.set_status("starting local API")
        self.root.after(1200, self.refresh_all)

    def _api_available(self) -> bool:
        try:
            self.client.get("/api/scripts")
            return True
        except Exception:
            return False

    def refresh_all(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self) -> None:
        try:
            state = self.client.get("/api/state")
            scripts = self.client.get("/api/scripts")
            runs = self.client.get("/api/runs")
            self.root.after(0, lambda: self.apply_refresh(state, scripts, runs))
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda message=message: self.set_status(f"API error: {message}"))
        finally:
            self.root.after(0, lambda: setattr(self, "_refreshing", False))

    def apply_refresh(self, state: dict[str, Any], scripts: list[dict[str, Any]], runs: list[dict[str, Any]]) -> None:
        self.render_state_card(state)
        self.render_nearby_metins()
        self.render_combat_status(state, runs)
        self.scripts = scripts
        names = [script["name"] for script in scripts]
        self.script_combo["values"] = names
        if not self.selected_script.get() and names:
            self.selected_script.set(names[0])
            self.render_selected_script()
        self.render_runs(runs)
        self.set_status("connected")

    def render_state_card(self, state: dict[str, Any]) -> None:
        text, hp_ratio = format_state_card(state)
        self.state_text.delete("1.0", "end")
        self.state_text.insert("end", text)
        self.hp_var.set(round(hp_ratio * 100.0, 1))
        if hp_ratio >= 0.6:
            self.hp_bar.configure(style="HpGreen.Horizontal.TProgressbar")
        elif hp_ratio >= 0.3:
            self.hp_bar.configure(style="HpYellow.Horizontal.TProgressbar")
        else:
            self.hp_bar.configure(style="HpRed.Horizontal.TProgressbar")

    def render_nearby_metins(self) -> None:
        text = format_nearby_metins_results(self.project_root)
        self.nearby_text.delete("1.0", "end")
        self.nearby_text.insert("end", text)
        if hasattr(self, "nearby_list"):
            selected = self.nearby_list.curselection()
            selected_index = selected[0] if selected else None
            self.nearby_metins = load_nearby_metins_artifact(self.project_root)
            self.nearby_list.delete(0, "end")
            for idx, metin in enumerate(self.nearby_metins, start=1):
                loc = metin.get("display_location") or metin.get("location") or {}
                evidence = _short_evidence_label(str(metin.get("evidence_label") or metin.get("source_type") or ""))
                move_note = "" if metin.get("_move_safe") else " (indicator only)"
                self.nearby_list.insert("end", f"#{idx} {metin.get('metin_name') or 'Metin'} ({loc.get('x', '?')},{loc.get('y', '?')}) [{evidence}]{move_note}")
            if selected_index is not None and selected_index < len(self.nearby_metins):
                self.nearby_list.selection_set(selected_index)

    def render_combat_status(self, state: dict[str, Any], runs: list[dict[str, Any]]) -> None:
        target = state.get("target") if isinstance(state.get("target"), dict) else {}
        player = state.get("player") if isinstance(state.get("player"), dict) else {}
        combat_run = next((run for run in runs if run.get("script") == "combat_metin_client_state" and run.get("running")), None)
        last_combat_run = next((run for run in reversed(runs) if run.get("script") == "combat_metin_client_state"), None)
        if combat_run is None:
            combat_run = last_combat_run
        parsed = parse_combat_log_tail(combat_run.get("log_tail", "")) if combat_run else None
        state_name = (parsed or {}).get("state", "idle")
        self.combat_state_var.set(f"State   {state_name}")
        self.combat_state_label.configure(fg=combat_state_color(state_name))
        lines = [
            f"Target  {target.get('name') or 'none'}  VID:{target.get('vid') or 0}  alive={target.get('alive')}",
            f"HP      {player.get('hp', '?')}/{player.get('max_hp', '?')}",
            f"Last act {(parsed or {}).get('action', '-')}",
        ]
        if combat_log_is_stale(combat_run):
            lines.append("WARNING  state feed stale")
        completed = next((run for run in reversed(runs) if run.get("script") == "combat_metin_client_state" and not run.get("running")), None)
        if completed:
            lines.extend(["", format_combat_summary(self.project_root, completed.get("run_id"))])
        self.combat_text.delete("1.0", "end")
        self.combat_text.insert("end", "\n".join(lines))

    def render_selected_script(self) -> None:
        for child in self.options_frame.winfo_children():
            child.destroy()
        script = self.get_selected_script()
        if not script:
            return
        if script["name"] not in self.script_vars:
            defaults = option_default_values(script)
            vars_for_script: dict[str, tk.Variable] = {}
            for opt in script.get("options", []):
                name = opt["name"]
                if opt.get("type") == "bool":
                    vars_for_script[name] = tk.BooleanVar(value=defaults[name])
                else:
                    vars_for_script[name] = tk.StringVar(value=defaults[name])
            self.script_vars[script["name"]] = vars_for_script
        vars_for_script = self.script_vars[script["name"]]
        for row, opt in enumerate(script.get("options", [])):
            ttk.Label(self.options_frame, text=opt["name"]).grid(row=row, column=0, sticky="w", pady=2)
            if opt.get("type") == "bool":
                ttk.Checkbutton(self.options_frame, variable=vars_for_script[opt["name"]]).grid(row=row, column=1, sticky="w")
            else:
                ttk.Entry(self.options_frame, textvariable=vars_for_script[opt["name"]], width=32).grid(row=row, column=1, sticky="ew", pady=2)
            ttk.Label(self.options_frame, text=opt.get("description", "")).grid(row=row, column=2, sticky="w", padx=8)
        self.options_frame.columnconfigure(1, weight=1)

    def render_runs(self, runs: list[dict[str, Any]]) -> None:
        selected = self.selected_run.get()
        self.runs_list.delete(0, "end")
        self._runs_by_id = {run["run_id"]: run for run in runs}
        should_refresh_nearby = False
        for run in runs:
            label = f"{run['run_id']} | {run['script']} | {run['mode']} | {'running' if run['running'] else 'exit '+str(run['exit_code'])}"
            self.runs_list.insert("end", label)
            if run["script"] == "find_nearby_metins" and not run.get("running") and run.get("exit_code") == 0:
                should_refresh_nearby = True
            if run["run_id"] == selected:
                self.runs_list.selection_set("end")
        if should_refresh_nearby:
            self.render_nearby_metins()
        if selected in self._runs_by_id:
            self.show_run_log(self._runs_by_id[selected])

    def get_selected_script(self) -> dict[str, Any] | None:
        name = self.selected_script.get()
        for script in self.scripts:
            if script["name"] == name:
                return script
        return None

    def start_selected(self, live: bool) -> None:
        script = self.get_selected_script()
        if not script:
            return
        confirm_live = False
        if script.get("force_dry_run"):
            live = False
        if live:
            if script.get("name") == "combat_metin_client_state":
                try:
                    runs = self.client.get("/api/runs")
                except Exception as exc:
                    messagebox.showerror("Confirm live run", f"Cannot read running scripts: {exc}")
                    return
                if has_active_live_combat_run(runs):
                    messagebox.showinfo("Confirm live run", "A live combat practice run is already running. Stop it before starting another.")
                    self.refresh_all()
                    return
            confirm_live = messagebox.askokcancel("Confirm live run", "Start LIVE run? This can control the game.")
            if not confirm_live:
                return
        vars_for_script = self.script_vars.get(script["name"], {})
        values = {name: var.get() for name, var in vars_for_script.items()}
        payload = {"script": script["name"], "live": live, "confirm_live": confirm_live, "options": option_payload_from_vars(script, values)}
        self._post_async("/api/start", payload)

    def open_login_game(self) -> None:
        if messagebox.askokcancel("Open game + login", "Restart/open MT2Portugalia, login as Yoshy, and press Começar/Enter? Credentials stay in Windows Credential Manager."):
            self._post_after_api_ready(build_quick_start_payload("open_login_game"))

    def integrate_client(self) -> None:
        if messagebox.askokcancel("Patch/integrate client", "Patch the local MT2Portugalia client state logger into loose game.py and pack/root? Close the game first if it is running."):
            self._post_after_api_ready(build_quick_start_payload("integrate_client"))

    def practice_dry_run(self) -> None:
        self._post_after_api_ready(build_quick_start_payload("practice_dry_run"))

    def find_nearby_metins(self) -> None:
        self._post_after_api_ready(build_quick_start_payload("find_nearby_metins"))

    def move_to_selected_metin(self) -> None:
        if not getattr(self, "nearby_metins", None):
            self.render_nearby_metins()
        selected = self.nearby_list.curselection() if hasattr(self, "nearby_list") else ()
        if not selected:
            messagebox.showinfo("Move to selected Metin", "Select a Metin from the Nearby Metins list first.")
            return
        metin = self.nearby_metins[selected[0]]
        payload, detail = build_move_payload_from_metin(metin)
        if payload is None:
            messagebox.showwarning("Move to selected Metin", f"Cannot start live movement: {detail}")
            return
        try:
            runs = self.client.get("/api/runs")
        except Exception as exc:
            messagebox.showerror("Move to selected Metin", f"Cannot read running scripts: {exc}")
            return
        if has_active_live_control_run(runs):
            messagebox.showinfo("Move to selected Metin", "A live movement/combat run is already running. Stop it before starting another.")
            self.refresh_all()
            return
        if messagebox.askokcancel("Move to selected Metin", build_move_confirmation_message(metin, detail)):
            self._post_after_api_ready(payload)

    def find_then_fight(self) -> None:
        threading.Thread(target=self._find_then_fight_worker, daemon=True).start()

    def _find_then_fight_worker(self) -> None:
        try:
            self.root.after(0, lambda: self.set_status("Find then Fight: scanning nearby Metins"))
            find_payload = build_quick_start_payload("find_nearby_metins")
            response = self.client.post("/api/start", find_payload)
            run_id = response.get("run_id") if isinstance(response, dict) else None
            if not run_id:
                raise RuntimeError("Find nearby Metins did not return a run_id")
            deadline = time.time() + 30.0
            finished = None
            while time.time() < deadline:
                runs = self.client.get("/api/runs")
                finished = next((run for run in runs if run.get("run_id") == run_id), None)
                if finished and not finished.get("running"):
                    break
                time.sleep(0.25)
            if not finished or finished.get("running"):
                self.root.after(0, lambda: self.set_status("Find then Fight: scan timed out"))
                return
            if finished.get("exit_code") not in (0, None):
                self.root.after(0, lambda: self.set_status(f"Find then Fight: scan failed exit={finished.get('exit_code')}"))
                return
            state = self.client.get("/api/state")
            payload, coord = build_combat_payload(self.project_root, state)
            if not coord:
                self.root.after(0, lambda: self.set_status("Find then Fight: found Metin but coordinate is estimate only; move closer or scan again"))
                self.root.after(0, self.refresh_all)
                return
            payload["options"]["max_cycles"] = "120"
            msg = build_combat_confirmation_message(state, coord)
            def confirm_and_start() -> None:
                self.set_status(f"Find then Fight: trusted coordinate ({coord['x']}, {coord['y']}) [{coord['label']}]")
                if messagebox.askokcancel("Find then Fight", msg):
                    self._post_after_api_ready(payload)
                else:
                    self.set_status("Find then Fight: cancelled")
                self.refresh_all()
            self.root.after(0, confirm_and_start)
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda message=message: messagebox.showerror("Find then Fight", message))

    def practice_live(self) -> None:
        try:
            runs = self.client.get("/api/runs")
        except Exception as exc:
            messagebox.showerror("Start practicing LIVE", f"Cannot read running scripts: {exc}")
            return
        if has_active_live_combat_run(runs):
            messagebox.showinfo("Start practicing LIVE", "A live combat practice run is already running. Use Stop selected or Emergency stop all before starting another.")
            self.refresh_all()
            return
        try:
            state = self.client.get("/api/state")
        except Exception as exc:
            messagebox.showerror("Start practicing LIVE", f"Cannot read client state: {exc}")
            return
        if not state_has_metin_target(state):
            messagebox.showwarning("Start practicing LIVE", "No Metin targeted — select one before starting live combat.")
            return
        target = state.get("target", {})
        player = state.get("player", {})
        payload, coord = build_combat_payload(self.project_root, state)
        msg = build_combat_confirmation_message(state, coord)
        if messagebox.askokcancel("Start practicing LIVE", msg):
            self._post_after_api_ready(payload)

    def _post_after_api_ready(self, payload: dict[str, Any]) -> None:
        if not (self.server_proc and self.server_proc.poll() is None):
            self.start_server()
            self.root.after(1500, lambda payload=payload: self._post_async("/api/start", payload))
        else:
            self._post_async("/api/start", payload)

    def stop_selected(self) -> None:
        rid = self.selected_run.get()
        if rid:
            self._post_async("/api/stop", {"run_id": rid})

    def archive_selected(self) -> None:
        rid = self.selected_run.get()
        if rid:
            self._post_async("/api/archive_run", {"run_id": rid})

    def stop_all(self) -> None:
        if messagebox.askokcancel("Emergency stop all", "Stop all dashboard-managed runs?"):
            self._post_async("/api/stop_all", {})

    def _post_async(self, path: str, payload: dict[str, Any]) -> None:
        if path == "/api/start":
            if getattr(self, "_start_in_flight", False):
                self.action_status.set("Last action: start already in progress")
                return
            self._start_in_flight = True
        def worker() -> None:
            try:
                response = self.client.post(path, payload)
                action = payload.get("script") or path
                mode = "live" if payload.get("live") else "dry-run"
                run_id = response.get("run_id") if isinstance(response, dict) else None
                suffix = f" ({run_id})" if run_id else ""
                self.root.after(0, lambda action=action, mode=mode, suffix=suffix: self.action_status.set(f"Last action: {action} started ({mode}){suffix}"))
                self.root.after(0, self.refresh_all)
            except Exception as exc:
                message = str(exc)
                self.root.after(0, lambda message=message: messagebox.showerror("API error", message))
            finally:
                if path == "/api/start":
                    self.root.after(0, lambda: setattr(self, "_start_in_flight", False))
        threading.Thread(target=worker, daemon=True).start()

    def open_selected_log(self) -> None:
        rid = self.selected_run.get()
        run = getattr(self, "_runs_by_id", {}).get(rid)
        log_path = run.get("log_path") if isinstance(run, dict) else None
        if not log_path:
            messagebox.showinfo("Open full log", "No log file is available for the selected run.")
            return
        try:
            subprocess.Popen(["notepad", str(log_path)])
        except Exception as exc:
            messagebox.showerror("Open full log", str(exc))

    def on_run_selected(self) -> None:
        sel = self.runs_list.curselection()
        if not sel:
            return
        label = self.runs_list.get(sel[0])
        rid = label.split(" | ", 1)[0]
        self.selected_run.set(rid)
        run = getattr(self, "_runs_by_id", {}).get(rid)
        if run:
            self.show_run_log(run)

    def show_run_log(self, run: dict[str, Any]) -> None:
        try:
            top, bottom = self.log_text.yview()
        except Exception:
            top, bottom = 0.0, 1.0
        was_at_bottom = bottom >= 0.999
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", run.get("log_tail", ""))
        if was_at_bottom:
            self.log_text.see("end")
        else:
            self.log_text.yview_moveto(top)

    def _schedule_refresh(self) -> None:
        if self.auto_refresh.get():
            self.refresh_all()
        self.root.after(1500, self._schedule_refresh)

    def set_status(self, text: str) -> None:
        self.status.set(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--project-root", default=".")
    ns = ap.parse_args(argv)
    root = tk.Tk()
    ControlPanelApp(root, DashboardApiClient(ns.base_url), project_root=Path(ns.project_root).resolve())
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
