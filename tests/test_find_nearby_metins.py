import csv
import json
from pathlib import Path

from scripts.find_nearby_metins import (
    LiveMetinHit,
    LiveMetinNameHit,
    VisualMetinHit,
    apply_display_offset,
    capture_window_screenshot,
    enrich_named_probe_rows_with_table_coords,
    find_nearby_metins,
    load_player_state,
    load_learned_spawn_rows,
    main as find_nearby_main,
    matching_learned_spawn_rows,
    memory_hits_to_rows,
    memory_name_hits_to_rows,
    record_learned_spawns,
    named_probe_to_rows,
    target_to_rows,
    visual_hits_to_rows,
    write_results,
)


def test_apply_display_offset_adds_user_reported_coordinate_projection():
    assert apply_display_offset({"x": 848, "y": 662}, 31, 15) == {"x": 879, "y": 677, "offset_x": 31, "offset_y": 15}


def test_find_nearby_metins_includes_display_offset_for_player_and_metins(tmp_path):
    coords = tmp_path / "metins.csv"
    coords.write_text(
        "id,map_name,metin_name,x,y,source_type,source_path,evidence,confidence,notes\n"
        "near,Yongan,Metin da Batalha,848,662,seed,seed,,0.98,\n",
        encoding="utf-8",
    )
    state = {"map": "metin2_map_a1", "player": {"x": 84788.0, "y": 66855.0, "hp": 1820, "max_hp": 1820}}

    result = find_nearby_metins(state, coords, radius=50, limit=5, display_offset_x=31, display_offset_y=15)

    assert result["player"]["coord"] == {"x": 848, "y": 669, "raw_x": 84788.0, "raw_y": 66855.0}
    assert result["player"]["display_coord"] == {"x": 879, "y": 684, "offset_x": 31, "offset_y": 15}
    assert result["metins"][0]["location"] == {"x": 848, "y": 662}
    assert result["metins"][0]["display_location"] == {"x": 879, "y": 677, "offset_x": 31, "offset_y": 15}


def test_find_nearby_metins_ranks_known_locations_around_player(tmp_path):
    coords = tmp_path / "metins.csv"
    coords.write_text(
        "id,map_name,metin_name,x,y,source_type,source_path,evidence,confidence,notes\n"
        "near,Yongan,Metin da Batalha,848,662,seed,seed,,0.98,\n"
        "far,Yongan,Metin do Combate,113,463,seed,seed,,0.98,\n"
        "other,OtherMap,Metin Negra,810,523,seed,seed,,0.98,\n",
        encoding="utf-8",
    )
    state = {
        "map": "metin2_map_a1",
        "player": {"x": 81043.0, "y": 52342.0, "z": 20346.0, "hp": 1820, "max_hp": 1820},
    }

    result = find_nearby_metins(state, coords, radius=300, limit=5)

    assert result["player"]["coord"] == {"x": 810, "y": 523, "raw_x": 81043.0, "raw_y": 52342.0}
    assert result["player"]["map_name"] == "Yongan"
    assert [m["id"] for m in result["metins"]] == ["near"]
    assert result["metins"][0]["location"] == {"x": 848, "y": 662}
    assert result["metins"][0]["distance"] < 150


def test_target_to_rows_uses_selected_metin_as_top_live_evidence():
    state = {
        "map": "metin2_map_a1",
        "player": {"x": 92880.0, "y": 84352.0},
        "target": {"vid": 12345, "name": "Metin da Batalha", "alive": True},
    }

    rows = target_to_rows(state)

    assert rows == [
        {
            "id": "target_yongan_12345",
            "map_name": "Yongan",
            "metin_name": "Metin da Batalha",
            "x": 929,
            "y": 844,
            "confidence": 1.0,
            "source_type": "target_selected",
            "source_path": "hermes_state.json target.vid=12345",
            "evidence": "selected target vid=12345 alive=True",
            "notes": "The client logger reports this Metin as the currently selected target; coordinate is the current player position estimate.",
            "vid": 12345,
            "alive": True,
        }
    ]


