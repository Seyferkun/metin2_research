"""Tests for the ONNX detector, controller, config loader, and memory probe."""

from pathlib import Path
from PIL import Image, ImageDraw

from metin2_research import load_config, find_project_root, __version__


def test_config_loader_loads_yaml():
    cfg = load_config()
    assert isinstance(cfg, dict)
    assert "model" in cfg
    assert cfg["model"]["backend"] == "onnx"


def test_config_loader_unknown_path_is_empty_dict(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg == {}


def test_estimate_distance_labels(tmp_path):
    from metin2_research.onnx_detector import estimate_distance, estimate_distance_meters

    assert estimate_distance({"height": 200}) == "close"
    assert estimate_distance({"height": 100}) == "medium"
    assert estimate_distance({"height": 40}) == "far"
    assert estimate_distance({"height": 10}) == "very_far"
    assert estimate_distance({"height": 0}) == "very_far"


def test_estimate_distance_meters():
    from metin2_research.onnx_detector import estimate_distance_meters

    assert estimate_distance_meters({"height": 200}) == 3.0
    assert estimate_distance_meters({"height": 100}) == 6.0
    assert estimate_distance_meters({"height": 40}) == 15.0
    assert estimate_distance_meters({"height": 20}) == 30.0
    assert estimate_distance_meters({"height": 0}) == 99.0
    assert estimate_distance_meters({"height": 3}) == 99.0


def test_letterbox_keeps_aspect_ratio(tmp_path):
    from metin2_research.onnx_detector import _letterbox

    img = Image.new("RGB", (1600, 900), "black")
    tensor, scale, px, py = _letterbox(img, target_size=640)
    assert tensor.shape == (3, 640, 640)
    # 1600->640 scale = 640/1600 = 0.4, scaled width = 640, scaled height = 900*0.4 = 360
    # padding = (640-640)//2 = 0, (640-360)//2 = 140
    assert scale == 0.4
    assert px == 0
    assert py == 140


def test_nms_suppresses_overlapping_boxes():
    from metin2_research.onnx_detector import _nms
    import numpy as np

    # Two identical boxes + one far away
    xyxy = np.array([
        [10, 10, 100, 100],
        [12, 12, 98, 98],    # high IoU with first
        [500, 500, 600, 600], # no overlap
    ], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)

    keep = _nms(xyxy, scores, iou_threshold=0.45)
    # First and third should be kept, second suppressed
    assert keep[0] == True
    assert keep[1] == False
    assert keep[2] == True


def test_nms_keeps_non_overlapping():
    from metin2_research.onnx_detector import _nms
    import numpy as np

    xyxy = np.array([
        [10, 10, 50, 50],
        [100, 100, 150, 150],
        [200, 200, 250, 250],
    ], dtype=np.float32)
    scores = np.array([0.5, 0.7, 0.9], dtype=np.float32)

    keep = _nms(xyxy, scores, iou_threshold=0.5)
    assert keep.sum() == 3  # all kept


def test_scale_back(tmp_path):
    from metin2_research.onnx_detector import _scale_back

    det = [{"xmin": 60, "ymin": 60, "xmax": 120, "ymax": 120, "x_center": 90, "y_center": 90, "width": 60, "height": 60, "confidence": 0.8, "class_id": 0, "name": "test"}]
    result = _scale_back(det, 640, 1600, 900, 0.4, 0, 140)
    assert len(result) == 1
    # Undo padding: y - 140, then / 0.4
    assert abs(result[0]["xmin"] - 150) < 1  # (60-0)/0.4
    assert abs(result[0]["ymin"] - (-200)) < 1  # (60-140)/0.4 -> -200, clamped to 0


def test_controller_initial_state():
    from metin2_research.controller import MetinController, ControllerState

    state = ControllerState()
    assert state.mode == "IDLE"
    assert state.step == 0
    assert state.target_visible is False
    assert state.potions_used == 0
    assert state.metins_destroyed == 0


def test_memory_probe_returns_unavailable_for_missing_process():
    from metin2_research.memory_probe import MemoryProbe

    probe = MemoryProbe()
    result = probe.read_state("nonexistent_process_xyz_123")
    assert result.available is False
    assert result.error is not None
    assert "not found" in result.error


def test_memory_probe_requires_confirmed_offsets_when_process_exists():
    from metin2_research.memory_probe import MemoryProbe

    # Use a known-valid current Python PID so the probe reaches the no-offsets branch
    import os
    probe = MemoryProbe({"memory_probe": {"offsets": {}}})
    result = probe.read_state(pid=os.getpid())
    assert result.available is False
    assert result.error is not None
    assert "No confirmed memory offsets" in result.error


def test_memory_probe_parse_addr():
    from metin2_research.memory_probe import MemoryProbe

    assert MemoryProbe._parse_addr(1234) == 1234
    assert MemoryProbe._parse_addr("0x10") == 16
    assert MemoryProbe._parse_addr("42") == 42
    assert MemoryProbe._parse_addr(None) is None


def test_memory_probe_result_dataclass():
    from metin2_research.memory_probe import MemoryProbeResult

    r = MemoryProbeResult(available=True, hp=500, max_hp=1000, player_x=123.4, player_y=567.8)
    # hp_percent is computed by _probe(), not the constructor
    assert r.hp == 500
    assert r.player_x == 123.4
    assert r.hp_percent is None  # computed during probe, not at construction


def test_load_config_importable():
    from metin2_research import load_config, find_project_root, __version__
    assert __version__ == "0.2.0"
    root = find_project_root()
    assert (root / "config.yaml").exists()