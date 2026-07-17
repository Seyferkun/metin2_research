from __future__ import annotations

import argparse
import ctypes
import json
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .state_bridge import format_state_bridge_report, state_bridge_report

DEFAULT_BASE_URL = "http://127.0.0.1:8767"
POLL_MS = 1000
MAIN_JSON_STATE = "D:/Games/MT2Portugalia/app/hermes_state.json"
MAIN_TSV_STATE = "D:/Games/MT2Portugalia/app/hermes_state.tsv"
BUFFER_JSON_STATE = "D:/Games/MT2PortugaliaBuffer/app/hermes_state.json"
BUFFER_TSV_STATE = "D:/Games/MT2PortugaliaBuffer/app/hermes_state.tsv"
FARMER_JSON_STATE = "D:/Games/MT2PortugaliaFarmer/app/hermes_state.json"
FARMER_TSV_STATE = "D:/Games/MT2PortugaliaFarmer/app/hermes_state.tsv"
CLIENT_PROFILES = {
    "main": {"label": "main", "json_state": MAIN_JSON_STATE, "tsv_state": MAIN_TSV_STATE},
    "buffer": {"label": "buffer", "json_state": BUFFER_JSON_STATE, "tsv_state": BUFFER_TSV_STATE},
    "farmer": {"label": "farmer", "json_state": FARMER_JSON_STATE, "tsv_state": FARMER_TSV_STATE},
}
LEARNED_CHANNEL_CLICK_POINTS = "0.4990,0.3986;0.4990,0.4326;0.4990,0.4665;0.4990,0.5005;0.4990,0.5344;0.4990,0.5684;0.4990,0.6023;0.4990,0.6363"
FIXED_SAPO_CHANNEL_CYCLE_STATE = "reports/dashboard_runs/fixed_sapo_channel_cycle_state.json"


def normalize_channel_click_points(points: str, *, skip_first: bool = False) -> str:
    rows = [row.strip() for row in str(points or LEARNED_CHANNEL_CLICK_POINTS).split(";") if row.strip()]
    if skip_first and len(rows) > 1:
        rows = rows[1:]
    return ";".join(rows)


def effective_channel_index(index: str, *, skip_first: bool = False) -> str:
    """Convert a user-facing raw visible-row index to the script's active list index.

    The UI value is always the raw row in the channel menu: CH1=0, CH2=1, ...
    If skip_first is enabled, row 0 is removed from the point list before it is
    sent to the script, so raw row 1 becomes active-list index 0.
    """
    value = max(0, int(float(index or 0)))
    if skip_first:
        value = max(0, value - 1)
    return str(value)


def pickup_count_from_seconds(seconds: str, *, interval: float = 0.08) -> str:
    duration = max(0.0, float(seconds or 0))
    if duration <= 0:
        return "0"
    return str(max(1, int(round(duration / max(0.02, interval)))))


def format_fixed_sapo_gate_details(state: dict[str, Any], runs: list[dict[str, Any]] | None = None, cycle_state: dict[str, Any] | None = None) -> str:
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    probe = state.get("named_metin_probe") or state.get("sapo_probe") or {}
    if not isinstance(probe, dict):
        nearby = state.get("nearby_entities") if isinstance(state.get("nearby_entities"), list) else []
        probe = next((row for row in nearby if isinstance(row, dict) and str(row.get("name") or "").lower() == "sapo de pedra"), {})
    age = state.get("age_seconds", state.get("_age_seconds"))
    if age is None and state.get("_file_mtime"):
        try:
            age = max(0.0, time.time() - float(state.get("_file_mtime")))
        except Exception:
            age = None
    buff_running = bool(find_running_buff_keeper_run(runs or []))
    live_running = bool(has_active_live_combat_run(runs or []))
    lines = ["Fixed Sapo gate details"]
    lines.append(f"state age: {float(age):.2f}s" if age is not None else "state age: unknown")
    lines.append(f"player: {player.get('name') or state.get('player_name') or '?'} hp={player.get('hp', state.get('hp'))}/{player.get('max_hp', state.get('max_hp'))} pos=({player.get('x', state.get('x'))}, {player.get('y', state.get('y'))})")
    lines.append(f"target: {target.get('name') or state.get('target_name') or '-'} alive={target.get('alive')}")
    lines.append(f"sapo probe: {probe.get('name') or '-'} alive={probe.get('alive')} vid={probe.get('vid')} pixel={probe.get('pixel_position')}")
    lines.append(f"buff keeper running: {buff_running}")
    lines.append(f"live combat/sweep already running: {live_running}")
    if cycle_state:
        lines.append(f"cycle state: last={cycle_state.get('last_channel_index')} next={cycle_state.get('next_channel_index')} points={cycle_state.get('points_count')}")
    return "\n".join(lines)


def format_fixed_sapo_sweep_summary(summary: dict[str, Any]) -> str:
    if not summary:
        return "No Fixed Sapo sweep summary found yet."
    lines = ["Fixed Sapo last sweep summary"]
    lines.append(f"run_id: {summary.get('run_id')}")
    lines.append(f"outcome: {summary.get('outcome')} reason={summary.get('reason')}")
    lines.append(f"completed cycles: {summary.get('completed_cycles', 0)}/{summary.get('requested_channels', len(summary.get('cycles', [])))}")
    final = summary.get("final_state") if isinstance(summary.get("final_state"), dict) else {}
    player = final.get("player") if isinstance(final.get("player"), dict) else {}
    if player:
        lines.append(f"final hp: {player.get('hp')}/{player.get('max_hp')} pos=({player.get('x')}, {player.get('y')})")
    for row in summary.get("cycles", []) or []:
        if not isinstance(row, dict):
            continue
        switch = row.get("channel_switch_result") if isinstance(row.get("channel_switch_result"), dict) else {}
        attack = row.get("attack") if isinstance(row.get("attack"), dict) else {}
        lines.append(
            f"cycle {row.get('cycle')}: {row.get('outcome')} reason={row.get('reason')} "
            f"destroyed={attack.get('destroyed')} hp_min={attack.get('hp_min')} "
            f"pickup={row.get('pickup_sent')} channel_index={switch.get('channel_index')} point={switch.get('screen_point')}"
        )
    if summary.get("log"):
        lines.append(f"log: {summary.get('log')}")
    if summary.get("screenshot_dir"):
        lines.append(f"screens: {summary.get('screenshot_dir')}")
    return "\n".join(lines)


def build_server_cmd(*, port: str = "8767", json_state: str = MAIN_JSON_STATE, tsv_state: str = MAIN_TSV_STATE) -> list[str]:
    return [
        sys.executable,
        "-m",
        "metin2_dashboard.server",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--project-root",
        ".",
        "--json-state",
        str(json_state),
        "--tsv",
        str(tsv_state),
    ]

DEFAULT_SERVER_CMD = build_server_cmd()


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


def build_quick_start_payload(
    action: str,
    *,
    login_username: str | None = None,
    login_app_dir: str | None = None,
    assume_mounted: bool | None = None,
    buff_keys: str | None = None,
    buff_durations: str | None = None,
    buff_refresh_margin_seconds: str | None = None,
    f1_active_attack_min_min: str | None = "200",
    f2_active_attack_speed_min: str | None = "130",
    channel_rotate_after_destroy: bool = False,
    channel_click_points: str | None = None,
    pickup_spam_count: str | None = None,
) -> dict[str, Any]:
    """Return allowlisted payloads for native one-click operator buttons."""
    if action == "open_login_game":
        options: dict[str, Any] = {}
        if login_username:
            options["username"] = str(login_username).strip()
        if login_app_dir:
            options["app_dir"] = str(login_app_dir).strip()
        return {"script": "login_mt2_local", "live": True, "confirm_live": True, "options": options}
    if action == "integrate_client":
        return {"script": "patch_mt2_root_state_logger", "live": False, "confirm_live": False, "options": {}}
    if action == "practice_dry_run":
        return {"script": "combat_metin_client_state", "live": False, "confirm_live": False, "options": {"max_cycles": "5"}}
    if action == "buff_only_dry_run":
        return {"script": "combat_metin_client_state", "live": False, "confirm_live": False, "options": {"max_cycles": "4", "buff_only": True, "buff_keys": "f1,f2", "buff_durations": "156,302", "buff_damage_guard_keys": "f1", "assume_mounted": True}}
    if action == "buff_only_live":
        options: dict[str, Any] = {"max_cycles": "0", "buff_only": True, "assume_mounted": True if assume_mounted is None else bool(assume_mounted), "buff_damage_guard_keys": "f1"}
        if buff_keys:
            options["buff_keys"] = str(buff_keys)
        if buff_durations:
            options["buff_durations"] = str(buff_durations)
        if buff_refresh_margin_seconds not in (None, ""):
            options["buff_refresh_margin_seconds"] = str(buff_refresh_margin_seconds)
        if f1_active_attack_min_min not in (None, ""):
            options["f1_active_attack_min_min"] = str(f1_active_attack_min_min)
        if f2_active_attack_speed_min not in (None, ""):
            options["f2_active_attack_speed_min"] = str(f2_active_attack_speed_min)
        return {"script": "combat_metin_client_state", "live": True, "confirm_live": True, "options": options}
    if action == "practice_live":
        return {"script": "combat_metin_client_state", "live": True, "confirm_live": True, "options": {"max_cycles": "0"}}
    if action == "attack_nearby_live":
        options: dict[str, Any] = {"max_cycles": "0", "attack_nearby_mobs": True, "visual_target_clicks": True, "allow_blind_target_clicks": True, "minimap_camera_hint": True}
        if channel_rotate_after_destroy:
            options["channel_rotate_after_destroy"] = True
            if channel_click_points:
                options["channel_click_points"] = str(channel_click_points)
            if pickup_spam_count not in (None, ""):
                options["pickup_spam_count"] = str(pickup_spam_count)
        return {"script": "combat_metin_client_state", "live": True, "confirm_live": True, "options": options}
    if action == "find_nearby_metins":
        return {"script": "find_nearby_metins", "live": False, "confirm_live": False, "options": {"radius": "300", "limit": "8"}}
    raise ValueError(f"unknown quick action: {action}")




def build_key_macro_payload(*, key: str, interval_seconds: str = "35", presses: str = "1", hold_seconds: str = "0.06", live: bool = True) -> dict[str, Any]:
    key = str(key).strip().lower()
    if key not in {"f1", "f2"}:
        raise ValueError("native key-test buttons only allow f1/f2")
    interval = max(0.05, float(interval_seconds))
    press_count = int(float(presses))
    hold = max(0.01, float(hold_seconds))
    return {
        "script": "key_macro_control",
        "live": bool(live),
        "confirm_live": bool(live),
        "options": {
            "key": key,
            "interval_seconds": str(interval),
            "presses": str(press_count),
            "hold_seconds": str(hold),
            "window_query": "MT2Portugalia",
            "elevate": True,
        },
    }


def build_fixed_sapo_payload(
    *,
    duration: str = "30",
    until_destroyed: bool = False,
    hp_stop_threshold: str = "2500",
    pickup_after_destroy: bool = False,
    channel_switch_after_pickup: bool = False,
    channel_click_points: str = LEARNED_CHANNEL_CLICK_POINTS,
    channel_index: str = "0",
    channel_auto_cycle: bool = True,
    channel_cycle_state: str = FIXED_SAPO_CHANNEL_CYCLE_STATE,
    skip_first_channel: bool = False,
    pickup_seconds: str = "1.6",
    min_distance: str = "350",
    live: bool = False,
    state_json: str = MAIN_JSON_STATE,
) -> dict[str, Any]:
    """Build the fixed-spawn Sapo test-bench payload.

    This path is intentionally no-click/no-move/no-potion. Live mode only holds
    Space after the script's preflight gates pass.
    """
    duration_s = max(0.0, float(duration))
    hp_stop = max(1, int(float(hp_stop_threshold)))
    min_dist = max(0.0, float(min_distance))
    pickup_count = pickup_count_from_seconds(pickup_seconds)
    stem = "fixed_sapo_until_destroy" if until_destroyed else f"fixed_sapo_{int(duration_s)}s"
    options: dict[str, Any] = {
        "state_json": str(state_json),
        "duration": str(duration_s),
        "hp_stop_threshold": str(hp_stop),
        "min_distance": str(min_dist),
        "window_query": "MT2Portugalia",
        "out": f"reports/dashboard_runs/{stem}.jsonl",
        "summary_out": f"reports/dashboard_runs/{stem}_summary.json",
    }
    if until_destroyed:
        options["until_destroyed"] = True
    if pickup_after_destroy:
        options["pickup_after_destroy"] = True
        options["pickup_count"] = pickup_count
    if channel_switch_after_pickup:
        options["channel_switch_after_pickup"] = True
        options["channel_click_points"] = normalize_channel_click_points(channel_click_points or LEARNED_CHANNEL_CLICK_POINTS, skip_first=skip_first_channel)
        options["channel_index"] = effective_channel_index(channel_index, skip_first=skip_first_channel)
        options["channel_auto_cycle"] = bool(channel_auto_cycle)
        options["channel_cycle_state"] = str(channel_cycle_state or FIXED_SAPO_CHANNEL_CYCLE_STATE)
    return {"script": "fixed_sapo_space_control", "live": bool(live), "confirm_live": bool(live), "options": options}


def build_fixed_sapo_sweep_payload(
    *,
    channels: str = "8",
    cycle_duration: str = "240",
    load_wait_seconds: str = "8",
    hp_stop_threshold: str = "2500",
    min_distance: str = "350",
    channel_click_points: str = LEARNED_CHANNEL_CLICK_POINTS,
    channel_index: str = "1",
    channel_auto_cycle: bool = True,
    skip_first_channel: bool = False,
    pickup_seconds: str = "1.6",
    repeat_while_running: bool = False,
    low_dps_adjust: bool = False,
    low_dps_threshold: str = "0.2",
    low_dps_window_seconds: str = "8",
    low_dps_max_cumulative_steps: str = "3",
    adjust_hold_seconds: str = "0.18",
    live: bool = False,
    state_json: str = MAIN_JSON_STATE,
) -> dict[str, Any]:
    channel_count = max(1, int(float(channels)))
    cycle_seconds = max(5.0, float(cycle_duration))
    load_wait = max(0.0, float(load_wait_seconds))
    hp_stop = max(1, int(float(hp_stop_threshold)))
    min_dist = max(0.0, float(min_distance))
    low_dps = max(0.0, float(low_dps_threshold))
    low_dps_window = max(1.0, float(low_dps_window_seconds))
    low_dps_max_steps = max(1, int(float(low_dps_max_cumulative_steps)))
    adjust_hold = max(0.03, min(0.5, float(adjust_hold_seconds)))
    pickup_count = pickup_count_from_seconds(pickup_seconds)
    options: dict[str, Any] = {
        "state_json": str(state_json),
        "channels": str(channel_count),
        "cycle_duration": str(cycle_seconds),
        "load_wait_seconds": str(load_wait),
        "hp_stop_threshold": str(hp_stop),
        "min_distance": str(min_dist),
        "pickup_count": pickup_count,
        "channel_click_points": normalize_channel_click_points(channel_click_points or LEARNED_CHANNEL_CLICK_POINTS, skip_first=skip_first_channel),
        "channel_index": effective_channel_index(channel_index, skip_first=skip_first_channel),
        "channel_auto_cycle": bool(channel_auto_cycle),
        "channel_cycle_state": FIXED_SAPO_CHANNEL_CYCLE_STATE,
        "window_query": "MT2Portugalia",
        "repeat_while_running": bool(repeat_while_running),
        "low_dps_adjust": bool(low_dps_adjust),
        "low_dps_threshold": str(low_dps),
        "low_dps_window_seconds": str(low_dps_window),
        "low_dps_max_cumulative_steps": str(low_dps_max_steps),
        "adjust_hold_seconds": str(adjust_hold),
        "out": "reports/dashboard_runs/fixed_sapo_channel_sweep.jsonl",
        "summary_out": "reports/dashboard_runs/fixed_sapo_channel_sweep_summary.json",
    }
    return {"script": "fixed_sapo_channel_sweep", "live": bool(live), "confirm_live": bool(live), "options": options}


def build_player_training_payload(*, duration: str = "180", interval: str = "0.25", capture_screenshots: bool = True, record_mouse: bool = True, window_query: str = "MT2Portugalia", state_json: str = MAIN_JSON_STATE) -> dict[str, Any]:
    """Build an observation-only recorder payload. It is intentionally dry-run/force_dry_run."""
    duration_s = max(1.0, float(duration))
    interval_s = max(0.05, float(interval))
    return {
        "script": "player_training_recorder",
        "live": False,
        "confirm_live": False,
        "options": {
            "duration": str(duration_s),
            "interval": str(interval_s),
            "capture_screenshots": bool(capture_screenshots),
            "screenshot_backend": "screen",
            "record_mouse": bool(record_mouse),
            "refresh_window_every": "1.0",
            "window_query": str(window_query or "MT2Portugalia"),
            "state_json": str(state_json),
        },
    }


