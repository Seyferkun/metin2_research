#!/usr/bin/env python
"""Sweep fixed-spawn Sapo de Pedra across channels.

Private Yoshy MT2Portugalia sandbox only. This script performs bounded cycles:
state preflight -> hold Space until Sapo destroyed -> pickup Z -> switch channel
-> wait for reload -> repeat. It does not move, combat-click, target-search, or
press potion keys.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for _path in (PROJECT_ROOT, SRC_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from metin2_research.live_input_lock import acquire_live_input_lease
from metin2_research.win_input import click_at, key_down, key_up, tap_key
from metin2_research.window_capture import capture_window_image, find_window
from scripts.target_hp_vision import estimate_target_hp_from_image

from fixed_sapo_space_control import (
    DEFAULT_CHANNEL_STATE,
    DEFAULT_STATE_JSON,
    channel_click_screen_point,
    distance_to_sapo,
    hp_value,
    parse_channel_click_points,
    pickup_then_channel_switch,
    player_from_state,
    read_state,
    release_space,
    robust_activate_window,
    sapo_probe_from_state,
    write_channel_cycle_state,
    target_name,
    write_summary,
)

DEFAULT_POINTS = "0.4990,0.3986;0.4990,0.4326;0.4990,0.4665;0.4990,0.5005;0.4990,0.5344;0.4990,0.5684;0.4990,0.6023;0.4990,0.6363"
DEFAULT_OUT = Path("reports/dashboard_runs/fixed_sapo_channel_sweep.jsonl")
DEFAULT_SUMMARY = Path("reports/dashboard_runs/fixed_sapo_channel_sweep_summary.json")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_this_script_elevated(argv: list[str], cwd: Path) -> int:
    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("fMask", ctypes.c_ulong),
            ("hwnd", ctypes.c_void_p),
            ("lpVerb", ctypes.c_wchar_p),
            ("lpFile", ctypes.c_wchar_p),
            ("lpParameters", ctypes.c_wchar_p),
            ("lpDirectory", ctypes.c_wchar_p),
            ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.c_void_p),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", ctypes.c_wchar_p),
            ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", ctypes.c_ulong),
            ("hIcon", ctypes.c_void_p),
            ("hProcess", ctypes.c_void_p),
        ]
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = subprocess.list2cmdline([str(Path(__file__).resolve()), *argv])
    info.lpDirectory = str(cwd)
    info.nShow = 1
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    print("relaunched_elevated_for_fixed_sapo_sweep approve_windows_uac_prompt waiting_for_elevated_child", flush=True)
    if info.hProcess:
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
        print(f"elevated_child_exit_code {exit_code.value}", flush=True)
        return int(exit_code.value)
    return 0


def emit(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(event, ensure_ascii=False, default=str), flush=True)


def state_brief(state: dict[str, Any]) -> dict[str, Any]:
    player = player_from_state(state)
    probe = sapo_probe_from_state(state)
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    return {
        "map": state.get("map"),
        "age_seconds": round(float(state.get("_age_seconds", 0.0)), 3) if state.get("_age_seconds") is not None else None,
        "player": {k: player.get(k) for k in ("name", "x", "y", "hp", "max_hp")},
        "target": {k: target.get(k) for k in ("name", "vid", "alive")},
        "sapo_probe": probe,
        "distance_to_sapo": round(distance_to_sapo(state), 3) if distance_to_sapo(state) is not None else None,
    }


def capture_safe(path: Path, window_query: str, screenshot_backend: str, out: Path, *, kind: str, run_id: str, cycle: int | None = None) -> str:
    try:
        window = find_window(window_query)
        capture_window_image(window, path, backend=screenshot_backend)
        emit(out, {"state": "SCREENSHOT", "kind": kind, "cycle": cycle, "path": str(path), "run_id": run_id})
        return str(path)
    except Exception as exc:
        emit(out, {"state": "SCREENSHOT_FAILED", "kind": kind, "cycle": cycle, "reason": str(exc), "run_id": run_id})
        return f"capture_failed:{exc}"


def wait_for_fresh_state(path: Path, *, max_age: float, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.time() + max(0.1, timeout)
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = read_state(path)
        age = last.get("_age_seconds")
        if age is not None and float(age) <= max_age:
            return last
        time.sleep(0.25)
    return last


def should_stop(stop_file: Path | None) -> bool:
    return bool(stop_file and stop_file.exists())


def blocked_cycle_action(reason: str, *, repeat_while_running: bool) -> str:
    """Return stop/continue for a preflight block.

    One-shot sweep treats any block as terminal. Continuous keep-running mode is
    different: a missing/far fixed-spawn Sapo on the current channel means this
    channel has nothing useful to attack, so advance to the next channel instead
    of exiting the managed run. Hard safety blocks still stop immediately.
    """
    if not repeat_while_running:
        return "stop"
    if reason in {"too_far_from_fixed_spawn", "sapo_probe_not_alive"}:
        return "continue"
    return "stop"


def run_channel_switch_with_input_lock(
    switch_fn,
    *,
    lock_path: Path,
    run_id: str,
    timeout_seconds: float = 12.0,
    out: Path | None = None,
    cycle: int | None = None,
    round_no: int | None = None,
    action: str = "channel_switch",
):
    with acquire_live_input_lease(
        Path(lock_path),
        owner="fixed_sapo_sweep",
        run_id=run_id,
        action=action,
        ttl_seconds=90.0,
        timeout_seconds=timeout_seconds,
    ):
        if out is not None:
            emit(out, {"state": "SWEEP_INPUT_LOCK_ACQUIRED", "cycle": cycle, "round": round_no, "action": action, "lock_path": str(lock_path), "run_id": run_id})
        try:
            return switch_fn()
        finally:
            if out is not None:
                emit(out, {"state": "SWEEP_INPUT_LOCK_RELEASED", "cycle": cycle, "round": round_no, "action": action, "lock_path": str(lock_path), "run_id": run_id})


def channel_menu_visible_in_screenshot(path: Path) -> bool:

    try:
        from PIL import Image

        im = Image.open(path).convert("RGB")
    except Exception:
        return False
    width, height = im.size
    left, top = int(width * 0.42), int(height * 0.33)
    right, bottom = int(width * 0.59), int(height * 0.71)
    total = max(1, (right - left) * (bottom - top))
    red_brown = 0
    grey_frame = 0
    for y in range(top, bottom):
        for x in range(left, right):
            r, g, b = im.getpixel((x, y))
            if r > 90 and 20 < g < 95 and b < 80 and r > g + 20:
                red_brown += 1
            if abs(r - g) < 20 and abs(g - b) < 20 and 70 < r < 170:
                grey_frame += 1
    return (red_brown / total) > 0.006 and (grey_frame / total) > 0.055


def finalize_deferred_channel_state(switch_result: dict[str, Any], *, run_id: str, menu_visible: bool) -> dict[str, Any]:
    """Write channel-cycle state only after the post-switch menu is gone."""
    if menu_visible:
        switch_result["verified"] = False
        switch_result["unverified_reason"] = "channel_menu_still_visible"
        return switch_result
    if switch_result.get("channel_auto_cycle") and switch_result.get("cycle_state_deferred"):
        write_channel_cycle_state(
            Path(str(switch_result["channel_cycle_state_path"])),
            used_index=int(switch_result["channel_index"]),
            points_count=int(switch_result.get("points_count") or 1),
            run_id=run_id,
        )
        switch_result["cycle_state_written"] = True
        switch_result.pop("cycle_state_deferred", None)
    switch_result["verified"] = True
    return switch_result


def finalize_channel_switch_with_retries(
    switch_result: dict[str, Any],
    *,
    run_id: str,
    cycle: int,
    round_no: int,
    screenshot_dir: Path,
    window_query: str,
    screenshot_backend: str,
    out: Path,
    initial_menu_visible: bool,
    max_retries: int = 2,
    retry_wait_seconds: float = 1.25,
    click_fn=click_at,
    capture_fn=capture_safe,
    menu_detector=channel_menu_visible_in_screenshot,
    sleep_fn=time.sleep,
) -> dict[str, Any]:
    """Retry a channel-row click when verification still sees the channel menu.

    A live channel row click can occasionally be ignored while the client is
    busy, leaving `Mudar de Canal` open and the current channel unchanged.  In
    keep-running mode that should get a small bounded retry before we abort the
    sweep as unconfirmed.  The cycle-state write is still deferred until a
    retry screenshot confirms the menu is gone.
    """
    menu_visible = bool(initial_menu_visible)
    retries: list[dict[str, Any]] = []
    if menu_visible:
        try:
            sx, sy = switch_result.get("screen_point") or [None, None]
            sx, sy = int(sx), int(sy)
        except Exception:
            sx = sy = None
        if sx is not None and sy is not None:
            for retry in range(1, max(0, int(max_retries)) + 1):
                click_fn(sx, sy)
                sleep_fn(max(0.0, float(retry_wait_seconds)))
                retry_path = screenshot_dir / f"{run_id}_r{round_no:02d}_cycle{cycle:02d}_switch_retry{retry:02d}.jpg"
                capture_fn(retry_path, window_query, screenshot_backend, out, kind="switch_retry", run_id=run_id, cycle=cycle)
                menu_visible = menu_detector(retry_path)
                attempt = {"retry": retry, "screen_point": [sx, sy], "menu_visible": menu_visible}
                retries.append(attempt)
                emit(out, {"state": "SWEEP_CHANNEL_SWITCH_RETRY", "cycle": cycle, "round": round_no, **attempt, "run_id": run_id})
                if not menu_visible:
                    break
    if retries:
        switch_result["retry_attempts"] = retries
        switch_result["retry_count"] = len(retries)
    return finalize_deferred_channel_state(switch_result, run_id=run_id, menu_visible=menu_visible)


def low_dps_adjustment_key(state: dict[str, Any], *, deadzone: float = 40.0) -> str | None:
    player = player_from_state(state)
    probe = sapo_probe_from_state(state)
    pos = probe.get("pixel_position")
    if not isinstance(pos, (list, tuple)) or len(pos) < 2:
        return None
    try:
        dx = float(pos[0]) - float(player.get("x"))
        dy = float(pos[1]) - float(player.get("y"))
    except Exception:
        return None
    if abs(dx) <= deadzone and abs(dy) <= deadzone:
        return None
    if abs(dx) >= abs(dy):
        return "d" if dx > 0 else "a"
    return "s" if dy > 0 else "w"


def target_hp_pct_from_state_or_screen(state: dict[str, Any], *, window_query: str, screenshot_backend: str, image_path: Path) -> tuple[float | None, str | None, dict[str, Any] | None]:
    target = state.get("target") if isinstance(state.get("target"), dict) else {}
    for source, raw in (("target.hp_pct", target.get("hp_pct")), ("state.target_hp_pct", state.get("target_hp_pct"))):
        try:
            if raw is not None:
                return float(raw), source, None
        except Exception:
            pass
    try:
        window = find_window(window_query)
        capture_window_image(window, image_path, backend=screenshot_backend)
        visual = estimate_target_hp_from_image(image_path)
        if visual.get("available"):
            return float(visual["hp_pct"]), "visual_target_bar_hp_pct", visual
        return None, None, visual
    except Exception as exc:
        return None, None, {"available": False, "reason": str(exc)}


def low_dps_drop_rate(
    hp_samples: list[tuple[float, float]],
    *,
    min_span_seconds: float,
    noise_margin_pct: float,
) -> float | None:
    """Return robust HP drop pct/sec, or None until enough stable history exists.

    Visual target-bar HP is noisy: leaf/name overlays can make estimates jump up
    and down by a few percentage points.  The old first-vs-last slope treated an
    upward noise jump as negative DPS and nudged every tick.  For movement
    decisions, compare the best low value in the first third of the window with
    the best low value in the last third, and ignore drops smaller than the
    calibrated visual-noise margin.
    """
    if len(hp_samples) < 4:
        return None
    span = max(0.0, hp_samples[-1][0] - hp_samples[0][0])
    if span < max(0.1, float(min_span_seconds)):
        return None
    ordered = sorted(hp_samples, key=lambda row: row[0])
    third = max(2, len(ordered) // 3)
    early_low = min(v for _t, v in ordered[:third])
    recent_low = min(v for _t, v in ordered[-third:])
    drop = early_low - recent_low
    if drop <= max(0.0, float(noise_margin_pct)):
        return 0.0
    return drop / span


OPPOSITE_NUDGE_KEY = {"w": "s", "s": "w", "a": "d", "d": "a"}


class LowDpsNudgeController:
    """Stateful hill-climb nudge controller for fixed-spawn Sapo DPS.

    The old nudge was stateless: every low-DPS tick tapped toward Sapo and then
    immediately cancelled the movement.  That creates constant motion but never
    learns whether the new position improved DPS.  This controller probes one
    small step, waits for the next DPS estimate, keeps the step if DPS improves,
    and undoes it if DPS gets worse or stays flat.
    """

    def __init__(self, *, cooldown_seconds: float = 6.0, improvement_margin: float = 0.15) -> None:
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.improvement_margin = max(0.0, float(improvement_margin))
        self.pending_key: str | None = None
        self.pending_baseline: float | None = None
        self.last_action_at = 0.0
        self._next_direction = 0

    def choose_key(self, preferred_key: str | None) -> str | None:
        candidates = [k for k in (preferred_key, "a", "d", "w", "s") if k in OPPOSITE_NUDGE_KEY]
        if not candidates:
            return None
        # De-duplicate while preserving the preferred direction first, then
        # rotate alternatives so repeated probes do not hammer one bad axis.
        unique = list(dict.fromkeys(candidates))
        key = unique[self._next_direction % len(unique)]
        self._next_direction += 1
        return key

    def update(self, *, dps: float, threshold: float, now: float, preferred_key: str | None) -> dict[str, Any] | None:
        if self.pending_key:
            baseline = self.pending_baseline if self.pending_baseline is not None else dps
            improvement = dps - baseline
            key = self.pending_key
            self.pending_key = None
            self.pending_baseline = None
            self.last_action_at = now
            if improvement >= self.improvement_margin or dps >= threshold:
                return {"kind": "keep", "key": key, "dps_before": baseline, "dps_after": dps, "dps_improvement": improvement}
            return {"kind": "undo", "key": OPPOSITE_NUDGE_KEY[key], "undo_for": key, "dps_before": baseline, "dps_after": dps, "dps_improvement": improvement}

        if dps >= threshold:
            return None
        if (now - self.last_action_at) < self.cooldown_seconds:
            return None
        key = self.choose_key(preferred_key)
        if not key:
            return None
        self.pending_key = key
        self.pending_baseline = dps
        self.last_action_at = now
        return {"kind": "probe", "key": key, "dps_before": dps, "dps_after": dps, "dps_improvement": 0.0}


def hold_space_until_destroyed(
    *,
    state_json: Path,
    out: Path,
    run_id: str,
    cycle: int,
    duration: float,
    hp_stop_threshold: int,
    stop_file: Path | None = None,
    low_dps_adjust: bool = False,
    low_dps_threshold: float = 0.2,
    low_dps_window_seconds: float = 8.0,
    low_dps_min_window_seconds: float = 8.0,
    low_dps_noise_margin_pct: float = 3.0,
    low_dps_adjust_cooldown_seconds: float = 6.0,
    low_dps_improvement_margin: float = 0.15,
    adjust_hold_seconds: float = 0.18,
    adjust_deadzone: float = 40.0,
    window_query: str = "MT2Portugalia",
    screenshot_backend: str = "screen",
) -> dict[str, Any]:
    release_space()
    key_down("space")
    start = time.time()
    hp_min = None
    reason = "duration_elapsed"
    destroyed = False
    ticks = 0
    hp_samples: list[tuple[float, float]] = []
    adjustments = 0
    nudge_controller = LowDpsNudgeController(
        cooldown_seconds=low_dps_adjust_cooldown_seconds,
        improvement_margin=low_dps_improvement_margin,
    )
    try:
        deadline = time.time() + max(0.1, duration)
        while time.time() < deadline and not should_stop(stop_file):
            time.sleep(1.0)
            ticks += 1
            try:
                state = read_state(state_json)
            except Exception as exc:
                reason = f"state_read_failed: {exc}"
                break
            hp = hp_value(state)
            if hp is not None:
                hp_min = hp if hp_min is None else min(hp_min, hp)
            probe = sapo_probe_from_state(state)
            target = state.get("target") if isinstance(state.get("target"), dict) else {}
            probe_alive = probe.get("alive")
            target_alive = target.get("alive")
            target_is_sapo = str(target.get("name") or "").lower() == "sapo de pedra"
            target_hp_pct = None
            target_hp_source = None
            visual_meta = None
            if low_dps_adjust:
                target_hp_pct, target_hp_source, visual_meta = target_hp_pct_from_state_or_screen(
                    state,
                    window_query=window_query,
                    screenshot_backend=screenshot_backend,
                    image_path=out.with_name(f"{run_id}_cycle{cycle:02d}_hp_{ticks:04d}.jpg"),
                )
                # The visual fallback measures the currently selected target bar.
                # If a demon steals target, do not use its HP as Sapo DPS proof;
                # clear history so we do not immediately nudge from stale/noisy
                # Sapo samples when target returns.
                if target.get("name") and not target_is_sapo and target_hp_source == "visual_target_bar_hp_pct":
                    hp_samples = []
                elif target_hp_pct is not None:
                    now = time.time()
                    hp_samples.append((now, target_hp_pct))
                    hp_samples = [(t, v) for t, v in hp_samples if now - t <= max(1.0, low_dps_window_seconds)]
                    dps = low_dps_drop_rate(
                        hp_samples,
                        min_span_seconds=low_dps_min_window_seconds,
                        noise_margin_pct=low_dps_noise_margin_pct,
                    )
                    if dps is not None:
                        preferred_key = low_dps_adjustment_key(state, deadzone=adjust_deadzone)
                        nudge = nudge_controller.update(
                            dps=dps,
                            threshold=low_dps_threshold,
                            now=now,
                            preferred_key=preferred_key,
                        )
                        if nudge and nudge.get("kind") in {"probe", "undo"}:
                            hold = max(0.03, min(0.5, adjust_hold_seconds))
                            tap_key(str(nudge["key"]), hold=hold)
                            adjustments += 1
                            emit(out, {"state": "SWEEP_LOW_DPS_ADJUST", "cycle": cycle, "t": ticks, "action": nudge.get("kind"), "key": nudge.get("key"), "undo_for": nudge.get("undo_for"), "preferred_key": preferred_key, "dps_pct_per_sec": round(dps, 3), "dps_before": round(float(nudge.get("dps_before", dps)), 3), "dps_after": round(float(nudge.get("dps_after", dps)), 3), "dps_improvement": round(float(nudge.get("dps_improvement", 0.0)), 3), "hp_pct": round(target_hp_pct, 3), "hp_source": target_hp_source, "samples": len(hp_samples), "sample_span_seconds": round(hp_samples[-1][0] - hp_samples[0][0], 3), "noise_margin_pct": low_dps_noise_margin_pct, "cooldown_seconds": low_dps_adjust_cooldown_seconds, "improvement_margin": low_dps_improvement_margin, "visual_hp": visual_meta, "run_id": run_id})
                        elif nudge and nudge.get("kind") == "keep":
                            emit(out, {"state": "SWEEP_LOW_DPS_KEEP", "cycle": cycle, "t": ticks, "key": nudge.get("key"), "preferred_key": preferred_key, "dps_pct_per_sec": round(dps, 3), "dps_before": round(float(nudge.get("dps_before", dps)), 3), "dps_after": round(float(nudge.get("dps_after", dps)), 3), "dps_improvement": round(float(nudge.get("dps_improvement", 0.0)), 3), "hp_pct": round(target_hp_pct, 3), "hp_source": target_hp_source, "samples": len(hp_samples), "sample_span_seconds": round(hp_samples[-1][0] - hp_samples[0][0], 3), "run_id": run_id})
            if ticks == 1 or ticks % 5 == 0 or probe_alive is False or target_is_sapo or (hp is not None and hp <= hp_stop_threshold):
                emit(out, {"state": "SWEEP_SPACE_TICK", "cycle": cycle, "t": ticks, "hp": hp, "target_name": target.get("name"), "target_alive": target_alive, "probe_alive": probe_alive, "target_hp_pct": target_hp_pct, "target_hp_source": target_hp_source, "adjustments": adjustments, "run_id": run_id})
            if (target_is_sapo and target_alive is False) or probe_alive is False:
                destroyed = True
                reason = "sapo_destroyed"
                break
            if hp is not None and hp <= 0:
                reason = "player_dead"
                break
            if hp is not None and hp <= hp_stop_threshold:
                reason = "hp_stop_threshold"
                break
        if should_stop(stop_file):
            reason = "stop_file"
    finally:
        release_space()
    return {"destroyed": destroyed, "reason": reason, "elapsed_seconds": round(time.time() - start, 3), "hp_min": hp_min, "ticks": ticks, "adjustments": adjustments}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--elevate", action="store_true", help="relaunch through UAC so Space/Z/X/click input reaches an elevated game client")
    ap.add_argument("--json-state", dest="state_json", type=Path, default=DEFAULT_STATE_JSON)
    ap.add_argument("--window-query", default="MT2Portugalia")
    ap.add_argument("--channels", type=int, default=8, help="max channel cycles to attempt; final cycle does not need a channel switch")
    ap.add_argument("--cycle-duration", type=float, default=240.0, help="max seconds to hold Space per channel")
    ap.add_argument("--load-wait-seconds", type=float, default=8.0, help="wait after channel click before next channel preflight")
    ap.add_argument("--hp-stop-threshold", type=int, default=2500)
    ap.add_argument("--max-state-age-seconds", type=float, default=2.0)
    ap.add_argument("--min-distance", type=float, default=350.0)
    ap.add_argument("--no-distance-block", action="store_true")
    ap.add_argument("--pickup-count", type=int, default=20)
    ap.add_argument("--pickup-interval", type=float, default=0.08)
    ap.add_argument("--channel-click-points", default=DEFAULT_POINTS)
    ap.add_argument("--channel-index", type=int, default=1, help="seed channel index when no cycle state exists")
    ap.add_argument("--channel-auto-cycle", action="store_true", default=True)
    ap.add_argument("--channel-cycle-state", type=Path, default=DEFAULT_CHANNEL_STATE)
    ap.add_argument("--channel-menu-delay-seconds", type=float, default=1.25)
    ap.add_argument("--channel-switch-wait-seconds", type=float, default=5.0)
    ap.add_argument("--screenshot-backend", default="screen", choices=["screen", "printwindow"])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--summary-out", type=Path, default=DEFAULT_SUMMARY)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--repeat-while-running", action="store_true", help="after a full sweep, keep cycling channels until dashboard stop file is requested")
    ap.add_argument("--stop-file", type=Path, default=None, help="cooperative stop-file path; defaults from HERMES_STOP_FILE")
    ap.add_argument("--live-input-lock", type=Path, default=Path("reports/dashboard_runs/live_input.lock"), help="shared live-input mutex path used to coordinate channel switching with buff keeper")
    ap.add_argument("--live-input-lock-timeout-seconds", type=float, default=12.0, help="seconds to wait for the shared live-input mutex")
    ap.add_argument("--low-dps-adjust", action="store_true", help="when target-bar DPS is low, tap small WASD nudges while Space remains held")
    ap.add_argument("--low-dps-threshold", type=float, default=0.2, help="minimum target HP percentage-points/sec before a WASD nudge is attempted")
    ap.add_argument("--low-dps-window-seconds", type=float, default=8.0)
    ap.add_argument("--low-dps-min-window-seconds", type=float, default=8.0, help="minimum HP sample span before DPS-based nudging is allowed")
    ap.add_argument("--low-dps-noise-margin", type=float, default=3.0, help="ignore target HP drops smaller than this many percentage points as visual noise")
    ap.add_argument("--low-dps-adjust-cooldown", type=float, default=6.0, help="minimum seconds between low-DPS WASD nudges")
    ap.add_argument("--low-dps-improvement-margin", type=float, default=0.15, help="minimum DPS gain to keep a probe nudge position")
    ap.add_argument("--adjust-hold-seconds", type=float, default=0.18)
    ap.add_argument("--adjust-deadzone", type=float, default=40.0)
    args = ap.parse_args()

    if args.elevate and not is_admin():
        return relaunch_this_script_elevated([arg for arg in sys.argv[1:] if arg != "--elevate"], PROJECT_ROOT)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("", encoding="utf-8")
    run_id = args.run_id or f"fixed_sapo_sweep_{int(time.time())}"
    start = time.time()
    screenshot_dir = args.out.with_name(f"{run_id}_screens")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    points = parse_channel_click_points(args.channel_click_points)
    max_channels = max(1, min(int(args.channels), len(points) + 1))
    cycles: list[dict[str, Any]] = []
    outcome = "dry_run" if not args.live else "started"
    reason = "not_started"
    stop_file = args.stop_file or (Path(os.environ["HERMES_STOP_FILE"]) if os.environ.get("HERMES_STOP_FILE") else None)

    emit(args.out, {"state": "SWEEP_START", "run_id": run_id, "live": bool(args.live), "channels": max_channels, "repeat_while_running": bool(args.repeat_while_running), "low_dps_adjust": bool(args.low_dps_adjust), "movement_limited_to_low_dps_wasd": bool(args.low_dps_adjust), "no_combat_click": True, "no_potion_1": True})
    pre = capture_safe(screenshot_dir / f"{run_id}_pre.jpg", args.window_query, args.screenshot_backend, args.out, kind="pre", run_id=run_id)
    first_state = wait_for_fresh_state(args.state_json, max_age=args.max_state_age_seconds, timeout=3.0)
    first_brief = state_brief(first_state)

    if not args.live:
        # Dry-run resolves the auto-cycle rows without pressing keys/clicks.
        dummy_window = find_window(args.window_query)
        for cycle in range(1, max_channels + 1):
            if cycle == max_channels and not args.repeat_while_running:
                switch_preview = {"final_channel": True, "would_switch": False, "pickup_count": args.pickup_count}
            else:
                try:
                    switch_preview = pickup_then_channel_switch(
                        window=dummy_window,
                        window_query=args.window_query,
                        points_spec=args.channel_click_points,
                        channel_index=args.channel_index + cycle - 1,
                        channel_auto_cycle=False,
                        channel_cycle_state=args.channel_cycle_state,
                        pickup_count=args.pickup_count,
                        pickup_interval=args.pickup_interval,
                        menu_delay=args.channel_menu_delay_seconds,
                        switch_wait=args.channel_switch_wait_seconds,
                        live=False,
                        run_id=run_id,
                    )
                except Exception as exc:
                    switch_preview = {"error": str(exc)}
            cycles.append({"cycle": cycle, "dry_run": True, "preflight": first_brief, "switch_preview": switch_preview})
        summary = {"run_id": run_id, "outcome": "dry_run", "reason": "dry_run_only", "cycles": cycles, "pre_screenshot": pre, "log": str(args.out), "repeat_while_running": bool(args.repeat_while_running), "low_dps_adjust": bool(args.low_dps_adjust), "movement_limited_to_low_dps_wasd": bool(args.low_dps_adjust), "no_potion_1": True}
        write_summary(args.summary_out, summary)
        emit(args.out, {"state": "SWEEP_FINISH", **summary})
        return 0

    try:
        window = find_window(args.window_query)
        robust_activate_window(window)
    except Exception as exc:
        reason = f"window_focus_failed: {exc}"
        summary = {"run_id": run_id, "outcome": "blocked", "reason": reason, "cycles": cycles, "preflight": first_brief, "pre_screenshot": pre}
        write_summary(args.summary_out, summary)
        emit(args.out, {"state": "SWEEP_BLOCKED", **summary})
        return 2

    round_no = 0
    while True:
        round_no += 1
        if should_stop(stop_file):
            outcome, reason = "stopped", "stop_file"
            emit(args.out, {"state": "SWEEP_STOP_REQUESTED", "round": round_no, "run_id": run_id})
            break
        emit(args.out, {"state": "SWEEP_ROUND_START", "round": round_no, "run_id": run_id})
        for cycle in range(1, max_channels + 1):
            if should_stop(stop_file):
                outcome, reason = "stopped", "stop_file"
                emit(args.out, {"state": "SWEEP_STOP_REQUESTED", "cycle": cycle, "round": round_no, "run_id": run_id})
                break
            cycle_start = time.time()
            state = wait_for_fresh_state(args.state_json, max_age=args.max_state_age_seconds, timeout=10.0)
            brief = state_brief(state)
            probe = sapo_probe_from_state(state)
            hp = hp_value(state)
            dist = distance_to_sapo(state)
            cycle_record: dict[str, Any] = {"cycle": cycle, "round": round_no, "preflight": brief}
            capture_safe(screenshot_dir / f"{run_id}_r{round_no:02d}_cycle{cycle:02d}_pre.jpg", args.window_query, args.screenshot_backend, args.out, kind="cycle_pre", run_id=run_id, cycle=cycle)
            emit(args.out, {"state": "SWEEP_CYCLE_PREFLIGHT", "cycle": cycle, "round": round_no, "preflight": brief, "run_id": run_id})

            block_reason = None
            if state.get("_age_seconds") is None or float(state.get("_age_seconds")) > args.max_state_age_seconds:
                block_reason = "state_stale"
            elif hp is not None and hp <= 0:
                block_reason = "player_dead"
            elif hp is not None and hp <= args.hp_stop_threshold:
                block_reason = "hp_stop_threshold"
            elif not probe or probe.get("alive") is not True:
                block_reason = "sapo_probe_not_alive"
            elif dist is not None and dist > args.min_distance and not args.no_distance_block:
                block_reason = "too_far_from_fixed_spawn"
            if block_reason:
                action = blocked_cycle_action(block_reason, repeat_while_running=args.repeat_while_running)
                cycle_record.update({"outcome": "skipped" if action == "continue" else "blocked", "reason": block_reason, "elapsed_seconds": round(time.time() - cycle_start, 3)})
                emit(args.out, {"state": "SWEEP_CYCLE_SKIPPED" if action == "continue" else "SWEEP_CYCLE_BLOCKED", "cycle": cycle, "round": round_no, "reason": block_reason, "action": action, "run_id": run_id})
                if action == "continue":
                    try:
                        def _skip_channel_advance():
                            window = find_window(args.window_query)
                            switch_result = pickup_then_channel_switch(
                                window=window,
                                window_query=args.window_query,
                                points_spec=args.channel_click_points,
                                channel_index=args.channel_index,
                                channel_auto_cycle=args.channel_auto_cycle,
                                channel_cycle_state=args.channel_cycle_state,
                                pickup_count=0,
                                pickup_interval=args.pickup_interval,
                                menu_delay=args.channel_menu_delay_seconds,
                                switch_wait=args.channel_switch_wait_seconds,
                                live=True,
                                run_id=run_id,
                                defer_cycle_state=True,
                            )
                            verify_path = screenshot_dir / f"{run_id}_r{round_no:02d}_cycle{cycle:02d}_switch_check.jpg"
                            capture_safe(verify_path, args.window_query, args.screenshot_backend, args.out, kind="switch_check", run_id=run_id, cycle=cycle)
                            menu_visible = channel_menu_visible_in_screenshot(verify_path)
                            switch_result = finalize_channel_switch_with_retries(
                                switch_result,
                                run_id=run_id,
                                cycle=cycle,
                                round_no=round_no,
                                screenshot_dir=screenshot_dir,
                                window_query=args.window_query,
                                screenshot_backend=args.screenshot_backend,
                                out=args.out,
                                initial_menu_visible=menu_visible,
                            )
                            cycle_record["channel_switch_result"] = switch_result
                            if not switch_result.get("verified"):
                                return {"switch_result": switch_result, "post_state": None}
                            emit(args.out, {"state": "SWEEP_CHANNEL_SWITCH_SENT", "cycle": cycle, "round": round_no, "result": switch_result, "skip_reason": block_reason, "run_id": run_id})
                            if args.load_wait_seconds > 0:
                                emit(args.out, {"state": "SWEEP_LOAD_WAIT", "cycle": cycle, "round": round_no, "seconds": args.load_wait_seconds, "skip_reason": block_reason, "run_id": run_id})
                                deadline = time.time() + args.load_wait_seconds
                                while time.time() < deadline and not should_stop(stop_file):
                                    time.sleep(min(0.25, max(0.0, deadline - time.time())))
                            try:
                                post_state = wait_for_fresh_state(args.state_json, max_age=args.max_state_age_seconds, timeout=10.0)
                            except Exception:
                                post_state = None
                            return {"switch_result": switch_result, "post_state": post_state}

                        transition_result = run_channel_switch_with_input_lock(
                            _skip_channel_advance,
                            lock_path=args.live_input_lock,
                            run_id=run_id,
                            timeout_seconds=args.live_input_lock_timeout_seconds,
                            out=args.out,
                            cycle=cycle,
                            round_no=round_no,
                            action="channel_switch",
                        )
                        switch_result = transition_result["switch_result"]
                        if not switch_result.get("verified"):
                            cycle_record.update({"outcome": "channel_switch_unconfirmed", "reason": switch_result.get("unverified_reason"), "skip_reason": block_reason, "elapsed_seconds": round(time.time() - cycle_start, 3)})
                            cycles.append(cycle_record)
                            outcome, reason = "channel_switch_unconfirmed", str(switch_result.get("unverified_reason"))
                            emit(args.out, {"state": "SWEEP_CHANNEL_SWITCH_UNCONFIRMED", "cycle": cycle, "round": round_no, "result": switch_result, "skip_reason": block_reason, "run_id": run_id})
                            break
                        if transition_result.get("post_state") is not None:
                            cycle_record["post_switch_state"] = state_brief(transition_result["post_state"])
                        cycle_record["elapsed_seconds"] = round(time.time() - cycle_start, 3)
                        cycles.append(cycle_record)
                        outcome, reason = "completed", "round_channel_attempted"
                        continue
                    except Exception as exc:
                        cycle_record.update({"outcome": "channel_switch_failed", "reason": str(exc), "skip_reason": block_reason, "elapsed_seconds": round(time.time() - cycle_start, 3)})
                        cycles.append(cycle_record)
                        outcome, reason = "channel_switch_failed", str(exc)
                        emit(args.out, {"state": "SWEEP_CHANNEL_SWITCH_FAILED", "cycle": cycle, "round": round_no, "reason": str(exc), "skip_reason": block_reason, "run_id": run_id})
                        break
                cycles.append(cycle_record)
                outcome, reason = "blocked", block_reason
                break

            attack = hold_space_until_destroyed(
                state_json=args.state_json,
                out=args.out,
                run_id=run_id,
                cycle=cycle,
                duration=args.cycle_duration,
                hp_stop_threshold=args.hp_stop_threshold,
                stop_file=stop_file,
                low_dps_adjust=args.low_dps_adjust,
                low_dps_threshold=args.low_dps_threshold,
                low_dps_window_seconds=args.low_dps_window_seconds,
                low_dps_min_window_seconds=args.low_dps_min_window_seconds,
                low_dps_noise_margin_pct=args.low_dps_noise_margin,
                low_dps_adjust_cooldown_seconds=args.low_dps_adjust_cooldown,
                low_dps_improvement_margin=args.low_dps_improvement_margin,
                adjust_hold_seconds=args.adjust_hold_seconds,
                adjust_deadzone=args.adjust_deadzone,
                window_query=args.window_query,
                screenshot_backend=args.screenshot_backend,
            )
            cycle_record["attack"] = attack
            if not attack.get("destroyed"):
                cycle_record.update({"outcome": "stopped", "reason": attack.get("reason"), "elapsed_seconds": round(time.time() - cycle_start, 3)})
                cycles.append(cycle_record)
                outcome, reason = "stopped", str(attack.get("reason"))
                emit(args.out, {"state": "SWEEP_STOPPED", "cycle": cycle, "round": round_no, "reason": reason, "run_id": run_id})
                break

            final_without_switch = cycle == max_channels and not args.repeat_while_running
            if final_without_switch:
                pickup_sent = 0
                for _idx in range(max(0, int(args.pickup_count))):
                    if should_stop(stop_file):
                        break
                    tap_key("z", hold=0.03)
                    pickup_sent += 1
                    time.sleep(max(0.02, args.pickup_interval))
                emit(args.out, {"state": "SWEEP_PICKUP_SENT", "cycle": cycle, "round": round_no, "count": pickup_sent, "run_id": run_id})
                cycle_record["pickup_sent"] = pickup_sent
                cycle_record.update({"outcome": "destroyed_final_channel", "reason": "final_channel_no_switch_needed", "elapsed_seconds": round(time.time() - cycle_start, 3)})
                capture_safe(screenshot_dir / f"{run_id}_r{round_no:02d}_cycle{cycle:02d}_post.jpg", args.window_query, args.screenshot_backend, args.out, kind="cycle_post", run_id=run_id, cycle=cycle)
                cycles.append(cycle_record)
                outcome, reason = "completed", "all_requested_channels_attempted"
                emit(args.out, {"state": "SWEEP_FINAL_CHANNEL_DONE", "cycle": cycle, "round": round_no, "run_id": run_id})
                break

            try:
                def _pickup_channel_transition():
                    pickup_sent = 0
                    for _idx in range(max(0, int(args.pickup_count))):
                        if should_stop(stop_file):
                            break
                        tap_key("z", hold=0.03)
                        pickup_sent += 1
                        time.sleep(max(0.02, args.pickup_interval))
                    emit(args.out, {"state": "SWEEP_PICKUP_SENT", "cycle": cycle, "round": round_no, "count": pickup_sent, "run_id": run_id})
                    cycle_record["pickup_sent"] = pickup_sent
                    window = find_window(args.window_query)
                    switch_result = pickup_then_channel_switch(
                        window=window,
                        window_query=args.window_query,
                        points_spec=args.channel_click_points,
                        channel_index=args.channel_index,
                        channel_auto_cycle=args.channel_auto_cycle,
                        channel_cycle_state=args.channel_cycle_state,
                        pickup_count=0,
                        pickup_interval=args.pickup_interval,
                        menu_delay=args.channel_menu_delay_seconds,
                        switch_wait=args.channel_switch_wait_seconds,
                        live=True,
                        run_id=run_id,
                        defer_cycle_state=True,
                    )
                    verify_path = screenshot_dir / f"{run_id}_r{round_no:02d}_cycle{cycle:02d}_switch_check.jpg"
                    capture_safe(verify_path, args.window_query, args.screenshot_backend, args.out, kind="switch_check", run_id=run_id, cycle=cycle)
                    menu_visible = channel_menu_visible_in_screenshot(verify_path)
                    switch_result = finalize_channel_switch_with_retries(
                        switch_result,
                        run_id=run_id,
                        cycle=cycle,
                        round_no=round_no,
                        screenshot_dir=screenshot_dir,
                        window_query=args.window_query,
                        screenshot_backend=args.screenshot_backend,
                        out=args.out,
                        initial_menu_visible=menu_visible,
                    )
                    cycle_record["channel_switch_result"] = switch_result
                    if not switch_result.get("verified"):
                        return {"switch_result": switch_result, "post_state": None}
                    emit(args.out, {"state": "SWEEP_CHANNEL_SWITCH_SENT", "cycle": cycle, "round": round_no, "result": switch_result, "run_id": run_id})
                    if args.load_wait_seconds > 0:
                        emit(args.out, {"state": "SWEEP_LOAD_WAIT", "cycle": cycle, "round": round_no, "seconds": args.load_wait_seconds, "run_id": run_id})
                        deadline = time.time() + args.load_wait_seconds
                        while time.time() < deadline and not should_stop(stop_file):
                            time.sleep(min(0.25, max(0.0, deadline - time.time())))
                    post_state = wait_for_fresh_state(args.state_json, max_age=args.max_state_age_seconds, timeout=10.0)
                    return {"switch_result": switch_result, "post_state": post_state}

                transition_result = run_channel_switch_with_input_lock(
                    _pickup_channel_transition,
                    lock_path=args.live_input_lock,
                    run_id=run_id,
                    timeout_seconds=args.live_input_lock_timeout_seconds,
                    out=args.out,
                    cycle=cycle,
                    round_no=round_no,
                    action="pickup_channel_transition",
                )
                switch_result = transition_result["switch_result"]
                if not switch_result.get("verified"):
                    cycle_record.update({"outcome": "channel_switch_unconfirmed", "reason": switch_result.get("unverified_reason"), "elapsed_seconds": round(time.time() - cycle_start, 3)})
                    cycles.append(cycle_record)
                    outcome, reason = "channel_switch_unconfirmed", str(switch_result.get("unverified_reason"))
                    emit(args.out, {"state": "SWEEP_CHANNEL_SWITCH_UNCONFIRMED", "cycle": cycle, "round": round_no, "result": switch_result, "run_id": run_id})
                    break
                cycle_record["post_switch_state"] = state_brief(transition_result["post_state"])
            except Exception as exc:
                cycle_record.update({"outcome": "channel_switch_failed", "reason": str(exc), "elapsed_seconds": round(time.time() - cycle_start, 3)})
                cycles.append(cycle_record)
                outcome, reason = "channel_switch_failed", str(exc)
                emit(args.out, {"state": "SWEEP_CHANNEL_SWITCH_FAILED", "cycle": cycle, "round": round_no, "reason": str(exc), "run_id": run_id})
                break

            cycle_record.update({"outcome": "destroyed_switched", "reason": "cycle_complete", "elapsed_seconds": round(time.time() - cycle_start, 3)})
            capture_safe(screenshot_dir / f"{run_id}_r{round_no:02d}_cycle{cycle:02d}_post.jpg", args.window_query, args.screenshot_backend, args.out, kind="cycle_post", run_id=run_id, cycle=cycle)
            cycles.append(cycle_record)
            outcome, reason = "completed", "all_requested_channels_attempted"
        if not args.repeat_while_running or outcome != "completed":
            break
        emit(args.out, {"state": "SWEEP_REPEAT_NEXT_ROUND", "round": round_no + 1, "run_id": run_id})

    final_state = None
    try:
        final_state = read_state(args.state_json)
    except Exception:
        final_state = {}
    summary = {
        "run_id": run_id,
        "outcome": outcome,
        "reason": reason,
        "live": True,
        "requested_channels": max_channels,
        "repeat_while_running": bool(args.repeat_while_running),
        "low_dps_adjust": bool(args.low_dps_adjust),
        "movement_limited_to_low_dps_wasd": bool(args.low_dps_adjust),
        "completed_cycles": sum(1 for c in cycles if c.get("outcome") in {"destroyed_switched", "destroyed_final_channel"}),
        "elapsed_seconds": round(time.time() - start, 3),
        "cycles": cycles,
        "final_state": state_brief(final_state) if isinstance(final_state, dict) else None,
        "pre_screenshot": pre,
        "screenshot_dir": str(screenshot_dir),
        "log": str(args.out),
        "no_combat_click": True,
        "no_potion_1": True,
    }
    write_summary(args.summary_out, summary)
    emit(args.out, {"state": "SWEEP_FINISH", **summary})
    return 0 if outcome in {"completed", "stopped"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
