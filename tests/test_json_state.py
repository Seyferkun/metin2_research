import json

import lzokay

from metin2_research.client_state.json_state import JsonClientStateSource, parse_json_state
from scripts.patch_mt2_root_state_logger import crypt_payload, iter_chunks, patch_loose_game, patch_pack, patch_source
from scripts.probe_client_state import build_probe_state



def test_parse_json_state_maps_player_target_entities_and_optional_lists():
    state = parse_json_state(
        {
            "timestamp_ms": 12345,
            "map": "metin2_map_a1",
            "player": {
                "name": "Yoshypt",
                "x": 100,
                "y": 200,
                "z": 3,
                "hp": 111,
                "max_hp": 222,
                "sp": 33,
                "max_sp": 44,
                "is_dead": False,
            },
            "target": {"vid": 777, "name": "Metin da Batalha", "alive": True, "type": 2, "hp": 50, "max_hp": 100, "hp_pct": 50.0, "pixel_position": [321.5, 222.25], "project_position": [10, 20, 30], "race_num": 8001, "alive_source": "chr.HasInstance"},
            "nearby_entities": [{"vid": 777, "name": "Metin da Batalha", "distance": 120}],
            "buffs": [{"id": 4, "remaining_ms": 1200}],
            "skills": [{"slot": 1, "cooldown_remaining_ms": 0}],
            "quickslots": [{"slot": 9, "item_vnum": 27001, "count": 47}],
            "_api_probe": {"GetNearInstanceList": True, "IsAffect": False},
            "entity_probe": "fail",
            "chr_probe": "HasInstance;GetInstanceType;",
        }
    )

    assert state.game is not None
    assert state.game.map_name == "metin2_map_a1"
    assert state.game.player_coord == [100, 200, 3]
    assert state.game.hp == 111
    assert state.game.max_hp == 222
    assert state.game.sp == 33
    assert state.game.max_sp == 44
    assert state.game.player_name == "Yoshypt"
    assert state.game.target_vid == 777
    assert state.game.target_name == "Metin da Batalha"
    assert state.game.target_alive is True
    assert state.game.target_type == 2
    assert state.game.target_hp == 50
    assert state.game.target_max_hp == 100
    assert state.game.target_hp_percent == 50.0
    assert state.game.target_pixel_position == [321.5, 222.25]
    assert state.game.target_project_position == [10.0, 20.0, 30.0]
    assert state.game.target_race_num == 8001
    assert state.game.target_liveness_source == "chr.HasInstance"
    assert state.game.nearby_entities == [{"vid": 777, "name": "Metin da Batalha", "distance": 120}]
    assert state.game.buffs == [{"id": 4, "remaining_ms": 1200}]
    assert state.game.skills == [{"slot": 1, "cooldown_remaining_ms": 0}]
    assert state.game.quickslots == [{"slot": 9, "item_vnum": 27001, "count": 47}]
    assert state.game.api_probe == {
        "GetNearInstanceList": True,
        "IsAffect": False,
        "entity_probe": "fail",
        "chr_probe": ["HasInstance", "GetInstanceType"],
    }
    assert state.sources["client_python_json"] == "local_read_only_client_python_json_state_logger"


def test_json_client_state_source_reads_atomic_json_file(tmp_path):
    path = tmp_path / "hermes_state.json"
    path.write_text(json.dumps({"timestamp_ms": 1, "map": "m", "player": {"x": 1, "y": 2, "z": 3}}), encoding="utf-8")

    state = JsonClientStateSource(path, max_age_seconds=0, now=lambda: path.stat().st_mtime + 1).read()

    assert state.game.map_name == "m"
    assert state.game.player_coord == [1, 2, 3]
    assert any("stale" in warning.lower() for warning in state.warnings)


def test_build_probe_state_prefers_json_state_over_tsv_fallback(tmp_path):
    json_path = tmp_path / "hermes_state.json"
    tsv_path = tmp_path / "hermes_state.tsv"
    json_path.write_text(json.dumps({"timestamp_ms": 2, "map": "json_map", "player": {"x": 9, "y": 8, "z": 7}}), encoding="utf-8")
    tsv_path.write_text("1\ttsv_map\t1\t2\t3\t10\t20\t30\t40\t0\tYoshypt\t\n", encoding="utf-8")

    state = build_probe_state(include_process_probe=False, json_state_path=json_path, tsv_state_path=tsv_path)

    assert state.game.map_name == "json_map"
    assert state.game.player_coord == [9, 8, 7]
    assert "client_python_json" in state.sources
    assert "client_python_tsv" not in state.sources


