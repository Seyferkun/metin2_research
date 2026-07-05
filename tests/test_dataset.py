from pathlib import Path

from PIL import Image, ImageDraw

from metin2_research.dataset import scan_yolo_dataset, parse_yolo_annotation
from metin2_research.policy import decide_next_action
from metin2_research.screenshot_state import build_state_from_annotation, save_annotated_preview
from metin2_research.detector import (
    box_iou,
    build_state_from_detections,
    evaluate_image_detections,
    save_failure_preview,
)
from metin2_research.failure_analysis import categorize_failure, summarize_failures
from metin2_research.review_manifest import build_review_manifest, write_review_manifest
from metin2_research.training_dataset import build_training_dataset
from metin2_research.predict import build_prediction_report
from metin2_research.live_try import add_screen_coordinates, parse_region
from metin2_research.actuator import plan_private_server_action, SafetyGateError
from metin2_research.live_filters import filter_world_metin_candidates, filter_world_metin_candidates_with_image, is_ui_or_overlay_box, screen_xy_for_metin_body
from metin2_research.live_navigation import NavigationConfig, choose_next_patrol_point
from metin2_research.live_ui import looks_like_escape_menu
from metin2_research.local_vision import build_local_state, compare_coordinate_feedback, parse_player_coordinate_text, crop_minimap_area
from metin2_research.spawn_memory import SpawnMemory
from metin2_research.win_input import SCANCODES
from metin2_research.client_state.schema import ClientState, GameInfo, ProcessInfo, WindowInfo
from metin2_research.client_state.sources import ClientStateSource, merge_client_states
from metin2_research.client_state.process_probe import ProcessWindowSnapshot, snapshot_to_client_state
from metin2_research.client_state.screenshot_source import ScreenshotStateSource, screenshot_to_client_state
from metin2_research.client_state.tsv_state import TsvClientStateSource, parse_tsv_state_line
from metin2_research.client_state.navigation import (
    movement_delta,
    summarize_movement_observations,
    choose_key_for_direction,
)
from scripts.probe_client_state import build_probe_state, write_probe_state
from scripts.seed_spawn_memory_from_events import extract_destroyed_spawn_clicks, seed_spawn_memory
from scripts.update_metin_coordinate_table import extract_from_json
from scripts.explore_yongan_metin_coords import (
    parse_patrol,
    choose_unvisited_target,
    choose_key_toward_target,
    build_map_reference,
    should_block_target,
    fallback_key,
)


def test_parse_yolo_annotation_returns_boxes_with_pixel_coordinates(tmp_path):
    annotation = tmp_path / "sample.txt"
    annotation.write_text("0 0.5 0.25 0.2 0.1\n", encoding="utf-8")

    boxes = parse_yolo_annotation(annotation, image_width=1000, image_height=800)

    assert boxes == [
        {
            "class_id": 0,
            "x_center": 500.0,
            "y_center": 200.0,
            "width": 200.0,
            "height": 80.0,
            "xmin": 400.0,
            "ymin": 160.0,
            "xmax": 600.0,
            "ymax": 240.0,
        }
    ]


def test_scan_yolo_dataset_pairs_images_and_annotations(tmp_path):
    image = tmp_path / "001.jpg"
    annotation = tmp_path / "001.txt"
    image.write_bytes(b"fake image bytes")
    annotation.write_text("0 0.5 0.5 0.1 0.1\n", encoding="utf-8")
    (tmp_path / "orphan.txt").write_text("0 0.1 0.1 0.1 0.1\n", encoding="utf-8")

    summary = scan_yolo_dataset(tmp_path)

    assert summary["image_count"] == 1
    assert summary["annotation_count"] == 2
    assert summary["paired_count"] == 1
    assert summary["orphan_annotation_count"] == 1
    assert summary["total_boxes"] == 1
    assert summary["classes"] == {"0": 1}


def test_policy_recommends_heal_before_attack():
    state = {
        "player_dead": False,
        "hp_percent": 25,
        "target_visible": True,
        "target_confirmed": True,
        "target_confidence": 0.95,
        "no_target_seconds": 0,
    }

    decision = decide_next_action(state)

    assert decision["action"] == "USE_HEAL"
    assert decision["reason"] == "HP below safe threshold"


def test_policy_recommends_attack_confirmed_target():
    state = {
        "player_dead": False,
        "hp_percent": 90,
        "target_visible": True,
        "target_confirmed": True,
        "target_confidence": 0.82,
        "no_target_seconds": 0,
    }

    decision = decide_next_action(state)

    assert decision["action"] == "APPROACH_OR_ATTACK"
    assert decision["confidence"] == 0.82


def test_policy_recommends_investigate_for_medium_confidence_live_target():
    state = {
        "player_dead": False,
        "hp_percent": 90,
        "target_visible": True,
        "target_confirmed": True,
        "target_confidence": 0.49,
        "target_xy": [509.5, 321.0],
        "no_target_seconds": 0,
    }

    decision = decide_next_action(state)

    assert decision["action"] == "INVESTIGATE_TARGET"
    assert decision["reason"] == "Candidate target visible below attack threshold"
    assert decision["confidence"] == 0.49
    assert decision["target_xy"] == [509.5, 321.0]


