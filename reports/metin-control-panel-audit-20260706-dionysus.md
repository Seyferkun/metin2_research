# Metin Control Panel Audit — 2026-07-06 — Dionysus

## Scope

Audit label: `metin-control-panel-audit-20260706-dionysus`

Project root:

`C:/Hermes Unreal/metin2_research`

Control panel surfaces reviewed and modified in this session:

- Native Tk control panel: `metin2_dashboard/control_panel.py`
- Browser/API dashboard: `metin2_dashboard/server.py`
- Script registry/config propagation: `metin2_dashboard/registry.py`, `metin2_dashboard/config.py`
- State bridge trust panel: `metin2_dashboard/state_bridge.py`
- Combat/buff runner: `scripts/combat_metin_client_state.py`
- Windows input helper: `src/metin2_research/win_input.py`
- Client logger patcher / loose client logger: `scripts/patch_mt2_root_state_logger.py`, `D:/Games/MT2Portugalia/app/game.py`

## Current status summary

The Metin control panel has been opened and relaunched during the session. A desktop launcher exists at:

`C:/Users/blade/Desktop/Open Metin2 Control Panel.bat`

The control panel can also be opened with:

```bash
cd '/c/Hermes Unreal/metin2_research' && python scripts/metin2_control_panel.py
```

The current localhost dashboard API is responding at:

`http://127.0.0.1:8767`

Current `/api/state` diagnostic at audit time:

- API reachable: yes
- `target`: `None`
- `nearby_entities`: `0`
- `named_metin_probe`: `0`
- state file age: about `104s` at the audit check

Current `/api/state_bridge` diagnostic at audit time:

- reason: `no selected target evidence`
- target: `None`

Interpretation: the control panel cannot currently grab a selected target because the client state feed is not reporting a selected target. The target-board fallback has been patched into the loose client file and logger patcher, but the running game client must be restarted/reloaded for loaded Python code changes to take effect.

## Major fixes completed

### 1. Keep buffs active / F1+F2

Root cause found:

- `src/metin2_research/win_input.py` used the wrong `SendInput` struct field type for `dwExtraInfo`.
- It used pointer fields where Windows expects an integer-sized `ULONG_PTR`/`WPARAM` style field.
- That can produce logs such as `BUFF_DUE` without the elevated/focused game reliably receiving the key input.

Fixes:

- `KEYBDINPUT.dwExtraInfo` and `MOUSEINPUT.dwExtraInfo` now use `wintypes.WPARAM`.
- Input constructors pass `0` instead of `None` for `dwExtraInfo`.
- `scripts/combat_metin_client_state.py` now supports explicit buff schedules:
  - `--buff-keys f1,f2`
  - `--buff-durations 109,301`
  - `--buff-refresh-margin-seconds 3`
- Native control panel now exposes:
  - `Buffs dry-run`
  - `Keep buffs active LIVE`
- Buff-only mode does not move, attack, click, or target.

Live proof performed earlier:

- Run id: `run-faa3272d52e7`
- Live log showed both:
  - `BUFF_DUE` / `press_buff` / `f1` / `dry_run=false`
  - `BUFF_DUE` / `press_buff` / `f2` / `dry_run=false`
- Visual verification rule corrected: only buff icons ABOVE the four blue top-left icons count.
- Screenshot path:
  - `C:/Hermes Unreal/metin2_research/reports/dashboard_runs/activate_both_buffs_run-faa3272d52e7.png`

### 2. Log tail usability

Problem:

- The control panel auto-refresh loop refreshed selected run log tail every ~1.5s.
- This made the log box hard to read, scroll, select, and copy.

Fixes:

- Log tail no longer auto-updates by default.
- Added `Refresh log tail` button.
- Added `follow log tail` checkbox for explicit live streaming.
- Added log-tail status text so the operator can see whether it is paused/following.
- Run/state auto-refresh is now separate from log-tail overwrites.

Expected behavior now:

- Default is safe for reading/copying.
- Click `Refresh log tail` for a manual snapshot.
- Enable `follow log tail` only when live streaming is desired.

### 3. Control panel organization