def build_boss_farm_tracker_payload(*, duration: str = "3600", interval: str = "1.0", boss_name: str = "", spawn_interval_minutes: str = "30", channels: str = "8", wait_menu: str = "alterar personagem", loot_name: str = "Cofre do Chefe Orc", loot_vnum: str = "50070", state_json: str = MAIN_JSON_STATE) -> dict[str, Any]:
    """Build an observation-first boss farm tracker payload; no gameplay input until the farm is learned."""
    duration_s = max(1.0, float(duration))
    interval_s = max(0.05, float(interval))
    spawn_minutes = max(1.0, float(spawn_interval_minutes))
    channel_count = max(1, int(float(channels)))
    return {
        "script": "boss_farm_tracker",
        "live": False,
        "confirm_live": False,
        "options": {
            "duration": str(duration_s),
            "interval": str(interval_s),
            "state_json": str(state_json),
            "boss_name": str(boss_name or ""),
            "spawn_interval_minutes": str(spawn_minutes),
            "channels": str(channel_count),
            "wait_menu": str(wait_menu or "alterar personagem"),
            "loot_name": str(loot_name or "Cofre do Chefe Orc"),
            "loot_vnum": str(int(float(str(loot_vnum or "50070").strip()))),
        },
    }


def build_boss_live_control_payload(*, duration: str = "600", interval: str = "0.25", max_kills: str = "0", boss_name: str = "Chefe Orc", loot_name: str = "Cofre do Chefe Orc", loot_vnum: str = "50070", channel_rotate: bool = False, channel_click_points: str = LEARNED_CHANNEL_CLICK_POINTS, pickup_spam_count: str = "12", state_json: str = MAIN_JSON_STATE) -> dict[str, Any]:
    """Build the explicitly gated live Chefe Orc control payload."""
    options: dict[str, Any] = {
        "duration": str(max(1.0, float(duration))),
        "interval": str(max(0.05, float(interval))),
        "max_kills": str(max(0, int(float(max_kills or 0)))),
        "state_json": str(state_json),
        "boss_name": str(boss_name or "Chefe Orc"),
        "loot_name": str(loot_name or "Cofre do Chefe Orc"),
        "loot_vnum": str(int(float(str(loot_vnum or "50070").strip()))),
        "pickup_spam_count": str(max(1, int(float(pickup_spam_count or 12)))),
        "channel_rotate": bool(channel_rotate),
    }
    if channel_rotate:
        options["channel_click_points"] = str(channel_click_points or LEARNED_CHANNEL_CLICK_POINTS)
    return {"script": "chefe_orc_live_control", "live": True, "confirm_live": True, "options": options}


def build_farm_metrics_payload(*, duration: str = "300", interval: str = "0.5", history_window: str = "20", screenshot_hp_fallback: bool = False, state_json: str = MAIN_JSON_STATE) -> dict[str, Any]:
    """Build a read-only DPS/item farm history tracker payload."""
    return {
        "script": "farm_metrics_tracker",
        "live": False,
        "confirm_live": False,
        "options": {
            "state_json": str(state_json),
            "duration": str(max(0.0, float(duration))),
            "interval": str(max(0.05, float(interval))),
            "history_window": str(max(0.05, float(history_window))),
            "screenshot_hp_fallback": bool(screenshot_hp_fallback),
            "window_query": "MT2Portugalia",
            "out": "reports/dashboard_runs/farm_metrics_tracker.jsonl",
            "summary_out": "reports/dashboard_runs/farm_metrics_tracker_summary.json",
        },
    }


def format_farm_metrics_summary(summary: dict[str, Any]) -> str:
    if not summary:
        return "No farm metrics summary found yet."
    dps = summary.get("dps") if isinstance(summary.get("dps"), dict) else {}
    items = summary.get("items") if isinstance(summary.get("items"), dict) else {}
    lines = ["Farm metrics summary"]
    lines.append(f"run_id: {summary.get('run_id')}")
    lines.append(f"outcome: {summary.get('outcome')} samples={summary.get('samples')} duration={summary.get('duration_seconds')}s")
    lines.append(f"DPS last={dps.get('last')} best={dps.get('best')} avg={dps.get('average')} total_damage={dps.get('total_damage')} kills≈{dps.get('kills_estimated')}")
    lines.append(f"items farmed total: {items.get('farmed_total_count')} inventory_available={items.get('inventory_available')}")
    farmed = items.get("farmed") if isinstance(items.get("farmed"), list) else []
    if farmed:
        lines.append("Top farmed items:")
        for row in farmed[:12]:
            if isinstance(row, dict):
                lines.append(f"  {row.get('count')}x {row.get('name')} ({row.get('vnum') or row.get('key')})")
    else:
        lines.append("Top farmed items: none detected yet")
    loot_events = items.get("loot_events") if isinstance(items.get("loot_events"), list) else []
    if loot_events:
        lines.append("Loot/event items:")
        for row in loot_events[:12]:
            if isinstance(row, dict):
                lines.append(f"  {row.get('count')}x {row.get('name')} ({row.get('vnum') or row.get('key')})")
    note = items.get("note")
    if note:
        lines.append(f"note: {note}")
    if summary.get("log"):
        lines.append(f"log: {summary.get('log')}")
    return "\n".join(lines)


def build_reroll_recorder_payload(*, duration: str = "180", interval: str = "0.10", target_slot: str = "", target_vnum: str = "", state_json: str = MAIN_JSON_STATE) -> dict[str, Any]:
    """Build an observation-only item-reroll recorder payload; it never starts live input."""
    duration_s = max(1.0, float(duration))
    interval_s = max(0.02, float(interval))
    options: dict[str, Any] = {
        "duration": str(duration_s),
        "interval": str(interval_s),
        "state_json": str(state_json),
    }
    if str(target_slot or "").strip():
        options["target_slot"] = str(int(float(str(target_slot).strip())))
    if str(target_vnum or "").strip():
        options["target_vnum"] = str(int(float(str(target_vnum).strip())))
    return {"script": "reroll_recorder", "live": False, "confirm_live": False, "options": options}


def _format_numeric_for_cli(value: str, *, minimum: float) -> str:
    number = max(minimum, float(value))
    return f"{number:g}"


def build_player_training_manual_command(*, duration: str = "300", interval: str = "0.25", capture_screenshots: bool = True, record_mouse: bool = True, window_query: str = "MT2Portugalia") -> str:
    duration_s = _format_numeric_for_cli(duration, minimum=1.0)
    interval_s = _format_numeric_for_cli(interval, minimum=0.05)
    screenshot_flag = " --capture-screenshots" if capture_screenshots else ""
    mouse_flag = " --record-mouse" if record_mouse else " --no-record-mouse"
    return (
        "cd '/c/Hermes Unreal/metin2_research' && PYTHONPATH='src;.' "
        "python scripts/player_training_recorder.py "
        f"--duration {duration_s} --interval {interval_s}{screenshot_flag}{mouse_flag} "
        "--screenshot-backend screen --refresh-window-every 1.0 --screenshot-every 8 --out-dir reports/player_training_runs "
        "--run-id manual-$(date +%Y%m%d-%H%M%S) "
        f"--window-query {window_query or 'MT2Portugalia'}"
    )


def format_player_training_panel_text(*, duration: str = "300", interval: str = "0.25", capture_screenshots: bool = True) -> str:
    command = build_player_training_manual_command(duration=duration, interval=interval, capture_screenshots=capture_screenshots)
    return "\n".join(
        [
            "Player training recorder — observation-only; sends no keys/clicks.",
            "Records state, keyboard timing, mouse clicks/positions, and optional screenshots.",
            "Use this while you manually find, select, attack, destroy, pick up with Z, and optionally change channel.",
            "Outputs:",
            "  reports/player_training_runs/<run-id>/events.jsonl",
            "  reports/player_training_runs/<run-id>/summary.json",
            "  reports/player_training_runs/<run-id>/recommendations.md",
            "  reports/player_training_runs/<run-id>/screenshots/  (when enabled)",
            "Manual command:",
            command,
        ]
    )


def latest_player_training_summary(project_root: Path) -> dict[str, str]:
    base = Path(project_root) / "reports" / "player_training_runs"
    summaries = sorted(base.glob("*/summary.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not summaries:
        return {"run_dir": "", "text": "No player training summaries yet. Start a recorder run first."}
    summary_path = summaries[0]
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    lines = [
        f"Latest run: {summary_path.parent}",
        f"samples: {data.get('samples')}",
        f"target locks: {data.get('target_lock_count')}",
        f"destroy candidates: {data.get('destroy_candidates')}",
        f"avg target→attack seconds: {data.get('avg_seconds_target_to_attack')}",
        f"avg target→left-click seconds: {data.get('avg_seconds_target_to_left_click')}",
        f"avg destroy→pickup seconds: {data.get('avg_seconds_destroy_to_pickup')}",
        f"mouse clicks: {data.get('mouse_down_counts') or {}}",
        "recommendations:",
    ]
    for rec in data.get("recommendations") or []:
        lines.append(f"- {rec.get('topic')}: {rec.get('suggestion')}")
    if not data.get("recommendations"):
        lines.append("- Not enough evidence yet; record a full manual Metin kill loop.")
    return {"run_dir": str(summary_path.parent), "text": "\n".join(lines)}


def find_running_player_training_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((run for run in runs if run.get("script") == "player_training_recorder" and run.get("running")), None)


def _player_training_artifact_dir(project_root: Path, run_id: str | None) -> Path:
    return Path(project_root) / "reports" / "player_training_runs" / str(run_id or "")


def _player_training_progress_counts(run_dir: Path) -> dict[str, int]:
    events_path = run_dir / "events.jsonl"
    samples = 0
    key_events = 0
    mouse_events = 0
    lines = 0
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            lines += 1
            try:
                event = json.loads(line)
            except Exception:
                continue
            if event.get("type") == "sample":
                samples += 1
            if event.get("type") in {"key_down", "key_up"}:
                key_events += 1
            if event.get("type") in {"mouse_down", "mouse_up"}:
                mouse_events += 1
    screenshot_dir = run_dir / "screenshots"
    screenshots = len(list(screenshot_dir.glob("*.jpg"))) if screenshot_dir.exists() else 0
    return {"event_lines": lines, "samples": samples, "key_events": key_events, "mouse_events": mouse_events, "screenshots": screenshots}


def format_player_training_run_status(project_root: Path, runs: list[dict[str, Any]]) -> dict[str, Any]:
    running = find_running_player_training_run(runs)
    if running:
        run_id = running.get("run_id")
        run_dir = _player_training_artifact_dir(project_root, run_id)
        counts = _player_training_progress_counts(run_dir)
        text = "\n".join(
            [
                f"RECORDING  {run_id}",
                f"Output: {run_dir}",
                f"samples so far: {counts['samples']}",
                f"key events so far: {counts['key_events']}",
                f"mouse events so far: {counts['mouse_events']}",
                f"screenshots so far: {counts['screenshots']}",
                "Status: observation-only; sends no keys/clicks.",
                "Stop it from Runs / Stop selected or Emergency stop all.",
            ]
        )
        return {"recording": True, "run_id": str(run_id), "run_dir": str(run_dir), "text": text}
    latest = latest_player_training_summary(project_root)
    text = "Latest analysis\n" + latest["text"]
    return {"recording": False, "run_id": "", "run_dir": latest.get("run_dir", ""), "text": text}

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


def _fmt_bool(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _fmt_coord_pair(value: Any) -> str:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        x = _safe_int(value[0])
        y = _safe_int(value[1])
        return f"({x}, {y})"
    return "not proven"


def _target_hp_percent(target: dict[str, Any]) -> float | None:
    for key in ("hp_pct", "target_hp_pct", "hp_percent", "target_hp_percent"):
        try:
            if target.get(key) not in (None, ""):
                return max(0.0, min(100.0, float(target[key])))
        except Exception:
            continue
    now_hp = target.get("target_hp_now", target.get("hp"))
    max_hp = target.get("target_hp_max", target.get("max_hp"))
    try:
        if now_hp not in (None, "") and max_hp not in (None, "", 0, "0"):
            return max(0.0, min(100.0, float(now_hp) / float(max_hp) * 100.0))
    except Exception:
        pass
    return None


def format_truth_dashboard(
    state: dict[str, Any],
    *,
    bridge_report: dict[str, Any] | None = None,
    combat_config: dict[str, Any] | None = None,
    buff_config: dict[str, Any] | None = None,
    runs: list[dict[str, Any]] | None = None,
    now: float | None = None,
) -> str:
    """Return the at-a-glance honest automation status for the operator UI."""

    report = bridge_report if isinstance(bridge_report, dict) else state_bridge_report(state, now=now)
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    report_target = report.get("target") if isinstance(report.get("target"), dict) else {}
    combat_config = combat_config if isinstance(combat_config, dict) else {}
    buff_config = buff_config if isinstance(buff_config, dict) else {}
    runs = list(runs or [])

    hp_percent = _target_hp_percent(target) if target else None
    pixel_position = _fmt_coord_pair(target.get("pixel_position")) if target else "not proven"
    project_position = _fmt_coord_pair(target.get("project_position")) if target else "not proven"
    z_value = target.get("z", report_target.get("z")) if target else report_target.get("z")
    z_text = "not proven" if z_value in (None, "") else str(z_value)
    age = report.get("age_seconds")
    age_text = "unknown" if age is None else f"{float(age):.1f}s"
    target_name = target.get("name") or report_target.get("name") or "none"
    target_type = target.get("type", "unknown") if target else "unknown"
    target_vid = target.get("vid") or report_target.get("vid") or 0
    trusted = bool(report.get("has_trusted_target"))
    target_status = "PROVEN" if trusted else "BLOCKED"

    live_action = str(report.get("live_action") or "NEED_METIN_TARGET")
    dry_run_action = str(report.get("dry_run_action") or "NEED_METIN_TARGET")
    live_attack_locked = live_action == "ENGAGE_TARGET"
    safety_status = "LOCKED" if live_attack_locked else "BLOCKED"
    safety_reason = str(report.get("reason") or "no gate reason available")

    use_buff_config = bool(buff_config.get("use_buff_config"))
    enabled_buffs = [str(row.get("key") or "?").upper() for row in buff_config.get("buffs", []) if isinstance(row, dict) and row.get("enabled")]
    buff_run = find_running_buff_keeper_run(runs)
    buff_status = "UNPROVEN"
    if buff_run:
        buff_status = "ATTEMPTING"
    buff_line = "manual buffs observed; automation not proven"
    if buff_run:
        buff_line = f"managed run {buff_run.get('run_id')} active; visual icon proof still required"
    elif use_buff_config or enabled_buffs:
        buff_line = "config/timers present; no active dispatcher proof in this panel refresh"

    attack_dry_run = bool(combat_config.get("attack_nearby_mobs"))
    attack_run = find_running_attack_nearby_run(runs)
    attack_status = "DRY-RUN ONLY"
    attack_line = "live attack remains locked; use Practice dry-run for gate trace"
    if attack_run:
        attack_line = f"managed attack-nearby run {attack_run.get('run_id')} detected; verify mode and stop if unintended"
    elif attack_dry_run:
        attack_line = "attack-nearby option ON; live start is visually disabled here; dry-run only"

    hp_line = "HP% not proven" if hp_percent is None else f"HP {hp_percent:.1f}%"
    lines = [
        "Automation truth dashboard",
        f"[TARGET {target_status}] {target_name} VID:{target_vid} type:{target_type} alive={_fmt_bool(target.get('alive') if target else report_target.get('is_alive'))} trust={report.get('trust_level') or 'NONE'} age={age_text}",
        f"  pixel_position={pixel_position}  project_position={project_position}  z={z_text}  {hp_line}",
        f"[SAFETY {safety_status}] dry-run={dry_run_action} live={live_action}  reason={safety_reason}",
        f"[BUFFS {buff_status}] {buff_line}; enabled={','.join(enabled_buffs) if enabled_buffs else 'none'} use_config={'ON' if use_buff_config else 'OFF'}",
        f"[ATTACK {attack_status}] {attack_line}; predicate warning: Metin-only target trust, generic mobs need a separate hostile-mob gate",
        "Checklist: select target -> confirm dry-run gate -> prove buffs with top-left mini-icons -> no live attack.",
    ]
    return "\n".join(lines)


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


def format_control_config_summary(combat: dict[str, Any] | None, buffs: dict[str, Any] | None) -> str:
    combat = combat if isinstance(combat, dict) else {}
    buffs = buffs if isinstance(buffs, dict) else {}
    lines = [
        "Operator config",
        f"attack nearby mobs: {'ON' if combat.get('attack_nearby_mobs') else 'OFF'}",
        f"use buff config: {'ON' if buffs.get('use_buff_config') else 'OFF'}",
    ]
    thresholds = buffs.get("active_stat_thresholds") if isinstance(buffs.get("active_stat_thresholds"), dict) else {}
    lines.append(f"F1 active threshold: attack_power >= {float(thresholds.get('f1_attack_min_min', 200)):g}")
    lines.append(f"F2 active threshold: attack_speed >= {float(thresholds.get('f2_attack_speed_min', 130)):g}")
    for row in buffs.get("buffs") or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "?").lower()
        if row.get("enabled"):
            lines.append(f"{key} enabled every {float(row.get('interval_seconds', 0)):g}s pre-cast {float(row.get('pre_cast_seconds', 0)):g}s")
        else:
            lines.append(f"{key} disabled")
    return "\n".join(lines)


def build_control_config_payload(
    *,
    attack_nearby_mobs: bool,
    use_buff_config: bool,
    f1_enabled: bool,
    f1_interval: str,
    f1_pre_cast: str,
    f2_enabled: bool,
    f2_interval: str,
    f2_pre_cast: str,
    f1_active_attack_min_min: str = "200",
    f2_active_attack_speed_min: str = "130",
) -> tuple[dict[str, Any], dict[str, Any]]:
    f1_threshold = float(f1_active_attack_min_min)
    f2_threshold = float(f2_active_attack_speed_min)
    if f1_threshold <= 0 or f2_threshold <= 0:
        raise ValueError("F1/F2 active stat thresholds must be > 0")
    return {"attack_nearby_mobs": bool(attack_nearby_mobs)}, {
        "use_buff_config": bool(use_buff_config),
        "active_stat_thresholds": {
            "f1_attack_min_min": f1_threshold,
            "f2_attack_speed_min": f2_threshold,
        },
        "buffs": [
            {"key": "f1", "enabled": bool(f1_enabled), "interval_seconds": float(f1_interval), "pre_cast_seconds": float(f1_pre_cast)},
            {"key": "f2", "enabled": bool(f2_enabled), "interval_seconds": float(f2_interval), "pre_cast_seconds": float(f2_pre_cast)},
        ],
    }


def _reroll_slots(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("slot")): row for row in config.get("equip_slots", []) if isinstance(row, dict) and row.get("slot")}


def format_reroll_slot_table(config: dict[str, Any], slot: str) -> str:
    slots = _reroll_slots(config)
    row = slots.get(str(slot)) or next(iter(slots.values()), {"slot": slot, "label": slot, "possible_rolls": []})
    desired = {int(item.get("attr_type")): item for item in (config.get("desired_stats", {}).get(row.get("slot"), []) or []) if isinstance(item, dict) and item.get("attr_type") is not None}
    lines = [f"{row.get('label') or row.get('slot')} — possible rolls for selected equipment slot", "type | stat | observed values | desired"]
    for roll in row.get("possible_rolls") or []:
        attr_type = int(roll.get("attr_type"))
        values = ",".join(str(v) for v in roll.get("observed_values", [])) or f"{roll.get('observed_min', '?')}..{roll.get('observed_max', '?')}"
        want = desired.get(attr_type)
        want_text = f"desired priority {want['priority']} target {want['target_value']}" if want else ""
        lines.append(f"{attr_type} | {roll.get('name') or '?'} | {values} | {want_text}")
    if len(lines) == 2:
        lines.append("No observed roll table yet. Record rerolls for this slot or add entries to config/reroll.json.")
    return "\n".join(lines)


def format_reroll_config_summary(config: dict[str, Any]) -> str:
    lines = ["Reroll Items", "Observed roll tables are evidence-backed; desired stats are operator targets, not automation yet."]
    desired = config.get("desired_stats") if isinstance(config.get("desired_stats"), dict) else {}
    for slot, rows in desired.items():
        if not rows:
            continue
        parts = [f"#{int(row.get('priority', idx))} attr {int(row.get('attr_type'))} >= {int(row.get('target_value', 0))}" for idx, row in enumerate(rows, start=1) if isinstance(row, dict) and row.get("attr_type") is not None]
        if parts:
            lines.append(f"{slot}: " + ", ".join(parts))
    return "\n".join(lines)


def reroll_stat_options(config: dict[str, Any], slot: str) -> list[str]:
    slots = _reroll_slots(config)
    row = slots.get(str(slot), {})
    options = [""]
    for roll in row.get("possible_rolls") or []:
        if not isinstance(roll, dict) or roll.get("attr_type") is None:
            continue
        options.append(f"{int(roll['attr_type'])} - {roll.get('name') or 'unknown'}")
    return options


def _attr_type_from_selection(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    return int(text.split("-", 1)[0].strip())


def build_reroll_config_payload(slot: str, desired_rows_input: list[dict[str, Any]], current_config: dict[str, Any]) -> dict[str, Any]:
    """Build /api/reroll payload from four separate best-stat rows."""
    desired_rows: list[dict[str, int]] = []
    for idx, row in enumerate(desired_rows_input, start=1):
        if not isinstance(row, dict):
            continue
        attr_type = _attr_type_from_selection(row.get("attr_type"))
        target_value = str(row.get("target_value") or "").strip()
        if attr_type is None and not target_value:
            continue
        if attr_type is None or not target_value:
            raise ValueError("each best stat row needs both a selected stat and a target value")
        desired_rows.append({"attr_type": attr_type, "target_value": int(float(target_value)), "priority": idx})
    return {"equip_slots": current_config.get("equip_slots", []), "desired_stats": {str(slot): desired_rows}}


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


def allow_practice_live_without_metin(state: dict[str, Any], combat_config: dict[str, Any] | None) -> bool:
    return not state_has_metin_target(state) and bool((combat_config or {}).get("attack_nearby_mobs"))


def has_active_live_combat_run(runs: list[dict[str, Any]]) -> bool:
    return any(
        run.get("script") == "combat_metin_client_state"
        and run.get("mode") == "live"
        and bool(run.get("running"))
        and not is_running_buff_keeper_run(run)
        for run in runs
    )


def has_active_live_control_run(runs: list[dict[str, Any]]) -> bool:
    return any(
        run.get("script") in {"combat_metin_client_state", "move_to_metin_client_state"}
        and run.get("mode") == "live"
        and bool(run.get("running"))
        and not is_running_buff_keeper_run(run)
        for run in runs
    )


def _run_command_text(run: dict[str, Any]) -> str:
    command = run.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command or "")


def is_running_buff_keeper_run(run: dict[str, Any]) -> bool:
    return (
        run.get("script") == "combat_metin_client_state"
        and run.get("mode") == "live"
        and bool(run.get("running"))
        and "--buff-only" in _run_command_text(run)
    )


def is_running_attack_nearby_run(run: dict[str, Any]) -> bool:
    command = _run_command_text(run)
    return (
        run.get("script") == "combat_metin_client_state"
        and run.get("mode") == "live"
        and bool(run.get("running"))
        and "--buff-only" not in command
        and "--attack-nearby-mobs" in command
    )


def find_running_buff_keeper_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((run for run in runs if is_running_buff_keeper_run(run)), None)


def find_running_attack_nearby_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((run for run in runs if is_running_attack_nearby_run(run)), None)


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


class GlobalStopHotkey:
    """Polling Ctrl+Alt+S emergency stop-all watcher.

    `WH_KEYBOARD_LL` hooks can silently fail under mixed integrity / game focus.
    Polling `GetAsyncKeyState` is less elegant but more reliable for this local
    elevated control panel: it sees physical key state even while MT2 has focus.
    The callback is marshalled back onto Tk's thread via root.after.
    """

    VK_S = 0x53
    VK_CONTROL = 0x11
    VK_MENU = 0x12  # Alt

    def __init__(self, root: tk.Tk, callback, *, poll_seconds: float = 0.05):
        self.root = root
        self.callback = callback
        self.poll_seconds = max(0.02, float(poll_seconds))
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_fire = 0.0
        self._user32 = getattr(ctypes, "windll", None).user32 if hasattr(ctypes, "windll") else None
        self.started = False
        self.last_error: str | None = None

    def start(self) -> bool:
        if not self._user32:
            self.last_error = "user32 unavailable"
            return False
        if self.started:
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="metin2-ctrl-alt-s-stop-hotkey", daemon=True)
        self._thread.start()
        self.started = True
        return True

    def stop(self) -> None:
        self._stop_event.set()
        self.started = False

    def _down(self, vk: int) -> bool:
        return bool(self._user32.GetAsyncKeyState(vk) & 0x8000)

    def poll_once(self) -> bool:
        """Return True if Ctrl+Alt+S fired this poll. Exposed for tests."""
        try:
            if self._down(self.VK_CONTROL) and self._down(self.VK_MENU) and self._down(self.VK_S):
                now = time.monotonic()
                if now - self._last_fire > 0.75:
                    self._last_fire = now
                    self.root.after(0, self.callback)
                    return True
        except Exception as exc:
            self.last_error = str(exc)
        return False

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self.poll_seconds)


