from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from PIL import ImageGrab

from metin2_research.detector import YoloV5Detector
from metin2_research.live_filters import filter_world_metin_candidates_with_image, screen_xy_for_metin_body
from metin2_research.live_navigation import choose_next_patrol_point, load_navigation_config, screen_to_relative_point
from metin2_research.live_ui import looks_like_escape_menu
from metin2_research.predict import build_prediction_report
from metin2_research.screenshot_state import save_annotated_preview
from metin2_research.spawn_memory import SpawnMemory
from metin2_research.win_input import click_xy, hold_key, tap_key
from metin2_research.window_capture import activate_window, find_window


class SpawnLearner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.out = Path(args.out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.events_path = self.out / "events.jsonl"
        self.events = self.events_path.open("w", encoding="utf-8")
        self.detector = YoloV5Detector(args.yolov5_dir, args.weights, trust_checkpoint=args.trust_checkpoint)
        self.navigation = load_navigation_config(args.navigation_config)
        self.memory = SpawnMemory(args.spawn_memory)
        self.start = time.monotonic()
        self.step = 0
        self.search_turns = 0
        self.window_bbox = (0, 0, 0, 0)

    def log(self, event: dict[str, Any]) -> None:
        event = {"step": self.step, "t": round(time.monotonic() - self.start, 2), **event}
        print(json.dumps(event), flush=True)
        self.events.write(json.dumps(event) + "\n")
        self.events.flush()

    def capture(self) -> Path:
        window = find_window(self.args.window_query)
        activate_window(window)
        time.sleep(0.08)
        self.window_bbox = window.bbox
        image = self.out / f"capture_{self.step:04d}.jpg"
        ImageGrab.grab(bbox=window.bbox).save(image)
        return image

    def close_overlays(self) -> None:
        # Do not blind-tap Esc here: in Metin2 it can open the game menu when no overlay is active.
        # The combat controller handles specific overlays; this learner is non-combat and should avoid menu toggles.
        return

    def step_once(self) -> None:
        image = self.capture()
        if looks_like_escape_menu(image):
            tap_key("esc")
            self.log({"action": "close_visible_menu_with_esc"})
            time.sleep(0.25)
            return
        detections = self.detector.detect(image)
        report = build_prediction_report(image, detections, min_confidence=self.args.min_confidence)
        state = report["state"]
        kept = filter_world_metin_candidates_with_image(image, report["detections"], int(state["image_width"]), int(state["image_height"]), min_confidence=self.args.min_confidence)
        state["boxes"] = kept
        state["box_count"] = len(kept)
        state["target_visible"] = bool(kept)
        if kept:
            box = kept[0]
            screen_xy = screen_xy_for_metin_body(box, self.window_bbox)
            rel = screen_to_relative_point(screen_xy, self.window_bbox)
            spawn = self.memory.record_observation(rel, screen_xy=screen_xy, confidence=float(box.get("confidence", 0.0)), now=time.monotonic() - self.start)
            self.memory.save()
            state["screen_xy"] = screen_xy
            state["target_box"] = box
            self.log({"action": "record_spawn", "spawn_id": spawn["id"], "screen_xy": screen_xy, "relative_xy": rel, "confidence": box.get("confidence")})
        else:
            state["screen_xy"] = None
            state["target_box"] = None
            if self.args.move and self.search_turns > 0 and self.search_turns % self.args.patrol_after_search_turns == 0:
                target = choose_next_patrol_point(self.memory.spawns, self.navigation, now=time.monotonic() - self.start, cooldown_seconds=self.args.spawn_cooldown, window_bbox=self.window_bbox)
                if target.get("screen_xy"):
                    xy = [int(target["screen_xy"][0]), int(target["screen_xy"][1])]
                    click_xy(xy[0], xy[1], clicks=1)
                    self.log({"action": "patrol_move", "target": target, "wait": self.args.approach_wait})
                    time.sleep(self.args.approach_wait)
            else:
                key = "e" if self.search_turns % 8 < 6 else "q"
                hold_key(key, self.args.rotate_seconds)
                self.log({"action": "search_rotate", "key": key, "seconds": self.args.rotate_seconds})
            self.search_turns += 1

        preview = self.out / "latest_preview.jpg"
        save_annotated_preview(image, state, preview)
        (self.out / "latest_state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def run(self) -> int:
        self.close_overlays()
        deadline = self.start + self.args.duration
        try:
            while self.step < self.args.count and time.monotonic() < deadline:
                self.step += 1
                self.step_once()
                time.sleep(self.args.interval)
            summary = {
                "status": "complete",
                "steps": self.step,
                "spawn_count": len(self.memory.spawns),
                "spawn_memory": str(self.memory.path),
                "events": str(self.events_path),
                "latest_preview": str(self.out / "latest_preview.jpg"),
            }
            (self.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(summary, indent=2))
            return 0
        finally:
            self.events.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Learn Metin spawn positions from live screenshots without attacking.")
    parser.add_argument("--window-query", default="MT2Portugalia")
    parser.add_argument("--yolov5-dir", default="C:/Users/blade/AppData/Local/Temp/metin2bot/yolov5")
    parser.add_argument("--weights", default="reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt")
    parser.add_argument("--trust-checkpoint", action="store_true")
    parser.add_argument("--navigation-config", default="configs/yoshypt_1922x1031_navigation.json")
    parser.add_argument("--spawn-memory", default="reports/metin_spawn_memory.json")
    parser.add_argument("--out-dir", default="reports/learn_metin_spawns")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument("--interval", type=float, default=0.4)
    parser.add_argument("--min-confidence", type=float, default=0.70)
    parser.add_argument("--rotate-seconds", type=float, default=0.40)
    parser.add_argument("--move", action="store_true", help="Allow short patrol clicks to configured safe points; default is rotate-only.")
    parser.add_argument("--patrol-after-search-turns", type=int, default=24)
    parser.add_argument("--approach-wait", type=float, default=2.2)
    parser.add_argument("--spawn-cooldown", type=float, default=120.0)
    args = parser.parse_args()
    return SpawnLearner(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
