# Metin Coordinate + HP Implementation Proof — 2026-07-06 — Dionysus

## Scope

Task label: `metin-coordinate-hp-implementation-execute-20260706-dionysus`

Project root:

`C:/Hermes Unreal/metin2_research`

Goal:

Implement and prove selected-target coordinate and HP extraction for the local/private MT2Portugalia client state logger.

Safety scope:

- Local/private-server Yoshy client only.
- Read-only client-state extraction from local client Python objects.
- No packets, server DB/API/files, credential/session extraction, MITM, or anti-cheat bypass.
- No public-server automation.

## Result summary

Overall result: **PARTIAL PASS / useful implementation complete**

What passed live:

1. Selected-target acquisition: **PASS**
2. Target-board diagnostic fields: **PASS**
3. Target HP extraction: **PASS**
4. Target HP percent extraction: **PASS**
5. Pixel/world position extraction via `chr.GetPixelPosition(vid)`: **PASS**
6. State bridge coordinate/HP propagation: **PASS**
7. Project/screen-space position extraction via `chr.GetProjectPosition(...)`: **NOT PROVEN**

The important implementation fix was adding a VID-argument fallback for pixel extraction:

```python
try:
    _pp=chr.GetPixelPosition(vid)
except:
    chr.SelectInstance(vid)
    _pp=chr.GetPixelPosition()
```

The no-argument pixel path failed in the live client, but `chr.GetPixelPosition(vid)` succeeded and produced target coordinates.

## Important operator note

During this proof the user reported:

> I had to manually activate the buffs

So this report does **not** claim the control panel successfully kept or activated buffs during the coordinate/HP proof. Buff activation/upkeep remains a separate live-control concern and should require visual proof above the four blue top-left icons.

## Changed implementation

Modified source of truth:

- `scripts/patch_mt2_root_state_logger.py`

Patched runtime loose client file:

- `D:/Games/MT2Portugalia/app/game.py`

Added/updated tests:

- `tests/test_json_state.py`

Runtime helper artifact used only to select a visible target with elevated input:

- `reports/dashboard_runs/elevated_click_once.py`

Reason for elevated input helper:

- MT2Portugalia/pgclient runs elevated on this machine.
- Non-elevated SendInput/click attempts can fail or click the wrong thing.
- The helper relaunches itself via UAC and sends a single click to the local game window.

## Logger fields added

The reusable logger now exports diagnostic/acquisition fields on `target`:

```json
{
  "player_target_vid": 1558008,
  "target_board_vid": 1558008,
  "target_board_available": 1,
  "target_board_error": "... optional ...",
  "target_pixel_position_error": "... optional ...",
  "target_project_position_error": "... optional ...",
  "target_hp_now": 103614,
  "target_hp_max": 119700,
  "target_hp_pct": 86.5614035088
}
```

The logger also emits compatibility HP fields consumed by the existing parser/state bridge:

```json
{
  "hp": 103614,
  "max_hp": 119700,
  "hp_pct": 86.5614035088
}
```

## HP extraction mechanism

The client has this target-board callback in loose `game.py`:

```python
def SetHPTargetBoard(self, vid, hpNow, hpMax):
    if vid != self.targetBoard.GetTargetVID():
        self.targetBoard.ResetTargetBoard()
        self.targetBoard.SetEnemyVID(vid)
    self.targetBoard.SetHP(hpNow, hpMax)
    self.targetBoard.Show()
```

The patcher now injects a small read-only cache immediately after `self.targetBoard.SetHP(hpNow, hpMax)`:

```python
try:
    self._hermes_target_hp_vid=vid
    self._hermes_target_hp_now=hpNow
    self._hermes_target_hp_max=hpMax
except: pass
```

The OnUpdate logger then uses that cache when the cached HP VID matches the selected target VID.

## Coordinate extraction mechanism

The previous no-arg coordinate extraction path was insufficient in live client conditions. The logger now uses:

```python
try:
    try:
        _pp=chr.GetPixelPosition(vid); px=_pp[0]; py=_pp[1]
    except:
        chr.SelectInstance(vid)
        _pp=chr.GetPixelPosition(); px=_pp[0]; py=_pp[1]
    pix='['+str(px)+','+str(py)+']'
except:
    pix=""; pixe="pixel_error"
```

Live proof showed the VID-argument path works.

Project-position extraction still returned:

```json
"target_project_position_error": "project_error"
```

So `project_position` remains not proven.

## Static verification

Command:

