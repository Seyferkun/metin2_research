import csv
import json
from argparse import Namespace
from pathlib import Path

from scripts.reroll_recorder import attrs_signature, build_reroll_summary, item_candidates_from_state, record_rerolls


def test_reroll_recorder_detects_distinct_item_attr_changes(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [
        {
            "type": "sample",
            "t": 0.0,
            "items": [
                {
                    "slot": 3,
                    "vnum": 2849,
                    "name": "Lança Fénix+9",
                    "attrs": [
                        {"index": 0, "type": 71, "value": 12},
                        {"index": 1, "type": 72, "value": -5},
                    ],
                },
                {
                    "slot": 4,
                    "vnum": 13000,
                    "name": "Escudo de Batalha+0",
                    "attrs": [{"index": 0, "type": 19, "value": 20}],
                },
            ],
        },
        {
            "type": "sample",
            "t": 0.2,
            "items": [
                {
                    "slot": 3,
                    "vnum": 2849,
                    "name": "Lança Fénix+9",
                    "attrs": [
                        {"index": 0, "type": 71, "value": 23},
                        {"index": 1, "type": 22, "value": 20},
                    ],
                },
                {
                    "slot": 4,
                    "vnum": 13000,
                    "name": "Escudo de Batalha+0",
                    "attrs": [{"index": 0, "type": 18, "value": 10}],
                },
            ],
        },
        {"type": "sample", "t": 0.4, "items": []},
    ]
    events.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    summary = build_reroll_summary(events)

    assert summary["samples"] == 3
    assert summary["roll_change_count"] == 4
    assert summary["observed_values_by_attr"]["71"] == [12, 23]
    assert summary["observed_values_by_attr"]["72"] == [-5]
    assert summary["observed_values_by_attr"]["22"] == [20]
    assert summary["observed_values_by_attr"]["19"] == [20]
    assert summary["observed_values_by_attr"]["18"] == [10]
    assert summary["observed_rolls"][2]["slot"] == 3
    assert summary["observed_rolls"][3]["slot"] == 4


def test_reroll_summary_reports_stale_state_samples(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [
        {"type": "sample", "t": 0.0, "state_age_seconds": 12.5, "items": []},
        {"type": "sample", "t": 0.1, "state_age_seconds": 14.0, "items": []},
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    summary = build_reroll_summary(events)

    assert summary["stale_state_samples"] == 2
    assert summary["max_state_age_seconds"] == 14.0
    assert any("stale" in note.casefold() for note in summary["notes"])


def test_reroll_recorder_selects_target_inventory_slot_before_equipped_weapon():
    state = {
        "equipped_weapon": {"slot": 3, "vnum": 2849, "name": "Equipped", "attrs": [{"index": 0, "type": 71, "value": 1}]},
        "inventory": [
            {"slot": 10, "vnum": 999, "name": "Other", "attrs": [{"index": 0, "type": 1, "value": 1000}]},
            {"slot": 12, "vnum": 2849, "name": "Target", "attrs": [{"index": 0, "type": 72, "value": 8}]},
        ],
    }

    candidates = item_candidates_from_state(state, target_slot=12, target_vnum=None)

    assert candidates[0]["name"] == "Target"
    assert attrs_signature(candidates[0]) == ((0, 72, 8),)


def test_record_rerolls_writes_json_csv_artifacts(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({
        "inventory": [{"slot": 12, "vnum": 2849, "name": "Target", "attrs": [{"index": 0, "type": 71, "value": 12}]}]
    }), encoding="utf-8")
    args = Namespace(
        duration=0.01,
        interval=0.01,
        state_json=str(state_path),
        target_slot=12,
        target_vnum=None,
        out_dir=str(tmp_path / "runs"),
        run_id="unit-reroll",
        stop_file=None,
    )

    result = record_rerolls(args)

    out_dir = Path(result["out_dir"])
    assert (out_dir / "events.jsonl").exists()
    assert (out_dir / "summary.json").exists()
    csv_path = out_dir / "observed_rolls.csv"
    assert csv_path.exists()
    csv_rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert csv_rows[0]["attr_type"] == "71"
    assert csv_rows[0]["attr_value"] == "12"
