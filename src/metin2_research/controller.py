"""Behavior state machine controller for Metin2 sandbox automation.

Replaces 6+ ad-hoc sandbox scripts with a single finite state machine:
IDLE → SEARCHING → APPROACHING → ATTACKING → VERIFYING → STUCK_RECOVERY

Each state has its own perceive→decide→act loop.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import load_config

logger = logging.getLogger("metin2_research.controller")


@dataclass
class ControllerState:
    """Mutable state of the controller at each tick."""
    step: int = 0
    mode: str = "IDLE"
    target_visible: bool = False
    target_confidence: float = 0.0
    target_xy: list[int] | None = None  # screen-space pixel coords
    screen_xy: list[int] | None = None  # absolute screen coords
    target_box: dict[str, Any] | None = None
    distance_label: str = "unknown"
    distance_meters: float = 99.0
    hp_percent: int = 100
    player_dead: bool = False
    inventory_full: bool = False
    no_target_seconds: float = 0.0
    last_action: str = "NO_OP"
    last_action_time: float = 0.0
    no_progress_seconds: float = 0.0  # stuck detector
    last_coord: list[int] | None = None  # in-game position
    potions_used: int = 0
    metins_destroyed: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)


def _safe_xy(state: ControllerState) -> tuple[int, int] | None:
    if state.screen_xy:
        return (int(state.screen_xy[0]), int(state.screen_xy[1]))
    if state.target_xy:
        return (int(state.target_xy[0]), int(state.target_xy[1]))
    return None


class MetinController:
    """Finite state machine for Metin2 sandbox automation.

    States:
      IDLE           — initial, waiting to start or manual intervention
      SEARCHING      — no target visible; rotate camera, move to known spawns
      APPROACHING    — target at medium/far range; move toward it
      ATTACKING      — target close; hold space + potions
      VERIFYING      — after attack cycle; check if target is gone or still alive
      STUCK_RECOVERY — no progress for N seconds; ESC then re-search
      HEALING        — HP below threshold; tap potion, wait
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        detector: Any = None,
        window_query: str | None = None,
        out_dir: str | Path = "reports/bot_run",
        dry_run: bool = False,
    ):
        if config is None:
            config = load_config()
        self.cfg = config
        self.detector = detector
        self.window_query = window_query or config.get("window", {}).get("query", "MT2Portugalia")
        self.out_dir = Path(out_dir)
        self.dry_run = dry_run

        # State
        self.state = ControllerState()
        self._start_time: float = 0.0
        self._last_capture_time: float = 0.0
        self._next_potion_time: float = 0.0
        self._last_detection_time: float = 0.0

        # Loaded lazily
        self._win_input = None
        self._window_capture = None
        self._live_filters = None
        self._spawn_memory = None
        self._memory_probe = None

        # Create output directory
        self.out_dir.mkdir(parents=True, exist_ok=True)

    @property
    def win_input(self):
        if self._win_input is None:
            from . import win_input
            self._win_input = win_input
        return self._win_input

    @property
    def window_capture(self):
        if self._window_capture is None:
            from . import window_capture as wc
            self._window_capture = wc
        return self._window_capture

    @property
    def live_filters(self):
        if self._live_filters is None:
            from . import live_filters as lf
            self._live_filters = lf
        return self._live_filters

    # ── Core helpers ──────────────────────────────────────────────────

    def _capture_and_detect(self) -> dict[str, Any] | None:
        """Capture the game window, run detection, return advisory state dict or None on error."""
        try:
            window = self.window_capture.find_window(self.window_query)
            self.window_capture.activate_window(window)
            time.sleep(0.15)

            img_path = self.out_dir / f"frame_{self.state.step:04d}.jpg"
            # Use PIL ImageGrab for full screen region matching window bbox
            from PIL import ImageGrab
            ImageGrab.grab(bbox=window.bbox).save(img_path)

            if self.detector is None:
                return None

            boxes = self.detector.detect(img_path)

            # Filter world Metin candidates (reject UI false positives)
            kept = self.live_filters.filter_world_metin_candidates(
                boxes, window.width, window.height, min_confidence=self.cfg.get("detection", {}).get("min_confidence", 0.25)
            )

            if kept:
                best = kept[0]
                sx = int(window.bbox[0] + float(best["x_center"]))
                sy = int(window.bbox[1] + float(best["y_center"]))
                from .onnx_detector import estimate_distance, estimate_distance_meters
                dist_label = estimate_distance(best)
                dist_m = estimate_distance_meters(best)

                self.state.target_visible = True
                self.state.target_confidence = float(best["confidence"])
                self.state.target_xy = [float(best["x_center"]), float(best["y_center"])]
                self.state.screen_xy = [sx, sy]
                self.state.target_box = best
                self.state.distance_label = dist_label
                self.state.distance_meters = dist_m
                self.state.no_target_seconds = 0.0
                self._last_detection_time = time.monotonic()
            else:
                self.state.target_visible = False
                self.state.target_confidence = 0.0
                self.state.target_xy = None
                self.state.screen_xy = None
                self.state.target_box = None

            return {
                "step": self.state.step,
                "image": str(img_path),
                "boxes": len(boxes),
                "kept": len(kept),
                "window": {"bbox": list(window.bbox), "pid": window.pid, "title": window.title},
            }
        except Exception as e:
            logger.warning("Capture/detect failed: %s", e)
            return None

    def _tap_potion(self) -> None:
        if not self.dry_run:
            self.win_input.tap_key("1", hold=0.04)
        self.state.potions_used += 1
        self._next_potion_time = time.monotonic() + self.cfg.get("behavior", {}).get("potion_interval", 10.0)

    def _click_target(self) -> None:
        xy = _safe_xy(self.state)
        if xy and not self.dry_run:
            self.win_input.click_xy(xy[0], xy[1], clicks=2, delay=0.12)

    def _hold_attack(self, duration: float) -> int:
        cfg = self.cfg.get("behavior", {})
        potion_interval = cfg.get("potion_interval", 10.0)
        if not self.dry_run:
            return self.win_input.hold_key_with_periodic_tap(
                "space", duration, tap="1", tap_every=potion_interval
            )
        return 0

    def _rotate_search(self) -> str:
        """Gentle camera rotation. Returns direction name."""
        if self.state.step % 2 == 0:
            if not self.dry_run:
                self.win_input.tap_key("e", hold=0.08)
            return "rotate_E"
        else:
            if not self.dry_run:
                self.win_input.tap_key("q", hold=0.08)
            return "rotate_Q"

    def _log_event(self, **kwargs) -> None:
        event = {
            "t": round(time.monotonic() - self._start_time, 2),
            "step": self.state.step,
            "mode": self.state.mode,
            "target_visible": self.state.target_visible,
            "confidence": self.state.target_confidence,
            "distance": self.state.distance_label,
            **kwargs,
        }
        self.state.events.append(event)
        logger.info(json.dumps(event))

    # ── State handlers ────────────────────────────────────────────────

    def _state_idle(self) -> str:
        logger.info("Controller started. Entering SEARCHING.")
        return "SEARCHING"

    def _state_searching(self) -> str:
        # Check if we should heal
        if self.state.hp_percent < self.cfg.get("behavior", {}).get("heal_threshold", 35):
            return "HEALING"

        # Potion timer
        if time.monotonic() >= self._next_potion_time:
            self._tap_potion()

        capture = self._capture_and_detect()
        if capture:
            logger.info("Capture: %d boxes, %d kept", capture["boxes"], capture["kept"])

        if self.state.target_visible and self.state.target_confidence >= self.cfg.get("detection", {}).get("attack_confidence", 0.60):
            return "ATTACKING"
        elif self.state.target_visible and self.state.target_confidence >= self.cfg.get("detection", {}).get("investigate_confidence", 0.35):
            return "APPROACHING"
        else:
            self.state.no_target_seconds += self.cfg.get("behavior", {}).get("capture_interval", 1.0)
            rotate_after = self.cfg.get("behavior", {}).get("no_target_rotate_after", 5.0)
            if self.state.no_target_seconds >= rotate_after:
                dir_name = self._rotate_search()
                self.state.no_target_seconds = 0.0
                self._log_event(action="search_rotate", direction=dir_name)
            else:
                self._log_event(action="wait")
            return "SEARCHING"

    def _state_approaching(self) -> str:
        # Double-click target to move toward it
        self._click_target()
        self._log_event(action="double_click_approach", screen_xy=self.state.screen_xy)

        # Wait for movement
        wait_time = self.cfg.get("behavior", {}).get("approach_click_wait", 3.0)
        time.sleep(wait_time)
        self.state.step += 1

        # Re-capture and check
        self._capture_and_detect()

        if not self.state.target_visible:
            self._log_event(action="target_lost_during_approach")
            return "SEARCHING"

        if self.state.target_confidence >= self.cfg.get("detection", {}).get("attack_confidence", 0.60):
            return "ATTACKING"

        return "APPROACHING"

    def _state_attacking(self) -> str:
        # Potion first
        self._tap_potion()

        # Double-click target to select/engage
        self._click_target()

        # Hold attack (Space) with periodic potions
        hold_duration = self.cfg.get("behavior", {}).get("hold_attack_duration", 5.0)
        pots = self._hold_attack(hold_duration)
        self.state.potions_used += pots

        self._log_event(action="attack_hold", duration=hold_duration, potions_during= pots)
        self.state.step += 1

        return "VERIFYING"

    def _state_verifying(self) -> str:
        verify_wait = self.cfg.get("behavior", {}).get("verify_wait", 1.5)
        time.sleep(verify_wait)
        self.state.step += 1

        self._capture_and_detect()

        if not self.state.target_visible:
            self.state.metins_destroyed += 1
            self._log_event(action="metin_destroyed")
            return "SEARCHING"
        elif self.state.target_confidence >= self.cfg.get("detection", {}).get("attack_confidence", 0.60):
            # Still there, attack again
            return "ATTACKING"
        else:
            self._log_event(action="target_present_but_low_conf")
            return "APPROACHING"

    def _state_healing(self) -> str:
        self._tap_potion()
        self._log_event(action="heal_potion")
        time.sleep(2.0)
        self.state.hp_percent = 100  # optimistic
        return "SEARCHING"

    def _state_stuck_recovery(self) -> str:
        self._log_event(action="stuck_recovery_escape")
        if not self.dry_run:
            self.win_input.tap_key("esc", hold=0.15)
            time.sleep(1.0)
        self.state.no_progress_seconds = 0.0
        return "SEARCHING"

    def _save_summary(self) -> Path:
        """Write summary.json with all events."""
        path = self.out_dir / "summary.json"
        summary = {
            "mode": self.state.mode,
            "total_steps": self.state.step,
            "potions_used": self.state.potions_used,
            "metins_destroyed_estimate": self.state.metins_destroyed,
            "last_state": {
                "mode": self.state.mode,
                "target_visible": self.state.target_visible,
                "target_confidence": self.state.target_confidence,
                "distance": self.state.distance_label,
                "hp_percent": self.state.hp_percent,
            },
            "events_count": len(self.state.events),
            "dry_run": self.dry_run,
        }
        path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return path

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self, duration: float = 60.0, *, max_iterations: int | None = None) -> dict[str, Any]:
        """Run the state machine loop for `duration` seconds (or max_iterations steps)."""
        self._start_time = time.monotonic()
        self._next_potion_time = time.monotonic() + self.cfg.get("behavior", {}).get("potion_interval", 10.0)

        state_handlers: dict[str, Callable[[], str]] = {
            "IDLE": self._state_idle,
            "SEARCHING": self._state_searching,
            "APPROACHING": self._state_approaching,
            "ATTACKING": self._state_attacking,
            "VERIFYING": self._state_verifying,
            "HEALING": self._state_healing,
            "STUCK_RECOVERY": self._state_stuck_recovery,
        }

        # Verify required components
        if self.detector is None:
            logger.error("No detector configured. Pass detector= to MetinController.")

        self.state.mode = "IDLE"
        iteration = 0

        while True:
            elapsed = time.monotonic() - self._start_time
            if elapsed >= duration:
                break
            if max_iterations is not None and iteration >= max_iterations:
                break

            handler = state_handlers.get(self.state.mode)
            if handler is None:
                logger.warning("Unknown state %s, resetting to SEARCHING", self.state.mode)
                self.state.mode = "SEARCHING"
                continue

            try:
                next_mode = handler()
                self.state.mode = next_mode
                self.state.last_action = next_mode
                self.state.last_action_time = time.monotonic()
            except Exception as e:
                logger.error("State %s error: %s", self.state.mode, e)
                self.state.mode = "STUCK_RECOVERY"

            self.state.step += 1
            iteration += 1

        summary = {
            "duration": round(time.monotonic() - self._start_time, 2),
            "dry_run": self.dry_run,
            "total_steps": self.state.step,
            "final_mode": self.state.mode,
            "potions_used": self.state.potions_used,
            "metins_destroyed_estimate": self.state.metins_destroyed,
            "summary_path": str(self._save_summary()),
        }
        return summary