def test_target_to_rows_ignores_non_metin_targets():
    state = {
        "map": "metin2_map_a1",
        "player": {"x": 92880.0, "y": 84352.0},
        "target": {"vid": 99, "name": "Lobo Cinza", "alive": True},
    }

    assert target_to_rows(state) == []


def test_find_nearby_metins_filters_stale_memory_rows_by_radius(tmp_path):
    state = {"map": "metin2_map_a1", "player": {"x": 92880.0, "y": 84352.0}}
    rows = memory_hits_to_rows(
        [
            LiveMetinHit(address=0x1111, metin_name="Metin da Dor", x=929, y=844, raw="near"),
            LiveMetinHit(address=0x2222, metin_name="Metin da Dor", x=879, y=677, raw="stale"),
        ],
        map_name="Yongan",
    )

    result = find_nearby_metins(state, tmp_path / "unused.csv", radius=80, rows=rows)

    assert [m["location"] for m in result["metins"]] == [{"x": 929, "y": 844}]
    assert result["metins"][0]["evidence_rank"] == 2
    assert result["metins"][0]["evidence_label"] == "Live memory label (exact coordinate)"


def test_find_nearby_metins_sorts_by_evidence_rank_before_distance(tmp_path):
    state = {"map": "metin2_map_a1", "player": {"x": 90000.0, "y": 90000.0}}
    rows = [
        {
            "id": "visual",
            "map_name": "Yongan",
            "metin_name": "Metin stone (visual)",
            "x": 900,
            "y": 900,
            "confidence": 0.4,
            "source_type": "visual_detector_current_position_estimate",
            "source_path": "visual",
            "evidence": "visual",
            "notes": "",
        },
        {
            "id": "target",
            "map_name": "Yongan",
            "metin_name": "Metin da Batalha",
            "x": 910,
            "y": 900,
            "confidence": 1.0,
            "source_type": "target_selected",
            "source_path": "target",
            "evidence": "target",
            "notes": "",
            "vid": 1,
            "alive": True,
        },
    ]

    result = find_nearby_metins(state, tmp_path / "unused.csv", radius=50, rows=rows)

    assert result["metins"] == []
    assert [m["id"] for m in result["live_indicators"]] == ["visual", "target"]
    assert [m["evidence_rank"] for m in result["live_indicators"]] == [4, 1]


def test_find_nearby_metins_preserves_scan_diagnostics_when_rows_filter_out(tmp_path):
    state = {"map": "metin2_map_a1", "player": {"x": 92880.0, "y": 84352.0}}
    rows = memory_hits_to_rows(
        [LiveMetinHit(address=0x2222, metin_name="Metin da Dor", x=879, y=677, raw="stale")],
        map_name="Yongan",
    )

    result = find_nearby_metins(
        state,
        tmp_path / "unused.csv",
        radius=80,
        rows=rows,
        diagnostics={"memory_raw_hits": 1, "live_candidate_rows": 1},
    )

    assert result["count"] == 0
    assert result["diagnostics"] == {
        "memory_raw_hits": 1,
        "live_candidate_rows": 1,
        "candidate_rows_before_radius_filter": 1,
        "rows_after_radius_filter": 0,
    }


def test_named_probe_to_rows_uses_live_vid_with_current_position_estimate():
    state = {
        "map": "metin2_map_a1",
        "player": {"x": 62784.0, "y": 61534.0},
        "named_metin_probe": [{"name": "Metin da Batalha", "vid": 456, "alive": True, "type": 6}],
    }

    rows = named_probe_to_rows(state)

    assert rows == [
        {
            "id": "named_probe_yongan_456",
            "map_name": "Yongan",
            "metin_name": "Metin da Batalha",
            "x": 628,
            "y": 615,
            "confidence": 0.92,
            "source_type": "named_metin_probe",
            "source_path": "hermes_state.json named_metin_probe vid=456",
            "evidence": "GetVIDByName resolved vid=456 alive=True type=6",
            "notes": "The client logger resolved this known Metin name to a live VID; coordinate is the current player position estimate.",
            "vid": 456,
            "alive": True,
        }
    ]


