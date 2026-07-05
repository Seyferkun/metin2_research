# Player Drill Summary — One Metin Combat Practice

Date: 2026-06-26
Context: Yoshy permitted live practice on the private server.
Safety: normal OS input only; no memory/packet/injection/anti-cheat bypass.

## Drill steps executed

1. Observation-only drill on actual `pgclient.app` game window.
   - Ran 5 captures under `reports/player_drill_observation_only_pgclient_20260626_02`.
   - Yoshypt was alive, HP full, no blocking UI overlay.
   - Detector confidence varied and initially classified the true Metin as low/medium confidence.
   - Important lesson: `window-query MT2Portugalia` matched the patcher window first; use `window-query pgclient` for the actual game.

2. Target-selection drill.
   - Manually selected the visually confirmed world Metin body at screen coordinate `[898, 282]`.
   - No attack key was sent during target selection.
   - Result: Yoshypt approached/selected `Lv 10 Metin do Combate`; selected target HP became visible at ~90.43%.

3. One-Metin combat drill.
   - Used scan-code `Space` attack bursts with frequent potion key `1` taps.
   - Burst 1: 12s, 5 potion taps; Metin HP dropped to ~58.40%.
   - Burst 2: 15s, 9 potion taps; Metin HP dropped to ~23.78%.
   - Burst 3: 12s, 7 potion taps; Metin disappeared/destroyed.
   - Yoshypt remained alive.
   - Loot/EXP messages were visible after combat.

4. Loot drill.
   - Tapped pickup key `Z` 8 times.
   - Some loot remained visible afterward (`Fatia de Bolo`, `Arco+0`, `Pocao Roxa(M)`), probably because Yoshypt was not close enough to all drops.
   - No random movement was attempted; stopped safely.

## Outcome

Success:
- Destroyed one visible Metin (`Lv 10 Metin do Combate`) during controlled practice.
- Kept Yoshypt alive with potion key `1`.
- Confirmed the scan-code attack loop is effective when the target is visibly selected and adjacent.
- Confirmed the detector can miss/lose the Metin during close combat even while visual target/HP is clear; live loop must trust selected target HP/visual state more than YOLO during melee.

Partial / needs improvement:
- Pickup `Z` alone did not collect all drops from the current standing position.
- Need a controlled loot-positioning drill: click/walk closer to visible drops, then press `Z`, with before/after captures.
- Need selected-target OCR or a deterministic parser; current validation used visual inspection.
- Need update to report/control loop: prefer `window-query pgclient`, not generic `MT2Portugalia`, to avoid capturing the patcher.

## Key artifacts

- Observation drill directory: `reports/player_drill_observation_only_pgclient_20260626_02`
- Target selection directory: `reports/player_drill_target_select_20260626_01`
- Combat drill directory: `reports/player_drill_one_metin_combat_20260626_01`
- After burst 2 capture: `reports/player_drill_one_metin_combat_20260626_01_after2/live_preview.jpg`
- After burst 3/destroy capture: `reports/player_drill_one_metin_combat_20260626_01_after3/live_preview.jpg`
- After loot capture: `reports/player_drill_one_metin_combat_20260626_01_after_loot/live_preview.jpg`

## Next recommended drill

`Metin2 Drill 2: controlled loot collection after one Metin kill`

Steps:
1. Start from visible loot field after a Metin kill.
2. Observe and list drop labels.
3. Move/click only toward visible loot pile center, not random ground.
4. Press `Z` repeatedly.
5. Re-observe and verify which labels disappeared.
6. Stop if mobs approach, HP drops, or focus/overlay is uncertain.
