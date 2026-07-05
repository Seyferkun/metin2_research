from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageGrab, ImageStat

from metin2_research.detector import YoloV5Detector
from metin2_research.live_filters import filter_world_metin_candidates_with_image, screen_xy_for_metin_body
from metin2_research.live_navigation import choose_next_patrol_point, load_navigation_config, relative_point_to_screen, screen_to_relative_point
from metin2_research.live_ui import looks_like_escape_menu
from metin2_research.predict import build_prediction_report
from metin2_research.screenshot_state import save_annotated_preview
from metin2_research.spawn_memory import SpawnMemory
from metin2_research.win_input import click_xy, hold_key, hold_key_with_periodic_tap, tap_key
from metin2_research.window_capture import activate_window, find_window


class MetinFiveController:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.out = Path(args.out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.detector = YoloV5Detector(args.yolov5_dir, args.weights, trust_checkpoint=args.trust_checkpoint)
        self.events_path = self.out / "events.jsonl"
        self.events = self.events_path.open("w", encoding="utf-8")
        self.destroyed_count = 0
        self.step = 0
        self.search_turns = 0
        self.last_target_xy: list[int] | None = None
        self.last_hp_percent: float | None = None
        self.last_hp_seen_at = 0.0
        self.last_target_clicked_at = 0.0
        self.stale_hp_ticks = 0
        self.window_bbox = (0, 0, 0, 0)
        self.navigation = load_navigation_config(args.navigation_config) if args.navigation_config else None
        self.spawn_memory = SpawnMemory(args.spawn_memory) if args.spawn_memory else None
        self.active_spawn_id: str | None = None

    def log(self, event: dict[str, Any]) -> None:
        event = {"step": self.step, "t": round(time.monotonic() - self.start, 2), **event}
        print(json.dumps(event), flush=True)
        self.events.write(json.dumps(event) + "\n")
        self.events.flush()

    def capture(self, name: str | None = None) -> tuple[Path, Any]:
        window = find_window(self.args.window_query)
        activate_window(window)
        time.sleep(0.08)
        self.window_bbox = window.bbox
        img = self.out / (name or f"capture_{self.step:04d}.jpg")
        ImageGrab.grab(bbox=window.bbox).save(img)
        return img, window

    def read_target_hp_percent(self, image_path: Path) -> float | None:
        """Estimate selected target HP from top-center red bar length.

        This is intentionally simple and robust enough for the Metin target bar:
        look in the fixed top bar region and measure red pixels across x.
        Returns None when no selected target bar is visible.
        """
        with Image.open(image_path).convert("RGB") as img:
            w, h = img.size
            crop = img.crop((int(w * 0.36), 48, int(w * 0.64), 105))
            # A real selected-target health bar sits inside a dark top-center frame.
            # Avoid treating red mob labels, minimap dots, or dungeon UI text as HP.
            dark_pixels = 0
            total_pixels = crop.width * crop.height
            for r, g, b in crop.getdata():
                if r < 85 and g < 85 and b < 85:
                    dark_pixels += 1
            if dark_pixels / max(1, total_pixels) < 0.18:
                return None
            red_columns = []
            for x in range(crop.width):
                count = 0
                for y in range(crop.height):
                    r, g, b = crop.getpixel((x, y))
                    if r > 115 and g < 80 and b < 80:
                        count += 1
                if count >= 2:
                    red_columns.append(x)
            if len(red_columns) < 8:
                return None
            span = max(red_columns) - min(red_columns) + 1
            # Empirical full bar in this UI occupies about 285 px inside this crop.
            return max(0.0, min(100.0, 100.0 * span / 285.0))

    def is_dead(self, image_path: Path) -> bool:
        with Image.open(image_path).convert("RGB") as img:
            # Death screen is mostly grayscale/desaturated and shows respawn buttons in top-left.
            hsv = img.convert("HSV")
            sat = ImageStat.Stat(hsv).mean[1]
            button_crop = img.crop((60, 95, 315, 210)).convert("L")
            button_mean = ImageStat.Stat(button_crop).mean[0]
            return sat < 55 and 35 < button_mean < 115

    def close_overlays(self) -> None:
        # Character panel, large map, dungeon-info window, and miscellaneous overlay close buttons; harmless if not open.
        # Do not blind-tap ESC here: when no menu is open, ESC opens the game menu.
        # Visible ESC menus are closed after capture via looks_like_escape_menu().
        click_xy(325, 306, clicks=1)
        time.sleep(0.08)
        click_xy(1468, 158, clicks=1)
        time.sleep(0.08)
        click_xy(1230, 187, clicks=1)
        time.sleep(0.08)
        # Move cursor away from taskbar/terminal thumbnails so it does not cover the game capture.
        import ctypes
        ctypes.windll.user32.SetCursorPos(1500, 500)

    def respawn_here(self) -> None:
        click_xy(185, 139, clicks=1)
        time.sleep(2.0)
        for _ in range(4):
            tap_key("1")
            time.sleep(0.15)

    def detect_world_state(self, image: Path) -> dict[str, Any]:
        detections = self.detector.detect(image)
        report = build_prediction_report(image, detections, min_confidence=0.20)
        state = report["state"]
        kept = filter_world_metin_candidates_with_image(image, report["detections"], int(state["image_width"]), int(state["image_height"]), min_confidence=0.20)
        state["raw_box_count"] = len(report["detections"])
        state["boxes"] = kept
        state["box_count"] = len(kept)
        state["target_visible"] = bool(kept)
        state["target_confirmed"] = bool(kept)
        if kept:
            target = kept[0]
            state["target_box"] = target
            state["target_confidence"] = float(target["confidence"])
            state["target_xy"] = [target["x_center"], target["y_center"]]
            state["screen_xy"] = screen_xy_for_metin_body(target, self.window_bbox)
            state["recommended_action"] = {"action": "APPROACH_OR_ATTACK", "reason": "filtered world Metin", "screen_xy": state["screen_xy"]}
        else:
            state["target_box"] = None
            state["target_confidence"] = 0.0
            state["target_xy"] = None
            state["screen_xy"] = None
            state["recommended_action"] = {"action": "SEARCH", "reason": "no filtered world Metin"}
        hp = self.read_target_hp_percent(image)
        state["target_hp_percent_estimate"] = hp
        state["player_dead_visual"] = self.is_dead(image)
        return state

    def save_state(self, image: Path, state: dict[str, Any]) -> None:
        preview = self.out / "latest_preview.jpg"
        save_annotated_preview(image, state, preview)
        (self.out / "latest_state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def search_step(self) -> None:
        patrol_after = int(self.args.patrol_after_search_turns)
        if self.navigation is not None and patrol_after > 0 and self.search_turns > 0 and self.search_turns % patrol_after == 0:
            target = choose_next_patrol_point(
                self.spawn_memory.spawns if self.spawn_memory is not None else [],
                self.navigation,
                now=time.monotonic() - self.start,
                cooldown_seconds=float(self.args.spawn_cooldown),
                window_bbox=self.window_bbox,
            )
            if target.get("screen_xy"):
                xy = [int(target["screen_xy"][0]), int(target["screen_xy"][1])]
                click_xy(xy[0], xy[1], clicks=1)
                self.search_turns += 1
                self.log({"action": "patrol_move", "target": target, "wait": self.args.approach_wait})
                time.sleep(self.args.approach_wait)
                return

        # Keep search controlled: rotate, don't blind-run. E proved useful in live testing.
        key = "e" if self.search_turns % 8 < 6 else "q"
        hold_key(key, 0.40)
        self.search_turns += 1
        self.log({"action": "search_rotate", "key": key, "seconds": 0.40})

    def approach_or_attack(self, state: dict[str, Any]) -> None:
        hp = state.get("target_hp_percent_estimate")
        if hp is not None:
            # Only trust a selected health bar if it follows a recent filtered Metin-body click.
            # Otherwise we may be looking at a wolf/mob target or unrelated UI.
            recent_metin_click = (time.monotonic() - self.last_target_clicked_at) < 75.0
            if not recent_metin_click and not state.get("target_visible"):
                self.log({"action": "abandon_untrusted_selected_target", "hp_estimate": round(float(hp), 2)})
                click_xy(1192, 76, clicks=1)  # close target bar
                self.last_hp_percent = None
                self.stale_hp_ticks = 0
                self.search_step()
                return

            if self.last_hp_percent is not None and abs(float(hp) - float(self.last_hp_percent)) < 3.0:
                self.stale_hp_ticks += 1
            else:
                self.stale_hp_ticks = 0
            if self.stale_hp_ticks >= 5:
                self.log({"action": "abandon_stale_target_hp", "hp_estimate": round(float(hp), 2), "stale_ticks": self.stale_hp_ticks})
                click_xy(1192, 76, clicks=1)
                self.last_hp_percent = None
                self.stale_hp_ticks = 0
                self.last_target_clicked_at = 0.0
                self.search_step()
                return

            self.last_hp_percent = float(hp)
            self.last_hp_seen_at = time.monotonic()
            # Already selected target: stay locked and attack.
            tap_key("1")
            pots = hold_key_with_periodic_tap("space", self.args.attack_burst, tap="1", tap_every=self.args.potion_every)
            self.log({"action": "attack_selected_target", "hp_estimate_before": round(float(hp), 2), "seconds": self.args.attack_burst, "potions": pots, "stale_ticks": self.stale_hp_ticks})
            return

        if state.get("screen_xy"):
            xy = [int(state["screen_xy"][0]), int(state["screen_xy"][1])]
            self.last_target_xy = xy
            self.last_target_clicked_at = time.monotonic()
            self.stale_hp_ticks = 0
            if self.spawn_memory is not None:
                rel = screen_to_relative_point(xy, self.window_bbox)
                spawn = self.spawn_memory.record_observation(
                    rel,
                    screen_xy=xy,
                    confidence=float(state.get("target_confidence", 0.0)),
                    now=time.monotonic() - self.start,
                )
                self.spawn_memory.save()
                self.active_spawn_id = str(spawn["id"])
            tap_key("1")
            click_xy(xy[0], xy[1], clicks=2)
            time.sleep(self.args.approach_wait)
            self.log({"action": "click_world_metin_body", "xy": xy, "wait": self.args.approach_wait})
            return

        # Last-known target lock only if recent selected HP exists; otherwise search.
        self.search_step()

    def maybe_count_destroyed(self, previous_hp: float | None, state: dict[str, Any]) -> bool:
        hp = state.get("target_hp_percent_estimate")
        if previous_hp is not None and previous_hp < 18 and hp is None:
            self.destroyed_count += 1
            if self.spawn_memory is not None and self.active_spawn_id is not None:
                self.spawn_memory.mark_destroyed(self.active_spawn_id, now=time.monotonic() - self.start)
                self.spawn_memory.save()
            self.log({"action": "count_destroyed", "destroyed_count": self.destroyed_count, "reason": "low HP disappeared", "spawn_id": self.active_spawn_id})
            self.active_spawn_id = None
            self.last_hp_percent = None
            self.last_target_xy = None
            return True
        return False

    def run(self) -> int:
        self.start = time.monotonic()
        self.close_overlays()
        deadline = self.start + self.args.duration
        try:
            while time.monotonic() < deadline and self.destroyed_count < self.args.goal:
                self.step += 1
                image, _window = self.capture()
                if looks_like_escape_menu(image):
                    tap_key("esc")
                    self.log({"action": "close_visible_menu_with_esc"})
                    time.sleep(0.25)
                    continue
                state = self.detect_world_state(image)
                self.save_state(image, state)

                if state.get("player_dead_visual"):
                    self.log({"action": "dead_recover", "destroyed_count": self.destroyed_count})
                    self.respawn_here()
                    continue

                previous_hp = self.last_hp_percent
                if self.maybe_count_destroyed(previous_hp, state):
                    continue

                # Close overlays if map/panels reappeared; avoid every tick cursor disruption by doing it periodically.
                if self.step % 8 == 0:
                    self.close_overlays()

                if state.get("target_hp_percent_estimate") is not None or state.get("target_visible"):
                    self.approach_or_attack(state)
                else:
                    self.search_step()

            summary = {
                "goal": self.args.goal,
                "destroyed_count": self.destroyed_count,
                "duration_actual_seconds": round(time.monotonic() - self.start, 2),
                "latest_preview": str(self.out / "latest_preview.jpg"),
                "events": str(self.events_path),
                "status": "complete" if self.destroyed_count >= self.args.goal else "incomplete",
            }
            (self.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(summary, indent=2))
            return 0 if self.destroyed_count >= self.args.goal else 2
        finally:
            self.events.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Private-sandbox Metin2 controller: destroy 5 Metins using safe OS input only.")
    parser.add_argument("--goal", type=int, default=5)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--window-query", default="MT2Portugalia")
    parser.add_argument("--yolov5-dir", default="C:/Users/blade/AppData/Local/Temp/metin2bot/yolov5")
    parser.add_argument("--weights", default="reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt")
    parser.add_argument("--trust-checkpoint", action="store_true")
    parser.add_argument("--out-dir", default="reports/best_player_5metins")
    parser.add_argument("--attack-burst", type=float, default=8.0)
    parser.add_argument("--potion-every", type=float, default=1.5)
    parser.add_argument("--approach-wait", type=float, default=2.2)
    parser.add_argument("--navigation-config", default="configs/yoshypt_1922x1031_navigation.json")
    parser.add_argument("--spawn-memory", default="reports/metin_spawn_memory.json")
    parser.add_argument("--patrol-after-search-turns", type=int, default=24)
    parser.add_argument("--spawn-cooldown", type=float, default=120.0)
    args = parser.parse_args()
    return MetinFiveController(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
