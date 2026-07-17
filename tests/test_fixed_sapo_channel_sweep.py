import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.fixed_sapo_channel_sweep import (
    blocked_cycle_action,
    channel_menu_visible_in_screenshot,
    finalize_channel_switch_with_retries,
    LowDpsNudgeController,
    low_dps_drop_rate,
    low_dps_adjustment_key,
    run_channel_switch_with_input_lock,
    should_stop,
)
from scripts.fixed_sapo_space_control import distance_to_sapo, read_state, sapo_probe_from_state


def test_read_state_retries_transient_empty_json_file(tmp_path):
    # The live client can briefly expose an empty hermes_state.json while it is
    # rewriting the file. A keep-sweep must not stop on one transient
    # JSONDecodeError: Expecting value: line 1 column 1.
    state_file = tmp_path / "hermes_state.json"
    state_file.write_text("", encoding="utf-8")

    def writer() -> None:
        time.sleep(0.03)
        state_file.write_text('{"map":"m","player":{"hp":8296,"max_hp":8296}}', encoding="utf-8")

    threading.Thread(target=writer, daemon=True).start()

    state = read_state(state_file, attempts=5, retry_delay=0.02)

    assert state["map"] == "m"
    assert state["player"]["hp"] == 8296
    assert state["_age_seconds"] >= 0


def test_low_dps_adjustment_key_centers_player_toward_sapo_probe_axes():
    state = {
        "player": {"x": 1000, "y": 1000},
        "named_metin_probe": [{"name": "Sapo de Pedra", "alive": True, "pixel_position": [1120, 980]}],
    }

    assert low_dps_adjustment_key(state, deadzone=40) == "d"

    state["named_metin_probe"][0]["pixel_position"] = [1005, 900]
    assert low_dps_adjustment_key(state, deadzone=40) == "w"

    state["named_metin_probe"][0]["pixel_position"] = [1005, 1010]
    assert low_dps_adjustment_key(state, deadzone=40) is None


def test_low_dps_drop_rate_waits_for_full_window_before_nudging():
    samples = [(0.0, 85.0), (1.0, 84.8), (2.0, 85.2), (3.0, 84.9)]

    assert low_dps_drop_rate(samples, min_span_seconds=8.0, noise_margin_pct=3.0) is None


def test_low_dps_drop_rate_ignores_visual_noise_jitter():
    # Old first-vs-last tracking turned this into negative DPS and nudged every
    # second. Treat sub-3pp drift as noise instead.
    samples = [(0.0, 84.0), (2.0, 85.5), (4.0, 83.8), (6.0, 86.0), (8.0, 84.5)]

    assert low_dps_drop_rate(samples, min_span_seconds=8.0, noise_margin_pct=3.0) == 0.0


def test_low_dps_drop_rate_detects_real_sapo_hp_progress():
    samples = [(0.0, 86.0), (2.0, 84.0), (4.0, 76.0), (6.0, 61.0), (8.0, 52.0)]

    assert low_dps_drop_rate(samples, min_span_seconds=8.0, noise_margin_pct=3.0) > 3.0


def test_low_dps_nudge_controller_keeps_improved_probe_position():
    c = LowDpsNudgeController(cooldown_seconds=0.0, improvement_margin=0.15)

    first = c.update(dps=0.0, threshold=0.2, now=10.0, preferred_key="w")
    second = c.update(dps=0.35, threshold=0.2, now=20.0, preferred_key="w")

    assert first["kind"] == "probe"
    assert first["key"] == "w"
    assert second["kind"] == "keep"
    assert second["key"] == "w"


def test_low_dps_nudge_controller_undoes_worse_probe_position():
    c = LowDpsNudgeController(cooldown_seconds=0.0, improvement_margin=0.15)

    first = c.update(dps=0.0, threshold=0.2, now=10.0, preferred_key="a")
    second = c.update(dps=0.05, threshold=0.2, now=20.0, preferred_key="a")

    assert first["kind"] == "probe"
    assert first["key"] == "a"
    assert second["kind"] == "undo"
    assert second["key"] == "d"
    assert second["undo_for"] == "a"


def test_low_dps_nudge_controller_respects_cooldown_between_probes():
    c = LowDpsNudgeController(cooldown_seconds=6.0, improvement_margin=0.15)

    assert c.update(dps=0.0, threshold=0.2, now=10.0, preferred_key="w")["kind"] == "probe"
    assert c.update(dps=0.0, threshold=0.2, now=11.0, preferred_key="w")["kind"] == "undo"
    assert c.update(dps=0.0, threshold=0.2, now=12.0, preferred_key="w") is None
    assert c.update(dps=0.0, threshold=0.2, now=17.0, preferred_key="w")["kind"] == "probe"


def test_sapo_probe_prefers_nearby_selected_target_over_far_probe_row():
    # Regression from fixed_sapo_sweep_1784226477: target board had a selected
    # nearby Sapo, but named_metin_probe's first Sapo row was far away, causing a
    # false too_far_from_fixed_spawn skip/channel switch.
    state = {
        "player": {"x": 17256.0, "y": 57575.0},
        "target": {"name": "Sapo de Pedra", "vid": 3793226, "alive": True, "pixel_position": [17200.0, 57400.0]},
        "named_metin_probe": [{"name": "Sapo de Pedra", "vid": 3669153, "alive": True, "pixel_position": [21300.0, 64300.0]}],
    }

    probe = sapo_probe_from_state(state)

    assert probe["vid"] == 3793226
    assert probe["source"] == "target"
    assert distance_to_sapo(state) < 350.0


