from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable, Any

SAFE_KEY_RELEASE_CODE = """
try:
    from pynput.keyboard import Controller, Key
    k = Controller()
    for key in ['w','a','s','d',' ']:
        k.release(key)
except Exception:
    pass
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class OptionSpec:
    name: str
    flag: str
    type: str = "str"
    default: Any = None
    description: str = ""
    live_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "flag": self.flag,
            "type": self.type,
            "default": self.default,
            "description": self.description,
            "live_only": self.live_only,
        }

    def coerce(self, value: Any) -> str:
        if self.type == "bool":
            if isinstance(value, str):
                truthy = value.strip().lower() in {"1", "true", "yes", "on"}
            else:
                truthy = bool(value)
            return "true" if truthy else "false"
        if self.type == "int":
            return str(int(value))
        if self.type == "float":
            return str(float(value))
        return str(value)


@dataclass(frozen=True)
class ScriptSpec:
    name: str
    path: str
    description: str
    default_args: tuple[str, ...] = ()
    live_args: tuple[str, ...] = ("--live",)
    exclusive_live_group: str | None = None
    options: tuple[OptionSpec, ...] = ()
    force_dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "description": self.description,
            "default_args": list(self.default_args),
            "live_args": list(self.live_args),
            "exclusive_live_group": self.exclusive_live_group,
            "force_dry_run": self.force_dry_run,
            "options": [opt.to_dict() for opt in self.options],
        }


DEFAULT_SCRIPTS: dict[str, ScriptSpec] = {
    "learn_runaround_client_tsv": ScriptSpec(
        name="learn_runaround_client_tsv",
        path="scripts/learn_runaround_client_tsv.py",
        description="navigation learning / runaround script; dry-run uses zero cycles and no focus/input",
        default_args=(
            "--cycles",
            "0",
            "--no-focus",
            "--seconds",
            "0.05",
            "--pause",
            "0.05",
            "--out-jsonl",
            "reports/dashboard_runs/learn_runaround_dryrun.jsonl",
            "--model-out",
            "reports/dashboard_runs/learn_runaround_dryrun_model.json",
        ),
        live_args=(
            "--cycles",
            "1",
            "--seconds",
            "0.2",
            "--pause",
            "0.35",
            "--out-jsonl",
            "reports/dashboard_runs/learn_runaround_live.jsonl",
            "--model-out",
            "reports/dashboard_runs/learn_runaround_live_model.json",
        ),
        options=(
            OptionSpec("cycles", "--cycles", "int", 1, "movement calibration cycles"),
            OptionSpec("seconds", "--seconds", "float", 0.2, "key hold seconds per probe"),
            OptionSpec("pause", "--pause", "float", 0.35, "pause after each probe"),
            OptionSpec("keys", "--keys", "str", "w,a,s,d", "movement keys to probe"),
            OptionSpec("potion_hp_ratio", "--potion-hp-ratio", "float", 0.5, "potion threshold; negative disables"),
        ),
    ),
    "combat_metin_client_state": ScriptSpec(
        name="combat_metin_client_state",
        path="scripts/combat_metin_client_state.py",
        description="combat state script; dry-run by default, live mode is exclusive",
        default_args=("--max-cycles", "1", "--out", "reports/dashboard_runs/combat_metin_dryrun.jsonl"),
        exclusive_live_group="combat",
        options=(
            OptionSpec("max_cycles", "--max-cycles", "int", 60, "decision loop cycles; 0 means run until stopped"),
            OptionSpec("state_json", "--json-state", "str", "D:/Games/MT2Portugalia/app/hermes_state.json", "client-state JSON path"),
            OptionSpec("burst_seconds", "--burst-seconds", "float", 2.5, "attack burst length"),
            OptionSpec("micro_move_seconds", "--micro-move-seconds", "float", 0.12, "micro-position probe length"),
            OptionSpec("metin_name", "--metin-name", "str", "Metin da Batalha", "target Metin name"),
            OptionSpec("metin_vid", "--metin-vid", "int", None, "known target VID"),
            OptionSpec("metin_x", "--metin-x", "int", None, "known Metin x coord"),
            OptionSpec("metin_y", "--metin-y", "int", None, "known Metin y coord"),
            OptionSpec("metin_coord_source", "--metin-coord-source", "str", None, "coordinate source: live_memory_visible_text or table"),
            OptionSpec("allow_selected_vid_without_exact_coords", "--allow-selected-vid-without-exact-coords", "bool", False, "operator-approved Space-only fallback for selected VID when exact projection is missing"),
            OptionSpec("attack_nearby_mobs", "--attack-nearby-mobs", "bool", False, "attack nearby mobs when client state reports a valid mob target or hostile nearby entity"),
            OptionSpec("buff_only", "--buff-only", "bool", False, "only run the configured buff keeper; do not target, move, or attack"),
            OptionSpec("buff_keys", "--buff-keys", "str", "f1,f2", "comma-separated buff quickslot keys for keepalive"),
            OptionSpec("buff_durations", "--buff-durations", "str", "156,302", "comma-separated buff durations in seconds"),
            OptionSpec("buff_refresh_margin_seconds", "--buff-refresh-margin-seconds", "float", 3.0, "refresh buffs this many seconds before duration"),
            OptionSpec("buff_damage_guard_keys", "--buff-damage-guard-keys", "str", "f1", "toggle-style buff keys whose timer refresh can be delayed while Metin damage still indicates active buff"),
            OptionSpec("f1_active_attack_min_min", "--f1-active-attack-min-min", "float", 200.0, "API stat threshold: treat F1/poder de ataque active at or above attack_power; stale 349-style configs map to 200 in the script"),
            OptionSpec("f2_active_attack_speed_min", "--f2-active-attack-speed-min", "float", 130.0, "API stat threshold: treat F2/rapidez de ataque active at or above this attack_speed"),
            OptionSpec("assume_mounted", "--assume-mounted", "bool", False, "buff-only live override: dismount/buff/remount each due buff without trusting mount detection"),
            OptionSpec("enable_combat_buffs", "--enable-combat-buffs", "bool", False, "opt-in: allow non-buff-only attack/combat runs to press configured buff keys"),
            OptionSpec("visual_target_clicks", "--visual-target-clicks", "bool", False, "attack-nearby: screenshot/YOLO-detected Metin click before blind fallback"),
            OptionSpec("visual_detector_model", "--visual-detector-model", "str", str(Path("reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.onnx")), "ONNX model for visual target clicks"),
            OptionSpec("visual_target_min_confidence", "--visual-target-min-confidence", "float", 0.30, "minimum YOLO confidence for visual target click"),
            OptionSpec("allow_blind_target_clicks", "--allow-blind-target-clicks", "bool", False, "operator-approved attack-nearby arbitrary window click probes to select visible Metins when Tab/state fails"),
            OptionSpec("target_search_move_seconds", "--target-search-move-seconds", "float", 0.25, "short bounded W step while attack-nearby searches for a target"),
            OptionSpec("target_click_cooldown_seconds", "--target-click-cooldown-seconds", "float", 4.0, "wait after a left-click target attempt before clicking again so Metin2 auto-attack can continue"),
            OptionSpec("target_camera_sweep_seconds", "--target-camera-sweep-seconds", "float", 0.16, "Q/E camera sweep duration while searching for visible Metins"),
            OptionSpec("minimap_camera_hint", "--minimap-camera-hint", "bool", False, "use yellow minimap dot/facing geometry to choose Q or E while searching"),
            OptionSpec("minimap_yellow_min_pixels", "--minimap-yellow-min-pixels", "int", 3, "minimum yellow pixels before trusting minimap camera hint"),
            OptionSpec("channel_rotate_after_destroy", "--channel-rotate-after-destroy", "bool", False, "after destroying a Metin, spam Z pickup, press X, click next configured channel, then continue"),
            OptionSpec("channel_click_points", "--channel-click-points", "str", "", "semicolon-separated channel menu click points as window fractions or screen pixels"),
            OptionSpec("pickup_spam_count", "--pickup-spam-count", "int", 12, "number of Z pickups before channel switch"),
            OptionSpec("pickup_spam_interval", "--pickup-spam-interval", "float", 0.08, "seconds between Z pickups"),
            OptionSpec("channel_menu_delay_seconds", "--channel-menu-delay-seconds", "float", 1.25, "wait after pressing X before channel click"),
            OptionSpec("channel_switch_wait_seconds", "--channel-switch-wait-seconds", "float", 4.0, "wait after clicking next channel before searching again"),
        ),
    ),
    "find_nearby_metins": ScriptSpec(
        name="find_nearby_metins",
        path="scripts/find_nearby_metins.py",
        description="rank known Metin coordinates around the current character and export JSON/CSV",
        default_args=(),
        live_args=(),
        force_dry_run=True,
        options=(
            OptionSpec("radius", "--radius", "float", 300.0, "search radius in map-coordinate units"),
            OptionSpec("limit", "--limit", "int", 8, "maximum nearby Metins to return"),
            OptionSpec("source", "--source", "str", "hybrid", "hybrid, live-memory, visual, table, or both"),
            OptionSpec("state_json", "--state-json", "str", "D:/Games/MT2Portugalia/app/hermes_state.json", "live client-state JSON path"),
            OptionSpec("coords_csv", "--coords-csv", "str", "data/metin_coordinates.csv", "known Metin coordinate table"),
            OptionSpec("out_json", "--out-json", "str", "reports/nearby_metins/latest_nearby_metins.json", "output JSON artifact"),
            OptionSpec("out_csv", "--out-csv", "str", "reports/nearby_metins/latest_nearby_metins.csv", "output CSV artifact"),
            OptionSpec("include_unknown_map", "--include-unknown-map", "bool", False, "include coordinates whose map is unknown"),
            OptionSpec("display_offset_x", "--display-offset-x", "int", 0, "optional x offset from client/logger coords to user/display coords"),
            OptionSpec("display_offset_y", "--display-offset-y", "int", 0, "optional y offset from client/logger coords to user/display coords"),
        ),
    ),
    "move_to_metin_client_state": ScriptSpec(
        name="move_to_metin_client_state",
        path="scripts/move_to_metin_client_state.py",
        description="movement-only direct approach to a trusted Metin coordinate; not used for fixed Sapo keep-sweep; live mode is exclusive and never attacks",
        default_args=("--metin-x", "0", "--metin-y", "0", "--metin-coord-source", "dry_run_placeholder", "--max-cycles", "1", "--out", "reports/dashboard_runs/move_to_metin_dryrun.jsonl"),
        exclusive_live_group="combat",
        options=(
            OptionSpec("max_cycles", "--max-cycles", "int", 90, "movement loop cycles"),
            OptionSpec("state_json", "--json-state", "str", "D:/Games/MT2Portugalia/app/hermes_state.json", "client-state JSON path"),
            OptionSpec("arrival_radius", "--arrival-radius", "float", 260.0, "stop when within this raw-unit radius"),
            OptionSpec("move_step_seconds", "--move-step-seconds", "float", 0.45, "maximum smooth movement hold per cycle"),
            OptionSpec("metin_name", "--metin-name", "str", "Metin da Batalha", "target Metin name"),
            OptionSpec("metin_x", "--metin-x", "int", None, "trusted Metin x coord"),
            OptionSpec("metin_y", "--metin-y", "int", None, "trusted Metin y coord"),
            OptionSpec("metin_coord_source", "--metin-coord-source", "str", None, "coordinate source: live_memory_visible_text or named_metin_probe; do not use for fixed Sapo keep-sweep"),
        ),
    ),
    "key_macro_control": ScriptSpec(
        name="key_macro_control",
        path="scripts/key_macro_control.py",
        description="direct F1/F2 key test and timed key macro; live mode is explicit and dashboard-managed",
        default_args=("--out", "reports/dashboard_runs/key_macro_dryrun.json"),
        exclusive_live_group="combat",
        options=(
            OptionSpec("key", "--key", "str", "f1", "key to tap, e.g. f1 or f2"),
            OptionSpec("interval_seconds", "--interval-seconds", "float", 35.0, "seconds between key taps for timed macro"),
            OptionSpec("presses", "--presses", "int", 1, "number of key taps; 0 runs until stopped"),
            OptionSpec("hold_seconds", "--hold-seconds", "float", 0.06, "seconds to hold each key tap"),
            OptionSpec("window_query", "--window-query", "str", "MT2Portugalia", "target window title/process query"),
            OptionSpec("elevate", "--elevate", "bool", True, "relaunch key sender elevated via UAC for elevated game clients"),
        ),
    ),
    "fixed_sapo_space_control": ScriptSpec(
        name="fixed_sapo_space_control",
        path="scripts/fixed_sapo_space_control.py",
        description="fixed-spawn Sapo de Pedra test bench: no click/no move/no potion, hold Space only when parked next to spawn",
        default_args=("--duration", "0", "--out", "reports/dashboard_runs/fixed_sapo_space_dryrun.jsonl", "--summary-out", "reports/dashboard_runs/fixed_sapo_space_dryrun_summary.json"),
        live_args=("--live", "--elevate"),
        exclusive_live_group="combat",
        options=(
            OptionSpec("state_json", "--json-state", "str", "D:/Games/MT2Portugalia/app/hermes_state.json", "client-state JSON path"),
            OptionSpec("duration", "--duration", "float", 30.0, "max seconds to hold Space; 0 means preflight only"),
            OptionSpec("until_destroyed", "--until-destroyed", "bool", False, "hold Space until Sapo probe/target disappears or duration timeout"),
            OptionSpec("hp_stop_threshold", "--hp-stop-threshold", "int", 2500, "stop if HP falls at/below this value"),
            OptionSpec("max_state_age_seconds", "--max-state-age-seconds", "float", 2.0, "fresh-state gate"),
            OptionSpec("min_distance", "--min-distance", "float", 350.0, "block live if farther than this raw distance from fixed Sapo probe"),
            OptionSpec("pickup_after_destroy", "--pickup-after-destroy", "bool", False, "spam Z after probe/target says Sapo destroyed"),
            OptionSpec("pickup_count", "--pickup-count", "int", 20, "Z pickup taps after destroy"),
            OptionSpec("channel_switch_after_pickup", "--channel-switch-after-pickup", "bool", False, "after Sapo is gone/items picked up: press X and click configured next-channel point"),
            OptionSpec("channel_click_points", "--channel-click-points", "str", "", "semicolon-separated channel menu click points as window fractions or screen pixels"),
            OptionSpec("channel_index", "--channel-index", "int", 0, "manual/seed channel row index; auto-cycle uses it only when no state exists"),
            OptionSpec("channel_auto_cycle", "--channel-auto-cycle", "bool", True, "choose next channel row from persistent cycle state"),
            OptionSpec("channel_cycle_state", "--channel-cycle-state", "str", "reports/dashboard_runs/fixed_sapo_channel_cycle_state.json", "JSON file storing last/next channel index"),
            OptionSpec("channel_menu_delay_seconds", "--channel-menu-delay-seconds", "float", 1.25, "wait after X opens the channel menu"),
            OptionSpec("channel_switch_wait_seconds", "--channel-switch-wait-seconds", "float", 4.0, "wait after clicking the channel row"),
            OptionSpec("window_query", "--window-query", "str", "MT2Portugalia", "target window title/process query"),
            OptionSpec("out", "--out", "str", "reports/dashboard_runs/fixed_sapo_space_control.jsonl", "JSONL detail log"),
            OptionSpec("summary_out", "--summary-out", "str", "reports/dashboard_runs/fixed_sapo_space_control_summary.json", "compact scorecard JSON"),
        ),
    ),
    "fixed_sapo_channel_sweep": ScriptSpec(
        name="fixed_sapo_channel_sweep",
        path="scripts/fixed_sapo_channel_sweep.py",
        description="sweep fixed-spawn Sapo across channels at the fixed spawn: destroy, pickup, switch, repeat; no travel movement/no combat-click/no potion",
        default_args=("--channels", "8", "--out", "reports/dashboard_runs/fixed_sapo_channel_sweep_dryrun.jsonl", "--summary-out", "reports/dashboard_runs/fixed_sapo_channel_sweep_dryrun_summary.json"),
        live_args=("--live", "--elevate"),
        exclusive_live_group="combat",
        options=(
            OptionSpec("state_json", "--json-state", "str", "D:/Games/MT2Portugalia/app/hermes_state.json", "client-state JSON path"),
            OptionSpec("channels", "--channels", "int", 8, "max channel cycles to attempt; final channel does not need a switch afterward"),
            OptionSpec("cycle_duration", "--cycle-duration", "float", 240.0, "max seconds to hold Space per channel"),
            OptionSpec("load_wait_seconds", "--load-wait-seconds", "float", 8.0, "wait after channel switch before next preflight"),
            OptionSpec("hp_stop_threshold", "--hp-stop-threshold", "int", 2500, "stop if HP falls at/below this value"),
            OptionSpec("max_state_age_seconds", "--max-state-age-seconds", "float", 2.0, "fresh-state gate"),
            OptionSpec("min_distance", "--min-distance", "float", 350.0, "block live if farther than this raw distance from fixed Sapo probe"),
            OptionSpec("pickup_count", "--pickup-count", "int", 20, "Z pickup taps after destroy"),
            OptionSpec("channel_click_points", "--channel-click-points", "str", "0.4990,0.3986;0.4990,0.4326;0.4990,0.4665;0.4990,0.5005;0.4990,0.5344;0.4990,0.5684;0.4990,0.6023;0.4990,0.6363", "semicolon-separated channel menu click points for CH1..CH8"),
            OptionSpec("channel_index", "--channel-index", "int", 1, "first raw visible row to click: CH1=0, CH2=1, ... CH8=7"),
            OptionSpec("channel_auto_cycle", "--channel-auto-cycle", "bool", True, "choose next channel row from persistent cycle state"),
            OptionSpec("channel_cycle_state", "--channel-cycle-state", "str", "reports/dashboard_runs/fixed_sapo_channel_cycle_state.json", "JSON file storing last/next channel index"),
            OptionSpec("repeat_while_running", "--repeat-while-running", "bool", False, "after a full sweep, keep cycling channels until dashboard stop is toggled"),
            OptionSpec("low_dps_adjust", "--low-dps-adjust", "bool", False, "when target-bar DPS is low, make tiny WASD centering nudges while Space remains held"),
            OptionSpec("low_dps_threshold", "--low-dps-threshold", "float", 0.2, "minimum target HP percentage-points/sec before a WASD nudge is attempted"),
            OptionSpec("low_dps_window_seconds", "--low-dps-window-seconds", "float", 8.0, "rolling seconds used to judge low visual DPS"),
            OptionSpec("low_dps_min_window_seconds", "--low-dps-min-window-seconds", "float", 8.0, "minimum HP sample span before DPS-based nudging is allowed"),
            OptionSpec("low_dps_noise_margin", "--low-dps-noise-margin", "float", 3.0, "ignore target HP drops smaller than this many percentage points as visual noise"),
            OptionSpec("low_dps_adjust_cooldown", "--low-dps-adjust-cooldown", "float", 6.0, "minimum seconds between low-DPS WASD probes"),
            OptionSpec("low_dps_improvement_margin", "--low-dps-improvement-margin", "float", 0.15, "minimum DPS gain to keep a probed nudge position"),
            OptionSpec("low_dps_max_cumulative_steps", "--low-dps-max-cumulative-steps", "int", 3, "maximum kept WASD nudge steps from the original attack position; values below 1 clamp to 1"),
            OptionSpec("adjust_hold_seconds", "--adjust-hold-seconds", "float", 0.18, "seconds to tap each WASD centering nudge"),
            OptionSpec("channel_menu_delay_seconds", "--channel-menu-delay-seconds", "float", 1.25, "wait after X opens the channel menu"),
            OptionSpec("channel_switch_wait_seconds", "--channel-switch-wait-seconds", "float", 5.0, "wait after clicking the channel row"),
            OptionSpec("window_query", "--window-query", "str", "MT2Portugalia", "target window title/process query"),
            OptionSpec("out", "--out", "str", "reports/dashboard_runs/fixed_sapo_channel_sweep.jsonl", "JSONL detail log"),
            OptionSpec("summary_out", "--summary-out", "str", "reports/dashboard_runs/fixed_sapo_channel_sweep_summary.json", "compact sweep summary JSON"),
        ),
    ),
    "farm_metrics_tracker": ScriptSpec(
        name="farm_metrics_tracker",
        path="scripts/farm_metrics_tracker.py",
        description="read-only DPS meter and item farm history tracker from hermes_state.json",
        default_args=("--duration", "60", "--interval", "0.5", "--out", "reports/dashboard_runs/farm_metrics_tracker.jsonl", "--summary-out", "reports/dashboard_runs/farm_metrics_tracker_summary.json"),
        live_args=(),
        force_dry_run=True,
        options=(
            OptionSpec("state_json", "--json-state", "str", "D:/Games/MT2Portugalia/app/hermes_state.json", "client-state JSON path"),
            OptionSpec("duration", "--duration", "float", 300.0, "seconds to sample DPS/items"),
            OptionSpec("interval", "--interval", "float", 0.5, "seconds between state samples"),
            OptionSpec("history_window", "--history-window", "float", 20.0, "seconds used for rolling DPS"),
            OptionSpec("screenshot_hp_fallback", "--screenshot-hp-fallback", "bool", True, "estimate target HP percent from visible target bar when JSON HP is absent"),
            OptionSpec("window_query", "--window-query", "str", "MT2Portugalia", "target game window for screenshot HP fallback"),
            OptionSpec("out", "--out", "str", "reports/dashboard_runs/farm_metrics_tracker.jsonl", "JSONL metric samples"),
            OptionSpec("summary_out", "--summary-out", "str", "reports/dashboard_runs/farm_metrics_tracker_summary.json", "compact DPS/item summary JSON"),
        ),
    ),
    "login_mt2_local": ScriptSpec(
        name="login_mt2_local",
        path="scripts/login_mt2_local.py",
        description="open/attach MT2Portugalia, login with Windows Credential Manager, and enter game without closing the other client",
        default_args=("--help",),
        live_args=("login", "--launch", "--click-fields", "--enter-game", "--enter-game-count", "3", "--delay", "5", "--window-timeout", "90"),
        options=(
            OptionSpec("username", "--username", "str", "yoshy", "Credential Manager username"),
            OptionSpec("app_dir", "--app-dir", "str", "D:/Games/MT2Portugalia/app", "client app directory containing pgclient.app"),
            OptionSpec("restart", "--restart", "bool", False, "close/restart only this app_dir before login; leave off to keep main+buffer open"),
            OptionSpec("enter_game_count", "--enter-game-count", "int", 3, "Enter/Começar presses after login"),
            OptionSpec("delay", "--delay", "float", 5.0, "seconds to wait before typing login"),
            OptionSpec("window_timeout", "--window-timeout", "float", 90.0, "seconds to wait for MT2Portugalia window after launch"),
        ),
    ),
    "patch_mt2_root_state_logger": ScriptSpec(
        name="patch_mt2_root_state_logger",
        path="scripts/patch_mt2_root_state_logger.py",
        description="patch/integrate the read-only Hermes client-state logger into loose game.py and pack/root",
        default_args=("--dry-run",),
        live_args=(),
        options=(
            OptionSpec("skip_loose", "--skip-loose", "bool", False, "skip loose app/game.py patch"),
            OptionSpec("dry_run", "--dry-run", "bool", False, "check patch size without writing"),
        ),
    ),
    "probe_client_state": ScriptSpec(
        name="probe_client_state",
        path="scripts/probe_client_state.py",
        description="client state probe; dry-run skips process/window probing",
        default_args=("--no-process", "--out", "reports/dashboard_runs/probe_client_state_dryrun.json"),
        live_args=("--out", "reports/dashboard_runs/probe_client_state_live.json"),
        options=(
            OptionSpec("no_process", "--no-process", "bool", True, "skip process/window probe"),
            OptionSpec("coordinate_text", "--coordinate-text", "str", None, "manual/OCR coordinate text"),
            OptionSpec("screenshot", "--screenshot", "str", None, "optional screenshot path"),
        ),
    ),
    "player_training_recorder": ScriptSpec(
        name="player_training_recorder",
        path="scripts/player_training_recorder.py",
        description="observation-only player training recorder; records state/key timeline and optional screenshots, sends no gameplay input",
        default_args=("--duration", "180", "--interval", "0.25"),
        live_args=(),
        force_dry_run=True,
        options=(
            OptionSpec("duration", "--duration", "float", 180.0, "seconds to observe manual gameplay"),
            OptionSpec("interval", "--interval", "float", 0.25, "seconds between state/input samples"),
            OptionSpec("capture_screenshots", "--capture-screenshots", "bool", False, "also capture game-window screenshots for visual review"),
            OptionSpec("screenshot_backend", "--screenshot-backend", "str", "screen", "screenshot backend: screen for DirectX game content, printwindow for HWND diagnostics"),
            OptionSpec("record_mouse", "--record-mouse", "bool", True, "record mouse button edges and cursor positions; observation-only"),
            OptionSpec("screenshot_every", "--screenshot-every", "int", 4, "capture every N samples when screenshots are enabled"),
            OptionSpec("refresh_window_every", "--refresh-window-every", "float", 1.0, "seconds between window-geometry refreshes for mouse-relative coordinates"),
            OptionSpec("window_query", "--window-query", "str", "MT2Portugalia", "game window title/process query for screenshot capture"),
            OptionSpec("state_json", "--state-json", "str", "D:/Games/MT2Portugalia/app/hermes_state.json", "client-state JSON path"),
        ),
    ),
    "reroll_recorder": ScriptSpec(
        name="reroll_recorder",
        path="scripts/reroll_recorder.py",
        description="observation-only item reroll recorder; samples item attrs and writes roll-change artifacts, sends no gameplay input",
        default_args=("--duration", "180", "--interval", "0.10"),
        live_args=(),
        force_dry_run=True,
        options=(
            OptionSpec("duration", "--duration", "float", 180.0, "seconds to observe manual rerolling"),
            OptionSpec("interval", "--interval", "float", 0.10, "seconds between item-state samples"),
            OptionSpec("state_json", "--state-json", "str", "D:/Games/MT2Portugalia/app/hermes_state.json", "client-state JSON path with inventory/equipment attrs"),
            OptionSpec("target_slot", "--target-slot", "int", None, "optional inventory/equipment slot to track"),
            OptionSpec("target_vnum", "--target-vnum", "int", None, "optional item vnum to track"),
        ),
    ),
    "boss_farm_tracker": ScriptSpec(
        name="boss_farm_tracker",
        path="scripts/boss_farm_tracker.py",
        description="observation-first boss farm tracker; tracks spawn windows/farm counts scaffold, sends no gameplay input",
        default_args=("--duration", "3600", "--interval", "1.0", "--spawn-interval-minutes", "30", "--channels", "8"),
        live_args=(),
        force_dry_run=True,
        options=(
            OptionSpec("duration", "--duration", "float", 3600.0, "seconds to track the boss farm session"),
            OptionSpec("interval", "--interval", "float", 1.0, "seconds between tracker samples"),
            OptionSpec("state_json", "--state-json", "str", "D:/Games/MT2Portugalia/app/hermes_state.json", "client-state JSON path"),
            OptionSpec("boss_name", "--boss-name", "str", "", "optional boss name substring for target matching"),
            OptionSpec("spawn_interval_minutes", "--spawn-interval-minutes", "float", 30.0, "minutes between boss spawns"),
            OptionSpec("channels", "--channels", "int", 8, "number of channels to track"),
            OptionSpec("wait_menu", "--wait-menu", "str", "alterar personagem", "menu to wait in between spawn windows"),
            OptionSpec("loot_name", "--loot-name", "str", "Cofre do Chefe Orc", "loot item name used to count confirmed boss kills"),
            OptionSpec("loot_vnum", "--loot-vnum", "int", 50070, "loot item vnum used to count confirmed boss kills"),
        ),
    ),
    "chefe_orc_live_control": ScriptSpec(
        name="chefe_orc_live_control",
        path="scripts/chefe_orc_live_control.py",
        description="gated Chefe Orc select/attack/pickup/channel loop; dry-run by default, live requires explicit confirmation",
        default_args=("--duration", "15", "--interval", "0.25", "--max-kills", "1"),
        exclusive_live_group="combat",
        options=(
            OptionSpec("duration", "--duration", "float", 600.0, "seconds to run the gated boss-control loop"),
            OptionSpec("interval", "--interval", "float", 0.25, "seconds between state checks"),
            OptionSpec("state_json", "--state-json", "str", "D:/Games/MT2Portugalia/app/hermes_state.json", "client-state JSON path"),
            OptionSpec("boss_name", "--boss-name", "str", "Chefe Orc", "boss target substring required before attacking"),
            OptionSpec("loot_name", "--loot-name", "str", "Cofre do Chefe Orc", "loot item name used to confirm pickup"),
            OptionSpec("loot_vnum", "--loot-vnum", "int", 50070, "loot item vnum used to confirm pickup"),
            OptionSpec("max_kills", "--max-kills", "int", 0, "stop after this many confirmed kills; 0 means duration/stop only"),
            OptionSpec("attack_burst_seconds", "--attack-burst-seconds", "float", 0.6, "bounded Space pulse while selected target is Chefe Orc"),
            OptionSpec("pickup_spam_count", "--pickup-spam-count", "int", 12, "Z pickups after HP-zero/loot confirmation"),
            OptionSpec("pickup_spam_interval", "--pickup-spam-interval", "float", 0.08, "seconds between Z pickups"),
            OptionSpec("channel_rotate", "--channel-rotate", "bool", False, "after confirmed kill: press X and click next configured channel point"),
            OptionSpec("channel_click_points", "--channel-click-points", "str", "0.4990,0.3986;0.4990,0.4326;0.4990,0.4665;0.4990,0.5005;0.4990,0.5344;0.4990,0.5684;0.4990,0.6023;0.4990,0.6363", "semicolon-separated channel row click points as window fractions for CH1..CH8"),
            OptionSpec("channel_menu_delay_seconds", "--channel-menu-delay-seconds", "float", 1.25, "wait after pressing X before clicking channel"),
            OptionSpec("channel_switch_wait_seconds", "--channel-switch-wait-seconds", "float", 4.0, "wait after channel click before resuming search"),
            OptionSpec("window_query", "--window-query", "str", "MT2Portugalia", "target game window query"),
        ),
    ),
}


@dataclass
class RunRecord:
    run_id: str
    script: str
    mode: str
    command: list[str]
    pid: int
    log_path: str
    started_at: str
    process: subprocess.Popen = field(repr=False)
    stopped_at: str | None = None
    stop_requested: bool = False
    stop_file: str | None = None
    archived_at: str | None = None

    def status(self) -> dict:
        code = self.process.poll()
        return {
            "run_id": self.run_id,
            "script": self.script,
            "mode": self.mode,
            "pid": self.pid,
            "command": self.command,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "running": code is None,
            "exit_code": code,
            "log_path": self.log_path,
            "stop_requested": self.stop_requested,
            "stop_file": self.stop_file,
            "archived": self.archived_at is not None,
            "archived_at": self.archived_at,
        }


class ProcessRegistry:
    """Tracks only dashboard-started children.

    stop_all never scans global python processes. it only touches Popen handles
    created by this registry. that is the safety rail.
    """

    def __init__(self, project_root: str | Path, reports_dir: str | Path = "reports/dashboard_runs", *, default_state_json: str | Path | None = None, default_login_username: str | None = None, default_login_app_dir: str | Path | None = None):
        self.project_root = Path(project_root)
        self.default_state_json = str(default_state_json) if default_state_json is not None else None
        self.default_login_username = str(default_login_username) if default_login_username else None
        self.default_login_app_dir = str(default_login_app_dir) if default_login_app_dir else None
        self.reports_dir = self.project_root / reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, RunRecord] = {}
        self._lock = RLock()

    def _script_dict_with_defaults(self, spec: ScriptSpec) -> dict:
        data = spec.to_dict()
        if self.default_state_json:
            for opt in data.get("options", []):
                if opt.get("name") == "state_json":
                    opt["default"] = self.default_state_json
                if opt.get("name") == "username" and self.default_login_username:
                    opt["default"] = self.default_login_username
                if opt.get("name") == "app_dir" and self.default_login_app_dir:
                    opt["default"] = self.default_login_app_dir
        return data

    def list_scripts(self) -> list[dict]:
        return [self._script_dict_with_defaults(spec) for spec in DEFAULT_SCRIPTS.values()]

    def build_option_args(self, spec: ScriptSpec, options: dict[str, Any] | None) -> list[str]:
        options = dict(options or {})
        by_name = {opt.name: opt for opt in spec.options}
        args: list[str] = []
        if self.default_state_json and any(opt.name == "state_json" for opt in spec.options) and "state_json" not in options:
            options["state_json"] = self.default_state_json
        if spec.name == "login_mt2_local":
            if self.default_login_username and "username" not in options:
                options["username"] = self.default_login_username
            if self.default_login_app_dir and "app_dir" not in options:
                options["app_dir"] = self.default_login_app_dir
        for name, value in options.items():
            if value is None or value == "":
                continue
            if name not in by_name:
                raise ValueError(f"unknown option for {spec.name}: {name}")
            opt = by_name[name]
            if opt.type == "bool":
                if opt.coerce(value) == "true":
                    args.append(opt.flag)
                continue
            args.extend([opt.flag, opt.coerce(value)])
        return args

    @staticmethod
    def live_conflict_key(script_name: str, command_args: Iterable[str]) -> str | None:
        """Return the live-control conflict bucket for an argv.

        Buff-only keepalive is intentionally allowed to run beside one attack/combat
        loop. Duplicate buff keepers and duplicate movement/combat loops still block.
        """
        args = [str(arg) for arg in command_args]
        if script_name == "combat_metin_client_state":
            return "buff" if "--buff-only" in args else "combat"
        spec = DEFAULT_SCRIPTS.get(script_name)
        return spec.exclusive_live_group if spec else None

    def start(self, script_name: str, *, live: bool = False, confirm_live: bool = False, extra_args: Iterable[str] = (), options: dict[str, Any] | None = None) -> dict:
        if script_name not in DEFAULT_SCRIPTS:
            raise ValueError(f"unknown script: {script_name}")
        spec = DEFAULT_SCRIPTS[script_name]
        if spec.force_dry_run:
            live = False
            confirm_live = False
        mode = "live" if live else "dry-run"
        if live and not confirm_live:
            raise PermissionError("live start requires confirm_live=true")

        script_path = self.project_root / spec.path
        if not script_path.exists():
            raise FileNotFoundError(f"script not found: {script_path}")

        option_args = self.build_option_args(spec, options)
        if not live and spec.name == "learn_runaround_client_tsv":
            # Dry-run calibration must never be turned into real movement by UI defaults or stale form values.
            option_args = []
        args = list(spec.live_args if live else spec.default_args) + option_args + list(extra_args)
        proposed_conflict_key = self.live_conflict_key(spec.name, args) if live else None

        with self._lock:
            if live and proposed_conflict_key:
                for rec in self._runs.values():
                    if rec.mode != "live" or rec.process.poll() is not None:
                        continue
                    if self.live_conflict_key(rec.script, rec.command) == proposed_conflict_key:
                        raise RuntimeError(f"duplicate live {proposed_conflict_key} run blocked: {rec.run_id}")

            run_id = f"run-{uuid.uuid4().hex[:12]}"
            log_path = self.reports_dir / f"{run_id}-{script_name}.log"
            stop_file = self.reports_dir / f"{run_id}.stop"
            try:
                stop_file.unlink()
            except FileNotFoundError:
                pass
            cmd = [sys.executable, str(script_path), *args]
            log_file = log_path.open("ab", buffering=0)
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            pythonpath_parts = [str(self.project_root / "src"), str(self.project_root)]
            existing_pythonpath = env.get("PYTHONPATH")
            if existing_pythonpath:
                pythonpath_parts.append(existing_pythonpath)
            env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
            env["HERMES_RUN_ID"] = run_id
            env["HERMES_STOP_FILE"] = str(stop_file)
            buff_config = self.project_root / "config" / "buffs.json"
            combat_config = self.project_root / "config" / "combat.json"
            if buff_config.exists():
                env["METIN2_BUFF_CONFIG"] = str(buff_config)
            if combat_config.exists():
                env["METIN2_COMBAT_CONFIG"] = str(combat_config)
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.project_root),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
            )
            rec = RunRecord(run_id, script_name, mode, cmd, proc.pid, str(log_path), utc_now(), proc, stop_file=str(stop_file))
            self._runs[run_id] = rec
            return rec.status()

    def status(self, run_id: str | None = None, *, tail_bytes: int = 4096, include_archived: bool = False) -> list[dict] | dict:
        with self._lock:
            if run_id:
                rec = self._runs[run_id]
                data = rec.status()
                data["log_tail"] = self.tail(rec.log_path, tail_bytes)
                return data
            return [
                self.status(rid, tail_bytes=tail_bytes, include_archived=True)
                for rid, rec in list(self._runs.items())
                if include_archived or rec.archived_at is None
            ]

    def archive(self, run_id: str) -> dict:
        with self._lock:
            rec = self._runs[run_id]
            if rec.process.poll() is None:
                raise RuntimeError("cannot archive running run; stop it first")
            if rec.archived_at is None:
                rec.archived_at = utc_now()
        return self.status(run_id)

    def archive_all(self) -> list[dict]:
        """Archive every completed managed run; leave running runs visible."""
        with self._lock:
            now = utc_now()
            for rec in self._runs.values():
                if rec.process.poll() is None:
                    continue
                if rec.archived_at is None:
                    rec.archived_at = now
        return self.status(include_archived=True)

    def stop(self, run_id: str, *, timeout: float = 3.0) -> dict:
        with self._lock:
            rec = self._runs[run_id]
            rec.stop_requested = True
        self.release_stuck_keys()
        if rec.stop_file:
            Path(rec.stop_file).parent.mkdir(parents=True, exist_ok=True)
            Path(rec.stop_file).touch()
        proc = rec.process
        if proc.poll() is None:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=timeout)
        rec.stopped_at = utc_now()
        return self.status(run_id)

    def stop_all(self, *, timeout: float = 3.0) -> list[dict]:
        with self._lock:
            ids = list(self._runs)
        return [self.stop(rid, timeout=timeout) for rid in ids]

    def release_stuck_keys(self) -> None:
        # defensive only. no click or attack action is sent.
        try:
            subprocess.run([sys.executable, "-c", SAFE_KEY_RELEASE_CODE], timeout=1)
        except Exception:
            pass

    @staticmethod
    def tail(path: str | Path, max_bytes: int = 4096) -> str:
        p = Path(path)
        if not p.exists():
            return ""
        with p.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes), os.SEEK_SET)
            return fh.read().decode("utf-8", errors="replace")
