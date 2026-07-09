# Metin Control Panel Live Target Proof — 2026-07-06 — Dionysus

## Scope

Proof label: `metin-control-panel-live-target-proof-20260706-dionysus`

Project root:

`C:/Hermes Unreal/metin2_research`

Purpose:

Verify live that the Metin control panel / API can again see the selected game target after patching the client logger to fall back from `player.GetTargetVID()` to `self.targetBoard.GetTargetVID()`.

## Preconditions

Relevant patched files:

- `C:/Hermes Unreal/metin2_research/scripts/patch_mt2_root_state_logger.py`
- `D:/Games/MT2Portugalia/app/game.py`

Relevant fallback added to the injected logger:

```python
vid = player.GetTargetVID()
if (not vid) and hasattr(self, "targetBoard") and self.targetBoard:
    try:
        vid = self.targetBoard.GetTargetVID()
    except:
        pass
```

Reason for live proof:

- Previous `/api/state` checks reported `target: None`.
- `/api/state_bridge` reported `no selected target evidence`.
- The patched `game.py` had to be loaded by restarting/reloading MT2Portugalia.

## Actions performed

1. Restarted/relaunched MT2Portugalia using the local login helper:

```bash
cd '/c/Hermes Unreal/metin2_research' && python scripts/login_mt2_local.py login --username yoshy --restart --click-fields --enter-game --enter-game-count 3 --delay 5
```

The helper relaunched as Administrator and the user needed to approve UAC.

2. Waited for the live client JSON state to refresh.

Initial checks showed old/stale targetless state, then fresh state began updating:

```text
check 9  age 0.4 map metin2_map_n_desert_01 player Yoshypt target None
check 10 age 0.1 map metin2_map_n_desert_01 player Yoshypt target None
...
```

3. After in-game target selection/engagement, the live JSON began reporting a selected target:

```text
check 17 age 0.1 map metin2_map_n_desert_01 player Yoshypt target {'vid': 1355188, 'name': 'Metin da Alma', 'alive': True, 'alive_source': 'chr.HasInstance', 'type': 2}
```

Subsequent checks continued to report the same selected target with fresh mtime age around 0.0–0.2s.

## API proof

Command shape:

```bash
cd '/c/Hermes Unreal/metin2_research' && python - <<'PY'
import json, time, urllib.request
for path in ['/api/state','/api/state_bridge']:
    with urllib.request.urlopen('http://127.0.0.1:8767'+path, timeout=2) as r:
        data=json.loads(r.read())
    print(path, data)
PY
```

### `/api/state`

Live selected target proof:

```json
{
  "vid": 1355188,
  "name": "Metin da Alma",
  "alive": true,
  "alive_source": "chr.HasInstance",
  "type": 2
}
```

Freshness at proof capture:

```text
age_sec 0.16
```

Other state counts at proof capture:

```text
named_probe_count 1
nearby_count 0
```

### `/api/state_bridge`

State bridge report at proof capture:

```json
{
  "available": true,
  "current_action": "TARGETING",
  "trust_level": "HIGH_EXACT",
  "freshness_window_seconds": 2.0,
  "age_seconds": 0.162,
  "has_trusted_target": true,
  "dry_run_action": "DRY_RUN_IDLE",
  "live_action": "ENGAGE_TARGET",
  "reason": "fresh HIGH_EXACT alive Metin target; dry-run still blocks engagement",
  "target": {
    "vid": 1355188,
    "name": "Metin da Alma",
    "is_metin": true,
    "is_alive": true,
    "x": null,
    "y": null,
    "z": null,
    "hp_percent": null
  }
}
```

Interpretation:

- The control panel/API can now see the selected target.
- The target is recognized as Metin-like and alive.
- The state evidence is fresh under the 2 second freshness gate.
- Dry-run remains safely blocked as `DRY_RUN_IDLE`.
- Live transition would be `ENGAGE_TARGET` according to the state bridge.

## Visual proof

Screenshot saved at:

`C:/Hermes Unreal/metin2_research/reports/dashboard_runs/live_target_proof_20260706_dionysus.png`

Visual inspection:

- A selected target bar is visible at the top center.
- Target: `Lv. 40 (Nível 6) Metin da Alma`
- HP shown: `56,188 / 119,700 (46.94%)`

This screenshot supports the API state: the game UI and `/api/state` both agree that a Metin target is selected.

## Result

Live target-grab proof: PASS.

The selected-target pipeline is working after restart/reload:

```text
Game UI selected target -> client logger -> hermes_state.json -> /api/state -> /api/state_bridge -> control panel trust card
```

Before restart/reload:

```text
/api/state target: None
/api/state_bridge reason: no selected target evidence
```

After restart/reload and target selection:

```text
/api/state target: Metin da Alma VID 1355188 alive True
/api/state_bridge has_trusted_target: True
/api/state_bridge live_action: ENGAGE_TARGET
```

## Remaining caveat

The target object currently does not include `pixel_position`, `project_position`, or `hp_pct` in the API proof payload:

```text
x: null
y: null
z: null
hp_percent: null
```

So this proves selected target acquisition and target-board fallback, but not exact screen/project coordinate extraction yet.

Next improvement if needed:

- Add/debug explicit client logger fields for:
  - `player_target_vid`
  - `target_board_vid`
  - `target_pixel_position_error`
  - `target_project_position_error`
- Verify why `chr.GetPixelPosition()` / `chr.GetProjectPosition()` are not making it into the JSON target object for this selected Metin.

## Safety note

This proof does not treat `named_metin_probe` as selected-target evidence. The pass condition is the selected `target` object in live `/api/state` plus a matching top-center target bar screenshot.
