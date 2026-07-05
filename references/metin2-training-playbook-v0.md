# Metin2 Training Playbook for Dionysus

Purpose: teach Dionysus to play Metin2 on Yoshy's allowed private server using normal OS input and local screen observation. This is not a model benchmark. The goal is practical gameplay competence.

## Non-negotiable boundaries

- Private server only, where Yoshy permits automation/testing.
- Use normal OS input only: keyboard, mouse, screenshot/window capture.
- No memory reading, packet manipulation, client injection, anti-cheat bypass, exploit automation, or credential handling.
- Stop all input immediately if Yoshy takes over, the client loses focus, an unexpected modal appears, HP/death state is uncertain, or the action loop becomes unstable.

## Learning ladder

### Stage 1 — Understand the screen

Dionysus must reliably identify:

- Player HP/MP/EXP state.
- Minimap and whether UI overlays are open.
- Inventory, hotbar, chat, quest tracker, target frame, selected target text.
- Difference between world Metin stones, minimap icons, UI art, mobs, NPCs, and quest markers.
- Death, stuck, low HP, inventory full, no target, wrong target, and overlay-blocked states.

Required output for each observation:

```json
{
  "state": "safe|warning|blocked|unknown",
  "hp_status": "healthy|low|critical|unknown",
  "selected_target": "text or unknown",
  "metin_candidate_visible": true,
  "ui_blocker": false,
  "recommended_next_action": "observe|reposition|select_target|attack|heal|retreat|stop",
  "reason": "short explanation"
}
```

### Stage 2 — Learn controls and calibration

Document actual keybinds on Yoshy's setup:

- Movement keys.
- Camera movement.
- Attack/select target behavior.
- Potion hotkeys.
- Loot key/mouse behavior.
- Inventory open/close.
- Escape/close overlay.

Calibrate:

- Press W for 250/500/1000 ms and estimate movement.
- Rotate camera left/right for fixed durations.
- Click near/center/far targets and observe selection reliability.
- Test hotbar potion behavior only in a safe area.

### Stage 3 — Safe combat loop

Preferred combat loop:

1. Observe.
2. If HP critical/unknown: stop or retreat/heal.
3. If overlay open: close overlay and re-observe.
4. If selected target text is readable and contains Metin: attack.
5. If only visual candidate exists: move/camera-adjust to confirm target; do not spam attack.
6. After each input, re-observe and verify the expected state changed.
7. If no progress for N actions, stop and report stuck state.

Never attack if:

- Target text is unknown and confidence is low.
- The detected object is on minimap/UI/hotbar/quest overlay.
- The client is not focused.
- HP is critical and potion/heal status is unknown.

### Stage 4 — Basic goal skills

Teach in this order:

1. Keep character alive with potion/retreat logic.
2. Select a visible Metin stone.
3. Destroy one Metin stone.
4. Loot/confirm objective completion if applicable.
5. Recover from wrong target, overlay, death, stuck, or no target.
6. Repeat for a small route.
7. Only after consistent success, attempt 5-Metin goal.

### Stage 5 — Logging discipline

For each run, save:

- `events.jsonl`: timestamp, observation, decision, action, result.
- `latest_state.json`: current structured state.
- `latest_preview.jpg`: annotated screenshot.
- `summary.json`: task goal, success/failure, failure reason, counts, duration.

Report artifacts to Zeus by path and SHA256. Never paste secrets.

## Immediate Dionysus task

Dionysus should now produce a `METIN2_PLAYER_SKILL_REPORT.md` locally with:

1. Current keybind/control map.
2. Current perception reliability by UI component.
3. What he can already do alone.
4. What still needs Yoshy confirmation.
5. Top blockers preventing reliable 5-Metin completion.
6. Next 5 concrete training drills.
7. Artifact paths and SHA256 for evidence.

## Success definition

Dionysus "knows how to play Metin2" when he can, on Yoshy's private server:

- Explain the current screen state accurately.
- Keep the player alive in simple combat.
- Select/attack only valid Metin targets.
- Recover from common UI/stuck/death states.
- Complete one Metin destruction with event logs.
- Attempt a multi-Metin route with clear failure recovery, not blind input spam.