def test_patch_source_replaces_existing_old_logger_block():
    src = (
        b"\tdef OnUpdate(self):\r\n\t\tapp.UpdateGame()\r\n"
        b"\t\t# HERMES_STATE_LOGGER_BEGIN - old\r\n"
        b"\t\told_logger = True\r\n"
        b"\t\t# HERMES_STATE_LOGGER_END\r\n"
        b"\t\tself.after = 1\r\n"
    )

    patched = patch_source(src)

    assert b"old_logger" not in patched
    assert b"hermes_state.json" in patched
    assert b"self.after = 1" in patched


def test_patch_source_removes_legacy_unmarked_tsv_body_after_marker_replacement():
    src = (
        b"\tdef OnUpdate(self):\r\n\t\tapp.UpdateGame()\r\n"
        b"\t\t# HERMES_STATE_LOGGER_BEGIN - old\r\n"
        b"\t\t# HERMES_STATE_LOGGER_END\r\n"
        b"\t\ttry:\r\n\t\t\tnow = app.GetGlobalTime()\r\n"
        b"\t\t\tif not hasattr(self, \"_hermes_state_next\"):\r\n"
        b"\t\t\t\tself._hermes_state_next = 0\r\n"
        b"\t\t\tif now >= self._hermes_state_next:\r\n"
        b"\t\t\t\tself._hermes_state_next = now + 500\r\n"
        b"\t\t\t\tx, y, z = player.GetMainCharacterPosition()\r\n"
        b"\t\t\tf = old_open(\"hermes_state.tsv\", \"a\")\r\n"
        b"\t\texcept:\r\n\t\t\tpass\r\n"
        b"\t\tif self.mapNameShower.IsShow():\r\n\t\t\tself.mapNameShower.Update()\r\n"
    )

    patched = patch_source(src)

    assert b"self._hermes_state_next = now + 500" not in patched
    assert patched.count(b"hermes_state.tsv") == 1
    assert b"if self.mapNameShower.IsShow()" in patched


def test_updated_pack_logger_injects_json_export_and_keeps_tsv_compat():
    src = b"\tdef OnUpdate(self):\r\n\t\tapp.UpdateGame()\r\n\t\tself.after = 1\r\n"

    patched = patch_source(src)

    assert b"HERMES_STATE_LOGGER_BEGIN" in patched
    assert b"hermes_state.json" in patched
    assert b"hermes_state.tsv" in patched
    assert b"nearby_entities" in patched
    assert b"entity_probe" in patched
    assert b"chr_probe" in patched
    assert b"dir(chr)" in patched
    assert b"GetNearInstanceList" in patched
    assert b"GetNameByVID" in patched
    assert b"HasInstance" in patched
    assert b"GetInstanceType" in patched
    assert b"GetPixelPosition" in patched
    assert b"GetProjectPosition(vid)" in patched
    assert b"GetProjectPosition()" in patched
    assert b"targetBoard.GetTargetVID()" in patched
    assert b"GetRaceNumByVID" in patched
    assert patched.count(b"pixel_position") >= 2
    assert b"chr.SelectInstance(nv)" in patched
    assert b"alive_source" in patched

    assert b"project_position" in patched
    assert b"race_num" in patched
    assert b"GetTargetHPPercent" not in patched
    assert b"import json" not in patched
    assert b"hermes_api_probe.json" not in patched
    assert b"_api_probe" not in patched
    assert b"old_open(\"hermes_state.json\",\"w\")" in patched
    assert b"app.UpdateGame()\r\n\t\t# HERMES_STATE_LOGGER_BEGIN" in patched


def test_patch_source_handles_onupdate_prelude_before_updategame():
    src = b"\tdef OnUpdate(self):\r\n\t\tself.__RefreshScreenSize()\r\n\t\tapp.UpdateGame()\r\n\t\tself.after = 1\r\n"

    patched = patch_source(src)

    assert b"self.__RefreshScreenSize()\r\n\t\tapp.UpdateGame()\r\n\t\t# HERMES_STATE_LOGGER_BEGIN" in patched
    assert b"hermes_state.json" in patched