def test_enrich_named_probe_rows_with_table_coords_adds_nearest_live_confirmed_table_destination():
    state = {"map": "metin2_map_a1", "player": {"x": 45300.0, "y": 62300.0}}
    named_rows = [
        {
            "id": "named_probe_yongan_3846",
            "map_name": "Yongan",
            "metin_name": "Metin da Batalha",
            "x": 541,
            "y": 219,
            "confidence": 0.92,
            "source_type": "named_metin_probe",
            "source_path": "hermes_state.json named_metin_probe vid=3846",
            "evidence": "GetVIDByName resolved vid=3846 alive=True type=2",
            "notes": "current-position estimate",
            "vid": 3846,
            "alive": True,
        }
    ]
    table_rows = [
        {"id": "battle_far", "map_name": "Yongan", "metin_name": "Metin da Batalha", "x": 848, "y": 662, "confidence": 0.98, "source_type": "memory_text_anchor_plus_user_confirmation", "source_path": "table", "evidence": "far", "notes": "far"},
        {"id": "battle_near", "map_name": "Yongan", "metin_name": "Metin da Batalha", "x": 453, "y": 623, "confidence": 0.96, "source_type": "live_memory_patrol", "source_path": "table", "evidence": "near", "notes": "near"},
    ]

    enriched = enrich_named_probe_rows_with_table_coords(named_rows, table_rows, state, radius=300)

    assert len(enriched) == 1
    assert enriched[0]["id"] == "named_table_yongan_3846_battle_near"
    assert enriched[0]["source_type"] == "table"
    assert enriched[0]["location_source_type"] == "live_memory_patrol"
    assert enriched[0]["x"] == 453
    assert enriched[0]["y"] == 623
    assert enriched[0]["vid"] == 3846
    assert enriched[0]["alive"] is True
    assert "Named probe confirmed alive" in enriched[0]["notes"]


def test_named_probe_to_rows_ignores_non_positive_vids():
    state = {
        "map": "metin2_map_a1",
        "player": {"x": 62784.0, "y": 61534.0},
        "named_metin_probe": [{"name": "Metin da Batalha", "vid": -1, "alive": False}],
    }

    assert named_probe_to_rows(state) == []


def test_find_nearby_metins_can_return_named_probe_and_table_enriched_destination(tmp_path):
    state = {"map": "metin2_map_a1", "player": {"x": 45300.0, "y": 62300.0}}
    named_rows = named_probe_to_rows(state | {"named_metin_probe": [{"name": "Metin da Batalha", "vid": 3846, "alive": True, "type": 2}]})
    table_rows = [
        {"id": "battle_near", "map_name": "Yongan", "metin_name": "Metin da Batalha", "x": 453, "y": 623, "confidence": 0.96, "source_type": "live_memory_patrol", "source_path": "table", "evidence": "near", "notes": "near"},
    ]
    rows = named_rows + enrich_named_probe_rows_with_table_coords(named_rows, table_rows, state, radius=300)

    result = find_nearby_metins(state, tmp_path / "unused.csv", radius=300, rows=rows)

    assert [m["source_type"] for m in result["metins"]] == ["table"]
    assert result["live_indicators"][0]["source_type"] == "named_metin_probe"
    table = result["metins"][0]
    assert table["location"] == {"x": 453, "y": 623}
    assert table["vid"] == 3846
    assert table["evidence_label"] == "Known coordinate table (not live proof)"


