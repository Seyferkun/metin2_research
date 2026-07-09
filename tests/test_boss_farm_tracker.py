import json
from argparse import Namespace
from pathlib import Path

from scripts.boss_farm_tracker import compact_state, run_tracker, summarize_events


def test_boss_farm_tracker_compacts_state_and_detects_age(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.boss_farm_tracker.time.time", lambda: 105.0)
    state = {
        "_file_mtime": 100.0,
        "map": "metin2_map_a1",
        "player": {"name": "Yoshy", "x": 1, "y": 2, "hp": 100, "max_hp": 200, "mounted": False},
        "target": {"name": "Boss Foo", "vid": 123},
    }

    out = compact_state(state)

    assert out["state_age_seconds"] == 5.0
    assert out["player"]["name"] == "Yoshy"
    assert out["target"]["name"] == "Boss Foo"


def test_boss_farm_summary_keeps_counts_zero_until_learned_detector(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [
        {"type": "sample", "t": 0.0, "state": {"state_age_seconds": 0.2}, "boss_target_match": True, "loot_count": 31},
        {"type": "sample", "t": 900.0, "state": {"state_age_seconds": 0.3}, "boss_target_match": False, "loot_count": 34},
        {"type": "sample", "t": 1800.0, "state": {"state_age_seconds": 3.0}, "boss_target_match": False, "loot_count": 38},
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    summary = summarize_events(events, spawn_interval_minutes=30, channels=8)

    assert summary["samples"] == 3
    assert summary["expected_spawn_windows_elapsed"] == 1
    assert summary["boss_target_samples"] == 1
    assert summary["boss_kills_confirmed"] == 7
    assert summary["channels_cleared"] == 7
    assert summary["loot_pickups_observed"] == 7
    assert summary["loot_increments"] == [
        {"t": 900.0, "from": 31, "to": 34, "delta": 3},
        {"t": 1800.0, "from": 34, "to": 38, "delta": 4},
    ]
    assert summary["stale_state_samples"] == 1


def test_boss_farm_tracker_writes_artifacts_without_live_input(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "map": "metin2_map_a1",
        "player": {"name": "Yoshy"},
        "target": {"name": "Boss Foo", "vid": 123},
    }), encoding="utf-8")
    args = Namespace(
        duration=0.05,
        interval=0.01,
        state_json=str(state_path),
        boss_name="Boss",
        spawn_interval_minutes=30,
        channels=8,
        wait_menu="alterar personagem",
        loot_name="Cofre do Chefe Orc",
        loot_vnum="50070",
        out_dir=str(tmp_path / "runs"),
        run_id="unit-boss-farm",
        stop_file=None,
    )

    result = run_tracker(args)

    out_dir = Path(result["out_dir"])
    assert (out_dir / "events.jsonl").exists()
    assert (out_dir / "summary.json").exists()
    assert result["boss_kills_confirmed"] == 0
    text = (out_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "observation_only" in text
    assert "WAITING_ALTERAR_PERSONAGEM_MENU" in text