def test_updated_logger_includes_target_coordinate_hp_diagnostics():
    from scripts.patch_mt2_root_state_logger import LOGGER_BLOCK

    required = [
        "player_target_vid",
        "target_board_vid",
        "target_board_available",
        "target_board_error",
        "target_pixel_position_error",
        "target_project_position_error",
        "target_hp_now",
        "target_hp_max",
        "target_hp_pct",
        "_hermes_target_hp_vid",
    ]
    for needle in required:
        assert needle in LOGGER_BLOCK


def test_patch_source_caches_target_board_hp_updates():
    src = (
        b"\tdef SetHPTargetBoard(self, vid, hpNow, hpMax):\r\n"
        b"\t\tif vid != self.targetBoard.GetTargetVID():\r\n"
        b"\t\t\tself.targetBoard.ResetTargetBoard()\r\n"
        b"\t\t\tself.targetBoard.SetEnemyVID(vid)\r\n"
        b"\t\tself.targetBoard.SetHP(hpNow, hpMax)\r\n"
        b"\t\tself.targetBoard.Show()\r\n"
        b"\tdef OnUpdate(self):\r\n\t\tapp.UpdateGame()\r\n"
    )

    patched = patch_source(src)

    assert b"self._hermes_target_hp_vid=vid" in patched
    assert b"self._hermes_target_hp_now=hpNow" in patched
    assert b"self._hermes_target_hp_max=hpMax" in patched
    assert patched.count(b"self._hermes_target_hp_vid=vid") == 1


def test_parse_json_state_accepts_chr_probe_list_without_splitting():
    state = parse_json_state({"timestamp_ms": 1, "chr_probe": ["HasInstance", "GetInstanceType"]})
    assert state.game.api_probe["chr_probe"] == ["HasInstance", "GetInstanceType"]


def test_patch_loose_game_writes_backup_and_logger(tmp_path):
    game_py = tmp_path / "game.py"
    backup_dir = tmp_path / "backups"
    game_py.write_bytes(b"\tdef OnUpdate(self):\r\n\t\tapp.UpdateGame()\r\n\t\tself.after = 1\r\n")

    backup = patch_loose_game(game_py, backup_dir=backup_dir)

    assert backup is not None
    assert backup.exists()
    assert b"app.UpdateGame()" in backup.read_bytes()
    patched = game_py.read_bytes()
    assert b"HERMES_STATE_LOGGER_BEGIN" in patched
    assert b"hermes_state.json" in patched


def _pack_chunk(src: bytes, pad_to: int = 512) -> bytes:
    comp = lzokay.compress(src)
    enc_size = ((4 + len(comp) + pad_to + 7) // 8) * 8
    inner = b"MCOZ" + comp + (b"\x00" * (enc_size - 4 - len(comp)))
    enc = crypt_payload(inner, encrypt=True)
    return b"MCOZ" + enc_size.to_bytes(4, "little") + len(comp).to_bytes(4, "little") + len(src).to_bytes(4, "little") + enc


def test_patch_pack_finds_game_chunk_by_content_not_fixed_index(tmp_path):
    dummy = _pack_chunk(b"class NotGame:\r\n\tpass\r\n")
    game_src = b"class GameWindow:\r\n\tdef OnUpdate(self):\r\n\t\tapp.UpdateGame()\r\n\t\tself.after = 1\r\n"
    game = _pack_chunk(game_src, pad_to=4096)
    pack = tmp_path / "root"
    pack.write_bytes(dummy + game)

    patch_pack(pack, dry_run=False)

    from scripts.patch_mt2_root_state_logger import decode_chunk

    chunks = list(iter_chunks(pack.read_bytes()))
    patched_game = None
    for chunk in chunks:
        try:
            data = decode_chunk(pack.read_bytes(), chunk)
        except Exception:
            continue
        if b"class GameWindow" in data:
            patched_game = data
            break
    assert patched_game is not None
    assert b"HERMES_STATE_LOGGER_BEGIN" in patched_game
    assert b"hermes_state.json" in patched_game