```bash
python -m py_compile scripts/patch_mt2_root_state_logger.py metin2_dashboard/state_bridge.py metin2_dashboard/server.py metin2_dashboard/control_panel.py
python -m pytest tests/test_json_state.py tests/test_state_bridge.py tests/test_dashboard.py tests/test_control_panel.py -q
```

Result:

```text
101 passed in 10.68s
```

Full suite command:

```bash
python -m pytest -q
```

Result:

```text
317 passed in 18.22s
```

## Live proof artifacts

Screenshot with selected target bar:

`C:/Hermes Unreal/metin2_research/reports/dashboard_runs/coordinate_hp_live_proof_20260706_dionysus.png`

Earlier screenshot after elevated click:

`C:/Hermes Unreal/metin2_research/reports/dashboard_runs/coordinate_hp_after_elevated_click_20260706.png`

The screenshot target bar showed:

```text
Lv. 40 (Nível 6) Metin da Alma
104,221 / 119,700
87.07%
```

The live API snapshot taken immediately before that screenshot showed a fresh target with coordinate + HP fields.

## Live `/api/state` proof

Live capture output:

```text
/api/state
age_sec 0.008
```

Target:

```json
{
  "vid": 1558008,
  "name": "Metin da Alma",
  "alive": true,
  "alive_source": "chr.HasInstance",
  "type": 2,
  "pixel_position": [
    95920.0,
    25729.0
  ],
  "player_target_vid": 1558008,
  "target_board_vid": 1558008,
  "target_board_available": 1,
  "target_project_position_error": "project_error",
  "hp": 103614,
  "target_hp_now": 103614,
  "max_hp": 119700,
  "target_hp_max": 119700,
  "hp_pct": 86.5614035088,
  "target_hp_pct": 86.5614035088
}
```

Interpretation:

- `pixel_position`: **PASS**
- `hp`, `max_hp`, `hp_pct`: **PASS**
- `player_target_vid` and `target_board_vid`: **PASS**
- `project_position`: **FAIL / not available through current path**

## Live `/api/state_bridge` proof

Live capture output:

```json
{
  "available": true,
  "current_action": "TARGETING",
  "trust_level": "HIGH_EXACT",
  "freshness_window_seconds": 2.0,
  "age_seconds": 0.03,
  "has_trusted_target": true,
  "dry_run_action": "DRY_RUN_IDLE",
  "live_action": "ENGAGE_TARGET",
  "reason": "fresh HIGH_EXACT alive Metin target; dry-run still blocks engagement",
  "target": {
    "vid": 1558008,
    "name": "Metin da Alma",
    "is_metin": true,
    "is_alive": true,
    "x": 95920.0,
    "y": 25729.0,
    "z": null,
    "hp_percent": 86.5614035088
  }
}
```

Interpretation:

- State bridge receives coordinate `x/y` from selected target pixel/world position.
- State bridge receives HP percent from target-board HP cache.
- State bridge still reports `z: null` because project/3D coordinate extraction is not proven.

## Current-state caveat after proof

A later API check after the target was no longer selected/current reported:

```text
/api/state age_sec 38.427 target null
/api/state_bridge reason: no selected target evidence
```

That does not invalidate the proof. It means the live target proof is time-sensitive: the target must be actively selected and the state file fresh.

## Working tree status at report time

Relevant modified/untracked files include:

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
?? reports/metin-dionysus-task-consolidation-execute-20260706.md
?? tests/test_state_bridge.py
```

This task specifically added/validated coordinate+HP work in:

```text
scripts/patch_mt2_root_state_logger.py
tests/test_json_state.py
D:/Games/MT2Portugalia/app/game.py
```

## Classification

Selected target:

```text
PASS
```

Pixel/world coordinate extraction:

```text
PASS via target.pixel_position [95920.0, 25729.0]
```

Project/screen-space coordinate extraction:

```text
NOT PROVEN; target_project_position_error="project_error"
```

HP extraction:

```text
PASS via target-board HP cache
```

State bridge exact target:

```text
PASS for x/y + hp_percent; z remains null
```

Buff activation during proof:

```text
NOT CLAIMED; user manually activated buffs
```

## Recommended next task

1. Keep `pixel_position` as the trusted selected-target coordinate source for movement/state bridge.
2. Do not rely on `project_position` until a separate client API path is found and live-proven.
3. If screen/mouse aiming needs exact 2D screen coordinates, build a separate screen-space proof path instead of misusing world pixel coordinates.
4. Separately debug buff auto-activation/elevated SendInput if the user wants the control panel to handle F1/F2 without manual intervention.