def test_build_state_from_annotation_uses_largest_box_as_target(tmp_path):
    image = tmp_path / "sample.jpg"
    annotation = tmp_path / "sample.txt"
    Image.new("RGB", (1000, 800), "black").save(image)
    annotation.write_text(
        "0 0.10 0.10 0.05 0.05\n"
        "0 0.50 0.25 0.20 0.10\n",
        encoding="utf-8",
    )

    state = build_state_from_annotation(image, annotation)

    assert state["image_width"] == 1000
    assert state["image_height"] == 800
    assert state["target_visible"] is True
    assert state["target_confirmed"] is True
    assert state["target_type"] == "metin_stone"
    assert state["target_xy"] == [500.0, 200.0]
    assert state["target_box"]["xmin"] == 400.0
    assert state["recommended_action"]["action"] == "APPROACH_OR_ATTACK"


def test_save_annotated_preview_writes_image_with_box(tmp_path):
    image = tmp_path / "sample.jpg"
    annotation = tmp_path / "sample.txt"
    output = tmp_path / "preview.jpg"
    Image.new("RGB", (100, 80), "black").save(image)
    annotation.write_text("0 0.50 0.50 0.20 0.25\n", encoding="utf-8")
    state = build_state_from_annotation(image, annotation)

    saved_path = save_annotated_preview(image, state, output)

    assert saved_path == output
    assert output.exists()
    assert Image.open(output).size == (100, 80)


def test_box_iou_returns_expected_overlap():
    first = {"xmin": 0, "ymin": 0, "xmax": 100, "ymax": 100}
    second = {"xmin": 50, "ymin": 50, "xmax": 150, "ymax": 150}

    assert round(box_iou(first, second), 3) == 0.143


def test_build_state_from_detections_uses_best_confidence():
    detections = [
        {"xmin": 0, "ymin": 0, "xmax": 10, "ymax": 10, "confidence": 0.25, "class_id": 0, "name": "metin"},
        {"xmin": 20, "ymin": 30, "xmax": 60, "ymax": 90, "confidence": 0.90, "class_id": 0, "name": "metin"},
    ]

    state = build_state_from_detections("sample.jpg", detections, image_size=(100, 100))

    assert state["target_visible"] is True
    assert state["target_confirmed"] is True
    assert state["target_confidence"] == 0.9
    assert state["target_xy"] == [40.0, 60.0]
    assert state["recommended_action"]["action"] == "APPROACH_OR_ATTACK"


def test_evaluate_image_detections_counts_true_positive_false_positive_and_false_negative(tmp_path):
    image = tmp_path / "sample.jpg"
    annotation = tmp_path / "sample.txt"
    Image.new("RGB", (100, 100), "black").save(image)
    annotation.write_text("0 0.50 0.50 0.20 0.20\n", encoding="utf-8")
    detections = [
        {"xmin": 41, "ymin": 41, "xmax": 59, "ymax": 59, "confidence": 0.8, "class_id": 0, "name": "metin"},
        {"xmin": 0, "ymin": 0, "xmax": 10, "ymax": 10, "confidence": 0.7, "class_id": 0, "name": "metin"},
    ]

    result = evaluate_image_detections(image, annotation, detections, iou_threshold=0.5)

    assert result["ground_truth_count"] == 1
    assert result["detection_count"] == 2
    assert result["true_positives"] == 1
    assert result["false_positives"] == 1
    assert result["false_negatives"] == 0
    assert result["best_iou"] > 0.8
    assert len(result["detections"]) == 2
    assert len(result["ground_truth"]) == 1


def test_save_failure_preview_draws_ground_truth_and_detections(tmp_path):
    image = tmp_path / "sample.jpg"
    output = tmp_path / "failure.jpg"
    Image.new("RGB", (100, 100), "black").save(image)
    result = {
        "ground_truth": [{"xmin": 40, "ymin": 40, "xmax": 60, "ymax": 60}],
        "detections": [{"xmin": 0, "ymin": 0, "xmax": 10, "ymax": 10, "confidence": 0.7}],
        "true_positives": 0,
        "false_positives": 1,
        "false_negatives": 1,
        "best_iou": 0.0,
    }

    saved_path = save_failure_preview(image, result, output)

    assert saved_path == output
    assert output.exists()
    assert Image.open(output).size == (100, 100)


def test_categorize_failure_labels_common_failure_modes():
    assert categorize_failure({"false_positives": 1, "false_negatives": 0, "best_iou": 0.9}) == "duplicate_or_extra_detection"
    assert categorize_failure({"false_positives": 0, "false_negatives": 1, "best_iou": 0.0, "detection_count": 0}) == "missed_detection"
    assert categorize_failure({"false_positives": 1, "false_negatives": 1, "best_iou": 0.42}) == "localization_below_iou_threshold"
    assert categorize_failure({"false_positives": 1, "false_negatives": 1, "best_iou": 0.0}) == "wrong_location_or_label_mismatch"