class ControlPanelApp:
    def __init__(self, root: tk.Tk, client: DashboardApiClient, *, project_root: Path, client_profile: str = "main", json_state: str | None = None, tsv_state: str | None = None):
        self.root = root
        self.client = client
        self.project_root = project_root
        profile = CLIENT_PROFILES.get(client_profile, CLIENT_PROFILES["main"])
        self.client_profile = client_profile if client_profile in CLIENT_PROFILES else "main"
        self.json_state_path = str(json_state or profile["json_state"])
        self.tsv_state_path = str(tsv_state or profile["tsv_state"])
        self.server_proc: subprocess.Popen | None = None
        self.scripts: list[dict[str, Any]] = []
        self.script_vars: dict[str, dict[str, tk.Variable]] = {}
        self.selected_script = tk.StringVar()
        self.selected_run = tk.StringVar()
        self.auto_refresh = tk.BooleanVar(value=True)
        self.follow_log_tail = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="ready")
        self.client_profile_var = tk.StringVar(value=f"Client: {self.client_profile} | {self.json_state_path}")
        self.login_main_user_var = tk.StringVar(value="yoshy")
        self.login_main_password_var = tk.StringVar(value="")
        self.login_buffer_user_var = tk.StringVar(value="nienna")
        self.login_buffer_password_var = tk.StringVar(value="")
        self.login_farmer_user_var = tk.StringVar(value="seyfer")
        self.login_farmer_password_var = tk.StringVar(value="")
        self.login_config_status_var = tk.StringVar(value="Login config not loaded")
        self.action_status = tk.StringVar(value="Last action: none")
        self.safety_gate_var = tk.StringVar(value="Live attack locked: dry-run only until explicitly approved and proven safe")
        self.ribbon_left_var = tk.StringVar(value="Connection: waiting for API")
        self.ribbon_mode_var = tk.StringVar(value="Mode: OBSERVE")
        self.ribbon_safety_var = tk.StringVar(value="LIVE ATTACK LOCKED")
        self.log_tail_status = tk.StringVar(value="Log tail: paused; select a run or click Refresh log tail")
        self.combat_state_var = tk.StringVar(value="State   idle")
        self.attack_nearby_mobs_var = tk.BooleanVar(value=False)
        self.use_buff_config_var = tk.BooleanVar(value=False)
        self.buff_start_mounted_var = tk.BooleanVar(value=True)
        self.channel_rotate_after_destroy_var = tk.BooleanVar(value=False)
        self.channel_click_points_var = tk.StringVar(value=LEARNED_CHANNEL_CLICK_POINTS)
        self.pickup_spam_count_var = tk.StringVar(value="12")
        self.f1_enabled_var = tk.BooleanVar(value=False)
        self.f2_enabled_var = tk.BooleanVar(value=False)
        self.f1_interval_var = tk.StringVar(value="35")
        self.f2_interval_var = tk.StringVar(value="35")
        self.f1_pre_cast_var = tk.StringVar(value="3")
        self.f2_pre_cast_var = tk.StringVar(value="3")
        self.f1_active_attack_min_min_var = tk.StringVar(value="200")
        self.f2_active_attack_speed_min_var = tk.StringVar(value="130")
        self.key_macro_interval_var = tk.StringVar(value="35")
        self.key_macro_presses_var = tk.StringVar(value="0")
        self.key_macro_hold_var = tk.StringVar(value="0.06")
        self.sapo_duration_var = tk.StringVar(value="30")
        self.sapo_until_destroy_duration_var = tk.StringVar(value="240")
        self.sapo_hp_stop_var = tk.StringVar(value="2500")
        self.sapo_min_distance_var = tk.StringVar(value="350")
        self.sapo_pickup_after_destroy_var = tk.BooleanVar(value=True)
        self.sapo_channel_switch_after_pickup_var = tk.BooleanVar(value=False)
        self.sapo_channel_auto_cycle_var = tk.BooleanVar(value=True)
        self.sapo_channel_index_var = tk.StringVar(value="1")
        self.sapo_skip_first_channel_var = tk.BooleanVar(value=True)
        self.sapo_pickup_seconds_var = tk.StringVar(value="1.6")
        self.sapo_sweep_channels_var = tk.StringVar(value="8")
        self.sapo_sweep_load_wait_var = tk.StringVar(value="8")
        self.sapo_sweep_low_dps_adjust_var = tk.BooleanVar(value=True)
        self.sapo_sweep_low_dps_threshold_var = tk.StringVar(value="0.2")
        self.sapo_sweep_max_steps_var = tk.StringVar(value="3")
        self.sapo_sweep_adjust_hold_var = tk.StringVar(value="0.18")
        self.sapo_status_var = tk.StringVar(value="Fixed Sapo: refresh preflight, keep buffs active, then Space-only test")
        self.player_training_duration_var = tk.StringVar(value="300")
        self.player_training_interval_var = tk.StringVar(value="0.25")
        self.player_training_screenshots_var = tk.BooleanVar(value=True)
        self.player_training_status_var = tk.StringVar(value="No player training run selected")
        self.boss_farm_duration_var = tk.StringVar(value="3600")
        self.boss_farm_interval_var = tk.StringVar(value="1.0")
        self.boss_farm_name_var = tk.StringVar(value="")
        self.boss_farm_spawn_interval_var = tk.StringVar(value="30")
        self.boss_farm_channels_var = tk.StringVar(value="8")
        self.boss_farm_wait_menu_var = tk.StringVar(value="alterar personagem")
        self.boss_farm_loot_name_var = tk.StringVar(value="Cofre do Chefe Orc")
        self.boss_farm_loot_vnum_var = tk.StringVar(value="50070")
        self.boss_live_max_kills_var = tk.StringVar(value="4")
        self.boss_live_channel_rotate_var = tk.BooleanVar(value=True)
        self.boss_farm_status_var = tk.StringVar(value="Boss farm tracker idle; record the next spawn to teach the loop")
        self.farm_metrics_duration_var = tk.StringVar(value="300")
        self.farm_metrics_interval_var = tk.StringVar(value="0.5")
        self.farm_metrics_window_var = tk.StringVar(value="20")
        self.farm_metrics_visual_hp_var = tk.BooleanVar(value=True)
        self.farm_metrics_status_var = tk.StringVar(value="Farm metrics idle; read-only DPS/item tracker")
        self.reroll_slot_var = tk.StringVar(value="weapon")
        self.reroll_desired_stat_vars = [tk.StringVar(value="") for _ in range(4)]
        self.reroll_desired_target_vars = [tk.StringVar(value="") for _ in range(4)]
        self.reroll_desired_combos: list[ttk.Combobox] = []
        self.reroll_status_var = tk.StringVar(value="Reroll config not loaded")
        self.reroll_recorder_duration_var = tk.StringVar(value="180")
        self.reroll_recorder_interval_var = tk.StringVar(value="0.10")
        self.reroll_recorder_slot_var = tk.StringVar(value="")
        self.reroll_recorder_vnum_var = tk.StringVar(value="")
        self.reroll_config: dict[str, Any] = {"equip_slots": [], "desired_stats": {}}
        self.hp_var = tk.DoubleVar(value=0.0)
        self.nearby_metins: list[dict[str, Any]] = []
        self._refreshing = False
        self._start_in_flight = False
        self._control_config_dirty = False
        self._loading_control_config = False
        self._login_config_dirty = False
        self._loading_login_config = False
        self._reroll_config_dirty = False
        self._loading_reroll_config = False
        self._install_control_config_traces()
        self._install_login_config_traces()
        self._install_reroll_config_traces()
        self._build_ui()
        self.global_stop_hotkey = GlobalStopHotkey(self.root, self.stop_all_now)
        hotkey_started = self.global_stop_hotkey.start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if hotkey_started:
            self.action_status.set("Last action: Ctrl+Alt+S global stop-all hotkey armed")
        else:
            self.action_status.set("Last action: Ctrl+Alt+S hotkey unavailable; use Emergency stop all")
        self.refresh_all()
        self._schedule_refresh()

    def _install_control_config_traces(self) -> None:
        for var in (
            self.attack_nearby_mobs_var,
            self.use_buff_config_var,
            self.f1_enabled_var,
            self.f2_enabled_var,
            self.f1_interval_var,
            self.f2_interval_var,
            self.f1_pre_cast_var,
            self.f2_pre_cast_var,
            self.f1_active_attack_min_min_var,
            self.f2_active_attack_speed_min_var,
        ):
            var.trace_add("write", self._mark_control_config_dirty)

    def _mark_control_config_dirty(self, *_args) -> None:
        if not getattr(self, "_loading_control_config", False):
            self._control_config_dirty = True

    def _install_login_config_traces(self) -> None:
        for var in (
            self.login_main_user_var,
            self.login_main_password_var,
            self.login_buffer_user_var,
            self.login_buffer_password_var,
            self.login_farmer_user_var,
            self.login_farmer_password_var,
        ):
            var.trace_add("write", self._mark_login_config_dirty)

    def _mark_login_config_dirty(self, *_args) -> None:
        if not getattr(self, "_loading_login_config", False):
            self._login_config_dirty = True

    def _install_reroll_config_traces(self) -> None:
        for var in [*self.reroll_desired_stat_vars, *self.reroll_desired_target_vars]:
            var.trace_add("write", self._mark_reroll_config_dirty)

    def _mark_reroll_config_dirty(self, *_args) -> None:
        if not getattr(self, "_loading_reroll_config", False):
            self._reroll_config_dirty = True

    def _build_ui(self) -> None:
        self.root.title("Metin2 Control Panel")
        self.root.geometry("1180x760")
        self.root.minsize(980, 620)
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
        ttk.Button(top, text="Buffs dry-run", command=self.buff_only_dry_run).pack(side="left", padx=3)
        ttk.Button(top, text="Record my play", command=self.start_player_training).pack(side="left", padx=3)
        ttk.Button(top, text="F1 once LIVE", command=lambda: self.press_key_once("f1")).pack(side="left", padx=3)
        ttk.Button(top, text="F2 once LIVE", command=lambda: self.press_key_once("f2")).pack(side="left", padx=3)
        self.buff_toggle_button = ttk.Button(top, text="Keep buffs active LIVE", command=self.toggle_buff_only_live)
        self.buff_toggle_button.pack(side="left", padx=3)
        self.attack_toggle_button = ttk.Button(top, text="Select/attack nearby LIVE", command=self.toggle_attack_nearby_live)
        self.attack_toggle_button.pack(side="left", padx=3)
        ttk.Checkbutton(top, text="auto refresh state/runs", variable=self.auto_refresh).pack(side="left", padx=12)
        ttk.Label(top, text="Ctrl+Alt+S = STOP ALL LIVE", foreground="#b22222", font=("Segoe UI", 9, "bold")).pack(side="left", padx=8)
        ttk.Label(top, textvariable=self.status).pack(side="right")
        ttk.Label(top, textvariable=self.client_profile_var, foreground="#334155").pack(side="right", padx=8)

        ribbon = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        ribbon.pack(fill="x")
        ttk.Label(ribbon, textvariable=self.ribbon_left_var, foreground="#2e8b57", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 14))
        ttk.Label(ribbon, textvariable=self.ribbon_mode_var, foreground="#d4a017", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 14))
        ttk.Label(ribbon, textvariable=self.ribbon_safety_var, foreground="#b22222", font=("Segoe UI", 9, "bold")).pack(side="left")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        state_tab = ttk.Frame(notebook, padding=6)
        keys_tab = ttk.Frame(notebook, padding=6)
        sapo_tab = ttk.Frame(notebook, padding=6)
        training_tab = ttk.Frame(notebook, padding=6)
        boss_farm_tab = ttk.Frame(notebook, padding=6)
        farm_metrics_tab = ttk.Frame(notebook, padding=6)
        reroll_tab = ttk.Frame(notebook, padding=6)
        right = ttk.Frame(notebook, padding=6)
        scripts_tab = ttk.Frame(notebook, padding=6)
        notebook.add(state_tab, text="State")
        notebook.add(keys_tab, text="F1/F2 + buffs")
        notebook.add(sapo_tab, text="Fixed Sapo")
        notebook.add(training_tab, text="Player training")
        notebook.add(boss_farm_tab, text="Boss farm")
        notebook.add(farm_metrics_tab, text="Farm metrics")
        notebook.add(reroll_tab, text="Reroll Items")
        notebook.add(right, text="Targets + runs")
        notebook.add(scripts_tab, text="Scripts")
        self.notebook = notebook

        ttk.Label(state_tab, text="Automation truth dashboard").pack(anchor="w")
        self.truth_text = tk.Text(state_tab, height=8, wrap="none", font=("Consolas", 10))
        self.truth_text.pack(fill="x", pady=(0, 4))
        ttk.Label(state_tab, textvariable=self.safety_gate_var, foreground="#b7791f", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(state_tab, text="Client state").pack(anchor="w")
        self.state_text = tk.Text(state_tab, height=6, wrap="none", font=("Consolas", 10))
        self.state_text.pack(fill="x", pady=(0, 4))
        self.hp_bar = ttk.Progressbar(state_tab, variable=self.hp_var, maximum=100)
        self.hp_bar.pack(fill="x", pady=(0, 8))
        ttk.Label(state_tab, textvariable=self.action_status).pack(anchor="w", pady=(0, 8))
        ttk.Label(state_tab, text="State bridge trust").pack(anchor="w")
        self.bridge_trust_text = tk.Text(state_tab, height=5, wrap="none", font=("Consolas", 9))
        self.bridge_trust_text.pack(fill="x", pady=(0, 8))
        ttk.Label(state_tab, text="Combat").pack(anchor="w")
        self.combat_state_label = tk.Label(state_tab, textvariable=self.combat_state_var, anchor="w", fg="black", font=("Consolas", 10, "bold"))
        self.combat_state_label.pack(fill="x")
        self.combat_text = tk.Text(state_tab, height=8, wrap="none", font=("Consolas", 9))
        self.combat_text.pack(fill="both", expand=True, pady=(0, 8))

        config_frame = ttk.LabelFrame(keys_tab, text="Buff keeper + mob control", padding=8)
        config_frame.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(config_frame, text="attack nearby mobs", variable=self.attack_nearby_mobs_var).grid(row=0, column=0, sticky="w", columnspan=4)
        ttk.Checkbutton(config_frame, text="use buff config", variable=self.use_buff_config_var).grid(row=1, column=0, sticky="w", columnspan=2)
        ttk.Checkbutton(config_frame, text="starting on horse (use Ctrl+G to dismount/buff/remount)", variable=self.buff_start_mounted_var).grid(row=1, column=2, sticky="w", columnspan=4)
        ttk.Checkbutton(config_frame, text="F1", variable=self.f1_enabled_var).grid(row=2, column=0, sticky="w")
        ttk.Label(config_frame, text="interval").grid(row=2, column=1, sticky="e")
        ttk.Entry(config_frame, textvariable=self.f1_interval_var, width=6).grid(row=2, column=2, sticky="w")
        ttk.Label(config_frame, text="pre-cast").grid(row=2, column=3, sticky="e")
        ttk.Entry(config_frame, textvariable=self.f1_pre_cast_var, width=6).grid(row=2, column=4, sticky="w")
        ttk.Checkbutton(config_frame, text="F2", variable=self.f2_enabled_var).grid(row=3, column=0, sticky="w")
        ttk.Label(config_frame, text="interval").grid(row=3, column=1, sticky="e")
        ttk.Entry(config_frame, textvariable=self.f2_interval_var, width=6).grid(row=3, column=2, sticky="w")
        ttk.Label(config_frame, text="pre-cast").grid(row=3, column=3, sticky="e")
        ttk.Entry(config_frame, textvariable=self.f2_pre_cast_var, width=6).grid(row=3, column=4, sticky="w")
        ttk.Label(config_frame, text="F1 active if attack_power ≥").grid(row=4, column=0, columnspan=2, sticky="e", pady=(4, 0))
        ttk.Entry(config_frame, textvariable=self.f1_active_attack_min_min_var, width=7).grid(row=4, column=2, sticky="w", pady=(4, 0))
        ttk.Label(config_frame, text="F2 active if attack_speed ≥").grid(row=4, column=3, columnspan=2, sticky="e", pady=(4, 0))
        ttk.Entry(config_frame, textvariable=self.f2_active_attack_speed_min_var, width=7).grid(row=4, column=5, sticky="w", pady=(4, 0))
        ttk.Button(config_frame, text="Save buff/mob config", command=self.save_control_config).grid(row=5, column=0, sticky="w", pady=(6, 0), columnspan=2)
        self.control_config_label = ttk.Label(config_frame, text="config not loaded", justify="left")
        self.control_config_label.grid(row=5, column=2, columnspan=4, sticky="w", padx=8, pady=(6, 0))
        login_frame = ttk.LabelFrame(keys_tab, text="Login accounts", padding=8)
        login_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(login_frame, text="Main user").grid(row=0, column=0, sticky="e", padx=3)
        ttk.Entry(login_frame, textvariable=self.login_main_user_var, width=16).grid(row=0, column=1, sticky="w", padx=3)
        ttk.Label(login_frame, text="new password").grid(row=0, column=2, sticky="e", padx=3)
        ttk.Entry(login_frame, textvariable=self.login_main_password_var, width=18, show="*").grid(row=0, column=3, sticky="w", padx=3)
        ttk.Button(login_frame, text="Open MAIN + login", command=lambda: self.open_login_profile("main")).grid(row=0, column=4, sticky="w", padx=6)
        ttk.Label(login_frame, text="D:/Games/MT2Portugalia/app", foreground="#666").grid(row=0, column=5, sticky="w", padx=3)
        ttk.Label(login_frame, text="Buffer user").grid(row=1, column=0, sticky="e", padx=3)
        ttk.Entry(login_frame, textvariable=self.login_buffer_user_var, width=16).grid(row=1, column=1, sticky="w", padx=3)
        ttk.Label(login_frame, text="new password").grid(row=1, column=2, sticky="e", padx=3)
        ttk.Entry(login_frame, textvariable=self.login_buffer_password_var, width=18, show="*").grid(row=1, column=3, sticky="w", padx=3)
        ttk.Button(login_frame, text="Open BUFFER + login", command=lambda: self.open_login_profile("buffer")).grid(row=1, column=4, sticky="w", padx=6)
        ttk.Label(login_frame, text="D:/Games/MT2PortugaliaBuffer/app", foreground="#666").grid(row=1, column=5, sticky="w", padx=3)
        ttk.Label(login_frame, text="Farmer user").grid(row=2, column=0, sticky="e", padx=3)
        ttk.Entry(login_frame, textvariable=self.login_farmer_user_var, width=16).grid(row=2, column=1, sticky="w", padx=3)
        ttk.Label(login_frame, text="new password").grid(row=2, column=2, sticky="e", padx=3)
        ttk.Entry(login_frame, textvariable=self.login_farmer_password_var, width=18, show="*").grid(row=2, column=3, sticky="w", padx=3)
        ttk.Button(login_frame, text="Open FARMER + login", command=lambda: self.open_login_profile("farmer")).grid(row=2, column=4, sticky="w", padx=6)
        ttk.Label(login_frame, text="D:/Games/MT2PortugaliaFarmer/app", foreground="#666").grid(row=2, column=5, sticky="w", padx=3)
        ttk.Button(login_frame, text="Save login config", command=self.save_login_config).grid(row=3, column=0, sticky="w", pady=(6, 0), padx=3)
        ttk.Label(login_frame, textvariable=self.login_config_status_var, justify="left").grid(row=3, column=1, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Checkbutton(config_frame, text="after destroy: spam Z, press X, click next channel", variable=self.channel_rotate_after_destroy_var).grid(row=6, column=0, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Label(config_frame, text="channel points x,y;x,y").grid(row=7, column=0, columnspan=2, sticky="e")
        ttk.Entry(config_frame, textvariable=self.channel_click_points_var, width=28).grid(row=7, column=2, columnspan=2, sticky="w")
        ttk.Label(config_frame, text="Z count").grid(row=7, column=4, sticky="e")
        ttk.Entry(config_frame, textvariable=self.pickup_spam_count_var, width=5).grid(row=7, column=5, sticky="w")

        key_frame = ttk.LabelFrame(keys_tab, text="Direct key test + timed macro", padding=8)
        key_frame.pack(fill="x", pady=(0, 8))
        ttk.Button(key_frame, text="Press F1 once LIVE", command=lambda: self.press_key_once("f1")).grid(row=0, column=0, sticky="w", padx=3, pady=2)
        ttk.Button(key_frame, text="Press F2 once LIVE", command=lambda: self.press_key_once("f2")).grid(row=0, column=1, sticky="w", padx=3, pady=2)
        ttk.Label(key_frame, text="macro interval seconds").grid(row=1, column=0, sticky="e", padx=3)
        ttk.Entry(key_frame, textvariable=self.key_macro_interval_var, width=7).grid(row=1, column=1, sticky="w", padx=3)
        ttk.Label(key_frame, text="presses (0 = until stopped)").grid(row=1, column=2, sticky="e", padx=3)
        ttk.Entry(key_frame, textvariable=self.key_macro_presses_var, width=7).grid(row=1, column=3, sticky="w", padx=3)
        ttk.Label(key_frame, text="hold").grid(row=1, column=4, sticky="e", padx=3)
        ttk.Entry(key_frame, textvariable=self.key_macro_hold_var, width=7).grid(row=1, column=5, sticky="w", padx=3)
        ttk.Button(key_frame, text="Start F1 timed macro LIVE", command=lambda: self.start_key_macro("f1")).grid(row=2, column=0, columnspan=2, sticky="w", padx=3, pady=2)
        ttk.Button(key_frame, text="Start F2 timed macro LIVE", command=lambda: self.start_key_macro("f2")).grid(row=2, column=2, columnspan=2, sticky="w", padx=3, pady=2)
        ttk.Label(key_frame, text="Uses managed runs; Stop selected/Emergency stop all stops repeating macros. Live key sender relaunches elevated; approve UAC if Windows asks.").grid(row=3, column=0, columnspan=6, sticky="w", padx=3)

        sapo_frame = ttk.LabelFrame(sapo_tab, text="Fixed-spawn Sapo test bench (no click / no move / no potion 1)", padding=8)
        sapo_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(sapo_frame, text="Workflow: Start/verify Keep Buffs Active LIVE, stand next to fixed Sapo spawn, then run Space-only tests. Channel rotation stays separate until current-channel proof passes.").grid(row=0, column=0, columnspan=7, sticky="w", pady=(0, 6))
        ttk.Button(sapo_frame, text="Refresh gate details", command=self.refresh_sapo_gate_details).grid(row=1, column=0, sticky="w", padx=3, pady=2)
        ttk.Button(sapo_frame, text="Start/Stop Keep Buffs LIVE", command=self.toggle_buff_only_live).grid(row=1, column=1, sticky="w", padx=3, pady=2)
        ttk.Button(sapo_frame, text="Dry-run preflight", command=self.sapo_preflight_dry_run).grid(row=1, column=2, sticky="w", padx=3, pady=2)
        ttk.Button(sapo_frame, text="Reset channel cycle", command=self.reset_sapo_channel_cycle).grid(row=1, column=3, sticky="w", padx=3, pady=2)
        ttk.Button(sapo_frame, text="Last sweep summary", command=self.refresh_sapo_sweep_summary).grid(row=1, column=4, sticky="w", padx=3, pady=2)
        ttk.Button(sapo_frame, text="Dry-run sweep preview", command=self.sapo_sweep_dry_run_preview).grid(row=1, column=5, columnspan=2, sticky="w", padx=3, pady=2)
        ttk.Label(sapo_frame, text="30s test seconds").grid(row=2, column=0, sticky="e", padx=3)
        ttk.Entry(sapo_frame, textvariable=self.sapo_duration_var, width=7).grid(row=2, column=1, sticky="w", padx=3)
        ttk.Button(sapo_frame, text="Run 30s Space test LIVE", command=self.sapo_space_short_live).grid(row=2, column=2, sticky="w", padx=3, pady=2)
        ttk.Label(sapo_frame, text="until-destroy max seconds").grid(row=3, column=0, sticky="e", padx=3)
        ttk.Entry(sapo_frame, textvariable=self.sapo_until_destroy_duration_var, width=7).grid(row=3, column=1, sticky="w", padx=3)
        ttk.Button(sapo_frame, text="Run until Sapo destroyed LIVE", command=self.sapo_space_until_destroy_live).grid(row=3, column=2, sticky="w", padx=3, pady=2)
        ttk.Button(sapo_frame, text="Full: destroy + pickup + switch LIVE", command=self.sapo_full_destroy_pickup_switch_live).grid(row=3, column=3, columnspan=3, sticky="w", padx=3, pady=2)
        ttk.Label(sapo_frame, text="HP stop ≤").grid(row=4, column=0, sticky="e", padx=3)
        ttk.Entry(sapo_frame, textvariable=self.sapo_hp_stop_var, width=7).grid(row=4, column=1, sticky="w", padx=3)
        ttk.Label(sapo_frame, text="max distance from spawn").grid(row=4, column=2, sticky="e", padx=3)
        ttk.Entry(sapo_frame, textvariable=self.sapo_min_distance_var, width=7).grid(row=4, column=3, sticky="w", padx=3)
        ttk.Checkbutton(sapo_frame, text="pickup Z after destroy", variable=self.sapo_pickup_after_destroy_var).grid(row=4, column=4, sticky="w", padx=3)
        ttk.Label(sapo_frame, text="pickup seconds").grid(row=4, column=5, sticky="e", padx=3)
        ttk.Entry(sapo_frame, textvariable=self.sapo_pickup_seconds_var, width=5).grid(row=4, column=6, sticky="w", padx=3)
        ttk.Checkbutton(sapo_frame, text="then switch channel", variable=self.sapo_channel_switch_after_pickup_var).grid(row=5, column=0, columnspan=2, sticky="w", padx=3, pady=(4, 0))
        ttk.Checkbutton(sapo_frame, text="auto next channel", variable=self.sapo_channel_auto_cycle_var).grid(row=5, column=1, columnspan=2, sticky="w", padx=3, pady=(4, 0))
        ttk.Checkbutton(sapo_frame, text="skip index 0", variable=self.sapo_skip_first_channel_var).grid(row=5, column=2, sticky="w", padx=3, pady=(4, 0))
        ttk.Label(sapo_frame, text="first raw row").grid(row=5, column=3, sticky="e", padx=3, pady=(4, 0))
        ttk.Entry(sapo_frame, textvariable=self.sapo_channel_index_var, width=5).grid(row=5, column=4, sticky="w", padx=3, pady=(4, 0))
        ttk.Button(sapo_frame, text="Test pickup + channel switch LIVE", command=self.sapo_pickup_channel_switch_live).grid(row=5, column=5, columnspan=2, sticky="w", padx=3, pady=(4, 0))
        ttk.Label(sapo_frame, text="sweep channels").grid(row=6, column=0, sticky="e", padx=3, pady=(4, 0))
        ttk.Entry(sapo_frame, textvariable=self.sapo_sweep_channels_var, width=5).grid(row=6, column=1, sticky="w", padx=3, pady=(4, 0))
        ttk.Label(sapo_frame, text="load wait s").grid(row=6, column=2, sticky="e", padx=3, pady=(4, 0))
        ttk.Entry(sapo_frame, textvariable=self.sapo_sweep_load_wait_var, width=5).grid(row=6, column=3, sticky="w", padx=3, pady=(4, 0))
        ttk.Button(sapo_frame, text="Sweep all channels LIVE", command=self.sapo_sweep_all_channels_live).grid(row=6, column=4, sticky="w", padx=3, pady=(4, 0))
        ttk.Button(sapo_frame, text="Keep sweep running LIVE", command=self.toggle_sapo_sweep_keep_running).grid(row=6, column=5, columnspan=2, sticky="w", padx=3, pady=(4, 0))
        ttk.Checkbutton(sapo_frame, text="low DPS: nudge WASD", variable=self.sapo_sweep_low_dps_adjust_var).grid(row=7, column=0, columnspan=2, sticky="w", padx=3, pady=(4, 0))
        ttk.Label(sapo_frame, text="low DPS <").grid(row=7, column=2, sticky="e", padx=3, pady=(4, 0))
        ttk.Entry(sapo_frame, textvariable=self.sapo_sweep_low_dps_threshold_var, width=5).grid(row=7, column=3, sticky="w", padx=3, pady=(4, 0))
        ttk.Label(sapo_frame, text="max kept steps").grid(row=7, column=4, sticky="e", padx=3, pady=(4, 0))
        ttk.Entry(sapo_frame, textvariable=self.sapo_sweep_max_steps_var, width=4).grid(row=7, column=5, sticky="w", padx=3, pady=(4, 0))
        ttk.Label(sapo_frame, text="WASD hold").grid(row=7, column=6, sticky="e", padx=3, pady=(4, 0))
        ttk.Entry(sapo_frame, textvariable=self.sapo_sweep_adjust_hold_var, width=5).grid(row=7, column=7, sticky="w", padx=3, pady=(4, 0))
        ttk.Label(sapo_frame, text="Channel switch: raw rows are CH1=0 through CH8=7. With skip index 0 on, row CH1/current is filtered out, so first raw row 1 starts on CH2. Reset cycle before changing this.", foreground="#b7791f").grid(row=8, column=0, columnspan=7, sticky="w", pady=(6, 0))
        ttk.Label(sapo_frame, text="Locked defaults: no combat click, no potion 1; only the low-DPS toggle allows tiny WASD centering nudges during Space attack. Use the Buff tab for F1/F2=156/302 timing.", foreground="#b7791f").grid(row=9, column=0, columnspan=7, sticky="w", pady=(3, 0))
        ttk.Label(sapo_frame, textvariable=self.sapo_status_var, justify="left").grid(row=10, column=0, columnspan=6, sticky="w", pady=(6, 0))
        self.sapo_preflight_text = tk.Text(sapo_tab, height=14, wrap="none", font=("Consolas", 9))
        self.sapo_preflight_text.pack(fill="both", expand=True)

        training_frame = ttk.LabelFrame(keys_tab, text="Player training recorder (observation only)", padding=8)
        training_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(training_frame, text="duration seconds").grid(row=0, column=0, sticky="e", padx=3)
        ttk.Entry(training_frame, textvariable=self.player_training_duration_var, width=7).grid(row=0, column=1, sticky="w", padx=3)
        ttk.Label(training_frame, text="interval").grid(row=0, column=2, sticky="e", padx=3)
        ttk.Entry(training_frame, textvariable=self.player_training_interval_var, width=7).grid(row=0, column=3, sticky="w", padx=3)
        ttk.Checkbutton(training_frame, text="capture screenshots", variable=self.player_training_screenshots_var).grid(row=0, column=4, sticky="w", padx=3)
        ttk.Button(training_frame, text="Start player training recorder", command=self.start_player_training).grid(row=1, column=0, columnspan=2, sticky="w", padx=3, pady=2)
        ttk.Button(training_frame, text="Refresh latest summary", command=self.refresh_player_training_summary).grid(row=1, column=2, sticky="w", padx=3, pady=2)
        ttk.Label(training_frame, text="Records state + your key timing + optional game screenshots. Sends no keys/clicks.").grid(row=1, column=3, columnspan=3, sticky="w", padx=3)

        training_controls = ttk.LabelFrame(training_tab, text="Player training recorder", padding=8)
        training_controls.pack(fill="x", pady=(0, 8))
        ttk.Label(training_controls, text="duration seconds").grid(row=0, column=0, sticky="e", padx=3)
        ttk.Entry(training_controls, textvariable=self.player_training_duration_var, width=8).grid(row=0, column=1, sticky="w", padx=3)
        ttk.Label(training_controls, text="interval").grid(row=0, column=2, sticky="e", padx=3)
        ttk.Entry(training_controls, textvariable=self.player_training_interval_var, width=8).grid(row=0, column=3, sticky="w", padx=3)
        ttk.Checkbutton(training_controls, text="capture screenshots", variable=self.player_training_screenshots_var).grid(row=0, column=4, sticky="w", padx=3)
        ttk.Button(training_controls, text="Start recording", command=self.start_player_training).grid(row=1, column=0, sticky="w", padx=3, pady=3)
        ttk.Button(training_controls, text="Refresh/analyze latest", command=self.refresh_player_training_summary).grid(row=1, column=1, columnspan=2, sticky="w", padx=3, pady=3)
        ttk.Label(training_controls, textvariable=self.player_training_status_var).grid(row=1, column=3, columnspan=3, sticky="w", padx=3)
        self.player_training_info_text = tk.Text(training_tab, height=11, wrap="word", font=("Consolas", 9))
        self.player_training_info_text.pack(fill="x", pady=(0, 8))
        self.player_training_info_text.insert("1.0", format_player_training_panel_text(duration=self.player_training_duration_var.get(), interval=self.player_training_interval_var.get(), capture_screenshots=self.player_training_screenshots_var.get()))
        self.player_training_info_text.configure(state="disabled")
        ttk.Label(training_tab, text="Latest recording summary / recommendations").pack(anchor="w")
        self.player_training_summary_text = tk.Text(training_tab, height=14, wrap="word", font=("Consolas", 9))
        self.player_training_summary_text.pack(fill="both", expand=True)
        self.refresh_player_training_summary()

        boss_controls = ttk.LabelFrame(boss_farm_tab, text="Boss farm tracker", padding=8)
        boss_controls.pack(fill="x", pady=(0, 8))
        ttk.Label(boss_controls, text="boss name optional").grid(row=0, column=0, sticky="e", padx=3)
        ttk.Entry(boss_controls, textvariable=self.boss_farm_name_var, width=24).grid(row=0, column=1, sticky="w", padx=3)
        ttk.Label(boss_controls, text="spawn minutes").grid(row=0, column=2, sticky="e", padx=3)
        ttk.Entry(boss_controls, textvariable=self.boss_farm_spawn_interval_var, width=7).grid(row=0, column=3, sticky="w", padx=3)
        ttk.Label(boss_controls, text="channels").grid(row=0, column=4, sticky="e", padx=3)
        ttk.Entry(boss_controls, textvariable=self.boss_farm_channels_var, width=5).grid(row=0, column=5, sticky="w", padx=3)
        ttk.Label(boss_controls, text="duration").grid(row=1, column=0, sticky="e", padx=3)
        ttk.Entry(boss_controls, textvariable=self.boss_farm_duration_var, width=8).grid(row=1, column=1, sticky="w", padx=3)
        ttk.Label(boss_controls, text="sample interval").grid(row=1, column=2, sticky="e", padx=3)
        ttk.Entry(boss_controls, textvariable=self.boss_farm_interval_var, width=7).grid(row=1, column=3, sticky="w", padx=3)
        ttk.Label(boss_controls, text="wait menu").grid(row=1, column=4, sticky="e", padx=3)
        ttk.Entry(boss_controls, textvariable=self.boss_farm_wait_menu_var, width=20).grid(row=1, column=5, sticky="w", padx=3)
        ttk.Label(boss_controls, text="loot name").grid(row=2, column=0, sticky="e", padx=3)
        ttk.Entry(boss_controls, textvariable=self.boss_farm_loot_name_var, width=24).grid(row=2, column=1, sticky="w", padx=3)
        ttk.Label(boss_controls, text="loot vnum").grid(row=2, column=2, sticky="e", padx=3)
        ttk.Entry(boss_controls, textvariable=self.boss_farm_loot_vnum_var, width=8).grid(row=2, column=3, sticky="w", padx=3)
        ttk.Button(boss_controls, text="Start boss farm tracker", command=self.start_boss_farm_tracker).grid(row=3, column=0, columnspan=2, sticky="w", padx=3, pady=(6, 0))
        ttk.Label(boss_controls, textvariable=self.boss_farm_status_var).grid(row=3, column=2, columnspan=4, sticky="w", padx=3, pady=(6, 0))
        ttk.Label(boss_controls, text="live max kills").grid(row=4, column=0, sticky="e", padx=3, pady=(6, 0))
        ttk.Entry(boss_controls, textvariable=self.boss_live_max_kills_var, width=7).grid(row=4, column=1, sticky="w", padx=3, pady=(6, 0))
        ttk.Checkbutton(boss_controls, text="after kill: Z pickup + X channel rotate", variable=self.boss_live_channel_rotate_var).grid(row=4, column=2, columnspan=3, sticky="w", padx=3, pady=(6, 0))
        ttk.Button(boss_controls, text="Start gated Chefe Orc LIVE", command=self.start_boss_live_control).grid(row=5, column=0, columnspan=3, sticky="w", padx=3, pady=(6, 0))
        ttk.Label(boss_farm_tab, text="Observation-first scaffold plus gated LIVE control: live only attacks selected Chefe Orc targets, confirms HP-zero/loot delta, then optionally picks up and rotates channel.", foreground="#666").pack(anchor="w", pady=(0, 8))
        self.boss_farm_text = tk.Text(boss_farm_tab, height=18, wrap="word", font=("Consolas", 9))
        self.boss_farm_text.pack(fill="both", expand=True)
        self.boss_farm_text.insert("1.0", "Boss farm plan:\n1. Start tracker before the next spawn.\n2. Record your manual boss kill/channel/menu-wait loop with Player training recorder.\n3. After learning, this panel will show boss_kills_confirmed, channels_cleared, loot_pickups_observed, and next spawn ETA.\n4. Waiting menu target: alterar personagem.\n")
        self.boss_farm_text.configure(state="disabled")

        farm_metrics_controls = ttk.LabelFrame(farm_metrics_tab, text="DPS meter + item farm history (read-only)", padding=8)
        farm_metrics_controls.pack(fill="x", pady=(0, 8))
        ttk.Label(farm_metrics_controls, text="duration").grid(row=0, column=0, sticky="e", padx=3)
        ttk.Entry(farm_metrics_controls, textvariable=self.farm_metrics_duration_var, width=8).grid(row=0, column=1, sticky="w", padx=3)
        ttk.Label(farm_metrics_controls, text="interval").grid(row=0, column=2, sticky="e", padx=3)
        ttk.Entry(farm_metrics_controls, textvariable=self.farm_metrics_interval_var, width=7).grid(row=0, column=3, sticky="w", padx=3)
        ttk.Label(farm_metrics_controls, text="DPS window s").grid(row=0, column=4, sticky="e", padx=3)
        ttk.Entry(farm_metrics_controls, textvariable=self.farm_metrics_window_var, width=7).grid(row=0, column=5, sticky="w", padx=3)
        ttk.Checkbutton(farm_metrics_controls, text="visual HP fallback", variable=self.farm_metrics_visual_hp_var).grid(row=0, column=6, sticky="w", padx=6)
        ttk.Button(farm_metrics_controls, text="Start DPS/item tracker", command=self.start_farm_metrics_tracker).grid(row=1, column=0, columnspan=2, sticky="w", padx=3, pady=(6, 0))
        ttk.Button(farm_metrics_controls, text="Refresh metrics summary", command=self.refresh_farm_metrics_summary).grid(row=1, column=2, columnspan=2, sticky="w", padx=3, pady=(6, 0))
        ttk.Label(farm_metrics_controls, textvariable=self.farm_metrics_status_var).grid(row=1, column=4, columnspan=3, sticky="w", padx=3, pady=(6, 0))
        ttk.Label(farm_metrics_tab, text="DPS uses target HP/HP% deltas from hermes_state.json. Item history uses inventory/loot deltas when those fields are exported by the client logger.", foreground="#666").pack(anchor="w", pady=(0, 8))
        self.farm_metrics_text = tk.Text(farm_metrics_tab, height=24, wrap="word", font=("Consolas", 9))
        self.farm_metrics_text.pack(fill="both", expand=True)
        self.refresh_farm_metrics_summary()

        reroll_controls = ttk.LabelFrame(reroll_tab, text="Reroll Items", padding=8)
        reroll_controls.pack(fill="x", pady=(0, 8))
        self.reroll_slot_notebook = ttk.Notebook(reroll_controls)
        self.reroll_slot_notebook.grid(row=0, column=0, columnspan=5, sticky="ew", padx=3, pady=(0, 6))
        self.reroll_slot_notebook.bind("<<NotebookTabChanged>>", self.on_reroll_slot_tab_changed)
        self.reroll_slot_tab_slots: dict[str, str] = {}
        ttk.Label(reroll_controls, text="Pick the four stats this slot's reroll must land, in priority order.").grid(row=1, column=0, columnspan=5, sticky="w", padx=3)
        self.reroll_desired_combos = []
        for idx, label in enumerate(("Best stat #1", "Best stat #2", "Best stat #3", "Best stat #4")):
            row_no = idx + 2
            ttk.Label(reroll_controls, text=label).grid(row=row_no, column=0, sticky="e", padx=3, pady=2)
            combo = ttk.Combobox(reroll_controls, textvariable=self.reroll_desired_stat_vars[idx], state="readonly", width=34)
            combo.grid(row=row_no, column=1, columnspan=2, sticky="w", padx=3, pady=2)
            self.reroll_desired_combos.append(combo)
            ttk.Label(reroll_controls, text="target ≥").grid(row=row_no, column=3, sticky="e", padx=3, pady=2)
            ttk.Entry(reroll_controls, textvariable=self.reroll_desired_target_vars[idx], width=8).grid(row=row_no, column=4, sticky="w", padx=3, pady=2)
        ttk.Button(reroll_controls, text="Save four best stats for slot", command=self.save_reroll_config).grid(row=6, column=0, sticky="w", padx=3, pady=(6, 0))
        ttk.Label(reroll_controls, textvariable=self.reroll_status_var, justify="left").grid(row=6, column=1, columnspan=4, sticky="w", pady=(6, 0))
        reroll_recorder = ttk.LabelFrame(reroll_tab, text="Dedicated reroll recorder", padding=8)
        reroll_recorder.pack(fill="x", pady=(0, 8))
        ttk.Label(reroll_recorder, text="duration").grid(row=0, column=0, sticky="e", padx=3)
        ttk.Entry(reroll_recorder, textvariable=self.reroll_recorder_duration_var, width=7).grid(row=0, column=1, sticky="w", padx=3)
        ttk.Label(reroll_recorder, text="interval").grid(row=0, column=2, sticky="e", padx=3)
        ttk.Entry(reroll_recorder, textvariable=self.reroll_recorder_interval_var, width=7).grid(row=0, column=3, sticky="w", padx=3)
        ttk.Label(reroll_recorder, text="target slot optional").grid(row=0, column=4, sticky="e", padx=3)
        ttk.Entry(reroll_recorder, textvariable=self.reroll_recorder_slot_var, width=7).grid(row=0, column=5, sticky="w", padx=3)
        ttk.Label(reroll_recorder, text="vnum optional").grid(row=0, column=6, sticky="e", padx=3)
        ttk.Entry(reroll_recorder, textvariable=self.reroll_recorder_vnum_var, width=8).grid(row=0, column=7, sticky="w", padx=3)
        ttk.Button(reroll_recorder, text="Start reroll recorder", command=self.start_reroll_recorder).grid(row=0, column=8, sticky="w", padx=6)
        ttk.Label(reroll_recorder, text="Observation-only: samples item attr changes into reports/reroll_recordings; sends no keys/clicks.", foreground="#666").grid(row=1, column=0, columnspan=9, sticky="w", padx=3, pady=(4, 0))
        ttk.Label(reroll_tab, text="Possible rolls for each equip slot").pack(anchor="w")
        self.reroll_table_text = tk.Text(reroll_tab, height=18, wrap="none", font=("Consolas", 9))
        self.reroll_table_text.pack(fill="both", expand=True, pady=(0, 8))
        ttk.Label(reroll_tab, text="Final objective: teach Hermes the observed roll tables, then choose target best stats per slot. This tab manages targets only; it does not click/reroll yet.", foreground="#666").pack(anchor="w")

        ttk.Label(scripts_tab, text="Script").pack(anchor="w")
        self.script_combo = ttk.Combobox(scripts_tab, textvariable=self.selected_script, state="readonly")
        self.script_combo.pack(fill="x")
        self.script_combo.bind("<<ComboboxSelected>>", lambda _e: self.render_selected_script())

        self.options_frame = ttk.LabelFrame(scripts_tab, text="Options", padding=8)
        self.options_frame.pack(fill="both", expand=True, pady=8)

        actions = ttk.Frame(scripts_tab)
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
        ttk.Button(run_buttons, text="Archive all completed", command=self.archive_all).pack(side="left", padx=3)
        ttk.Button(run_buttons, text="Emergency stop all", command=self.stop_all).pack(side="left", padx=3)
        ttk.Button(run_buttons, text="Refresh log tail", command=self.refresh_selected_log).pack(side="left", padx=3)
        ttk.Checkbutton(run_buttons, text="follow log tail", variable=self.follow_log_tail).pack(side="left", padx=3)
        ttk.Button(run_buttons, text="Open full log", command=self.open_selected_log).pack(side="left", padx=3)

        ttk.Label(right, textvariable=self.log_tail_status).pack(anchor="w")
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
            build_server_cmd(port=self.client.base_url.rsplit(":", 1)[-1], json_state=self.json_state_path, tsv_state=self.tsv_state_path),
            cwd=str(self.project_root),
            stdout=server_log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        self.set_status(f"starting local API for {self.client_profile}: {self.json_state_path}")
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
            combat_config = self.client.get("/api/combat")
            buff_config = self.client.get("/api/buffs")
            login_config = self.client.get("/api/login_config")
            reroll_config = self.client.get("/api/reroll")
            self.root.after(0, lambda: self.apply_refresh(state, scripts, runs, combat_config, buff_config, login_config, reroll_config))
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda message=message: self.set_status(f"API error: {message}"))
        finally:
            self.root.after(0, lambda: setattr(self, "_refreshing", False))

    def apply_refresh(self, state: dict[str, Any], scripts: list[dict[str, Any]], runs: list[dict[str, Any]], combat_config: dict[str, Any] | None = None, buff_config: dict[str, Any] | None = None, login_config: dict[str, Any] | None = None, reroll_config: dict[str, Any] | None = None) -> None:
        self.render_truth_dashboard(state, runs, combat_config or {}, buff_config or {})
        self.render_state_card(state)
        self.render_sapo_preflight(state, runs, buff_config or {})
        self.render_state_bridge(state)
        self.render_nearby_metins()
        self.render_combat_status(state, runs)
        if combat_config is not None or buff_config is not None:
            self.render_control_config(combat_config or {}, buff_config or {})
        if login_config is not None:
            self.render_login_config(login_config)
        if reroll_config is not None:
            self.render_reroll_config(reroll_config)
        self.scripts = scripts
        names = [script["name"] for script in scripts]
        self.script_combo["values"] = names
        if not self.selected_script.get() and names:
            self.selected_script.set(names[0])
            self.render_selected_script()
        self.render_runs(runs)
        self.set_status("connected")

    def render_truth_dashboard(self, state: dict[str, Any], runs: list[dict[str, Any]], combat_config: dict[str, Any], buff_config: dict[str, Any]) -> None:
        if not hasattr(self, "truth_text"):
            return
        report = state_bridge_report(state)
        text = format_truth_dashboard(state, bridge_report=report, combat_config=combat_config, buff_config=buff_config, runs=runs)
        self.truth_text.delete("1.0", "end")
        self.truth_text.insert("end", text)
        player = state.get("player") if isinstance(state.get("player"), dict) else {}
        age = report.get("age_seconds")
        age_text = "unknown" if age is None else f"{float(age):.1f}s"
        mode = "DRY-RUN" if report.get("dry_run_action") == "DRY_RUN_IDLE" else "TARGETING" if state.get("target") else "OBSERVE"
        self.ribbon_left_var.set(f"API age {age_text} | {player.get('name') or state.get('player_name') or 'unknown'} | {state.get('map') or state.get('map_name') or 'unknown map'}")
        self.ribbon_mode_var.set(f"Mode: {mode} / {report.get('current_action') or 'IDLE'}")
        self.ribbon_safety_var.set("LIVE ATTACK ENABLED — explicit confirmation required")
        self.safety_gate_var.set(str(report.get("reason") or "Live attack locked; dry-run only"))

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

    def render_sapo_preflight(self, state: dict[str, Any], runs: list[dict[str, Any]], buff_config: dict[str, Any]) -> None:
        widget = getattr(self, "sapo_preflight_text", None)
        if widget is None:
            return
        player = state.get("player") if isinstance(state.get("player"), dict) else {}
        probes = []
        for key in ("named_metin_probe", "nearby_entities"):
            value = state.get(key)
            if isinstance(value, list):
                probes.extend(row for row in value if isinstance(row, dict))
        sapo = next((row for row in probes if str(row.get("name") or "").lower() == "sapo de pedra"), {})
        age = None
        try:
            age = max(0.0, time.time() - float(state.get("_file_mtime"))) if state.get("_file_mtime") else None
        except Exception:
            age = None
        dist = "unknown"
        pos = sapo.get("pixel_position")
        try:
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                dx = float(player.get("x")) - float(pos[0])
                dy = float(player.get("y")) - float(pos[1])
                dist = f"{(dx * dx + dy * dy) ** 0.5:.1f}"
        except Exception:
            dist = "unknown"
        buff_run = find_running_buff_keeper_run(runs)
        by_key = {str(row.get("key") or "").lower(): row for row in buff_config.get("buffs", []) if isinstance(row, dict)} if isinstance(buff_config, dict) else {}
        lines = [
            "FIXED SAPO TEST BENCH PREFLIGHT",
            "=================================",
            f"State age: {'unknown' if age is None else f'{age:.2f}s'}  ({'PASS' if age is not None and age <= 2 else 'WARN/STALE'})",
            f"Player: {player.get('name') or state.get('player_name') or '?'} HP {player.get('hp', '?')}/{player.get('max_hp', '?')} pos ({player.get('x', '?')}, {player.get('y', '?')})",
            f"Sapo probe: alive={sapo.get('alive')} vid={sapo.get('vid')} pos={sapo.get('pixel_position')} distance={dist}",
            f"Selected target: {(state.get('target') if isinstance(state.get('target'), dict) else {}).get('name') or 'none'}",
            f"Buff keeper: {'RUNNING ' + str(buff_run.get('run_id')) if buff_run else 'not running'}",
            f"F1 duration: {by_key.get('f1', {}).get('interval_seconds', 156)}s | F2 duration: {by_key.get('f2', {}).get('interval_seconds', 302)}s",
            "Locked live behavior: no click, no movement, no potion 1; Space only after preflight gates pass.",
            "Recommended order: Keep buffs LIVE -> Dry-run preflight -> 30s Space test -> Until-destroy proof -> pickup/channel later.",
        ]
        self.sapo_status_var.set(f"Sapo preflight: age={'unknown' if age is None else f'{age:.1f}s'} | buff keeper={'ON' if buff_run else 'OFF'} | sapo_alive={sapo.get('alive')}")
        widget.delete("1.0", "end")
        widget.insert("end", "\n".join(lines))

    def render_state_bridge(self, state: dict[str, Any]) -> None:
        if not hasattr(self, "bridge_trust_text"):
            return
        report = state_bridge_report(state)
        self.bridge_trust_text.delete("1.0", "end")
        self.bridge_trust_text.insert("end", format_state_bridge_report(report))

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

    def render_control_config(self, combat_config: dict[str, Any], buff_config: dict[str, Any]) -> None:
        if getattr(self, "_control_config_dirty", False):
            if hasattr(self, "control_config_label"):
                saved = format_control_config_summary(combat_config, buff_config)
                self.control_config_label.configure(text="Operator config editing; not overwritten by auto-refresh\nSaved config:\n" + saved)
            return
        self._loading_control_config = True
        try:
            self.attack_nearby_mobs_var.set(bool(combat_config.get("attack_nearby_mobs")))
            self.use_buff_config_var.set(bool(buff_config.get("use_buff_config")))
            by_key = {str(row.get("key") or "").lower(): row for row in buff_config.get("buffs", []) if isinstance(row, dict)}
            thresholds = buff_config.get("active_stat_thresholds") if isinstance(buff_config.get("active_stat_thresholds"), dict) else {}
            self.f1_active_attack_min_min_var.set(str(float(thresholds.get("f1_attack_min_min", 200.0))).rstrip("0").rstrip("."))
            self.f2_active_attack_speed_min_var.set(str(float(thresholds.get("f2_attack_speed_min", 130.0))).rstrip("0").rstrip("."))
            for key, enabled_var, interval_var, pre_cast_var in (
                ("f1", self.f1_enabled_var, self.f1_interval_var, self.f1_pre_cast_var),
                ("f2", self.f2_enabled_var, self.f2_interval_var, self.f2_pre_cast_var),
            ):
                row = by_key.get(key, {})
                enabled_var.set(bool(row.get("enabled")))
                interval_var.set(str(float(row.get("interval_seconds", 35.0))).rstrip("0").rstrip("."))
                pre_cast_var.set(str(float(row.get("pre_cast_seconds", 3.0))).rstrip("0").rstrip("."))
        finally:
            self._loading_control_config = False
        if hasattr(self, "control_config_label"):
            self.control_config_label.configure(text=format_control_config_summary(combat_config, buff_config))


    def render_login_config(self, login_config: dict[str, Any]) -> None:
        profiles = login_config.get("profiles") if isinstance(login_config.get("profiles"), dict) else {}
        main = profiles.get("main", {}) if isinstance(profiles.get("main"), dict) else {}
        buffer = profiles.get("buffer", {}) if isinstance(profiles.get("buffer"), dict) else {}
        farmer = profiles.get("farmer", {}) if isinstance(profiles.get("farmer"), dict) else {}
        def pw_status(row: dict[str, Any]) -> str:
            if row.get("password_updated"):
                return "password saved now"
            return "password saved" if row.get("has_password") else "no password saved"
        saved_status = "Main: {main_user} ({main_pw}) | Buffer: {buffer_user} ({buffer_pw}) | Farmer: {farmer_user} ({farmer_pw})".format(
            main_user=main.get("username") or "?",
            main_pw=pw_status(main),
            buffer_user=buffer.get("username") or "?",
            buffer_pw=pw_status(buffer),
            farmer_user=farmer.get("username") or "?",
            farmer_pw=pw_status(farmer),
        )

        if getattr(self, "_login_config_dirty", False):
            self.login_config_status_var.set("Login config editing; auto-refresh will not overwrite fields. Saved config: " + saved_status)
            return
        self._loading_login_config = True
        try:
            self.login_main_user_var.set(str(main.get("username") or "yoshy"))
            self.login_buffer_user_var.set(str(buffer.get("username") or "nienna"))
            self.login_farmer_user_var.set(str(farmer.get("username") or "seyfer"))
            # Never populate passwords from API/config. Leave password boxes empty unless the operator is typing.
            self.login_main_password_var.set("")
            self.login_buffer_password_var.set("")
            self.login_farmer_password_var.set("")
        finally:
            self._loading_login_config = False
        self.login_config_status_var.set(saved_status)

    def build_reroll_slot_tabs(self, slots: list[dict[str, Any]]) -> None:
        notebook = getattr(self, "reroll_slot_notebook", None)
        if notebook is None:
            return
        slot_keys = [str(row.get("slot")) for row in slots if isinstance(row, dict) and row.get("slot")]
        existing_keys = list(getattr(self, "reroll_slot_tab_slots", {}).values())
        if existing_keys == slot_keys:
            return
        for tab_id in notebook.tabs():
            notebook.forget(tab_id)
        self.reroll_slot_tab_slots = {}
        for row in slots:
            if not isinstance(row, dict) or not row.get("slot"):
                continue
            slot = str(row.get("slot"))
            label = str(row.get("label") or slot).replace("&", "+")
            frame = ttk.Frame(notebook, padding=2)
            ttk.Label(frame, text=f"{label}: choose four best stats below").pack(anchor="w")
            notebook.add(frame, text=label)
            tab_id = notebook.tabs()[-1]
            self.reroll_slot_tab_slots[str(tab_id)] = slot
        if slot_keys:
            wanted = self.reroll_slot_var.get() if self.reroll_slot_var.get() in slot_keys else slot_keys[0]
            for tab_id, slot in self.reroll_slot_tab_slots.items():
                if slot == wanted:
                    notebook.select(tab_id)
                    break
            self.reroll_slot_var.set(wanted)

    def on_reroll_slot_tab_changed(self, _event=None) -> None:
        notebook = getattr(self, "reroll_slot_notebook", None)
        if notebook is None:
            return
        selected = str(notebook.select())
        slot = getattr(self, "reroll_slot_tab_slots", {}).get(selected)
        if slot and slot != self.reroll_slot_var.get():
            self.reroll_slot_var.set(slot)
            self.render_reroll_config(self.reroll_config)

    def render_reroll_config(self, reroll_config: dict[str, Any]) -> None:
        self.reroll_config = reroll_config if isinstance(reroll_config, dict) else {"equip_slots": [], "desired_stats": {}}
        slot_rows = [row for row in self.reroll_config.get("equip_slots", []) if isinstance(row, dict) and row.get("slot")]
        slots = [str(row.get("slot")) for row in slot_rows]
        self.build_reroll_slot_tabs(slot_rows)
        slot = self.reroll_slot_var.get() or (str(slots[0]) if slots else "weapon")
        options = reroll_stat_options(self.reroll_config, slot)
        if hasattr(self, "reroll_desired_combos"):
            for combo in self.reroll_desired_combos:
                combo["values"] = options
        if getattr(self, "_reroll_config_dirty", False):
            self.reroll_status_var.set("Reroll config editing; not overwritten by auto-refresh\nSaved config:\n" + format_reroll_config_summary(self.reroll_config))
            return
        self._loading_reroll_config = True
        try:
            if slots and self.reroll_slot_var.get() not in slots:
                self.reroll_slot_var.set(str(slots[0]))
                slot = self.reroll_slot_var.get()
                options = reroll_stat_options(self.reroll_config, slot)
                if hasattr(self, "reroll_desired_combos"):
                    for combo in self.reroll_desired_combos:
                        combo["values"] = options
            desired = self.reroll_config.get("desired_stats", {}).get(slot, []) if isinstance(self.reroll_config.get("desired_stats"), dict) else []
            by_attr = {int(row.get("attr_type")): row for row in desired if isinstance(row, dict) and row.get("attr_type") is not None}
            ordered = sorted(by_attr.values(), key=lambda row: int(row.get("priority", 99)))
            for idx in range(4):
                row = ordered[idx] if idx < len(ordered) else None
                if row:
                    attr_type = int(row.get("attr_type"))
                    option = next((value for value in options if value.startswith(f"{attr_type} -") or value == str(attr_type)), str(attr_type))
                    self.reroll_desired_stat_vars[idx].set(option)
                    self.reroll_desired_target_vars[idx].set(str(row.get("target_value", "")))
                else:
                    self.reroll_desired_stat_vars[idx].set("")
                    self.reroll_desired_target_vars[idx].set("")
        finally:
            self._loading_reroll_config = False
        if hasattr(self, "reroll_table_text"):
            self.reroll_table_text.delete("1.0", "end")
            self.reroll_table_text.insert("end", format_reroll_slot_table(self.reroll_config, slot))
        self.reroll_status_var.set(format_reroll_config_summary(self.reroll_config))

    def save_reroll_config(self) -> None:
        try:
            desired_rows = [
                {"attr_type": stat_var.get(), "target_value": target_var.get()}
                for stat_var, target_var in zip(self.reroll_desired_stat_vars, self.reroll_desired_target_vars)
            ]
            payload = build_reroll_config_payload(self.reroll_slot_var.get(), desired_rows, self.reroll_config)
        except Exception as exc:
            messagebox.showerror("Save reroll config", f"Invalid desired stats: {exc}")
            return
        def worker() -> None:
            try:
                saved = self.client.post("/api/reroll", payload)
                def apply_saved() -> None:
                    self._reroll_config_dirty = False
                    self.render_reroll_config(saved)
                    self.action_status.set("Last action: saved reroll desired stats")
                self.root.after(0, apply_saved)
            except Exception as exc:
                message = str(exc)
                self.root.after(0, lambda message=message: messagebox.showerror("Save reroll config", message))
        threading.Thread(target=worker, daemon=True).start()


    def save_login_config(self) -> None:
        payload = {
            "active_profile": self.client_profile,
            "profiles": {
                "main": {"username": self.login_main_user_var.get(), "app_dir": MAIN_JSON_STATE.rsplit("/", 1)[0], "password": self.login_main_password_var.get()},
                "buffer": {"username": self.login_buffer_user_var.get(), "app_dir": BUFFER_JSON_STATE.rsplit("/", 1)[0], "password": self.login_buffer_password_var.get()},
                "farmer": {"username": self.login_farmer_user_var.get(), "app_dir": FARMER_JSON_STATE.rsplit("/", 1)[0], "password": self.login_farmer_password_var.get()},
            },
        }
        def worker() -> None:
            try:
                saved = self.client.post("/api/login_config", payload)
                def apply_saved() -> None:
                    self._loading_login_config = True
                    try:
                        self.login_main_password_var.set("")
                        self.login_buffer_password_var.set("")
                        self.login_farmer_password_var.set("")
                    finally:
                        self._loading_login_config = False
                    self._login_config_dirty = False
                    self.render_login_config(saved)
                    self.action_status.set("Last action: saved login config / Credential Manager passwords")
                self.root.after(0, apply_saved)
            except Exception as exc:
                message = str(exc)
                self.root.after(0, lambda message=message: messagebox.showerror("Save login config", message))
        threading.Thread(target=worker, daemon=True).start()

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
        self._last_runs = list(runs)
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
        if hasattr(self, "buff_toggle_button"):
            if find_running_buff_keeper_run(runs):
                self.buff_toggle_button.configure(text="Stop buffs active LIVE")
            else:
                self.buff_toggle_button.configure(text="Keep buffs active LIVE")
        if hasattr(self, "attack_toggle_button"):
            if find_running_attack_nearby_run(runs):
                self.attack_toggle_button.configure(text="Stop select/attack LIVE", state="normal")
            else:
                self.attack_toggle_button.configure(text="Select/attack nearby LIVE (operator enabled)", state="normal")
        if selected in self._runs_by_id:
            if getattr(self, "follow_log_tail", None) is not None and self.follow_log_tail.get():
                self.show_run_log(self._runs_by_id[selected])
            elif hasattr(self, "log_tail_status"):
                self.log_tail_status.set(f"Log tail: paused for {selected}; click Refresh log tail or enable follow")
        if hasattr(self, "player_training_status_var"):
            self.refresh_player_training_summary()

    def get_selected_script(self) -> dict[str, Any] | None:
        name = self.selected_script.get()
        for script in self.scripts:
            if script["name"] == name:
                return script
        return None

    def save_control_config(self) -> None:
        try:
            combat_payload, buff_payload = build_control_config_payload(
                attack_nearby_mobs=self.attack_nearby_mobs_var.get(),
                use_buff_config=self.use_buff_config_var.get(),
                f1_enabled=self.f1_enabled_var.get(),
                f1_interval=self.f1_interval_var.get(),
                f1_pre_cast=self.f1_pre_cast_var.get(),
                f2_enabled=self.f2_enabled_var.get(),
                f2_interval=self.f2_interval_var.get(),
                f2_pre_cast=self.f2_pre_cast_var.get(),
                f1_active_attack_min_min=self.f1_active_attack_min_min_var.get(),
                f2_active_attack_speed_min=self.f2_active_attack_speed_min_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("Save buff/mob config", f"Invalid config: {exc}")
            return
        def worker() -> None:
            try:
                combat = self.client.post("/api/combat", combat_payload)
                buffs = self.client.post("/api/buffs", buff_payload)
                def apply_saved() -> None:
                    self._control_config_dirty = False
                    self.render_control_config(combat, buffs)
                self.root.after(0, apply_saved)
                self.root.after(0, lambda: self.action_status.set("Last action: saved buff/mob config"))
            except Exception as exc:
                message = str(exc)
                self.root.after(0, lambda message=message: messagebox.showerror("Save buff/mob config", message))
        threading.Thread(target=worker, daemon=True).start()

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

    def login_profile_values(self, profile: str) -> tuple[str, str]:
        if profile == "farmer":
            return self.login_farmer_user_var.get().strip(), FARMER_JSON_STATE.rsplit("/", 1)[0]
        if profile == "buffer":
            return self.login_buffer_user_var.get().strip(), BUFFER_JSON_STATE.rsplit("/", 1)[0]
        return self.login_main_user_var.get().strip(), MAIN_JSON_STATE.rsplit("/", 1)[0]

    def open_login_profile(self, profile: str) -> None:
        username, app_dir = self.login_profile_values(profile)
        label = "FARMER" if profile == "farmer" else ("BUFFER" if profile == "buffer" else "MAIN")
        if not username:
            messagebox.showerror("Open game + login", f"{label} username is empty. Fill it in and save login config first.")
            return
        if messagebox.askokcancel("Open game + login", f"Open/attach {label} Metin client from {app_dir}, login as {username}, and press Começar/Enter? This keeps the other client open. Credentials stay in Windows Credential Manager."):
            self._post_after_api_ready(build_quick_start_payload("open_login_game", login_username=username, login_app_dir=app_dir))

    def open_login_game(self) -> None:
        self.open_login_profile(self.client_profile if self.client_profile in {"buffer", "farmer"} else "main")

    def integrate_client(self) -> None:
        if messagebox.askokcancel("Patch/integrate client", "Patch the local MT2Portugalia client state logger into loose game.py and pack/root? Close the game first if it is running."):
            self._post_after_api_ready(build_quick_start_payload("integrate_client"))

    def practice_dry_run(self) -> None:
        self._post_after_api_ready(build_quick_start_payload("practice_dry_run"))

    def buff_only_dry_run(self) -> None:
        self._post_after_api_ready(build_quick_start_payload("buff_only_dry_run"))

    def refresh_player_training_summary(self) -> None:
        try:
            status = format_player_training_run_status(self.project_root, getattr(self, "_last_runs", []))
            text = status["text"]
            if status.get("recording"):
                self.player_training_status_var.set(f"RECORDING: {status.get('run_id')}")
            else:
                run_dir = status.get("run_dir") or "none"
                self.player_training_status_var.set(f"Latest analysis: {run_dir}")
        except Exception as exc:
            text = f"Could not read player training status/summary: {exc}"
            self.player_training_status_var.set("Player training: error")
        widget = getattr(self, "player_training_summary_text", None)
        if widget is not None:
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.configure(state="normal")

    def refresh_player_training_info_text(self) -> None:
        widget = getattr(self, "player_training_info_text", None)
        if widget is None:
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", format_player_training_panel_text(duration=self.player_training_duration_var.get(), interval=self.player_training_interval_var.get(), capture_screenshots=self.player_training_screenshots_var.get()))
        widget.configure(state="disabled")

    def start_boss_farm_tracker(self) -> None:
        try:
            payload = build_boss_farm_tracker_payload(
                duration=self.boss_farm_duration_var.get(),
                interval=self.boss_farm_interval_var.get(),
                boss_name=self.boss_farm_name_var.get(),
                spawn_interval_minutes=self.boss_farm_spawn_interval_var.get(),
                channels=self.boss_farm_channels_var.get(),
                wait_menu=self.boss_farm_wait_menu_var.get(),
                loot_name=self.boss_farm_loot_name_var.get(),
                loot_vnum=self.boss_farm_loot_vnum_var.get(),
                state_json=self.json_state_path,
            )
        except Exception as exc:
            messagebox.showerror("Boss farm tracker", f"Invalid boss farm settings: {exc}")
            return
        if messagebox.askokcancel(
            "Boss farm tracker",
            "Start observation-first boss farm tracker? It tracks spawn windows/farm counters scaffold and sends no keys/clicks. Record the next manual spawn loop so Hermes can learn before any live farming is enabled.",
        ):
            self._post_after_api_ready(payload)
            self.boss_farm_status_var.set("Tracker requested; watch Runs for boss_farm_tracker")

    def start_boss_live_control(self) -> None:
        try:
            payload = build_boss_live_control_payload(
                duration=self.boss_farm_duration_var.get(),
                interval="0.25",
                max_kills=self.boss_live_max_kills_var.get(),
                boss_name="Chefe Orc",
                loot_name=self.boss_farm_loot_name_var.get(),
                loot_vnum=self.boss_farm_loot_vnum_var.get(),
                channel_rotate=self.boss_live_channel_rotate_var.get(),
                channel_click_points=LEARNED_CHANNEL_CLICK_POINTS,
                pickup_spam_count="12",
                state_json=self.json_state_path,
            )
        except Exception as exc:
            messagebox.showerror("Gated Chefe Orc LIVE", f"Invalid live boss-control settings: {exc}")
            return
        if messagebox.askokcancel(
            "Gated Chefe Orc LIVE",
            "Start gated LIVE input control? It can press Tab/Space/Z/X and click the learned channel rows. It will only attack when the selected target name contains Chefe Orc, uses fresh JSON state, and can be stopped from Runs / Emergency stop all.",
        ):
            self._post_after_api_ready(payload)
            self.boss_farm_status_var.set("Gated Chefe Orc LIVE requested; watch Runs for chefe_orc_live_control")

    def start_farm_metrics_tracker(self) -> None:
        try:
            payload = build_farm_metrics_payload(
                duration=self.farm_metrics_duration_var.get(),
                interval=self.farm_metrics_interval_var.get(),
                history_window=self.farm_metrics_window_var.get(),
                screenshot_hp_fallback=self.farm_metrics_visual_hp_var.get(),
                state_json=self.json_state_path,
            )
        except Exception as exc:
            messagebox.showerror("Farm metrics tracker", f"Invalid farm metrics settings: {exc}")
            return
        self._post_after_api_ready(payload)
        self.farm_metrics_status_var.set("DPS/item tracker requested; refresh summary after it samples")

    def refresh_farm_metrics_summary(self) -> None:
        path = self.project_root / "reports/dashboard_runs/farm_metrics_tracker_summary.json"
        try:
            summary = json.loads(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else {}
            text = format_farm_metrics_summary(summary)
            if summary:
                dps = summary.get("dps") if isinstance(summary.get("dps"), dict) else {}
                items = summary.get("items") if isinstance(summary.get("items"), dict) else {}
                self.farm_metrics_status_var.set(f"DPS last={dps.get('last')} best={dps.get('best')} | items={items.get('farmed_total_count')}")
        except Exception as exc:
            text = f"Could not read farm metrics summary: {exc}"
            self.farm_metrics_status_var.set("Farm metrics: error")
        widget = getattr(self, "farm_metrics_text", None)
        if widget is not None:
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.configure(state="normal")

    def start_reroll_recorder(self) -> None:
        try:
            payload = build_reroll_recorder_payload(
                duration=self.reroll_recorder_duration_var.get(),
                interval=self.reroll_recorder_interval_var.get(),
                target_slot=self.reroll_recorder_slot_var.get(),
                target_vnum=self.reroll_recorder_vnum_var.get(),
                state_json=self.json_state_path,
            )
        except Exception as exc:
            messagebox.showerror("Reroll recorder", f"Invalid reroll recorder settings: {exc}")
            return
        if messagebox.askokcancel(
            "Reroll recorder",
            "Start observation-only reroll recorder? It samples item/equipment attr changes into reports/reroll_recordings and sends no keys/clicks. Keep manually rerolling while it records.",
        ):
            self._post_after_api_ready(payload)
            self.action_status.set("Last action: reroll recorder requested; watch Runs for reroll_recorder")

    def start_player_training(self) -> None:
        self.refresh_player_training_info_text()
        try:
            payload = build_player_training_payload(
                duration=self.player_training_duration_var.get(),
                interval=self.player_training_interval_var.get(),
                capture_screenshots=self.player_training_screenshots_var.get(),
                state_json=self.json_state_path,
            )
        except Exception as exc:
            messagebox.showerror("Player training recorder", f"Invalid recorder settings: {exc}")
            return
        if messagebox.askokcancel(
            "Player training recorder",
            "Start observation-only player training recorder? It records client state, your key timing, and optional game screenshots. It sends no keys/clicks and can be stopped from Runs / Emergency stop all.",
        ):
            self._post_after_api_ready(payload)
            self.player_training_status_var.set("Recording requested; watch Runs for player_training_recorder")

    def _toggle_existing_run_or_none(self, *, finder, label: str) -> bool:
        try:
            runs = self.client.get("/api/runs")
        except Exception as exc:
            messagebox.showerror(label, f"Cannot read running scripts: {exc}")
            return True
        run = finder(runs)
        if run:
            rid = run.get("run_id")
            if rid:
                self._post_async("/api/stop", {"run_id": rid})
                self.action_status.set(f"Last action: stopping {label} ({rid})")
            return True
        return False

    def toggle_buff_only_live(self) -> None:
        if self._toggle_existing_run_or_none(finder=find_running_buff_keeper_run, label="buff keeper"):
            return
        mode = "HORSE: it will use Ctrl+G before/after due buffs" if self.buff_start_mounted_var.get() else "GROUND: it will NOT use Ctrl+G"
        enabled_buffs: list[tuple[str, str, str]] = []
        for key, enabled_var, interval_var, pre_cast_var in (
            ("f1", self.f1_enabled_var, self.f1_interval_var, self.f1_pre_cast_var),
            ("f2", self.f2_enabled_var, self.f2_interval_var, self.f2_pre_cast_var),
        ):
            if enabled_var.get():
                try:
                    interval = float(interval_var.get())
                    pre_cast = float(pre_cast_var.get())
                except Exception as exc:
                    messagebox.showerror("Keep buffs active LIVE", f"Invalid {key.upper()} buff timing: {exc}")
                    return
                if interval <= 0:
                    messagebox.showerror("Keep buffs active LIVE", f"Invalid {key.upper()} interval: must be > 0")
                    return
                if pre_cast < 0:
                    messagebox.showerror("Keep buffs active LIVE", f"Invalid {key.upper()} pre-cast: must be >= 0")
                    return
                enabled_buffs.append((key, f"{interval:g}", f"{pre_cast:g}"))
        if not enabled_buffs:
            messagebox.showerror("Keep buffs active LIVE", "Enable at least one buff key (F1 or F2) before starting the keeper.")
            return
        try:
            f1_threshold = float(self.f1_active_attack_min_min_var.get())
            f2_threshold = float(self.f2_active_attack_speed_min_var.get())
        except Exception as exc:
            messagebox.showerror("Keep buffs active LIVE", f"Invalid active-stat threshold: {exc}")
            return
        if f1_threshold <= 0 or f2_threshold <= 0:
            messagebox.showerror("Keep buffs active LIVE", "F1/F2 active-stat thresholds must be > 0")
            return
        buff_keys = ",".join(row[0] for row in enabled_buffs)
        buff_durations = ",".join(row[1] for row in enabled_buffs)
        # The CLI has one refresh margin; use the smallest visible pre-cast value
        # so no enabled buff is refreshed earlier than its UI row implies.
        refresh_margin = min(float(row[2]) for row in enabled_buffs)
        effective = ", ".join(f"{key.upper()} every {duration}s (rebuff at ~{max(0.0, float(duration) - refresh_margin):g}s)" for key, duration, _pre in enabled_buffs)
        if messagebox.askokcancel(
            "Keep buffs active LIVE",
            "Start LIVE F1/F2 keep-buffs-active toggle? It will keep running until you click the same button again. It focuses MT2Portugalia and presses only configured buff keys when due. It will not attack, move, or target.\n\nStarting mode: " + mode + f"\nBuff timing: {effective}\nPre-cast margin: {refresh_margin:g}s\nAPI active thresholds: F1 attack_power ≥ {f1_threshold:g}; F2 attack_speed ≥ {f2_threshold:g}\n\nVerify the small top-left buff icons after it starts.",
        ):
            self._post_after_api_ready(
                build_quick_start_payload(
                    "buff_only_live",
                    assume_mounted=self.buff_start_mounted_var.get(),
                    buff_keys=buff_keys,
                    buff_durations=buff_durations,
                    buff_refresh_margin_seconds=f"{refresh_margin:g}",
                    f1_active_attack_min_min=f"{f1_threshold:g}",
                    f2_active_attack_speed_min=f"{f2_threshold:g}",
                )
            )

    def toggle_attack_nearby_live(self) -> None:
        if self._toggle_existing_run_or_none(finder=find_running_attack_nearby_run, label="select/attack nearby"):
            return
        try:
            runs = self.client.get("/api/runs")
        except Exception as exc:
            messagebox.showerror("Select/attack nearby LIVE", f"Cannot read running scripts: {exc}")
            return
        if has_active_live_control_run(runs):
            messagebox.showinfo("Select/attack nearby LIVE", "Another live movement/combat/control run is already running. Stop it before starting select/attack nearby.")
            self.refresh_all()
            return
        channel_rotation_enabled = bool(self.channel_rotate_after_destroy_var.get())
        channel_points = self.channel_click_points_var.get().strip()
        pickup_spam_count = self.pickup_spam_count_var.get().strip()
        if channel_rotation_enabled:
            try:
                from scripts.combat_metin_client_state import parse_channel_click_points

                if not parse_channel_click_points(channel_points):
                    raise ValueError("enter at least one channel point")
                if int(float(pickup_spam_count)) < 0:
                    raise ValueError("Z count must be >= 0")
            except Exception as exc:
                messagebox.showerror("Select/attack nearby LIVE", f"Invalid channel-rotation settings: {exc}")
                return
        extra = "\n\nChannel rotation ON: after each destroyed Metin it will spam Z, press X, left-click next configured channel point, then search again." if channel_rotation_enabled else ""
        if messagebox.askokcancel(
            "Select/attack nearby LIVE",
            "Start LIVE select/attack nearby mobs/Metins toggle? It will keep running until you click the same button again. It may focus MT2Portugalia, select valid nearby targets, and attack. Use only in the local private-server research sandbox." + extra,
        ):
            self._post_after_api_ready(
                build_quick_start_payload(
                    "attack_nearby_live",
                    channel_rotate_after_destroy=channel_rotation_enabled,
                    channel_click_points=channel_points,
                    pickup_spam_count=pickup_spam_count,
                )
            )

    def _start_key_macro_payload(self, key: str, *, presses: str) -> None:
        try:
            payload = build_key_macro_payload(
                key=key,
                interval_seconds=self.key_macro_interval_var.get(),
                presses=presses,
                hold_seconds=self.key_macro_hold_var.get(),
                live=True,
            )
        except Exception as exc:
            messagebox.showerror("Key test / timed macro", f"Invalid key macro settings: {exc}")
            return
        if messagebox.askokcancel(
            "Key test / timed macro LIVE",
            f"Start LIVE {key.upper()} key {'macro' if str(presses) == '0' or int(float(presses)) != 1 else 'test'}?\n\n"
            "This focuses MT2Portugalia and sends only the selected key. Use Stop selected/Emergency stop all for repeating macros.",
        ):
            self._post_after_api_ready(payload)

    def press_key_once(self, key: str) -> None:
        self._start_key_macro_payload(key, presses="1")

    def start_key_macro(self, key: str) -> None:
        self._start_key_macro_payload(key, presses=self.key_macro_presses_var.get())

    def _sapo_cycle_state_path(self) -> Path:
        return self.project_root / FIXED_SAPO_CHANNEL_CYCLE_STATE

    def reset_sapo_channel_cycle(self) -> None:
        path = self._sapo_cycle_state_path()
        try:
            if path.exists():
                path.unlink()
                detail = f"Reset channel cycle: deleted {path}"
            else:
                detail = f"Channel cycle already reset: {path} does not exist"
            self.sapo_status_var.set(detail)
            self.sapo_preflight_text.delete("1.0", "end")
            self.sapo_preflight_text.insert("end", detail)
        except Exception as exc:
            messagebox.showerror("Reset Fixed Sapo channel cycle", str(exc))

    def refresh_sapo_gate_details(self) -> None:
        try:
            state = self.client.get("/api/state")
        except Exception as exc:
            messagebox.showerror("Fixed Sapo gate details", f"Cannot read state: {exc}")
            return
        try:
            runs = self.client.get("/api/runs")
        except Exception:
            runs = []
        cycle_state = None
        path = self._sapo_cycle_state_path()
        if path.exists():
            try:
                cycle_state = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                cycle_state = {"error": "could not parse cycle state"}
        text = format_fixed_sapo_gate_details(state, runs, cycle_state)
        self.sapo_preflight_text.delete("1.0", "end")
        self.sapo_preflight_text.insert("end", text)
        self.sapo_status_var.set("Fixed Sapo gate details refreshed")

    def refresh_sapo_sweep_summary(self) -> None:
        path = self.project_root / "reports/dashboard_runs/fixed_sapo_channel_sweep_summary.json"
        try:
            summary = json.loads(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else {}
        except Exception as exc:
            summary = {"outcome": "summary_read_failed", "reason": str(exc)}
        text = format_fixed_sapo_sweep_summary(summary)
        self.sapo_preflight_text.delete("1.0", "end")
        self.sapo_preflight_text.insert("end", text)
        self.sapo_status_var.set("Fixed Sapo latest sweep summary refreshed")

    def sapo_sweep_dry_run_preview(self) -> None:
        try:
            payload = build_fixed_sapo_sweep_payload(
                channels=self.sapo_sweep_channels_var.get(),
                cycle_duration=self.sapo_until_destroy_duration_var.get(),
                load_wait_seconds=self.sapo_sweep_load_wait_var.get(),
                hp_stop_threshold=self.sapo_hp_stop_var.get(),
                min_distance=self.sapo_min_distance_var.get(),
                channel_click_points=self.channel_click_points_var.get(),
                channel_index=self.sapo_channel_index_var.get(),
                channel_auto_cycle=self.sapo_channel_auto_cycle_var.get(),
                skip_first_channel=self.sapo_skip_first_channel_var.get(),
                pickup_seconds=self.sapo_pickup_seconds_var.get(),
                live=False,
                state_json=self.json_state_path,
            )
        except Exception as exc:
            messagebox.showerror("Fixed Sapo sweep preview", f"Invalid sweep preview settings: {exc}")
            return
        self._post_after_api_ready(payload)
        self.sapo_status_var.set("Fixed Sapo dry-run sweep preview requested; refresh Last sweep summary after it exits")

    def _start_fixed_sapo_payload(self, *, duration: str, until_destroyed: bool, pickup_after_destroy: bool, live: bool) -> None:
        try:
            payload = build_fixed_sapo_payload(
                duration=duration,
                until_destroyed=until_destroyed,
                hp_stop_threshold=self.sapo_hp_stop_var.get(),
                pickup_after_destroy=pickup_after_destroy,
                channel_switch_after_pickup=self.sapo_channel_switch_after_pickup_var.get() if until_destroyed else False,
                channel_click_points=self.channel_click_points_var.get(),
                channel_index=self.sapo_channel_index_var.get(),
                channel_auto_cycle=self.sapo_channel_auto_cycle_var.get(),
                channel_cycle_state=FIXED_SAPO_CHANNEL_CYCLE_STATE,
                skip_first_channel=self.sapo_skip_first_channel_var.get(),
                pickup_seconds=self.sapo_pickup_seconds_var.get(),
                min_distance=self.sapo_min_distance_var.get(),
                live=live,
                state_json=self.json_state_path,
            )
        except Exception as exc:
            messagebox.showerror("Fixed Sapo", f"Invalid Fixed Sapo settings: {exc}")
            return
        if live:
            try:
                runs = self.client.get("/api/runs")
            except Exception as exc:
                messagebox.showerror("Fixed Sapo", f"Cannot read running scripts: {exc}")
                return
            if has_active_live_combat_run(runs):
                messagebox.showinfo("Fixed Sapo", "A live combat/Space run is already running. Stop it before starting another.")
                self.refresh_all()
                return
            buff_run = find_running_buff_keeper_run(runs)
            warning = "Buff keeper is RUNNING." if buff_run else "WARNING: buff keeper is not running. Start Keep Buffs Active LIVE first unless this is an input-only probe."
            action = "hold Space until Sapo destroyed" if until_destroyed else f"hold Space for {duration}s"
            if not messagebox.askokcancel(
                "Fixed Sapo Space-only LIVE",
                f"Start Fixed Sapo Space-only LIVE?\n\nAction: {action}\n{warning}\n\nThe script will NOT click, move, target-search, or press potion 1. It blocks on stale state, dead/missing Sapo probe, player death, or too far from fixed spawn. Confirm?",
            ):
                return
        self._post_after_api_ready(payload)
        self.sapo_status_var.set("Fixed Sapo run requested; watch Runs and preflight/summary files")

    def sapo_preflight_dry_run(self) -> None:
        self._start_fixed_sapo_payload(duration="0", until_destroyed=False, pickup_after_destroy=False, live=False)

    def sapo_space_short_live(self) -> None:
        self._start_fixed_sapo_payload(duration=self.sapo_duration_var.get(), until_destroyed=False, pickup_after_destroy=False, live=True)

    def sapo_space_until_destroy_live(self) -> None:
        self._start_fixed_sapo_payload(
            duration=self.sapo_until_destroy_duration_var.get(),
            until_destroyed=True,
            pickup_after_destroy=self.sapo_pickup_after_destroy_var.get(),
            live=True,
        )

    def sapo_full_destroy_pickup_switch_live(self) -> None:
        self.sapo_pickup_after_destroy_var.set(True)
        self.sapo_channel_switch_after_pickup_var.set(True)
        self._start_fixed_sapo_payload(
            duration=self.sapo_until_destroy_duration_var.get(),
            until_destroyed=True,
            pickup_after_destroy=True,
            live=True,
        )

    def sapo_sweep_all_channels_live(self) -> None:
        try:
            payload = build_fixed_sapo_sweep_payload(
                channels=self.sapo_sweep_channels_var.get(),
                cycle_duration=self.sapo_until_destroy_duration_var.get(),
                load_wait_seconds=self.sapo_sweep_load_wait_var.get(),
                hp_stop_threshold=self.sapo_hp_stop_var.get(),
                min_distance=self.sapo_min_distance_var.get(),
                channel_click_points=self.channel_click_points_var.get(),
                channel_index=self.sapo_channel_index_var.get(),
                channel_auto_cycle=self.sapo_channel_auto_cycle_var.get(),
                skip_first_channel=self.sapo_skip_first_channel_var.get(),
                pickup_seconds=self.sapo_pickup_seconds_var.get(),
                low_dps_adjust=self.sapo_sweep_low_dps_adjust_var.get(),
                low_dps_threshold=self.sapo_sweep_low_dps_threshold_var.get(),
                low_dps_max_cumulative_steps=self.sapo_sweep_max_steps_var.get(),
                adjust_hold_seconds=self.sapo_sweep_adjust_hold_var.get(),
                live=True,
                state_json=self.json_state_path,
            )
        except Exception as exc:
            messagebox.showerror("Fixed Sapo sweep", f"Invalid sweep settings: {exc}")
            return
        try:
            runs = self.client.get("/api/runs")
        except Exception as exc:
            messagebox.showerror("Fixed Sapo sweep", f"Cannot read running scripts: {exc}")
            return
        if has_active_live_combat_run(runs):
            messagebox.showinfo("Fixed Sapo sweep", "A live combat/Space/channel test is already running. Stop it before starting the sweep.")
            self.refresh_all()
            return
        buff_run = find_running_buff_keeper_run(runs)
        warning = "Buff keeper is RUNNING." if buff_run else "WARNING: buff keeper is not running. Start Keep Buffs Active LIVE first unless you are intentionally testing without it."
        if not messagebox.askokcancel(
            "Sweep all Sapo channels LIVE",
            f"Start full Fixed Sapo sweep LIVE?\n\n"
            f"Cycles: {payload['options']['channels']} channel attempts\n"
            f"Per channel: hold Space up to {payload['options']['cycle_duration']}s, pickup Z, switch channel, wait {payload['options']['load_wait_seconds']}s.\n"
            f"{warning}\n\n"
            "The script will NOT combat-click, target-search, or press potion 1. If low-DPS adjustment is enabled, it may tap tiny WASD centering nudges while Space remains held. It stops on stale state, death/low HP, missing Sapo probe, too far from spawn, timeout, or channel-switch failure. Confirm?",
        ):
            return
        self._post_after_api_ready(payload)
        self.sapo_status_var.set("Fixed Sapo all-channel sweep requested; watch Runs and fixed_sapo_channel_sweep_summary.json")

    def toggle_sapo_sweep_keep_running(self) -> None:
        try:
            runs = self.client.get("/api/runs")
        except Exception as exc:
            messagebox.showerror("Fixed Sapo keep sweep", f"Cannot read running scripts: {exc}")
            return
        for run in runs:
            if run.get("script") == "fixed_sapo_channel_sweep" and run.get("mode") == "live" and run.get("running"):
                self._post_async("/api/stop", {"run_id": run.get("run_id")})
                self.sapo_status_var.set(f"Stopping Fixed Sapo keep-running sweep: {run.get('run_id')}")
                return
        if has_active_live_combat_run(runs):
            messagebox.showinfo("Fixed Sapo keep sweep", "A live combat/Space/channel test is already running. Stop it before starting keep-running sweep.")
            self.refresh_all()
            return
        try:
            payload = build_fixed_sapo_sweep_payload(
                channels=self.sapo_sweep_channels_var.get(),
                cycle_duration=self.sapo_until_destroy_duration_var.get(),
                load_wait_seconds=self.sapo_sweep_load_wait_var.get(),
                hp_stop_threshold=self.sapo_hp_stop_var.get(),
                min_distance=self.sapo_min_distance_var.get(),
                channel_click_points=self.channel_click_points_var.get(),
                channel_index=self.sapo_channel_index_var.get(),
                channel_auto_cycle=self.sapo_channel_auto_cycle_var.get(),
                skip_first_channel=self.sapo_skip_first_channel_var.get(),
                pickup_seconds=self.sapo_pickup_seconds_var.get(),
                repeat_while_running=True,
                low_dps_adjust=self.sapo_sweep_low_dps_adjust_var.get(),
                low_dps_threshold=self.sapo_sweep_low_dps_threshold_var.get(),
                low_dps_max_cumulative_steps=self.sapo_sweep_max_steps_var.get(),
                adjust_hold_seconds=self.sapo_sweep_adjust_hold_var.get(),
                live=True,
                state_json=self.json_state_path,
            )
        except Exception as exc:
            messagebox.showerror("Fixed Sapo keep sweep", f"Invalid keep-running settings: {exc}")
            return
        buff_run = find_running_buff_keeper_run(runs)
        warning = "Buff keeper is RUNNING." if buff_run else "WARNING: buff keeper is not running. Start Keep Buffs Active LIVE first unless intentionally testing without it."
        if not messagebox.askokcancel(
            "Keep Fixed Sapo sweep running LIVE",
            f"Start continuous Fixed Sapo channel sweep until you toggle this button again or Emergency Stop?\n\n"
            f"Cycles repeat every {payload['options']['channels']} channel attempts. Low-DPS WASD nudges: {payload['options']['low_dps_adjust']}.\n"
            f"{warning}\n\n"
            "It holds Space to attack, picks up, switches channel, and may send tiny WASD centering taps only when low-DPS adjustment is enabled. Confirm?",
        ):
            return
        self._post_after_api_ready(payload)
        self.sapo_status_var.set("Fixed Sapo keep-running sweep requested; toggle again or Emergency Stop to stop it")

    def sapo_pickup_channel_switch_live(self) -> None:
        try:
            payload = build_fixed_sapo_payload(
                duration="0",
                until_destroyed=False,
                hp_stop_threshold=self.sapo_hp_stop_var.get(),
                pickup_after_destroy=True,
                channel_switch_after_pickup=True,
                channel_click_points=self.channel_click_points_var.get(),
                channel_index=self.sapo_channel_index_var.get(),
                channel_auto_cycle=self.sapo_channel_auto_cycle_var.get(),
                channel_cycle_state=FIXED_SAPO_CHANNEL_CYCLE_STATE,
                skip_first_channel=self.sapo_skip_first_channel_var.get(),
                pickup_seconds=self.sapo_pickup_seconds_var.get(),
                min_distance=self.sapo_min_distance_var.get(),
                live=True,
                state_json=self.json_state_path,
            )
        except Exception as exc:
            messagebox.showerror("Fixed Sapo channel switch", f"Invalid channel-switch settings: {exc}")
            return
        try:
            runs = self.client.get("/api/runs")
        except Exception as exc:
            messagebox.showerror("Fixed Sapo channel switch", f"Cannot read running scripts: {exc}")
            return
        if has_active_live_combat_run(runs):
            messagebox.showinfo("Fixed Sapo channel switch", "A live combat/Space/channel test is already running. Stop it before starting another.")
            self.refresh_all()
            return
        if messagebox.askokcancel(
            "Fixed Sapo pickup + channel switch LIVE",
            "Test only the post-destroy step?\n\nGate: current Sapo probe must already be gone/alive=false.\nAction: press Z pickups, press X, then click configured channel row.\nIt will not attack, hold Space, move, or press potion 1. Confirm?",
        ):
            self._post_after_api_ready(payload)
            self.sapo_status_var.set("Fixed Sapo channel-switch test requested; watch Runs and summary JSON")

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
        try:
            combat_config = self.client.get("/api/combat")
        except Exception:
            combat_config = {}
        if not state_has_metin_target(state) and not allow_practice_live_without_metin(state, combat_config):
            messagebox.showwarning("Start practicing LIVE", "No Metin targeted — select one before starting live combat, or enable/save attack nearby mobs to patrol mobs until a Metin appears.")
            return
        target = state.get("target", {})
        player = state.get("player", {})
        payload, coord = build_combat_payload(self.project_root, state)
        if allow_practice_live_without_metin(state, combat_config):
            payload["options"]["attack_nearby_mobs"] = True
        msg = build_combat_confirmation_message(state, coord)
        if allow_practice_live_without_metin(state, combat_config):
            msg += "\n\nNo Metin is selected. attack nearby mobs is ON, so the loop will attack valid nearby mobs and keep watching for a Metin target/spawn."
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

    def archive_all(self) -> None:
        if messagebox.askokcancel("Archive all completed", "Archive all completed dashboard-managed runs? Running runs stay visible and are not stopped."):
            self._post_async("/api/archive_all", {})

    def stop_all(self) -> None:
        if messagebox.askokcancel("Emergency stop all", "Stop all dashboard-managed runs?"):
            self.stop_all_now(source="button")

    def stop_all_now(self, *, source: str = "hotkey") -> None:
        """Immediately stop all dashboard-managed live/managed runs.

        Used by Ctrl+Alt+S; no confirmation because it is the emergency path.
        """
        self.action_status.set(f"Last action: Ctrl+Alt+S stop-all requested" if source == "hotkey" else "Last action: stop-all requested")
        self._post_async("/api/stop_all", {})

    def on_close(self) -> None:
        try:
            hotkey = getattr(self, "global_stop_hotkey", None)
            if hotkey:
                hotkey.stop()
        finally:
            self.root.destroy()

    def _post_async(self, path: str, payload: dict[str, Any]) -> None:
        if path == "/api/start":
            if getattr(self, "_start_in_flight", False):
                self.action_status.set("Last action: start already in progress")
                return
            self._start_in_flight = True
        def worker() -> None:
            try:
                response = self.client.post(path, payload)
                if path == "/api/start":
                    action = payload.get("script") or path
                    mode = "live" if payload.get("live") else "dry-run"
                    run_id = response.get("run_id") if isinstance(response, dict) else None
                    suffix = f" ({run_id})" if run_id else ""
                    text = f"Last action: {action} started ({mode}){suffix}"
                else:
                    text = f"Last action: {path.rsplit('/', 1)[-1]} completed"
                self.root.after(0, lambda text=text: self.action_status.set(text))
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

    def refresh_selected_log(self) -> None:
        rid = self.selected_run.get()
        run = getattr(self, "_runs_by_id", {}).get(rid)
        if not run:
            messagebox.showinfo("Refresh log tail", "Select a run first.")
            return
        self.show_run_log(run)

    def show_run_log(self, run: dict[str, Any]) -> None:
        try:
            top, bottom = self.log_text.yview()
        except Exception:
            top, bottom = 0.0, 1.0
        was_at_bottom = bottom >= 0.999
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", run.get("log_tail", ""))
        if hasattr(self, "log_tail_status"):
            self.log_tail_status.set(f"Log tail: showing {run.get('run_id', 'selected run')} (manual refresh/follow)")
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
    ap.add_argument("--client-profile", choices=sorted(CLIENT_PROFILES), default="main")
    ap.add_argument("--json-state", default=None, help="client-state JSON path for this panel/server")
    ap.add_argument("--tsv", default=None, help="fallback TSV path for this panel/server")
    ns = ap.parse_args(argv)
    root = tk.Tk()
    ControlPanelApp(root, DashboardApiClient(ns.base_url), project_root=Path(ns.project_root).resolve(), client_profile=ns.client_profile, json_state=ns.json_state, tsv_state=ns.tsv)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