def test_sapo_probe_still_uses_probe_lists_when_no_selected_sapo_target():
    state = {
        "player": {"x": 1000, "y": 1000},
        "target": {"name": "Orc Preto", "vid": 1, "alive": True, "pixel_position": [1000, 1000]},
        "named_metin_probe": [{"name": "Sapo de Pedra", "vid": 2, "alive": True, "pixel_position": [1120, 980]}],
    }

    probe = sapo_probe_from_state(state)

    assert probe["vid"] == 2
    assert probe["source"] == "named_metin_probe"


def test_should_stop_obeys_dashboard_stop_file(tmp_path):
    stop_file = tmp_path / "run.stop"
    assert should_stop(stop_file) is False

    stop_file.write_text("stop", encoding="utf-8")
    assert should_stop(stop_file) is True


def test_repeat_sweep_keeps_running_when_fixed_spawn_is_far():
    # User repro: keep-running mode exited immediately on too_far_from_fixed_spawn.
    # Continuous mode should treat that as a skip/advance condition, not finish the run.
    assert blocked_cycle_action("too_far_from_fixed_spawn", repeat_while_running=True) == "continue"


def test_repeat_sweep_still_stops_on_hard_safety_blocks():
    assert blocked_cycle_action("player_dead", repeat_while_running=True) == "stop"
    assert blocked_cycle_action("hp_stop_threshold", repeat_while_running=True) == "stop"
    assert blocked_cycle_action("state_stale", repeat_while_running=True) == "stop"


def test_channel_menu_visible_detector_flags_synthetic_menu_screenshot(tmp_path):
    from PIL import Image, ImageDraw

    menu = tmp_path / "menu.png"
    no_menu = tmp_path / "no_menu.png"
    im = Image.new("RGB", (1000, 800), (0, 0, 0))
    draw = ImageDraw.Draw(im)
    crop = (int(1000 * 0.42), int(800 * 0.33), int(1000 * 0.59), int(800 * 0.71))
    draw.rectangle(crop, fill=(118, 118, 118))
    draw.rectangle((crop[0] + 35, crop[1] + 45, crop[2] - 35, crop[1] + 95), fill=(135, 52, 32))
    im.save(menu)
    Image.new("RGB", (1000, 800), (0, 0, 0)).save(no_menu)

    assert channel_menu_visible_in_screenshot(menu) is True
    assert channel_menu_visible_in_screenshot(no_menu) is False


def test_channel_switch_retries_same_row_when_menu_stays_visible_once(tmp_path):
    cycle_state = tmp_path / "cycle_state.json"
    clicks = []
    captures = []
    detections = iter([False])

    result = finalize_channel_switch_with_retries(
        {
            "screen_point": [959, 441],
            "channel_auto_cycle": True,
            "cycle_state_deferred": True,
            "channel_cycle_state_path": str(cycle_state),
            "channel_index": 0,
            "points_count": 7,
        },
        run_id="retry-test",
        cycle=6,
        round_no=1,
        screenshot_dir=tmp_path,
        window_query="MT2Portugalia",
        screenshot_backend="screen",
        out=tmp_path / "run.jsonl",
        initial_menu_visible=True,
        max_retries=2,
        retry_wait_seconds=0,
        click_fn=lambda x, y: clicks.append((x, y)),
        capture_fn=lambda path, *args, **kwargs: captures.append(path),
        menu_detector=lambda path: next(detections),
        sleep_fn=lambda seconds: None,
    )

    assert clicks == [(959, 441)]
    assert len(captures) == 1
    assert result["verified"] is True
    assert result["retry_count"] == 1
    assert result["retry_attempts"][0]["menu_visible"] is False
    assert result["cycle_state_written"] is True
    assert "cycle_state_deferred" not in result


def test_channel_switch_retry_still_unconfirmed_after_bounded_failures(tmp_path):
    clicks = []
    result = finalize_channel_switch_with_retries(
        {
            "screen_point": [959, 441],
            "channel_auto_cycle": True,
            "cycle_state_deferred": True,
            "channel_cycle_state_path": str(tmp_path / "cycle_state.json"),
            "channel_index": 0,
            "points_count": 7,
        },
        run_id="retry-test",
        cycle=6,
        round_no=1,
        screenshot_dir=tmp_path,
        window_query="MT2Portugalia",
        screenshot_backend="screen",
        out=tmp_path / "run.jsonl",
        initial_menu_visible=True,
        max_retries=2,
        retry_wait_seconds=0,
        click_fn=lambda x, y: clicks.append((x, y)),
        capture_fn=lambda *args, **kwargs: None,
        menu_detector=lambda path: True,
        sleep_fn=lambda seconds: None,
    )

    assert clicks == [(959, 441), (959, 441)]
    assert result["verified"] is False
    assert result["unverified_reason"] == "channel_menu_still_visible"
    assert result["retry_count"] == 2


def test_channel_switch_holds_shared_input_lock(tmp_path):
    from metin2_research.live_input_lock import read_live_input_lock

    observed = []

    def fake_switch():
        observed.append(read_live_input_lock(tmp_path / "live_input.lock"))
        return {"verified": True}

    result = run_channel_switch_with_input_lock(
        fake_switch,
        lock_path=tmp_path / "live_input.lock",
        run_id="sweep-run",
        timeout_seconds=0,
    )

    assert result == {"verified": True}
    assert observed[0]["owner"] == "fixed_sapo_sweep"
    assert observed[0]["action"] == "channel_switch"
    assert read_live_input_lock(tmp_path / "live_input.lock") is None
