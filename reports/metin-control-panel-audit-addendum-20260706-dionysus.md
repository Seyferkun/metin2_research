# Metin Control Panel Audit Addendum — 2026-07-06 — Dionysus

## Scope

Audit addendum label: `metin-control-panel-audit-addendum-20260706-dionysus`

Primary audit this extends:

`C:/Hermes Unreal/metin2_research/reports/metin-control-panel-audit-20260706-dionysus.md`

Project root:

`C:/Hermes Unreal/metin2_research`

This addendum records the post-audit live status check, target-grab blocker status, and verification performed after the selected-target fallback patch.

## Live status check

Check time from host:

```text
2026-07-06 09:56:21
```

Dashboard API checked:

`http://127.0.0.1:8767`

### `/api/state`

Current response summary:

```text
target: None
nearby_entities: 0
named_metin_probe: 0
state_age: about 134.9s
entity_probe: fail
```

Interpretation:

- The dashboard API is reachable.
- The state feed is stale at the time of this addendum.
- The state feed still has no selected target object.
- Because `/api/state.target` is `None`, the control panel has no selected-target evidence to grab.

### `/api/state_bridge`

Current response summary:

```text
reason: no selected target evidence
target: None
trusted: False
```

Interpretation:

- The state bridge is behaving as designed.
- It correctly refuses to claim a selected/trusted Metin target when the client state feed reports no selected target.

### `/api/runs`

Current response summary:

```text
runs: 4
running: 0
```

Interpretation:

- The API has historical managed runs visible.
- No dashboard-managed script is currently running.

## File status checks

Checked files:

```text
D:/Games/MT2Portugalia/app/game.py
D:/Games/MT2Portugalia/app/hermes_state.json
C:/Users/blade/Desktop/Open Metin2 Control Panel.bat
reports/metin-control-panel-audit-20260706-dionysus.md
```

Results:

```text
D:/Games/MT2Portugalia/app/game.py exists True size 130001 age 2346.6s
D:/Games/MT2Portugalia/app/hermes_state.json exists True size 585 age 134.9s
C:/Users/blade/Desktop/Open Metin2 Control Panel.bat exists True size 100 age 4166.1s
reports/metin-control-panel-audit-20260706-dionysus.md exists True size 8328 age 983.6s
```

The loose client file contains the target-board fallback:

```text
game_py_targetBoard_fallback True
```

## Target-grab addendum

The selected-target regression is still blocked live, but for a different reason than before:

- The reusable patcher contains the fallback.
- The loose `D:/Games/MT2Portugalia/app/game.py` contains the fallback.
- The live state feed still reports `target: None` and is stale.

Most likely cause now:

1. The running MT2 client has not reloaded the patched `game.py`, or
2. the current in-game state has no selected target, or
3. the client state logger stopped updating after the last interaction.

Required next verification:

1. Restart/reload MT2Portugalia so the patched loose `game.py` is actually loaded.
2. Log into Yoshypt.
3. Select a target in-game.
4. Immediately run:

```bash
cd '/c/Hermes Unreal/metin2_research' && python scripts/verify_json_state.py --once
```

Expected if the fallback works:

- output target is not `None`
- target has a nonzero VID
- target has a name
- `/api/state_bridge` changes away from `no selected target evidence`

If it still fails after restart and target selection, add temporary debug fields to the client JSON logger:

```text
player_target_vid
target_board_vid
target_board_available
target_board_error
```

This will distinguish:

- `player.GetTargetVID()` empty but target board nonzero
- both empty
- target board not available
- target board method throwing

Do not use `named_metin_probe` as selected-target proof. It can show a Metin exists or is visible/probed, but it must not replace selected VID/name/alive evidence for exact-target combat.

## Verification performed for this addendum

Command:

```bash
cd '/c/Hermes Unreal/metin2_research' && python -m pytest tests/test_json_state.py::test_updated_pack_logger_injects_json_export_and_keeps_tsv_compat tests/test_control_panel.py::test_control_panel_log_tail_has_manual_refresh_and_follow_toggle tests/test_state_bridge.py -q
```

Result:

```text
6 passed in 0.31s
```

This verifies:

- injected logger source includes the target-board fallback
- log-tail UI remains manual/follow instead of destructive auto-refresh
- state bridge tests still pass

## Working tree status at addendum time

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
?? tests/test_state_bridge.py
```

This addendum itself is newly written at:

`C:/Hermes Unreal/metin2_research/reports/metin-control-panel-audit-addendum-20260706-dionysus.md`

## Operator reminder

Open the control panel with either:

```bash
cd '/c/Hermes Unreal/metin2_research' && python scripts/metin2_control_panel.py
```

or double-click:

`C:/Users/blade/Desktop/Open Metin2 Control Panel.bat`

For log reading/copying:

- leave `follow log tail` off
- use `Refresh log tail` manually

For buff proof:

- count only the small buff icons ABOVE the four blue top-left icons
- ignore icons below the four blue top-left icons

For target proof:

- `/api/state.target` must show a target object
- `/api/state_bridge.reason` must no longer be `no selected target evidence`
- visible/probed Metins are not enough for selected-target proof