def test_find_nearby_metins_ranks_named_probe_below_memory_above_visual(tmp_path):
    state = {"map": "metin2_map_a1", "player": {"x": 90000.0, "y": 90000.0}}
    rows = [
        {
            "id": "visual",
            "map_name": "Yongan",
            "metin_name": "Metin stone (visual)",
            "x": 900,
            "y": 900,
            "confidence": 0.4,
            "source_type": "visual_detector_current_position_estimate",
            "source_path": "visual",
            "evidence": "visual",
            "notes": "",
        },
        {
            "id": "named",
            "map_name": "Yongan",
            "metin_name": "Metin do Combate",
            "x": 900,
            "y": 900,
            "confidence": 0.92,
            "source_type": "named_metin_probe",
            "source_path": "named",
            "evidence": "named",
            "notes": "",
            "vid": 2,
            "alive": True,
        },
        {
            "id": "memory",
            "map_name": "Yongan",
            "metin_name": "Metin da Batalha",
            "x": 900,
            "y": 901,
            "confidence": 0.99,
            "source_type": "live_memory_visible_text",
            "source_path": "memory",
            "evidence": "memory",
            "notes": "",
        },
    ]

    result = find_nearby_metins(state, tmp_path / "unused.csv", radius=50, rows=rows)

    assert [m["id"] for m in result["metins"]] == ["memory"]
    assert [m["evidence_rank"] for m in result["metins"]] == [2]
    assert [m["id"] for m in result["live_indicators"]] == ["visual", "named"]


def test_find_nearby_metins_suppresses_same_name_named_probe_when_exact_memory_exists(tmp_path):
    state = {"map": "metin2_map_a1", "player": {"x": 56237.0, "y": 73964.0}}
    rows = [
        {
            "id": "memory",
            "map_name": "Yongan",
            "metin_name": "Metin da Batalha",
            "x": 573,
            "y": 733,
            "confidence": 0.99,
            "source_type": "live_memory_visible_text",
            "source_path": "memory",
            "evidence": "Metin da Batalha(573,733)",
            "notes": "exact memory coord",
        },
        {
            "id": "named",
            "map_name": "Yongan",
            "metin_name": "Metin da Batalha",
            "x": 562,
            "y": 740,
            "confidence": 0.92,
            "source_type": "named_metin_probe",
            "source_path": "named vid=122907",
            "evidence": "GetVIDByName resolved vid=122907",
            "notes": "current player position estimate",
            "vid": 122907,
            "alive": True,
        },
    ]

    result = find_nearby_metins(state, tmp_path / "unused.csv", radius=300, rows=rows, diagnostics={"named_probe_rows": 1, "live_memory_rows": 1})

    assert result["count"] == 1
    assert result["metins"][0]["id"] == "memory"
    assert result["metins"][0]["location"] == {"x": 573, "y": 733}
    assert result["diagnostics"]["rows_after_radius_filter"] == 1
    assert result["diagnostics"]["suppressed_position_estimate_rows"] == 1


def test_find_nearby_metins_suppresses_named_probe_when_selected_target_has_same_vid(tmp_path):
    state = {"map": "metin2_map_a1", "player": {"x": 68200.0, "y": 72000.0}}
    rows = [
        {
            "id": "target",
            "map_name": "Yongan",
            "metin_name": "Metin da Batalha",
            "x": 682,
            "y": 720,
            "confidence": 1.0,
            "source_type": "target_selected",
            "source_path": "hermes_state.json target",
            "evidence": "target vid=3831",
            "notes": "selected target estimate",
            "vid": 3831,
            "alive": True,
        },
        {
            "id": "named",
            "map_name": "Yongan",
            "metin_name": "Metin da Batalha",
            "x": 682,
            "y": 720,
            "confidence": 0.92,
            "source_type": "named_metin_probe",
            "source_path": "named vid=3831",
            "evidence": "GetVIDByName resolved vid=3831",
            "notes": "current player position estimate",
            "vid": 3831,
            "alive": True,
        },
    ]

    result = find_nearby_metins(state, tmp_path / "unused.csv", radius=300, rows=rows, diagnostics={"target_rows": 1, "named_probe_rows": 1})

    assert result["count"] == 0
    assert result["metins"] == []
    assert [item["id"] for item in result["live_indicators"]] == ["target", "named"]
    assert result["diagnostics"]["suppressed_position_estimate_rows"] == 2