The native Tk control panel now uses tabs:

- `State + controls`
- `Targets + runs`

This keeps state cards, state bridge trust, buff/mob config, scripts, nearby Metins, runs, and logs more organized.

### 4. State bridge trust panel

Added `metin2_dashboard/state_bridge.py` and integrated it into:

- Native control panel
- Browser dashboard
- `/api/state_bridge`

Trust gate rules:

A selected target is trusted only when:

- target exists
- target name is Metin-like
- target is alive
- evidence is fresh, under 2 seconds old by state file mtime
- trust level is `HIGH_EXACT`

Dry-run invariant:

- trusted + dry-run => `DRY_RUN_IDLE`
- trusted + live => `ENGAGE_TARGET`
- untrusted/stale/missing => `NEED_METIN_TARGET`

### 5. Selected target regression diagnosis and patch

Observed problem:

- `/api/state` reported `target: null` even while target-related UI/probes could exist.
- The injected logger only read `player.GetTargetVID()`.
- In this client, selected target can be tracked by `self.targetBoard.GetTargetVID()` even when `player.GetTargetVID()` is empty.

Patch applied:

- `scripts/patch_mt2_root_state_logger.py`
- `D:/Games/MT2Portugalia/app/game.py`

New logic:

```python
vid = player.GetTargetVID()
if (not vid) and hasattr(self, "targetBoard") and self.targetBoard:
    try:
        vid = self.targetBoard.GetTargetVID()
    except:
        pass
```

Important blocker:

- The running MT2 client has already loaded `game.py`.
- The game must be restarted/reloaded for the target-board fallback patch to affect the live state logger.

Safety note:

- `named_metin_probe` is not treated as selected target proof.
- A visible/probed Metin is not the same as the user’s selected target.

## Current changed files

`git status --short` at audit time:

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
?? tests/test_state_bridge.py
```

## Verification performed

Targeted quick audit verification command:

```bash
cd '/c/Hermes Unreal/metin2_research' && python -m pytest tests/test_json_state.py tests/test_control_panel.py tests/test_dashboard.py -q
```

Result:

```text
95 passed in 10.40s
```

Earlier full-suite verification after log-tail/control-panel changes:

```text
315 passed in 18.66s
```

Earlier state bridge verification:

```text
306 passed in 16.82s
```

Earlier buff/mob/UI verification included targeted tests for:

- `SendInput` struct ABI
- F1/F2 buff schedule
- buff-only dry-run/live payloads
- attack-nearby-mobs dry-run safety
- notebook/tab UI organization
- log-tail manual/follow behavior

## Current blockers / next actions

1. Restart MT2Portugalia to load patched `D:/Games/MT2Portugalia/app/game.py`.
2. After restart/login, select a target in-game.
3. Verify:

```bash
cd '/c/Hermes Unreal/metin2_research' && python scripts/verify_json_state.py --once
```

Expected after selecting a target:

- `target` should no longer be `None`
- `/api/state` should show target vid/name/alive fields
- `/api/state_bridge` should move from `no selected target evidence` to either trusted or a specific freshness/trust blocker

4. If target still fails after restart:

- inspect whether `self.targetBoard.GetTargetVID()` exists/returns nonzero in this client build
- add a temporary client-side debug field for both raw values:
  - `player_target_vid`
  - `target_board_vid`
- do not weaken safety gates by treating visible/probed mobs as selected targets

5. If live input stops working again:

- verify dashboard/API and game run at matching integrity/elevation
- verify top-left buff icons visually, using only icons above the four blue icons

## Operator notes

Easy open command:

```bash
cd '/c/Hermes Unreal/metin2_research' && python scripts/metin2_control_panel.py
```

Desktop launcher:

`C:/Users/blade/Desktop/Open Metin2 Control Panel.bat`

Log tail usage:

- Leave `follow log tail` off when reading/copying logs.
- Use `Refresh log tail` for manual snapshots.
- Turn on `follow log tail` only when actively watching a running script.

Buff visual proof rule:

- Count only small buff icons ABOVE the four blue top-left icons.
- Ignore icons below the four blue icons.
