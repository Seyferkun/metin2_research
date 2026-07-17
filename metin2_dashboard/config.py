from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_BUFF_CONFIG = {
    "use_buff_config": False,
    "active_stat_thresholds": {
        "f1_attack_min_min": 200.0,
        "f2_attack_speed_min": 130.0,
    },
    "buffs": [
        {"key": "f1", "enabled": False, "interval_seconds": 35.0, "pre_cast_seconds": 3.0},
        {"key": "f2", "enabled": False, "interval_seconds": 35.0, "pre_cast_seconds": 3.0},
    ],
}

DEFAULT_COMBAT_CONFIG = {
    "attack_nearby_mobs": False,
}

DEFAULT_REROLL_CONFIG = {
    "equip_slots": [
        {
            "slot": "weapon",
            "label": "Weapon",
            "possible_rolls": [
                {"attr_type": 71, "name": "Dano Médio", "observed_values": [-49, 23], "observed_min": -49, "observed_max": 23, "source": "recording run-a6d380a03695"},
                {"attr_type": 72, "name": "Dano de Habilidade", "observed_values": [-29, 12], "observed_min": -29, "observed_max": 12, "source": "recording run-a6d380a03695"},
                {"attr_type": 15, "name": "Chance de Golpes Críticos", "observed_values": [1, 3, 10], "observed_min": 1, "observed_max": 10},
                {"attr_type": 16, "name": "Chance de Golpes Perfurantes", "observed_values": [5, 10], "observed_min": 5, "observed_max": 10},
                {"attr_type": 17, "name": "Forte contra Semi-Humanos", "observed_values": [5], "observed_min": 5, "observed_max": 5},
                {"attr_type": 18, "name": "Forte contra Animais", "observed_values": [10, 20], "observed_min": 10, "observed_max": 20},
                {"attr_type": 19, "name": "Forte contra Orcs", "observed_values": [6, 10], "observed_min": 6, "observed_max": 10},
                {"attr_type": 20, "name": "Forte contra Esotéricos", "observed_values": [6, 10], "observed_min": 6, "observed_max": 10},
                {"attr_type": 21, "name": "Forte contra Mortos-Vivos", "observed_values": [6, 10, 20], "observed_min": 6, "observed_max": 20},
                {"attr_type": 22, "name": "Forte contra Demónios", "observed_values": [4, 6, 10, 20], "observed_min": 4, "observed_max": 20},
            ],
        },
        {
            "slot": "armor",
            "label": "Armor",
            "possible_rolls": [
                {"attr_type": 1, "name": "PV Máx / Max HP", "observed_values": [1000, 1500, 2000], "observed_min": 1000, "observed_max": 2000, "source": "recording run-501b31b276f2"},
                {"attr_type": 9, "name": "Rapidez de Feitiço", "observed_values": [4, 6, 10, 20], "observed_min": 4, "observed_max": 20, "source": "recording run-501b31b276f2"},
                {"attr_type": 23, "name": "Absorver HP / Roubar HP", "observed_values": [2, 3, 5, 10], "observed_min": 2, "observed_max": 10, "source": "recording run-501b31b276f2"},
                {"attr_type": 24, "name": "Absorver SP / Roubar SP", "observed_values": [3, 5, 10], "observed_min": 3, "observed_max": 10, "source": "recording run-501b31b276f2"},
                {"attr_type": 29, "name": "Defesa contra Espada", "observed_values": [6, 10, 15], "observed_min": 6, "observed_max": 15, "source": "recording run-501b31b276f2"},
                {"attr_type": 30, "name": "Defesa contra Duas Mãos", "observed_values": [6, 10, 15], "observed_min": 6, "observed_max": 15, "source": "recording run-501b31b276f2"},
                {"attr_type": 31, "name": "Defesa contra Punhal", "observed_values": [6, 10, 15], "observed_min": 6, "observed_max": 15, "source": "recording run-501b31b276f2"},
                {"attr_type": 32, "name": "Defesa contra Sino", "observed_values": [6, 10, 15], "observed_min": 6, "observed_max": 15, "source": "recording run-501b31b276f2"},
                {"attr_type": 33, "name": "Defesa contra Leque", "observed_values": [6, 10, 15], "observed_min": 6, "observed_max": 15, "source": "recording run-501b31b276f2"},
                {"attr_type": 34, "name": "Resistência a Flechas", "observed_values": [6, 10, 15], "observed_min": 6, "observed_max": 15, "source": "recording run-501b31b276f2"},
                {"attr_type": 35, "name": "Resistência ao Fogo", "observed_values": [4, 6, 10, 15], "observed_min": 4, "observed_max": 15, "source": "recording run-501b31b276f2"},
                {"attr_type": 36, "name": "Resistência a Relâmpagos", "observed_values": [4, 6, 10, 15], "observed_min": 4, "observed_max": 15, "source": "recording run-501b31b276f2"},
                {"attr_type": 37, "name": "Resistência à Magia", "observed_values": [4, 6, 15], "observed_min": 4, "observed_max": 15, "source": "recording run-501b31b276f2"},
                {"attr_type": 38, "name": "Resistência ao Vento", "observed_values": [4, 6, 10, 15], "observed_min": 4, "observed_max": 15, "source": "recording run-501b31b276f2"},
                {"attr_type": 39, "name": "Refletir ataque corporal", "observed_values": [3, 6, 10], "observed_min": 3, "observed_max": 10, "source": "recording run-501b31b276f2"},
                {"attr_type": 53, "name": "Valor de Ataque", "observed_values": [15], "observed_min": 15, "observed_max": 15, "source": "recording run-501b31b276f2"},
                {"attr_type": 96, "name": "Tipo 96 (custom/unknown)", "observed_values": [6, 10, 15], "observed_min": 6, "observed_max": 15, "source": "recording run-501b31b276f2"},
            ],
        },
        {"slot": "helmet", "label": "Helmet", "possible_rolls": []},
        {"slot": "shield", "label": "Shield", "possible_rolls": []},
        {"slot": "bracelet", "label": "Bracelet", "possible_rolls": []},
        {"slot": "shoes", "label": "Shoes", "possible_rolls": []},
        {"slot": "necklace", "label": "Necklace", "possible_rolls": []},
        {"slot": "earrings", "label": "Earrings", "possible_rolls": []},
    ],
    "desired_stats": {
        "weapon": [{"attr_type": 71, "target_value": 50, "priority": 1}, {"attr_type": 72, "target_value": 20, "priority": 2}],
        "armor": [{"attr_type": 1, "target_value": 2000, "priority": 1}, {"attr_type": 37, "target_value": 15, "priority": 2}],
    },
}