def test_memory_hits_to_rows_preserve_live_names_and_coords():
    hits = [
        LiveMetinHit(address=0x1234, metin_name="Metin do Combate", x=801, y=536, raw="Metin do Combate(801, 536)"),
        LiveMetinHit(address=0x4567, metin_name="Metin da Batalha", x=900, y=540, raw="Metin da Batalha(900, 540)"),
    ]

    rows = memory_hits_to_rows(hits, map_name="Yongan")

    assert [row["metin_name"] for row in rows] == ["Metin do Combate", "Metin da Batalha"]
    assert rows[0]["x"] == 801
    assert rows[0]["y"] == 536
    assert rows[0]["source_type"] == "live_memory_visible_text"
    assert "0x1234" in rows[0]["source_path"]


def test_learned_spawn_memory_records_exact_live_memory_rows(tmp_path):
    path = tmp_path / "learned_spawns.json"
    rows = memory_hits_to_rows(
        [LiveMetinHit(address=0x1234, metin_name="Metin da Batalha", x=542, y=219, raw="Metin da Batalha(542, 219)")],
        map_name="Yongan",
    )

    written = record_learned_spawns(rows, path=path, now=100.0)
    record_learned_spawns(rows, path=path, now=130.0)
    loaded = load_learned_spawn_rows(path)

    assert written[0]["id"] == "learned_yongan_metin_da_batalha_542_219"
    assert len(loaded) == 1
    assert loaded[0]["metin_name"] == "Metin da Batalha"
    assert loaded[0]["x"] == 542
    assert loaded[0]["y"] == 219
    assert loaded[0]["observations"] == 2
    assert loaded[0]["source_type"] == "learned_live_memory"
    assert loaded[0]["destroyed"] is False
    assert "not destroyed proof" in loaded[0]["notes"]


