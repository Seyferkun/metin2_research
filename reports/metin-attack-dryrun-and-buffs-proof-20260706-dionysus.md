# Metin Attack Dry-Run + Buffs Proof — 2026-07-06 — Dionysus

## Scope

Task label: `metin-attack-dryrun-and-buffs-proof-20260706-dionysus`

Project root:

`C:/Hermes Unreal/metin2_research`

Purpose:

1. Prove the dashboard/API-managed combat script can be started in **dry-run attack** mode without sending live attack/movement/click input.
2. Prove F1/F2 buff status using the correct visual area: active buff mini-icons **above** the four blue top-left icons.
3. Record blockers honestly, especially stale client JSON state and any distinction between key-send logs and visual buff proof.

Safety scope:

- Local/private MT2Portugalia/Yoshy client only.
- Dry-run attack path only; no live attack, no live movement, no live mouse-targeting.
- Live buff-only run used only F1/F2 key keepalive and no target/move/attack behavior.
- No server/packet/database/API work.

## Result summary

Attack dry-run proof: **PASS as a safety/blocking proof**

- Managed dry-run was started through `/api/start`.
- It exited `0`.
- It did not attack, move, click, or send live input.
- It correctly blocked on stale client state with `WAIT_FRESH_STATE`.

Buff proof: **RETRACTED as activation proof; log-only key-attempt proof + visual status only**

- Managed live buff-only run logged F1 then F2 key attempts.
- The screenshot after the run showed buff-like mini-icons in the correct top-left area, but that does **not** prove I activated them.
- User correction after the report: "You didnt activate any buffs". Treat this as authoritative for activation causality.
- Correct conclusion: I proved only that the managed run attempted F1/F2 and that icons were visible/status-present at capture time, not that my run caused activation.

Important caveat:

- This does not prove live attack readiness because `/api/state` was stale and `/api/state_bridge` had no selected target evidence at the time of the attack dry-run.
- This does not claim the combat script attacked anything; that is the point of the dry-run proof.
- This does not claim the control panel activated buffs.

## Current state before proof

Baseline screenshot:

`C:/Hermes Unreal/metin2_research/reports/dashboard_runs/attack_dryrun_buffs_baseline_20260706_dionysus.png`

API baseline:

```text
/api/state age_sec ≈ 120.265
/api/state target: null

/api/state_bridge:
  available: false
  trust_level: NONE
  has_trusted_target: false
  dry_run_action: NEED_METIN_TARGET
  live_action: NEED_METIN_TARGET
  reason: no selected target evidence
```

This meant attack/combat targeting was correctly blocked before proof began.

## Attack dry-run execution

Started through dashboard API:

```json
{
  "script": "combat_metin_client_state",
  "live": false,
  "confirm_live": false,
  "options": {
    "max_cycles": "3",
    "attack_nearby_mobs": true,
    "metin_name": "Metin da Alma",
    "buff_keys": "f1,f2",
    "buff_durations": "109,301"
  }
}
```

Run id:

```text
run-ea9a7411a7b4
```

Log path:

`C:/Hermes Unreal/metin2_research/reports/dashboard_runs/run-ea9a7411a7b4-combat_metin_client_state.log`

Command recorded by registry:

```text
C:\Python312\python.exe
C:\Hermes Unreal\metin2_research\scripts\combat_metin_client_state.py
--max-cycles 1
--out reports/dashboard_runs/combat_metin_dryrun.jsonl
--max-cycles 3
--attack-nearby-mobs
--metin-name "Metin da Alma"
--buff-keys f1,f2
--buff-durations 109,301
```

Exit:

```text
exit_code: 0
```

Log evidence:

```json
{"cycle": 1, "state": "WAIT_FRESH_STATE", "command": "pause", "reason": "No fresh JSON client game state: ['Client Python JSON state is stale: age=195.74s path=D:\\\\Games\\\\MT2Portugalia\\\\app\\\\hermes_state.json']", "run_id": "run-ea9a7411a7b4"}
{"cycle": 2, "state": "WAIT_FRESH_STATE", "command": "pause", "reason": "No fresh JSON client game state: ['Client Python JSON state is stale: age=196.24s path=D:\\\\Games\\\\MT2Portugalia\\\\app\\\\hermes_state.json']", "run_id": "run-ea9a7411a7b4"}
{"cycle": 3, "state": "WAIT_FRESH_STATE", "command": "pause", "reason": "No fresh JSON client game state: ['Client Python JSON state is stale: age=196.75s path=D:\\\\Games\\\\MT2Portugalia\\\\app\\\\hermes_state.json']", "run_id": "run-ea9a7411a7b4"}
```

Interpretation:

- The attack dry-run did not reach attack/move/click decisions because client state was stale.
- It correctly chose `command: pause` for all cycles.
- This is a safe dry-run/blocking proof, not an attack-decision proof against a fresh target.

## Attack dry-run automated regression proof

Targeted tests run:

```bash
python -m pytest \
  tests/test_combat_metin_client_state.py::test_combat_script_dry_run_attack_nearby_mobs_logs_decision_without_live_input \
  tests/test_combat_metin_client_state.py::test_buff_only_cli_f1_f2_preset_presses_each_key_once_in_dry_run \
  tests/test_combat_metin_client_state.py::test_buff_only_does_not_require_fresh_client_state \
  tests/test_combat_metin_client_state.py::test_buff_only_mode_logs_idle_and_never_attacks_selected_mob \
  -q
```

Result:

```text
4 passed in 2.04s
```

This specifically backs the dry-run safety claim: the dry-run attack-nearby-mobs path logs intent without live input in tests, and buff-only mode does not attack selected mobs.

## Buff-only live proof execution

Started through dashboard API:

```json
{
  "script": "combat_metin_client_state",
  "live": true,
  "confirm_live": true,
  "options": {
    "buff_only": true,
    "buff_keys": "f1,f2",
    "buff_durations": "109,301",
    "max_cycles": "2"
  }
}
```

Run id:

```text
run-b2c2390cf59a
```

Log path:

`C:/Hermes Unreal/metin2_research/reports/dashboard_runs/run-b2c2390cf59a-combat_metin_client_state.log`

Exit:

```text
exit_code: 0
```

Log evidence:

```json
{"cycle": 1, "dry_run": false, "state": "BUFF_DUE", "command": "press_buff", "reason": "buff-only mode: configured buff due; client-state freshness is not required for key keepalive", "buff": {"key": "f1", "interval_seconds": 109.0, "pre_cast_seconds": 3.0, "elapsed_seconds": null}, "run_id": "run-b2c2390cf59a"}
[state=BUFF_DUE] [action=press_buff] [buff=f1] [dry_run=False]
{"cycle": 2, "dry_run": false, "state": "BUFF_DUE", "command": "press_buff", "reason": "buff-only mode: configured buff due; client-state freshness is not required for key keepalive", "buff": {"key": "f2", "interval_seconds": 301.0, "pre_cast_seconds": 3.0, "elapsed_seconds": null}, "run_id": "run-b2c2390cf59a"}
[state=BUFF_DUE] [action=press_buff] [buff=f2] [dry_run=False]
```

Interpretation:

- F1 and F2 were both attempted by the managed live buff-only run.
- The run was bounded to two cycles and exited successfully.
- Buff-only mode did not target, move, click, or attack.

## Buff visual proof

Final screenshot:

`C:/Hermes Unreal/metin2_research/reports/dashboard_runs/attack_dryrun_buffs_after_buff_live_20260706_dionysus.png`

Visual rule used:

- Count only the mini active-buff icons **above the four blue top-left icons**.
- Ignore icons below the four blue icons.

Visual result:

```text
Two active mini-icons are visible above the four blue top-left icons.
No inventory/equipment/wiki/menu overlay is open in the final screenshot.
```

Classification:

```text
F1/F2 activation by control panel: NOT PROVEN / RETRACTED
F1/F2 key-attempt log: PASS
F1/F2 visual status at screenshot time: STATUS ONLY, not activation causality
```

Caveat:

- The screenshot may show existing/manually activated buffs; it does not prove the run activated them.
- The log proves only that the managed buff-only run attempted F1/F2.
- User correction after report says the buffs were not activated by me/control-panel, so do not present this as activation proof.

## Current API state after proof

Final API check:

```text
/api/state age_sec ≈ 367.669
/api/state target: null

/api/state_bridge:
  available: false
  current_action: IDLE
  trust_level: NONE
  has_trusted_target: false
  dry_run_action: NEED_METIN_TARGET
  live_action: NEED_METIN_TARGET
  reason: no selected target evidence
```

Interpretation:

- Target/combat state is stale and not trusted.
- Buff-only proof is still valid because buff-only explicitly does not require fresh combat state.
- Any real attack/move/targeting follow-up must first restore fresh JSON state and selected-target evidence.

## Pass/fail matrix

```text
Dashboard-managed attack dry-run started: PASS
Attack dry-run exits 0: PASS
Attack dry-run sends no live attack/move/click input: PASS
Attack dry-run reaches fresh-target attack decision: NOT PROVEN; stale state blocked it
Dry-run safety tests: PASS, 4 passed
Dashboard-managed live buff-only run started: PASS
F1 log entry: PASS as key-attempt log only
F2 log entry: PASS as key-attempt log only
Live buff-only exits 0: PASS
Visual F1/F2 status above four blue icons: STATUS ONLY, not activation proof
Control-panel/me activated buffs: NOT PROVEN / USER CORRECTION SAYS NO
Inventory/equipment/wiki overlay closed in final screenshot: PASS
Fresh selected target after proof: FAIL / not present
```

## Recommended next steps

1. If the next task is real combat targeting, first fix/refresh the client state logger:
   - `/api/state` must be fresh, ideally age < 2 seconds.
   - `/api/state.target` must be non-null with selected Metin evidence.
   - `/api/state_bridge.has_trusted_target` should be true.
2. Do not start live attack from the current stale/no-target state.
3. If buff automation remains questionable, run a dedicated F1/F2 elevated-input proof where the buffs are intentionally allowed to expire or are visually absent before pressing, then verify they appear above the four blue icons afterward.