def _config_dir(project_root: str | Path) -> Path:
    return Path(project_root) / "config"


def buff_config_path(project_root: str | Path) -> Path:
    return _config_dir(project_root) / "buffs.json"


def combat_config_path(project_root: str | Path) -> Path:
    return _config_dir(project_root) / "combat.json"


def reroll_config_path(project_root: str | Path) -> Path:
    return _config_dir(project_root) / "reroll.json"

DEFAULT_LOGIN_CONFIG = {
    "active_profile": "main",
    "profiles": {
        "main": {"username": "yoshy", "app_dir": "D:/Games/MT2Portugalia/app"},
        "buffer": {"username": "nienna", "app_dir": "D:/Games/MT2PortugaliaBuffer/app"},
        "farmer": {"username": "seyfer", "app_dir": "D:/Games/MT2PortugaliaFarmer/app"},
    },
}


def login_config_path(project_root: str | Path) -> Path:
    return _config_dir(project_root) / "login.json"


def _credential_status(username: str) -> dict[str, Any]:
    try:
        from scripts.login_mt2_local import credential_exists, credential_target

        target = credential_target(username)
        exists = credential_exists(username)
    except Exception:
        target = f"MT2Portugalia:{str(username).strip()}"
        exists = False
    return {"credential_target": target, "has_password": bool(exists)}