def test_marked_destroyed_learned_spawn_is_historic(tmp_path):
    path = tmp_path / "learned_spawns.json"
    path.write_text(
        json.dumps(
            {
                "spawns": [
                    {
                        "id": "learned_yongan_metin_da_batalha_542_219",
                        "map_name": "Yongan",
                        "metin_name": "Metin da Batalha",
                        "x": 542,
                        "y": 219,
                        "confidence": 0.91,
                        "observations": 1,
                        "marked_destroyed": True,
                        "last_destroyed_ts": 123.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = load_learned_spawn_rows(path)

    assert loaded[0]["source_type"] == "destroyed_live_memory"
    assert loaded[0]["destroyed"] is True
    assert "Historic destroyed/marked" in loaded[0]["notes"]


def test_bad_locations_suppress_named_probe_table_destination(tmp_path):
    bad_path = tmp_path / "bad_locations.json"
    bad_path.write_text(
        json.dumps({"locations": [{"map_name": "Yongan", "metin_name": "Metin da Batalha", "x": 284, "y": 218, "reason": "user confirmed no Metin there"}]}),
        encoding="utf-8",
    )
    state = {"map": "metin2_map_a1", "player": {"x": 53722.0, "y": 20766.0}}
    named_rows = [
        {"id": "named_probe_yongan_3846", "map_name": "Yongan", "metin_name": "Metin da Batalha", "x": 537, "y": 208, "source_type": "named_metin_probe", "vid": 3846, "alive": True, "evidence": "GetVIDByName resolved"}
    ]
    table_rows = [
        {"id": "bad", "map_name": "Yongan", "metin_name": "Metin da Batalha", "x": 284, "y": 218, "confidence": 0.96, "source_type": "live_memory_patrol", "source_path": "table", "evidence": "old", "notes": "old"},
    ]

    enriched = enrich_named_probe_rows_with_table_coords(named_rows, table_rows, state, radius=300, bad_locations_path=bad_path)

    assert enriched == []


def test_find_nearby_metins_treats_named_probe_current_position_as_indicator_not_location(tmp_path):
    state = {"map": "metin2_map_a1", "player": {"x": 52504.0, "y": 20680.0}}
    named_rows = named_probe_to_rows(state | {"named_metin_probe": [{"name": "Metin da Batalha", "vid": 3846, "alive": True, "type": 2}]})

    result = find_nearby_metins(state, tmp_path / "unused.csv", radius=300, rows=named_rows)

    assert result["metins"] == []
    assert result["count"] == 0
    assert result["live_indicators"][0]["source_type"] == "named_metin_probe"
    assert result["live_indicators"][0]["location"] == {"x": 525, "y": 207}


def test_matching_learned_spawn_rows_uses_destroyed_or_marked_spawn_without_player_location(tmp_path):
    learned_path = tmp_path / "learned.json"
    learned_path.write_text(
        json.dumps({"spawns": [{"id": "learned_yongan_metin_da_batalha_542_219", "map_name": "Yongan", "metin_name": "Metin da Batalha", "x": 542, "y": 219, "confidence": 0.91, "observations": 1, "marked_destroyed": True}]}),
        encoding="utf-8",
    )
    state = {"map": "metin2_map_a1", "player": {"x": 52504.0, "y": 20680.0}}
    named_rows = named_probe_to_rows(state | {"named_metin_probe": [{"name": "Metin da Batalha", "vid": 3846, "alive": True, "type": 2}]})
    rows = named_rows + matching_learned_spawn_rows(named_rows, path=learned_path)

    result = find_nearby_metins(state, tmp_path / "unused.csv", radius=300, rows=rows)

    assert [m["source_type"] for m in result["metins"]] == ["destroyed_live_memory"]
    assert result["metins"][0]["location"] == {"x": 542, "y": 219}
    assert "Historic destroyed" in result["metins"][0]["evidence_label"]
    assert result["live_indicators"][0]["source_type"] == "named_metin_probe"


def test_matching_learned_spawn_rows_ignores_observed_only_spawns(tmp_path):
    learned_path = tmp_path / "learned.json"
    learned_path.write_text(
        json.dumps({"spawns": [{"id": "learned_yongan_metin_da_batalha_542_219", "map_name": "Yongan", "metin_name": "Metin da Batalha", "x": 542, "y": 219, "confidence": 0.91, "observations": 1}]}),
        encoding="utf-8",
    )
    state = {"map": "metin2_map_a1", "player": {"x": 52504.0, "y": 20680.0}}
    named_rows = named_probe_to_rows(state | {"named_metin_probe": [{"name": "Metin da Batalha", "vid": 3846, "alive": True, "type": 2}]})

    assert matching_learned_spawn_rows(named_rows, path=learned_path) == []


def test_visual_hits_to_rows_use_current_position_when_memory_text_missing():
    state = {"map": "metin2_map_a1", "player": {"x": 87630.0, "y": 68464.0}, "target": None}
    rows = visual_hits_to_rows([VisualMetinHit(confidence=0.35, screen_x=183.0, screen_y=828.0, width=42.0, height=55.0)], state)

    assert rows[0]["metin_name"] == "Metin stone (visual)"
    assert rows[0]["x"] == 876
    assert rows[0]["y"] == 685
    assert rows[0]["source_type"] == "visual_detector_current_position_estimate"
    assert rows[0]["confidence"] == 0.35


def test_memory_name_hits_to_rows_are_live_indicators_not_exact_destinations(tmp_path):
    state = {"map": "map_a2", "player": {"x": 80950.0, "y": 78753.0}}
    rows = memory_name_hits_to_rows(
        [LiveMetinNameHit(address=0x6273858, metin_name="Metin da Sombra", raw="Atormentador Negro ... Metin da Sombra")],
        state,
    )

    result = find_nearby_metins(state, tmp_path / "unused.csv", radius=300, rows=rows, diagnostics={"memory_name_rows": 1})

    assert result["metins"] == []
    assert result["count"] == 0
    assert result["live_indicators"][0]["metin_name"] == "Metin da Sombra"
    assert result["live_indicators"][0]["source_type"] == "live_memory_name_only"
    assert result["live_indicators"][0]["location"] == {"x": 810, "y": 788}
    assert result["diagnostics"]["memory_name_rows"] == 1
    assert result["diagnostics"]["suppressed_position_estimate_rows"] == 1


def test_visual_hits_to_rows_keep_only_best_current_position_estimate():
    state = {"map": "metin2_map_a1", "player": {"x": 92880.0, "y": 84352.0}, "target": None}

    rows = visual_hits_to_rows(
        [
            VisualMetinHit(confidence=0.20, screen_x=100.0, screen_y=100.0, width=40.0, height=50.0),
            VisualMetinHit(confidence=0.45, screen_x=615.0, screen_y=431.0, width=110.0, height=140.0),
            VisualMetinHit(confidence=0.27, screen_x=792.0, screen_y=568.0, width=70.0, height=105.0),
        ],
        state,
    )

    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.45
    assert rows[0]["x"] == 929
    assert rows[0]["y"] == 844


def test_capture_window_screenshot_foregrounds_window_then_restores_previous(tmp_path, monkeypatch):
    calls = []

    class FakeUser32:
        def GetForegroundWindow(self):
            return 111

        def ShowWindow(self, hwnd, command):
            calls.append(("show", hwnd, command))
            return 1

        def SetForegroundWindow(self, hwnd):
            calls.append(("foreground", hwnd))
            return 1

    class FakeImage:
        def save(self, path):
            calls.append(("save", Path(path).name))
            Path(path).write_text("fake image", encoding="utf-8")

    class FakeImageGrab:
        @staticmethod
        def grab(*, bbox):
            calls.append(("grab", bbox))
            return FakeImage()

    monkeypatch.setattr("scripts.find_nearby_metins.time.sleep", lambda _seconds: calls.append(("sleep", _seconds)))
    window = type("Window", (), {"hwnd": 222, "bbox": (1, 2, 3, 4)})()

    capture_window_screenshot(window, tmp_path / "shot.jpg", user32=FakeUser32(), image_grab=FakeImageGrab, foreground=True, restore_foreground=True)

    assert calls == [
        ("show", 222, 9),
        ("foreground", 222),
        ("sleep", 0.35),
        ("grab", (1, 2, 3, 4)),
        ("save", "shot.jpg"),
        ("foreground", 111),
    ]


def test_visual_hits_prefer_target_name_when_selected():
    state = {
        "map": "metin2_map_a1",
        "player": {"x": 87630.0, "y": 68464.0},
        "target": {"name": "Metin do Combate"},
    }
    rows = visual_hits_to_rows([VisualMetinHit(confidence=0.35, screen_x=183.0, screen_y=828.0, width=42.0, height=55.0)], state)

    assert rows[0]["metin_name"] == "Metin do Combate"


def test_visual_hits_ignore_non_metin_target_name():
    state = {
        "map": "metin2_map_a1",
        "player": {"x": 59684.0, "y": 99548.0},
        "target": {"name": "Lobo Alfa Azul Feroz"},
    }

    rows = visual_hits_to_rows([VisualMetinHit(confidence=0.42, screen_x=822.0, screen_y=513.0, width=153.0, height=188.0)], state)

    assert rows[0]["metin_name"] == "Metin stone (visual)"


def test_find_nearby_metins_can_use_live_memory_rows_instead_of_stale_table(tmp_path):
    coords = tmp_path / "metins.csv"
    coords.write_text(
        "id,map_name,metin_name,x,y,source_type,source_path,evidence,confidence,notes\n"
        "stale,Yongan,Metin da Batalha,848,662,old,old,,0.98,\n",
        encoding="utf-8",
    )
    state = {"map": "metin2_map_a1", "player": {"x": 81634.0, "y": 49130.0}}
    live_rows = memory_hits_to_rows(
        [LiveMetinHit(address=0x1234, metin_name="Metin do Combate", x=801, y=536, raw="Metin do Combate(801, 536)")],
        map_name="Yongan",
    )

    result = find_nearby_metins(state, coords, radius=300, limit=5, rows=live_rows)

    assert result["source"] == "live_memory_visible_text"
    assert [m["metin_name"] for m in result["metins"]] == ["Metin do Combate"]
    assert result["metins"][0]["location"] == {"x": 801, "y": 536}


def test_hybrid_with_no_live_or_visual_rows_does_not_fall_back_to_table(tmp_path):
    coords = tmp_path / "metins.csv"
    coords.write_text(
        "id,map_name,metin_name,x,y,source_type,source_path,evidence,confidence,notes\n"
        "near,Yongan,Metin da Batalha,453,623,seed,seed,,0.98,\n",
        encoding="utf-8",
    )
    state = {"map": "metin2_map_a1", "player": {"x": 54000.0, "y": 40900.0}}

    result = find_nearby_metins(
        state,
        coords,
        radius=300,
        rows=[],
        diagnostics={"memory_raw_hits": 0, "visual_raw_hits": 0},
    )

    assert result["count"] == 0
    assert result["metins"] == []
    assert result["source"] == "no_candidate_rows"
    assert result["diagnostics"]["candidate_rows_before_radius_filter"] == 0


def test_load_player_state_accepts_client_json_shape(tmp_path):
    state_path = tmp_path / "hermes_state.json"
    state_path.write_text(json.dumps({"map": "metin2_map_a1", "player": {"x": 81043, "y": 52342}}), encoding="utf-8")

    state = load_player_state(state_path)

    assert state["map"] == "metin2_map_a1"
    assert state["player"]["x"] == 81043


def test_write_results_creates_json_and_csv(tmp_path):
    result = {
        "player": {"map_name": "Yongan", "coord": {"x": 810, "y": 523, "raw_x": 81043.0, "raw_y": 52342.0}},
        "metins": [{"id": "near", "metin_name": "Metin da Batalha", "location": {"x": 848, "y": 662}, "distance": 144.1}],
    }

    paths = write_results(result, tmp_path / "nearby.json", tmp_path / "nearby.csv")

    assert Path(paths["json"]).exists()
    assert Path(paths["csv"]).exists()
    with open(paths["csv"], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["metin_name"] == "Metin da Batalha"
    assert rows[0]["x"] == "848"


def test_main_hybrid_records_memory_scan_error_and_falls_back_without_crashing(tmp_path, monkeypatch):
    state_json = tmp_path / "hermes_state.json"
    state_json.write_text(json.dumps({"map": "metin2_map_a1", "player": {"x": 54000.0, "y": 40900.0}}), encoding="utf-8")
    coords = tmp_path / "metins.csv"
    coords.write_text(
        "id,map_name,metin_name,x,y,source_type,source_path,evidence,confidence,notes\n"
        "near,Yongan,Metin da Batalha,453,623,seed,seed,,0.98,\n",
        encoding="utf-8",
    )
    out_json = tmp_path / "nearby.json"
    out_csv = tmp_path / "nearby.csv"

    monkeypatch.setattr("scripts.find_nearby_metins.scan_live_metin_memory", lambda _process_name: (_ for _ in ()).throw(RuntimeError("OpenProcess failed for pid 123")))
    monkeypatch.setattr("scripts.find_nearby_metins.scan_visual_metins", lambda **_kwargs: [])

    assert find_nearby_main([
        "--state-json", str(state_json),
        "--coords-csv", str(coords),
        "--source", "hybrid",
        "--out-json", str(out_json),
        "--out-csv", str(out_csv),
    ]) == 0
    result = json.loads(out_json.read_text(encoding="utf-8"))
    assert result["metins"] == []
    assert result["diagnostics"]["memory_raw_hits"] == 0
    assert result["diagnostics"]["memory_scan_error"] == "OpenProcess failed for pid 123"
