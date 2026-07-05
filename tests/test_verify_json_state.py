import pytest

from scripts.verify_json_state import summarize_state


def test_summarize_state_reports_entity_buff_and_target_counts():
    assert summarize_state(
        {
            "timestamp_ms": 12,
            "map": "metin2_map_a1",
            "player": {"hp": 10, "max_hp": 20, "x": 1, "y": 2, "z": 3},
            "nearby_entities": [{"vid": 1}, {"vid": 2}],
            "buffs": [{"id": 3}],
            "target": {"vid": 2, "name": "Metin", "alive": True, "type": 2, "hp_pct": 80, "race_num": 8001},
        }
    ) == "[12] map=metin2_map_a1 hp=10/20 pos=(1,2,3) entities=2 buffs=1 target=vid=2 name=Metin alive=True type=2 hp_pct=80 race_num=8001"


def test_summarize_state_tolerates_missing_player_object():
    assert summarize_state({"timestamp_ms": 1, "map": "m"}) == "[1] map=m hp=None/None pos=(None,None) entities=0 buffs=0 target=None"


def test_summarize_state_reports_entity_probe_when_present():
    assert summarize_state({"timestamp_ms": 2, "map": "m", "entity_probe": "fail"}).endswith(" entity_probe=fail")