def normalize_login_config(data: dict[str, Any] | None) -> dict[str, Any]:
    raw = data or {}
    profiles = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
    out = {"active_profile": str(raw.get("active_profile") or DEFAULT_LOGIN_CONFIG["active_profile"]), "profiles": {}}
    for profile, defaults in DEFAULT_LOGIN_CONFIG["profiles"].items():
        row = profiles.get(profile) if isinstance(profiles.get(profile), dict) else {}
        username = str(row.get("username") or defaults["username"]).strip()
        app_dir = str(row.get("app_dir") or defaults["app_dir"]).strip()
        safe = {"username": username, "app_dir": app_dir}
        safe.update(_credential_status(username))
        out["profiles"][profile] = safe
    if out["active_profile"] not in out["profiles"]:
        out["active_profile"] = "main"
    return out


def read_login_config(project_root: str | Path) -> dict[str, Any]:
    return normalize_login_config(_read_json(login_config_path(project_root)))


def write_login_config(project_root: str | Path, data: dict[str, Any]) -> dict[str, Any]:
    raw = data or {}
    existing = read_login_config(project_root)
    merged = {"active_profile": raw.get("active_profile", existing.get("active_profile", "main")), "profiles": {}}
    input_profiles = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
    password_updates: set[str] = set()
    for profile, current in existing["profiles"].items():
        row = input_profiles.get(profile) if isinstance(input_profiles.get(profile), dict) else {}
        username = str(row.get("username") or current.get("username") or "").strip()
        app_dir = str(row.get("app_dir") or current.get("app_dir") or "").strip()
        password = str(row.get("password") or "")
        if password:
            from scripts.login_mt2_local import credential_exists, write_credential

            write_credential(username, password)
            if not credential_exists(username):
                raise RuntimeError(f"Credential Manager write did not persist for profile {profile}: MT2Portugalia:{username}")
            password_updates.add(profile)
        merged["profiles"][profile] = {"username": username, "app_dir": app_dir}
    normalized = normalize_login_config(merged)
    for profile in password_updates:
        normalized["profiles"][profile]["password_updated"] = True
    path = login_config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = {"active_profile": normalized["active_profile"], "profiles": {}}
    for profile, row in normalized["profiles"].items():
        stored["profiles"][profile] = {"username": row["username"], "app_dir": row["app_dir"]}
    path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"config root must be an object: {path}")
    return data


def normalize_buff_config(data: dict[str, Any] | None) -> dict[str, Any]:
    raw = data or {}
    out = dict(DEFAULT_BUFF_CONFIG)
    out["active_stat_thresholds"] = dict(DEFAULT_BUFF_CONFIG["active_stat_thresholds"])
    out["use_buff_config"] = bool(raw.get("use_buff_config", out["use_buff_config"]))
    raw_thresholds = raw.get("active_stat_thresholds") if isinstance(raw.get("active_stat_thresholds"), dict) else {}
    out["active_stat_thresholds"] = {
        "f1_attack_min_min": float(raw_thresholds.get("f1_attack_min_min", raw_thresholds.get("f1_attack_power_min", out["active_stat_thresholds"]["f1_attack_min_min"]))),
        "f2_attack_speed_min": float(raw_thresholds.get("f2_attack_speed_min", out["active_stat_thresholds"]["f2_attack_speed_min"])),
    }
    buffs = raw.get("buffs") if isinstance(raw.get("buffs"), list) else out["buffs"]
    normalized = []
    for row in buffs:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").lower().strip()
        if not key:
            continue
        normalized.append(
            {
                "key": key,
                "enabled": bool(row.get("enabled", False)),
                "interval_seconds": float(row.get("interval_seconds", 35.0)),
                "pre_cast_seconds": float(row.get("pre_cast_seconds", 3.0)),
            }
        )
    out["buffs"] = normalized
    return out


def normalize_combat_config(data: dict[str, Any] | None) -> dict[str, Any]:
    raw = data or {}
    out = dict(DEFAULT_COMBAT_CONFIG)
    out["attack_nearby_mobs"] = bool(raw.get("attack_nearby_mobs", False))
    return out


