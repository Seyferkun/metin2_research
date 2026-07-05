from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from PIL import ImageGrab

from metin2_research.local_vision import build_local_state, compare_coordinate_feedback, crop_minimap_area
from metin2_research.win_input import hold_key, tap_key
from metin2_research.window_capture import activate_window, find_window


def capture_game_window(window_query: str, image_path: Path) -> tuple[Path, tuple[int, int, int, int]]:
    window = find_window(window_query)
    activate_window(window)
    time.sleep(0.15)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=window.bbox).save(image_path)
    return image_path, window.bbox


def save_state(image_path: Path, state_path: Path, *, coordinate_text: str | None = None) -> dict[str, Any]:
    state = build_local_state(image_path, coordinate_text=coordinate_text)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    crop = crop_minimap_area(image_path)
    crop.save(state_path.with_suffix(".minimap.jpg"))
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Metin2 perception/mapping probe: screenshot -> coordinate-aware state JSON -> movement feedback.")
    parser.add_argument("--window-query", default="MT2Portugalia")
    parser.add_argument("--out-dir", default="reports/local_mapping_probe")
    parser.add_argument("--coord-text", default=None, help="Manual/OCR coordinate text such as 'Yoshypt (607, 1025)' for the before frame.")
    parser.add_argument("--after-coord-text", default=None, help="Manual/OCR coordinate text for the after frame when --move-key is used.")
    parser.add_argument("--move-key", default=None, help="Optional bounded movement key, e.g. w/a/s/d/q/e. No movement if omitted.")
    parser.add_argument("--move-seconds", type=float, default=0.8)
    parser.add_argument("--potion-key", default="1")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    before_img, bbox = capture_game_window(args.window_query, out_dir / "before.jpg")
    before_state = save_state(before_img, out_dir / "before_state.json", coordinate_text=args.coord_text)

    result: dict[str, Any] = {
        "window_bbox": list(bbox),
        "before_image": str(before_img),
        "before_state": str(out_dir / "before_state.json"),
        "before_player_coord": before_state.get("player_coord"),
        "movement": None,
        "feedback": None,
    }

    if args.move_key:
        tap_key(args.potion_key)
        time.sleep(0.05)
        hold_key(args.move_key, args.move_seconds)
        time.sleep(0.25)
        after_img, _bbox = capture_game_window(args.window_query, out_dir / "after.jpg")
        after_state = save_state(after_img, out_dir / "after_state.json", coordinate_text=args.after_coord_text)
        feedback = compare_coordinate_feedback(
            before_state,
            after_state,
            expected_action=f"hold {args.move_key} {args.move_seconds:.2f}s",
        )
        result.update(
            {
                "movement": {"key": args.move_key, "seconds": args.move_seconds},
                "after_image": str(after_img),
                "after_state": str(out_dir / "after_state.json"),
                "after_player_coord": after_state.get("player_coord"),
                "feedback": feedback,
            }
        )

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