def test_summarize_failures_counts_categories_and_writes_markdown(tmp_path):
    report = {
        "evaluated_images": 3,
        "precision": 0.5,
        "recall": 0.5,
        "images": [
            {"image": "a.jpg", "false_positives": 1, "false_negatives": 0, "best_iou": 0.9, "failure_preview": "a_preview.jpg"},
            {"image": "b.jpg", "false_positives": 0, "false_negatives": 1, "best_iou": 0.0, "detection_count": 0},
            {"image": "c.jpg", "false_positives": 0, "false_negatives": 0, "best_iou": 0.8},
        ],
    }
    out = tmp_path / "summary.md"

    summary = summarize_failures(report, markdown_out=out)

    assert summary["failure_count"] == 2
    assert summary["category_counts"] == {"duplicate_or_extra_detection": 1, "missed_detection": 1}
    assert out.exists()
    assert "duplicate_or_extra_detection" in out.read_text(encoding="utf-8")


def test_build_review_manifest_adds_suggested_actions_and_pending_status(tmp_path):
    preview = tmp_path / "preview.jpg"
    Image.new("RGB", (20, 20), "black").save(preview)
    report = {
        "images": [
            {
                "image": str(tmp_path / "sample.jpg"),
                "annotation": str(tmp_path / "sample.txt"),
                "failure_preview": str(preview),
                "false_positives": 1,
                "false_negatives": 0,
                "best_iou": 0.9,
                "detection_count": 2,
            }
        ]
    }

    rows = build_review_manifest(report)

    assert rows == [
        {
            "id": "0001",
            "image": str(tmp_path / "sample.jpg"),
            "annotation": str(tmp_path / "sample.txt"),
            "preview": str(preview),
            "category": "duplicate_or_extra_detection",
            "best_iou": 0.9,
            "false_positives": 1,
            "false_negatives": 0,
            "suggested_action": "add_hard_negative; inspect_extra_detection",
            "status": "pending",
            "notes": "",
        }
    ]


def test_write_review_manifest_writes_json_csv_and_review_copies(tmp_path):
    preview = tmp_path / "preview.jpg"
    Image.new("RGB", (20, 20), "black").save(preview)
    rows = [
        {
            "id": "0001",
            "image": "sample.jpg",
            "annotation": "sample.txt",
            "preview": str(preview),
            "category": "missed_detection",
            "best_iou": 0.0,
            "false_positives": 0,
            "false_negatives": 1,
            "suggested_action": "add_small_object_augmentation; oversample_image",
            "status": "pending",
            "notes": "",
        }
    ]

    outputs = write_review_manifest(
        rows,
        json_out=tmp_path / "manifest.json",
        csv_out=tmp_path / "manifest.csv",
        review_dir=tmp_path / "review",
    )

    assert outputs["json"] == str(tmp_path / "manifest.json")
    assert outputs["csv"] == str(tmp_path / "manifest.csv")
    assert outputs["review_dir"] == str(tmp_path / "review")
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "manifest.csv").exists()
    assert (tmp_path / "review" / "missed_detection" / "0001_preview.jpg").exists()