def _normalize_possible_roll(row: dict[str, Any]) -> dict[str, Any]:
    values = row.get("observed_values") if isinstance(row.get("observed_values"), list) else []
    normalized_values = []
    for value in values:
        try:
            normalized_values.append(int(value))
        except Exception:
            continue
    attr_type = int(row.get("attr_type"))
    out = {
        "attr_type": attr_type,
        "name": str(row.get("name") or f"attr {attr_type}"),
        "observed_values": sorted(set(normalized_values)),
        "observed_min": int(row.get("observed_min", min(normalized_values) if normalized_values else 0)),
        "observed_max": int(row.get("observed_max", max(normalized_values) if normalized_values else 0)),
    }
    if row.get("source"):
        out["source"] = str(row["source"])
    return out


def normalize_reroll_config(data: dict[str, Any] | None) -> dict[str, Any]:
    raw = data or {}
    defaults_by_slot = {row["slot"]: row for row in DEFAULT_REROLL_CONFIG["equip_slots"]}
    input_slots = raw.get("equip_slots") if isinstance(raw.get("equip_slots"), list) else []
    input_by_slot = {str(row.get("slot")): row for row in input_slots if isinstance(row, dict) and row.get("slot")}
    equip_slots = []
    for slot, default in defaults_by_slot.items():
        src = input_by_slot.get(slot, default)
        rolls_src = src.get("possible_rolls") if isinstance(src.get("possible_rolls"), list) else default.get("possible_rolls", [])
        rolls = []
        for roll in rolls_src:
            if not isinstance(roll, dict) or roll.get("attr_type") in (None, ""):
                continue
            rolls.append(_normalize_possible_roll(roll))
        equip_slots.append({"slot": slot, "label": str(src.get("label") or default.get("label") or slot.title()), "possible_rolls": rolls})

    desired_raw = raw.get("desired_stats") if isinstance(raw.get("desired_stats"), dict) else DEFAULT_REROLL_CONFIG.get("desired_stats", {})
    desired_stats: dict[str, list[dict[str, int]]] = {}
    for slot in defaults_by_slot:
        rows = desired_raw.get(slot) if isinstance(desired_raw.get(slot), list) else DEFAULT_REROLL_CONFIG.get("desired_stats", {}).get(slot, [])
        normalized_rows = []
        for row in rows:
            if not isinstance(row, dict) or row.get("attr_type") in (None, ""):
                continue
            normalized_rows.append(
                {
                    "attr_type": int(row.get("attr_type")),
                    "target_value": int(row.get("target_value", row.get("value", 0))),
                    "priority": int(row.get("priority", len(normalized_rows) + 1)),
                }
            )
        desired_stats[slot] = sorted(normalized_rows, key=lambda r: r["priority"])
    return {"equip_slots": equip_slots, "desired_stats": desired_stats}


def read_buff_config(project_root: str | Path) -> dict[str, Any]:
    return normalize_buff_config(_read_json(buff_config_path(project_root)))


def read_combat_config(project_root: str | Path) -> dict[str, Any]:
    return normalize_combat_config(_read_json(combat_config_path(project_root)))


def read_reroll_config(project_root: str | Path) -> dict[str, Any]:
    return normalize_reroll_config(_read_json(reroll_config_path(project_root)))


def write_buff_config(project_root: str | Path, data: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_buff_config(data)
    path = buff_config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def write_combat_config(project_root: str | Path, data: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_combat_config(data)
    path = combat_config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def write_reroll_config(project_root: str | Path, data: dict[str, Any]) -> dict[str, Any]:
    existing = read_reroll_config(project_root)
    merged = dict(existing)
    if isinstance(data.get("equip_slots"), list):
        merged["equip_slots"] = data["equip_slots"]
    if isinstance(data.get("desired_stats"), dict):
        desired = dict(existing.get("desired_stats", {}))
        for slot, rows in data["desired_stats"].items():
            if isinstance(rows, list):
                desired[str(slot)] = rows
        merged["desired_stats"] = desired
    normalized = normalize_reroll_config(merged)
    path = reroll_config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized
