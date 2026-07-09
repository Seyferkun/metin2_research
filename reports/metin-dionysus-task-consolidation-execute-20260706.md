# Metin Dionysus Task Consolidation Execute — 2026-07-06

## Scope

Task label: `metin-dionysus-task-consolidation-execute-20260706`

Project root:

`C:/Hermes Unreal/metin2_research`

Purpose:

Consolidate the current Metin2 control-panel work into one executable handoff: what changed, what is proven, what remains blocked, and what commands/artifacts should be used next.

## Executive status

Overall status: PASS with one known follow-up caveat.

Completed and verified:

- Metin control panel opens from a desktop launcher and terminal command.
- Native control panel is organized into pages/tabs.
- Log tail no longer destructive-auto-refreshes while the operator reads/copies text.
- F1/F2 keep-buffs path has been repaired at the Windows `SendInput` ABI layer and exposed in the control panel.
- Buff live proof exists and must be interpreted using the correct icon area: ABOVE the four blue top-left icons.
- State bridge trust panel and `/api/state_bridge` are integrated.
- Selected-target acquisition is live-proven after adding `targetBoard.GetTargetVID()` fallback and restarting MT2Portugalia.
- Tests pass.

Known caveat:

- Selected target acquisition is proven, but exact coordinate extraction is not yet proven. `/api/state.target` currently has VID/name/alive/type, but target `pixel_position`, `project_position`, and `hp_pct` are not reliably present in the proof payloads.

## Current live API status

Checked API:

`http://127.0.0.1:8767`

Current `/api/state` summary at consolidation time:

```text
age_sec: 0.143
target: {'vid': 1554842, 'name': 'Metin da Alma', 'alive': True, 'alive_source': 'chr.HasInstance', 'type': 2}
map: metin2_map_n_desert_01
player: Yoshypt
```

Current `/api/state_bridge` summary:

```text
trusted: True
reason: fresh HIGH_EXACT alive Metin target; dry-run still blocks engagement
live_action: ENGAGE_TARGET
```

Current `/api/runs` summary:

```text
runs: 4
running: 0
```

Interpretation:

- The client state feed is fresh.
- The API and state bridge currently see a selected alive Metin target.
- No dashboard-managed script is currently running.

## Implemented changes by area

### 1. Control panel launch

Desktop launcher:

`C:/Users/blade/Desktop/Open Metin2 Control Panel.bat`

Terminal command:

```bash
cd '/c/Hermes Unreal/metin2_research' && python scripts/metin2_control_panel.py
```

### 2. Control panel UI organization

File:

`metin2_dashboard/control_panel.py`

Implemented:

- Multi-page `ttk.Notebook` layout.
- `State + controls` tab.
- `Targets + runs` tab.
- State bridge trust card.
- Buff keeper + mob control group.
- Explicit buff dry-run/live buttons.

### 3. Log tail usability

File:

`metin2_dashboard/control_panel.py`

Problem fixed:

- Auto-refresh was repeatedly deleting/reinserting log tail text, making it hard to read/copy.

Implemented:

- Log tail is paused by default.
- `Refresh log tail` button gives a manual snapshot.
- `follow log tail` checkbox opts into live streaming.
- Auto-refresh still updates state/runs without overwriting log tail when follow is off.

### 4. Buff keeper / F1 F2

Files:

- `src/metin2_research/win_input.py`
- `scripts/combat_metin_client_state.py`
- `metin2_dashboard/registry.py`
- `metin2_dashboard/control_panel.py`
- `tests/test_win_input.py`
- `tests/test_combat_metin_client_state.py`
- `tests/test_control_panel.py`

Root cause fixed:

- Windows `SendInput` structs used wrong `dwExtraInfo` pointer fields.
- Replaced with integer-sized `wintypes.WPARAM` and `0` values.

Implemented:

- `--buff-keys f1,f2`
- `--buff-durations 109,301`
- buff-only dry-run/live paths
- explicit safe control-panel buttons
- tests for F1/F2 schedule and key map behavior

Live proof artifact:

`C:/Hermes Unreal/metin2_research/reports/dashboard_runs/activate_both_buffs_run-faa3272d52e7.png`

Important proof rule:

- Only count buff icons ABOVE the four blue top-left icons.
- Ignore icons below the four blue icons.

### 5. Attack-nearby-mobs / select-attack flow

Files:

- `scripts/combat_metin_client_state.py`
- `metin2_dashboard/config.py`
- `metin2_dashboard/registry.py`
- `metin2_dashboard/control_panel.py`
- `tests/test_combat_metin_client_state.py`
- `tests/test_dashboard.py`
- `tests/test_control_panel.py`

Implemented/verified:

- `attack_nearby_mobs` config exists and round-trips.
- Registry exposes typed option.
- Dry-run paths do not send live input.
- If no selected Metin and attack-nearby-mobs is enabled, the loop waits/watches instead of aborting as exact-Metin combat.
- Safety gates remain separate: visible/probed mobs/Metins are not treated as selected exact target proof.

### 6. State bridge trust panel

Files:

- `metin2_dashboard/state_bridge.py`
- `metin2_dashboard/server.py`
- `metin2_dashboard/control_panel.py`
- `tests/test_state_bridge.py`
- `tests/test_dashboard.py`
- `tests/test_control_panel.py`

Endpoint:

`GET /api/state_bridge`

Trust behavior:

- selected Metin target + alive + fresh state => trusted
- dry-run remains `DRY_RUN_IDLE`
- live transition reports `ENGAGE_TARGET`
- missing/untrusted/stale target reports `NEED_METIN_TARGET`

Current live status at consolidation:

```text
trusted: True
live_action: ENGAGE_TARGET
```

### 7. Selected-target acquisition regression

Files:

- `scripts/patch_mt2_root_state_logger.py`
- `D:/Games/MT2Portugalia/app/game.py`
- `tests/test_json_state.py`

Root cause:

- The injected logger only used `player.GetTargetVID()`.
- In this client, selected target can be held on `self.targetBoard.GetTargetVID()`.

Fix:

```python
vid = player.GetTargetVID()
if (not vid) and hasattr(self, "targetBoard") and self.targetBoard:
    try:
        vid = self.targetBoard.GetTargetVID()
    except:
        pass
```

Live proof artifact:

`C:/Hermes Unreal/metin2_research/reports/metin-control-panel-live-target-proof-20260706-dionysus.md`

Screenshot proof:

`C:/Hermes Unreal/metin2_research/reports/dashboard_runs/live_target_proof_20260706_dionysus.png`

Proof result:

- selected target acquisition: PASS
- exact coordinate extraction: NOT YET PROVEN

## Existing reports/artifacts

Primary audit:

`C:/Hermes Unreal/metin2_research/reports/metin-control-panel-audit-20260706-dionysus.md`

Audit addendum:

`C:/Hermes Unreal/metin2_research/reports/metin-control-panel-audit-addendum-20260706-dionysus.md`

Live target proof:

`C:/Hermes Unreal/metin2_research/reports/metin-control-panel-live-target-proof-20260706-dionysus.md`

This consolidation report:

`C:/Hermes Unreal/metin2_research/reports/metin-dionysus-task-consolidation-execute-20260706.md`

Key screenshot artifacts:

- `C:/Hermes Unreal/metin2_research/reports/dashboard_runs/activate_both_buffs_run-faa3272d52e7.png`
- `C:/Hermes Unreal/metin2_research/reports/dashboard_runs/live_target_proof_20260706_dionysus.png`

## Working tree status at consolidation

```text
 M metin2_dashboard/control_panel.py
 M metin2_dashboard/registry.py
 M metin2_dashboard/server.py
 M scripts/combat_metin_client_state.py
 M scripts/patch_mt2_root_state_logger.py
 M src/metin2_research/win_input.py
 M tests/test_combat_metin_client_state.py
 M tests/test_control_panel.py
 M tests/test_dashboard.py
 M tests/test_json_state.py
 M tests/test_win_input.py
?? config/
?? metin2_dashboard/config.py
?? metin2_dashboard/state_bridge.py
?? reports/metin-control-panel-audit-20260706-dionysus.md
?? reports/metin-control-panel-audit-addendum-20260706-dionysus.md
?? reports/metin-control-panel-live-target-proof-20260706-dionysus.md
?? tests/test_state_bridge.py
```

Note: this consolidation report is additionally untracked after creation.

## Verification executed for consolidation

### Static/content checks

Confirmed all of these are present:

```text
control_panel log tail manual: True
state bridge API import: True
buff keys registry: True
sendinput ABI: True
targetBoard fallback patcher: True
loose game targetBoard fallback: True
```

### Compile + targeted suite

Command:

```bash
cd '/c/Hermes Unreal/metin2_research' && python -m py_compile metin2_dashboard/config.py metin2_dashboard/registry.py metin2_dashboard/server.py metin2_dashboard/control_panel.py metin2_dashboard/state_bridge.py scripts/combat_metin_client_state.py scripts/patch_mt2_root_state_logger.py src/metin2_research/win_input.py && python -m pytest tests/test_win_input.py tests/test_combat_metin_client_state.py tests/test_control_panel.py tests/test_dashboard.py tests/test_json_state.py tests/test_state_bridge.py -q
```

Result:

```text
152 passed in 17.55s
```

### Full test suite

Command:

```bash
cd '/c/Hermes Unreal/metin2_research' && python -m pytest -q
```

Result:

```text
315 passed in 17.80s
```

## Next concrete task

Recommended next task:

`metin-dionysus-exact-coordinate-extraction-20260706`

Goal:

Prove/fix target `pixel_position`, `project_position`, and optionally `hp_pct` in live `/api/state.target`.

Reason:

The control panel can now acquire selected target VID/name/alive and state bridge can trust it, but exact coordinate fields are missing. Mouse/position-perfect engagement should not rely on selected target alone until exact coordinate extraction is proven.

Suggested steps:

1. Add temporary debug fields in the injected client JSON target object:
   - `player_target_vid`
   - `target_board_vid`
   - `target_pixel_position_error`
   - `target_project_position_error`
   - raw repr/type for pixel/project calls if possible without stdlib JSON.
2. Restart/reload MT2Portugalia.
3. Select `Metin da Alma` or another Metin.
4. Verify `/api/state.target.pixel_position` or `/api/state.target.project_position` appears fresh.
5. Only then upgrade exact coordinate trust or mouse targeting.

## Operator quick commands

Open control panel:

```bash
cd '/c/Hermes Unreal/metin2_research' && python scripts/metin2_control_panel.py
```

Desktop launcher:

`C:/Users/blade/Desktop/Open Metin2 Control Panel.bat`

Check live state:

```bash
cd '/c/Hermes Unreal/metin2_research' && python scripts/verify_json_state.py --once
```

Check API state bridge:

```bash
python - <<'PY'
import json, urllib.request
for path in ['/api/state','/api/state_bridge']:
    with urllib.request.urlopen('http://127.0.0.1:8767'+path, timeout=2) as r:
        print(path, json.dumps(json.loads(r.read()), indent=2)[:2000])
PY
```

Run tests:

```bash
cd '/c/Hermes Unreal/metin2_research' && python -m pytest -q
```