def test_build_training_dataset_creates_yolo_splits_with_corrected_and_negative_labels(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for stem in ["a", "b", "c", "d"]:
        Image.new("RGB", (32, 32), "black").save(source / f"{stem}.jpg")
        (source / f"{stem}.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")

    corrected = tmp_path / "corrected"
    corrected.mkdir()
    (corrected / "a.txt").write_text("0 0.4 0.4 0.3 0.3\n", encoding="utf-8")

    hard_negative_manifest = tmp_path / "hard_negatives.csv"
    hard_negative_manifest.write_text(
        "id,image,answer\n0001," + str(source / "b.jpg") + ",terrain/object false positive\n",
        encoding="utf-8",
    )
    oversample_manifest = tmp_path / "oversample.csv"
    oversample_manifest.write_text(
        "id,image,answer\n0002," + str(source / "c.jpg") + ",small/distant target\n",
        encoding="utf-8",
    )

    summary = build_training_dataset(
        source_dataset=source,
        output_dir=tmp_path / "train_ds",
        corrected_labels_dir=corrected,
        hard_negative_manifest=hard_negative_manifest,
        oversample_manifest=oversample_manifest,
        val_fraction=0.25,
    )

    assert summary["base_images"] == 4
    assert summary["corrected_labels_applied"] == 1
    assert summary["hard_negative_images"] == 1
    assert summary["oversample_images"] == 1
    assert (tmp_path / "train_ds" / "dataset.yaml").exists()
    assert (tmp_path / "train_ds" / "images" / "train").exists()
    assert (tmp_path / "train_ds" / "labels" / "train").exists()
    negative_labels = [p for p in (tmp_path / "train_ds" / "labels" / "train").glob("*_hardneg*.txt")]
    assert negative_labels
    assert negative_labels[0].read_text(encoding="utf-8") == ""
    assert any(p.name.endswith("_aug1.jpg") for p in (tmp_path / "train_ds" / "images" / "train").glob("*.jpg"))


def test_build_prediction_report_without_annotation(tmp_path):
    image = tmp_path / "sample.jpg"
    Image.new("RGB", (100, 80), "black").save(image)
    detections = [
        {
            "class_id": 0,
            "name": "metin_stone",
            "confidence": 0.75,
            "xmin": 10,
            "ymin": 20,
            "xmax": 40,
            "ymax": 60,
        }
    ]
    report = build_prediction_report(image, detections)
    assert report["state"]["target_visible"] is True
    assert report["state"]["recommended_action"]["action"] == "APPROACH_OR_ATTACK"
    assert report["detections"][0]["confidence"] == 0.75


def test_parse_live_try_region():
    assert parse_region("10,20,300,400") == (10, 20, 310, 420)


def test_live_try_adds_absolute_screen_coordinates_for_window_crop():
    state = {
        "target_xy": [50.4, 75.6],
        "recommended_action": {"action": "APPROACH_OR_ATTACK", "target_xy": [50.4, 75.6]},
    }

    add_screen_coordinates(state, (100, 200, 500, 600))

    assert state["screen_xy"] == [150.4, 275.6]
    assert state["recommended_action"]["screen_xy"] == [150.4, 275.6]


def test_private_server_actuator_prefers_screen_coordinates_for_cropped_capture():
    state = {
        "recommended_action": {"action": "APPROACH_OR_ATTACK", "target_xy": [50, 75]},
        "target_xy": [50, 75],
        "screen_xy": [150, 275],
        "target_confidence": 0.75,
    }

    plan = plan_private_server_action(state, execute=False, private_server_confirmed=True)

    assert plan["intended_action"] == "CLICK_TARGET"
    assert plan["screen_xy"] == [150, 275]


def test_private_server_actuator_plans_investigate_without_executing():
    state = {
        "recommended_action": {"action": "INVESTIGATE_TARGET", "target_xy": [509.0, 321.0]},
        "target_xy": [509.0, 321.0],
        "target_confidence": 0.53,
    }

    plan = plan_private_server_action(state, execute=False, private_server_confirmed=True)

    assert plan["mode"] == "dry_run"
    assert plan["intended_action"] == "MOVE_CURSOR_TO_TARGET"
    assert plan["screen_xy"] == [509, 321]
    assert plan["executed"] is False


def test_private_server_actuator_requires_private_server_confirmation():
    state = {"recommended_action": {"action": "APPROACH_OR_ATTACK"}, "target_xy": [100, 100]}

    try:
        plan_private_server_action(state, execute=False, private_server_confirmed=False)
    except SafetyGateError as exc:
        assert "private server" in str(exc)
    else:
        raise AssertionError("expected SafetyGateError")


def test_live_filter_rejects_minimap_and_task_icon_false_positives():
    image_width, image_height = 1922, 1031
    minimap = {"x_center": 1830, "y_center": 113, "width": 183, "height": 226, "xmin": 1738, "ymin": 0, "xmax": 1922, "ymax": 226, "confidence": 0.66}
    task_icon = {"x_center": 181, "y_center": 827, "width": 47, "height": 60, "xmin": 158, "ymin": 803, "xmax": 205, "ymax": 863, "confidence": 0.33}
    left_edge = {"x_center": 44, "y_center": 178, "width": 80, "height": 110, "xmin": 4, "ymin": 123, "xmax": 84, "ymax": 233, "confidence": 0.5}

    assert is_ui_or_overlay_box(minimap, image_width, image_height) is True
    assert is_ui_or_overlay_box(task_icon, image_width, image_height) is True
    assert is_ui_or_overlay_box(left_edge, image_width, image_height) is True


def test_live_filter_keeps_world_metin_and_sorts_by_confidence():
    image_width, image_height = 1922, 1031
    minimap = {"x_center": 1830, "y_center": 113, "width": 183, "height": 226, "xmin": 1738, "ymin": 0, "xmax": 1922, "ymax": 226, "confidence": 0.80}
    world_low = {"x_center": 855, "y_center": 550, "width": 78, "height": 105, "xmin": 816, "ymin": 498, "xmax": 894, "ymax": 603, "confidence": 0.25}
    world_high = {"x_center": 965, "y_center": 455, "width": 90, "height": 130, "xmin": 920, "ymin": 390, "xmax": 1010, "ymax": 520, "confidence": 0.55}

    kept = filter_world_metin_candidates([minimap, world_low, world_high], image_width, image_height)

    assert kept == [world_high, world_low]


def test_screen_xy_for_metin_body_clicks_below_center():
    box = {"x_center": 855, "y_center": 550, "width": 78, "height": 105, "xmin": 816, "ymin": 498, "xmax": 894, "ymax": 603}

    assert screen_xy_for_metin_body(box, (9, 0, 1931, 1031)) == [864, 559]


def test_visual_filter_rejects_candidate_with_green_mob_level_label(tmp_path):
    image_path = tmp_path / "mob_candidate.jpg"
    img = Image.new("RGB", (400, 300), (70, 120, 65))
    draw = ImageDraw.Draw(img)
    draw.rectangle((175, 85, 215, 95), fill=(40, 220, 40))
    img.save(image_path)
    candidate = {"x_center": 200, "y_center": 150, "width": 80, "height": 110, "xmin": 160, "ymin": 100, "xmax": 240, "ymax": 210, "confidence": 0.7}

    assert filter_world_metin_candidates_with_image(image_path, [candidate], 400, 300) == []


def test_visual_filter_keeps_candidate_without_green_level_label(tmp_path):
    image_path = tmp_path / "metin_candidate.jpg"
    img = Image.new("RGB", (400, 300), (70, 120, 65))
    draw = ImageDraw.Draw(img)
    draw.rectangle((170, 105, 230, 220), fill=(80, 75, 70))
    img.save(image_path)
    candidate = {"x_center": 200, "y_center": 155, "width": 80, "height": 120, "xmin": 160, "ymin": 95, "xmax": 240, "ymax": 215, "confidence": 0.7}

    assert filter_world_metin_candidates_with_image(image_path, [candidate], 400, 300) == [candidate]


def test_navigation_config_converts_normalized_point_to_screen_xy():
    cfg = NavigationConfig(reference_size=(1922, 1031), patrol_points={"center": [0.5, 0.5]})

    assert cfg.point_to_screen("center", (100, 50, 2022, 1081)) == [1061, 566]


def test_escape_menu_detector_detects_central_button_stack(tmp_path):
    path = tmp_path / "menu.jpg"
    img = Image.new("RGB", (1000, 700), (90, 150, 70))
    draw = ImageDraw.Draw(img)
    draw.rectangle((420, 210, 580, 560), fill=(15, 15, 15))
    for y in range(230, 520, 45):
        draw.rectangle((435, y, 565, y + 28), fill=(70, 70, 70), outline=(140, 140, 140))
    img.save(path)

    assert looks_like_escape_menu(path) is True


def test_escape_menu_detector_ignores_clear_gameplay_screen(tmp_path):
    path = tmp_path / "clear.jpg"
    img = Image.new("RGB", (1000, 700), (80, 145, 70))
    img.save(path)

    assert looks_like_escape_menu(path) is False


def test_navigation_config_converts_region_to_absolute_box():
    cfg = NavigationConfig(reference_size=(1922, 1031), ui_regions={"target_bar": [0.25, 0.10, 0.75, 0.20]})

    assert cfg.region_to_screen("target_bar", (100, 50, 2022, 1081)) == [580, 153, 1542, 256]


def test_spawn_memory_records_and_merges_nearby_spawn_observations(tmp_path):
    path = tmp_path / "spawns.json"
    memory = SpawnMemory(path)

    first = memory.record_observation([0.50, 0.40], screen_xy=[960, 412], confidence=0.6, now=10.0)
    second = memory.record_observation([0.52, 0.41], screen_xy=[999, 423], confidence=0.8, now=20.0)

    assert first["id"] == second["id"]
    assert len(memory.spawns) == 1
    assert memory.spawns[0]["observations"] == 2
    assert memory.spawns[0]["confidence"] == 0.8


def test_spawn_memory_marks_destroyed(tmp_path):
    memory = SpawnMemory(tmp_path / "spawns.json")
    spawn = memory.record_observation([0.50, 0.40], screen_xy=[960, 412], confidence=0.6, now=10.0)

    memory.mark_destroyed(spawn["id"], now=30.0)

    assert memory.spawns[0]["destroyed_count"] == 1
    assert memory.spawns[0]["last_destroyed_ts"] == 30.0


def test_extract_destroyed_spawn_clicks_uses_last_click_before_destroy():
    events = [
        {"step": 1, "action": "click_world_metin_body", "xy": [100, 200], "t": 1.0},
        {"step": 2, "action": "search_rotate", "t": 2.0},
        {"step": 3, "action": "click_world_metin_body", "xy": [300, 400], "t": 3.0},
        {"step": 4, "action": "attack_selected_target", "t": 4.0},
        {"step": 5, "action": "count_destroyed", "destroyed_count": 1, "t": 5.0},
    ]

    assert extract_destroyed_spawn_clicks(events) == [
        {
            "xy": [300, 400],
            "click_step": 3,
            "click_t": 3.0,
            "destroy_step": 5,
            "destroy_t": 5.0,
            "destroyed_count": 1,
        }
    ]


def test_seed_spawn_memory_makes_historical_spawns_immediately_eligible(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                '{"step": 1, "action": "click_world_metin_body", "xy": [961, 515], "t": 1.0}',
                '{"step": 2, "action": "count_destroyed", "destroyed_count": 1, "t": 9.0}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    memory = seed_spawn_memory([events_path], tmp_path / "spawns.json", window_bbox=(0, 0, 1922, 1031))
    cfg = NavigationConfig(reference_size=(1922, 1031), patrol_points={"fallback": [0.1, 0.1]})

    target = choose_next_patrol_point(memory.spawns, cfg, now=0.0, cooldown_seconds=120.0)

    assert target["kind"] == "known_spawn"
    assert target["spawn_id"] == "spawn_001"
    assert memory.spawns[0]["last_visited_ts"] < -999.0


def test_scan_code_input_knows_visible_movement_and_navigation_keys():
    for key in ["w", "a", "s", "d", "q", "e", "r", "f", "t", "g", "z", "tab", "space", "1", "m", "esc"]:
        assert key in SCANCODES
        assert isinstance(SCANCODES[key], int)


def test_parse_player_coordinate_text_extracts_name_and_xy():
    parsed = parse_player_coordinate_text("Yoshypt (607, 1025)")

    assert parsed == {"player_name": "Yoshypt", "coord": [607, 1025], "raw_text": "Yoshypt (607, 1025)"}


def test_crop_minimap_area_uses_normalized_top_right_region(tmp_path):
    image_path = tmp_path / "screen.jpg"
    Image.new("RGB", (1922, 1031), "black").save(image_path)

    crop = crop_minimap_area(image_path)

    assert crop.size == (384, 309)


def test_build_local_state_combines_coordinate_text_and_visual_metadata(tmp_path):
    image_path = tmp_path / "screen.jpg"
    Image.new("RGB", (1922, 1031), "black").save(image_path)

    state = build_local_state(image_path, coordinate_text="Yoshypt (607, 1025)")

    assert state["player_coord"] == [607, 1025]
    assert state["player_name"] == "Yoshypt"
    assert state["ocr"]["coordinate_source"] == "provided_text"
    assert state["image"]["width"] == 1922
    assert state["regions"]["minimap_area"] == [1538, 30, 1922, 339]


def test_compare_coordinate_feedback_reports_successful_delta():
    before = {"player_coord": [607, 1025]}
    after = {"player_coord": [612, 1019]}

    feedback = compare_coordinate_feedback(before, after, expected_action="hold W 1.0s")

    assert feedback["status"] == "moved"
    assert feedback["delta"] == [5, -6]
    assert feedback["expected_action"] == "hold W 1.0s"


def test_compare_coordinate_feedback_reports_missing_coordinates():
    feedback = compare_coordinate_feedback({"player_coord": [607, 1025]}, {"player_coord": None}, expected_action="hold W")

    assert feedback["status"] == "unknown_no_after_coordinate"
    assert feedback["needs_feedback"] == "manual hover or OCR coordinate read"


def test_patrol_prefers_oldest_known_spawn_over_fallback():
    cfg = NavigationConfig(reference_size=(1922, 1031), patrol_points={"fallback": [0.50, 0.50]})
    spawns = [
        {"id": "new", "window_relative_xy": [0.20, 0.20], "last_visited_ts": 95.0, "last_seen_ts": 80.0},
        {"id": "old", "window_relative_xy": [0.70, 0.35], "last_visited_ts": 10.0, "last_seen_ts": 20.0},
    ]

    target = choose_next_patrol_point(spawns, cfg, now=120.0, cooldown_seconds=30.0)

    assert target["kind"] == "known_spawn"
    assert target["spawn_id"] == "old"
    assert target["relative_xy"] == [0.70, 0.35]


def test_patrol_falls_back_to_config_points_when_known_spawns_recent():
    cfg = NavigationConfig(reference_size=(1922, 1031), patrol_points={"fallback": [0.50, 0.50]})
    spawns = [{"id": "recent", "window_relative_xy": [0.20, 0.20], "last_visited_ts": 110.0, "last_seen_ts": 100.0}]

    target = choose_next_patrol_point(spawns, cfg, now=120.0, cooldown_seconds=30.0)

    assert target["kind"] == "fallback_patrol"
    assert target["relative_xy"] == [0.50, 0.50]


def test_client_state_serializes_process_window_and_source_metadata():
    state = ClientState(
        process=ProcessInfo(pid=1234, name="pgclient.app", executable_path="C:/Games/MT2/pgclient.app"),
        window=WindowInfo(hwnd=5678, title="MT2Portugalia", focused=True, rect=[10, 20, 810, 620]),
        sources={"process_window_probe": "read_only"},
    )

    data = state.to_dict()

    assert data["schema_version"] == 1
    assert data["process"]["pid"] == 1234
    assert data["window"]["title"] == "MT2Portugalia"
    assert data["sources"]["process_window_probe"] == "read_only"


def test_merge_client_states_keeps_non_null_latest_values():
    first = ClientState(process=ProcessInfo(pid=1234, name="pgclient.app"), sources={"process": "probe"})
    second = ClientState(window=WindowInfo(title="MT2Portugalia", focused=True), sources={"window": "probe"})

    merged = merge_client_states(first, second)

    assert merged.process.pid == 1234
    assert merged.window.title == "MT2Portugalia"
    assert merged.sources == {"process": "probe", "window": "probe"}


def test_parse_tsv_state_line_maps_client_python_logger_fields():
    line = "1782779502\tmetin2_map_a1\t91058\t89449\t20344\t374\t1776\t1238\t1238\t42\tYoshypt\tMetin da Batalha\r\n"

    state = parse_tsv_state_line(line)

    assert state.game is not None
    assert state.game.map_name == "metin2_map_a1"
    assert state.game.player_coord == [91058, 89449, 20344]
    assert state.game.hp == 374
    assert state.game.max_hp == 1776
    assert state.game.sp == 1238
    assert state.game.max_sp == 1238
    assert state.game.target_vid == 42
    assert state.game.player_name == "Yoshypt"
    assert state.game.target_name == "Metin da Batalha"
    assert state.sources["client_python_tsv"] == "local_read_only_client_python_state_logger"


def test_tsv_client_state_source_reads_latest_valid_line_and_warns_on_stale(tmp_path):
    tsv = tmp_path / "hermes_state.tsv"
    tsv.write_text(
        "bad\tline\n"
        "1782779000\tmetin2_map_a1\t1\t2\t3\t10\t20\t30\t40\t0\tYoshypt\t\n"
        "1782779502\tmetin2_map_a1\t91058\t89449\t20344\t374\t1776\t1238\t1238\t42\tYoshypt\tMetin da Batalha\n",
        encoding="utf-8",
    )

    state = TsvClientStateSource(tsv, max_age_seconds=0, now=lambda: tsv.stat().st_mtime + 1).read()

    assert state.game is not None
    assert state.game.player_coord == [91058, 89449, 20344]
    assert state.game.target_vid == 42
    assert state.game.target_name == "Metin da Batalha"
    assert any("stale" in warning.lower() for warning in state.warnings)


def test_movement_delta_uses_client_tsv_coordinates_only():
    before = parse_tsv_state_line(
        "1\tmetin2_map_a1\t100\t200\t3\t10\t20\t30\t40\t0\tYoshypt\t\n"
    )
    after = parse_tsv_state_line(
        "2\tmetin2_map_a1\t160\t170\t4\t10\t20\t30\t40\t0\tYoshypt\t\n"
    )

    delta = movement_delta(before, after, key="w", seconds=0.5)

    assert delta["key"] == "w"
    assert delta["from"] == [100, 200, 3]
    assert delta["to"] == [160, 170, 4]
    assert delta["delta"] == [60, -30, 1]
    assert delta["distance_xy"] == 67.082
    assert delta["moved"] is True


def test_summarize_movement_observations_builds_navigation_model():
    model = summarize_movement_observations([
        {"key": "w", "delta": [100, -50, 1], "distance_xy": 111.8, "moved": True},
        {"key": "w", "delta": [80, -30, 0], "distance_xy": 85.4, "moved": True},
        {"key": "a", "delta": [0, 0, 0], "distance_xy": 0.0, "moved": False},
    ])

    assert model["w"]["samples"] == 2
    assert model["w"]["mean_delta_xy"] == [90.0, -40.0]
    assert model["w"]["mean_distance_xy"] == 98.6
    assert model["a"]["moved_samples"] == 0


def test_choose_key_for_direction_prefers_observed_projection():
    model = {
        "w": {"mean_delta_xy": [100.0, 0.0]},
        "s": {"mean_delta_xy": [-100.0, 0.0]},
        "a": {"mean_delta_xy": [0.0, -100.0]},
        "d": {"mean_delta_xy": [0.0, 100.0]},
    }

    assert choose_key_for_direction((1000, 1000), (1300, 980), model) == "w"
    assert choose_key_for_direction((1000, 1000), (970, 1300), model) == "d"


def test_process_window_snapshot_maps_to_client_state_without_memory_or_packets():
    snapshot = ProcessWindowSnapshot(
        pid=4321,
        process_name="pgclient.app",
        executable_path="C:/Games/MT2/pgclient.app",
        hwnd=9876,
        window_title="MT2Portugalia",
        focused=False,
        rect=[0, 0, 1280, 720],
    )

    state = snapshot_to_client_state(snapshot)

    assert state.process.pid == 4321
    assert state.window.hwnd == 9876
    assert state.sources["process_window_probe"] == "read_only_os_metadata"
    assert "memory" not in state.sources


def test_client_state_source_protocol_accepts_simple_source():
    class StaticSource:
        name = "static"

        def read(self):
            return ClientState(sources={"static": "test"})

    source: ClientStateSource = StaticSource()

    assert source.read().sources == {"static": "test"}


def test_screenshot_to_client_state_maps_image_ocr_and_detector_state(tmp_path):
    image_path = tmp_path / "screen.jpg"
    Image.new("RGB", (640, 480), "black").save(image_path)
    detector_state = {
        "target_visible": True,
        "target_confirmed": True,
        "target_confidence": 0.91,
        "target_xy": [320.0, 240.0],
        "target_box": {"xmin": 300, "ymin": 220, "xmax": 340, "ymax": 260},
        "recommended_action": {"action": "APPROACH_OR_ATTACK"},
    }

    state = screenshot_to_client_state(image_path, detector_state=detector_state, coordinate_text="Yoshypt (607, 1025)")
    data = state.to_dict()

    assert data["screenshot"]["path"].endswith("screen.jpg")
    assert data["screenshot"]["width"] == 640
    assert data["screenshot"]["height"] == 480
    assert data["ocr"]["player_name"] == "Yoshypt"
    assert data["ocr"]["player_coord"] == [607, 1025]
    assert data["visual"]["target_visible"] is True
    assert data["visual"]["target_confidence"] == 0.91
    assert data["sources"]["screenshot_state"] == "image_metadata_and_visible_state"
    assert "memory" not in data["sources"]


def test_screenshot_source_warns_without_ocr_instead_of_inventing_values(tmp_path):
    image_path = tmp_path / "screen.jpg"
    Image.new("RGB", (320, 200), "black").save(image_path)

    state = ScreenshotStateSource(image_path).read()

    assert state.ocr is not None
    assert state.ocr.player_coord is None
    assert any("coordinate" in warning.lower() for warning in state.warnings)
    assert state.sources["screenshot_state"] == "image_metadata_and_visible_state"


def test_build_probe_state_merges_process_and_screenshot_sources(tmp_path):
    image_path = tmp_path / "screen.jpg"
    Image.new("RGB", (200, 100), "black").save(image_path)
    process_state = ClientState(process=ProcessInfo(pid=12, name="pgclient.app"), sources={"process_window_probe": "test"})

    state = build_probe_state(
        screenshot=image_path,
        coordinate_text="Yoshypt: 1, 2",
        process_state=process_state,
        json_state_path=None,
        tsv_state_path=None,
    )
    data = state.to_dict()

    assert data["process"]["pid"] == 12
    assert data["screenshot"]["width"] == 200
    assert data["ocr"]["player_coord"] == [1, 2]
    assert data["sources"] == {
        "process_window_probe": "test",
        "screenshot_state": "image_metadata_and_visible_state",
    }


def test_build_probe_state_includes_client_python_tsv_when_available(tmp_path):
    tsv = tmp_path / "hermes_state.tsv"
    tsv.write_text(
        "1782779502\tmetin2_map_a1\t91058\t89449\t20344\t374\t1776\t1238\t1238\t0\tYoshypt\t\n",
        encoding="utf-8",
    )
    process_state = ClientState(process=ProcessInfo(pid=12, name="pgclient.app"), sources={"process_window_probe": "test"})

    state = build_probe_state(process_state=process_state, json_state_path=None, tsv_state_path=tsv)
    data = state.to_dict()

    assert data["process"]["pid"] == 12
    assert data["game"]["map_name"] == "metin2_map_a1"
    assert data["game"]["player_coord"] == [91058, 89449, 20344]
    assert data["sources"]["client_python_tsv"] == "local_read_only_client_python_state_logger"


def test_write_probe_state_writes_json_artifact(tmp_path):
    out = tmp_path / "latest_client_state.json"
    state = ClientState(sources={"unit": "test"})

    written = write_probe_state(state, out)

    assert written == out
    assert '"schema_version": 1' in out.read_text(encoding="utf-8")


def test_update_metin_coordinate_table_extracts_metin_memory_text(tmp_path):
    report = tmp_path / "memory_report.json"
    report.write_text(
        '{"hits": [{"raw": "Cão Selvagem Feroz  Metin da Batalha(284, 218)  V"}]}',
        encoding="utf-8",
    )

    rows = extract_from_json(report)

    assert len(rows) == 1
    assert rows[0].id == "unknown_current_map_metin_da_batalha_284_218"
    assert rows[0].map_name == "unknown_current_map"
    assert rows[0].metin_name == "Metin da Batalha"
    assert rows[0].x == 284
    assert rows[0].y == 218


def test_metin_coordinate_table_records_confirmed_yongan_map():
    table = Path("data/metin_coordinates.csv")
    rows = table.read_text(encoding="utf-8")

    assert "yongan_metin_da_batalha_284_218" in rows
    assert "Yongan,Metin da Batalha,284,218" in rows


def test_explore_yongan_parse_patrol():
    assert parse_patrol("w:1.5,a:2,d:0.25") == [("w", 1.5), ("a", 2.0), ("d", 0.25)]


def test_choose_unvisited_target_prefers_nearest_uncovered_cell():
    target = choose_unvisited_target(
        current=(100, 100),
        visited_cells={(1, 1), (2, 2)},
        bounds=(0, 0, 300, 300),
        cell_size=50,
    )

    assert target == (125, 75)


def test_choose_key_toward_target_uses_learned_deltas():
    observations = {
        "w": [(-12, 8), (-24, 16)],
        "a": [(12, 9)],
        "s": [(4, -15)],
        "d": [(-12, -8)],
    }

    key = choose_key_toward_target((100, 100), (70, 120), observations)

    assert key == "w"


def test_build_map_reference_summarizes_metin_and_coverage():
    reference = build_map_reference(
        map_name="Yongan",
        metins=[{"metin_name": "Metin da Batalha", "x": 284, "y": 218}],
        visited_cells={(5, 4), (5, 5)},
        blocked_cells={(6, 5)},
        observations=[{"key": "w", "delta_x": -12, "delta_y": 8}],
        bounds=(0, 0, 500, 500),
        cell_size=50,
    )

    assert reference["map_name"] == "Yongan"
    assert reference["known_metins"][0]["coord"] == [284, 218]
    assert reference["coverage"]["visited_cells"] == 2
    assert reference["coverage"]["blocked_cells"] == 1
    assert reference["coverage"]["blocked_cell_ids"] == [[6, 5]]
    assert reference["navigation_model"]["w"]["mean_delta"] == [-12.0, 8.0]


def test_load_blocked_cells_from_map_reference(tmp_path):
    from scripts.explore_yongan_metin_coords import load_blocked_cells_from_reference

    ref = tmp_path / "map_reference.json"
    ref.write_text('{"coverage": {"blocked_cell_ids": [[18, 9], [12, 4]]}}', encoding="utf-8")

    assert load_blocked_cells_from_reference(ref) == {(18, 9), (12, 4)}


def test_choose_unvisited_target_skips_blocked_cells():
    target = choose_unvisited_target(
        current=(100, 100),
        visited_cells={(1, 1)},
        blocked_cells={(2, 1)},
        bounds=(0, 0, 300, 300),
        cell_size=50,
    )

    assert target != (125, 75)
    assert target == (75, 125)


def test_should_block_target_detects_stalls_and_oscillation():
    assert should_block_target([(332, 135), (332, 135), (332, 135), (332, 135)], stall_limit=3)
    assert should_block_target([(332, 135), (332, 146), (332, 135), (332, 146)], oscillation_limit=4)
    assert not should_block_target([(320, 140), (325, 140), (330, 140)], stall_limit=3)


def test_fallback_key_rotates_away_from_recent_keys():
    assert fallback_key(["w", "s", "w", "s"]) == "a"
    assert fallback_key(["a", "d", "a", "d"]) == "w"
