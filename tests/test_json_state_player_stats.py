from metin2_research.client_state.json_state import parse_json_state


def test_parse_json_state_preserves_nested_player_stats_for_buff_probe():
    state = parse_json_state(
        {
            "timestamp_ms": 1,
            "map": "metin2_map_a1",
            "player": {
                "name": "Yoshypt",
                "x": 100,
                "y": 200,
                "z": 3,
                "stats": {
                    "attack_power": 219,
                    "attack_speed": 132,
                    "attack_min": 349,
                    "attack_max": 375,
                },
            },
        }
    )

    assert state.game.player_stats["attack_power"] == 219
    assert state.game.player_stats["attack_speed"] == 132


def test_parse_json_state_preserves_top_level_player_stats_fallback():
    state = parse_json_state(
        {
            "timestamp_ms": 1,
            "map": "metin2_map_a1",
            "player": {"x": 100, "y": 200, "z": 3},
            "player_stats": {"attack_power": 219, "attack_speed": 132},
        }
    )

    assert state.game.player_stats == {"attack_power": 219, "attack_speed": 132}
