import pytest

from metin2_research.client_state.navigation import OnlineNavModel, choose_waypoint


def calibrated_model():
    return {
        "w": {"mean_delta_xy": [100.0, 0.0], "mean_distance_xy": 100.0},
        "a": {"mean_delta_xy": [0.0, 100.0], "mean_distance_xy": 100.0},
        "s": {"mean_delta_xy": [-100.0, 0.0], "mean_distance_xy": 100.0},
        "d": {"mean_delta_xy": [0.0, -100.0], "mean_distance_xy": 100.0},
    }


def test_online_nav_model_falls_back_to_calibration_until_min_live_samples():
    model = OnlineNavModel(calibrated_model())

    model.record_step("w", (0, 0), (150, 0))
    model.record_step("w", (0, 0), (150, 0))

    assert model.get_delta("w") == (100.0, 0.0)
    assert model.summary()["w"] == {"n_live": 2, "cal": [100.0, 0.0], "current": [100.0, 0.0]}


def test_online_nav_model_blends_live_and_calibrated_after_min_samples():
    model = OnlineNavModel(calibrated_model())

    for _ in range(3):
        model.record_step("w", (0, 0), (200, 50))

    assert model.get_delta("w") == pytest.approx((170.0, 35.0))
    assert model.get_distance_per_sec("w") == pytest.approx((200.0**2 + 50.0**2) ** 0.5 / 0.6)
    assert model.summary()["w"] == {"n_live": 3, "cal": [100.0, 0.0], "current": [170.0, 35.0]}


def test_online_nav_model_discards_calibration_on_sign_flip():
    model = OnlineNavModel(calibrated_model())

    for _ in range(3):
        model.record_step("w", (0, 0), (-120, 0))

    assert model.get_delta("w") == pytest.approx((-120.0, 0.0))
    assert model.choose_steps((0, 0), (-1000, 0), max_hold=0.45)[0][0] == {"w"}


def test_choose_steps_returns_simultaneous_set_for_diagonal_complementary_keys():
    model = OnlineNavModel(
        {
            "w": {"mean_delta_xy": [100.0, 0.0], "mean_distance_xy": 100.0},
            "a": {"mean_delta_xy": [0.0, 100.0], "mean_distance_xy": 100.0},
            "s": {"mean_delta_xy": [-100.0, 0.0], "mean_distance_xy": 100.0},
            "d": {"mean_delta_xy": [0.0, -100.0], "mean_distance_xy": 100.0},
        }
    )

    steps = model.choose_steps((0, 0), (1000, 1000), max_hold=0.45)

    assert steps == [({"w", "a"}, 0.45)]


def test_choose_steps_does_not_combine_opposing_keys():
    model = OnlineNavModel(
        {
            "w": {"mean_delta_xy": [100.0, 0.0], "mean_distance_xy": 100.0},
            "s": {"mean_delta_xy": [99.0, 1.0], "mean_distance_xy": 99.0},
            "a": {"mean_delta_xy": [-100.0, 0.0], "mean_distance_xy": 100.0},
            "d": {"mean_delta_xy": [0.0, -100.0], "mean_distance_xy": 100.0},
        }
    )

    steps = model.choose_steps((0, 0), (1000, 10), max_hold=0.45)

    assert steps[0][0] == {"w"}


def test_online_nav_model_decay_and_reset_suspect_key():
    model = OnlineNavModel(calibrated_model())
    for i in range(7):
        model.record_step("w", (0, 0), (200 + i, 0))

    model.decay_suspect_key("w")
    assert len(model.live_obs["w"]) == 3
    assert model.live_obs["w"][0][0] == 204

    model.reset_key("w")
    assert model.live_obs["w"] == []
    assert model.get_delta("w") == (100.0, 0.0)


def test_choose_waypoint_targets_dominant_axis_first():
    assert choose_waypoint((39701, 19651), (28400, 21800)) == (28400, 19651)
    assert choose_waypoint((28400, 10000), (29000, 21800)) == (28400, 21800)
    assert choose_waypoint((1000, 1000), (1600, 1500)) == (1600, 1500)


def test_online_nav_model_choose_steps_with_waypoint_uses_axis_target_when_enabled():
    model = OnlineNavModel(calibrated_model())
    direct = model.choose_steps((39701, 19651), (28400, 21800), max_hold=0.45)
    waypoint = model.choose_steps_with_waypoint((39701, 19651), (28400, 21800), use_waypoint=True, max_hold=0.45)

    assert choose_waypoint((39701, 19651), (28400, 21800)) == (28400, 19651)
    assert waypoint[0][0] == {"s"}
    assert direct


def test_online_nav_model_ignores_near_zero_and_caps_observation_history():
    model = OnlineNavModel(calibrated_model())

    model.record_step("w", (0, 0), (5, 0))
    for i in range(10):
        model.record_step("w", (0, 0), (100 + i, 0))

    assert len(model.live_obs["w"]) == 8
    assert model.live_obs["w"][0][0] == 102
