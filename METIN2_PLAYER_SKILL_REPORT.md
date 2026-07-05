# METIN2 Player Skill Report — Dionysus

Date: 2026-06-26
Task: `task-2fad38df7ff4`
Project: `C:/Hermes Unreal/metin2_research`
Training playbook adopted locally: `references/metin2-training-playbook-v0.md`

## 1. Safety boundaries confirmed

Current Metin2 work remains inside the allowed research/private-server boundary:

- Private server only, where Yoshy permits testing.
- Normal OS input only: screenshot/window capture, mouse, keyboard.
- No memory reading.
- No packet manipulation.
- No client injection.
- No anti-cheat bypass.
- No credential/cookie/private-key handling.
- Input should stop immediately if Yoshy takes over, client focus is uncertain, an unexpected modal appears, HP/death state is uncertain, or the loop becomes unstable.

## 2. Current keybind/control map

Confirmed or project-supported bindings for Yoshypt / MT2Portugalia:

| Function | Key(s) | Current confidence | Notes |
|---|---|---:|---|
| Move forward | `W` | High | Also arrow-key alternates are visible in keybind screen history; primary harness uses scan-code `W`. |
| Move left / strafe/turn | `A` | High | Movement/rotation effect depends client state/camera. |
| Move backward | `S` | High | Supported by scan-code map. |
| Move right / strafe/turn | `D` | High | Movement/rotation effect depends client state/camera. |
| Camera/character rotate left | `Q` | High | Earlier probes showed strong rotation. |
| Camera/character rotate right | `E` | High | Earlier probes showed opposite rotation. |
| Zoom in/out | `R` / `F` | Medium | In scan-code map; needs deliberate calibration drill. |
| Camera vertical | `T` / `G` | Medium | In scan-code map; needs deliberate calibration drill. |
| Attack | `Space` | High | Legacy VK was unreliable; scan-code SendInput produced visible attack animation in prior tests. |
| Potion / quick slot 1 | `1` | High | Used during combat loops to keep Yoshypt alive. |
| Pickup | `Z` | Medium | In scan-code map; needs loot drill. |
| Next target | `Tab` | Medium | In scan-code map; needs wrong-target drill before combat use. |
| Big map | `M` | High | Can block screen; must verify closed before combat. |
| Close overlay/menu | `Esc` | Medium/conditional | ESC closes visible overlays, but blind ESC can open menu when no overlay is visible. Use visual menu detection before pressing. |

Implementation evidence:

- `src/metin2_research/win_input.py` maps scan-codes for `1`, `space`, `esc`, `tab`, `q/w/e/r/t/a/s/d/f/g/z/m` and uses `SendInput` scan-code events.
- The playbook requires Stage 2 calibration: fixed-duration W movement, fixed-duration Q/E rotations, click reliability, and safe potion behavior.

## 3. Current perception reliability by UI component

| Component / state | Current reliability | Evidence / issue |
|---|---:|---|
| World Metin stone detection | Medium-high | Best YOLO model: `reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt`; round2 recall is strongest in audit. Still needs false-positive filtering. |
| UI/minimap/hotbar rejection | Medium | `live_filters.py` and taxonomy reject UI/minimap/hotbar/quest overlays; residual false positives still occurred historically. |
| Selected target text | Low | OCR stack is not installed; combat should not fully trust target HP without selected-name confirmation. |
| Target HP bar | Medium-low | Dark-frame filter added, but historical failure parsed overlay/red UI text as target HP. Needs selected target text OCR. |
| Player HP / death state | Medium | Some scripts read state visually; one latest state had `player_dead_visual: true` while `player_dead: false`, so death/HP needs stronger reconciliation. |
| Overlay / big map / dungeon panel | Medium | Overlay close logic exists, but dungeon overlay previously blocked run2 and caused false HP/stall. |
| Minimap coordinates | Low | Local coordinate parser exists, but OCR backends are absent; minimap hover did not reliably expose coordinates in prior probes. Manual coordinate text can be supplied. |
| Game focus/window state | Medium | Window capture and raise-window helpers exist, but live action should re-check focus before input. |
| Loot/EXP completion confirmation | Low-medium | Needs drill; destruction should be confirmed via disappearance + loot/EXP/drop signals, not HP alone. |
| Wrong target / mobs | Low-medium | Historical blocker: wolf/mob target selected as if Metin. Needs selected-target OCR and mob-label rejection. |

## 4. What Dionysus can already do alone

- Inspect the Metin2 project and run the full test suite.
- Load the Zeus playbook from the artifact vault and keep a verified local copy.
- Capture and analyze screenshots using the local detector/state pipeline when the game is available.
- Use the best known YOLO weights for Metin detection.
- Produce state JSON, previews, and event logs.
- Use safety-gated normal OS input through `SendInput` scan-code helpers.
- Press/tap/hold the main controls through code (`W/A/S/D`, `Q/E`, `Space`, `1`, `Z`, `M`, `Esc`, etc.).
- Close known overlays when visually detected, with caution around ESC.
- Run bounded private-server controller attempts and save artifacts.
- Build local structured state for minimap coordinate text if Yoshy supplies the visible coordinate string manually.
- Explain blockers and produce drill plans instead of pretending a live result happened.

## 5. What still needs Yoshy confirmation

- Whether the MT2Portugalia client is open, logged in, and safe to observe/practice.
- Whether short safe practice drills are permitted right now.
- Exact live keybinds in the current client build if the settings screen changed.
- Whether quick slot `1` currently contains the intended potion and whether using it in safe drills is acceptable.
- Whether attacking/looting/route practice is allowed in the current location.
- Whether Yoshy wants OCR dependencies installed for selected-target/minimap text.
- Whether manual coordinate hover/readout will be used until OCR is installed.
- Whether big-map/minimap coordinate/navigation probes should continue despite prior minimap-hover tooltip not appearing.

## 6. Current blocker preventing reliable 5-Metin completion

1. Selected-target text is not machine-readable yet, so the controller can still attack wolves/mobs or stale/wrong targets.
2. OCR stack is absent (`tesseract`, `pytesseract`, and `easyocr` unavailable in the earlier local inventory), blocking reliable selected target and minimap coordinate reading.
3. HP bar parser can still be fooled by overlays unless target-name confirmation is added.
4. Dungeon/big-map overlays can block view or produce false UI readings.
5. Detector still has residual false positives on minimap/UI/quest icons/mobs despite hard-negative work.
6. Death and HP state need stronger reconciliation: latest historical state includes contradictory fields (`player_dead_visual: true` while `player_dead: false`).
7. Navigation is still local/heuristic; no robust spawn route or occupancy-map movement policy exists yet.
8. Movement calibration is incomplete for fixed-duration W/Q/E/R/F/T/G drills.
9. Loot/destruction confirmation is not robust enough; should use target disappearance + loot/EXP/system message, not only HP.
10. Metin2 was not currently open for a live drill in this task; only WinRAR/notepad references to Metin2 assets were seen, no `pgclient`/MT2Portugalia game process.

## 7. Next 5 concrete training drills

These are ordered for gameplay competence, not benchmarking.

### Drill 1 — Observation-only screen state drill

Goal: Given a live screenshot, output the playbook JSON state:

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

Run only when game is open. Save `latest_state.json`, `latest_preview.jpg`, and the raw screenshot.

### Drill 2 — Key calibration in safe area

Goal: Calibrate controls without combat.

- W 250/500/1000 ms, observe movement/coordinate or optical-flow delta.
- Q/E 250/500/1000 ms, observe camera turn delta.
- R/F and T/G single taps, verify camera effects.
- Press `1` only if Yoshy confirms potion slot is safe and needed.

Stop if focus/menu/death state is uncertain.

### Drill 3 — Target selection validation drill

Goal: Click a visible Metin body and verify selected target is actually a Metin before any attack.

- Capture before click.
- Click only filtered world Metin candidate body.
- Capture after click.
- Require selected-target text/OCR if available; otherwise mark `selected_target: unknown` and do not escalate to autonomous attack.

### Drill 4 — One-Metin safe combat drill

Goal: Destroy exactly one Metin with conservative stop conditions.

- Keep potion key `1` available and tap periodically only if permitted.
- Attack only after candidate is world-filtered and target selection is validated as Metin or Yoshy confirms visually.
- After each burst, re-observe HP/target/disappearance.
- Stop on wrong target, no HP decrease, low/unknown HP, overlay, death, or focus loss.

### Drill 5 — Recovery drill

Goal: Practice recovering from common blockers without blind input.

- Overlay open: visually detect then close; do not blind-ESC if no menu is visible.
- Wrong target: stop attack, clear selection/reacquire.
- No target: rotate/search gently; no random run-clicks.
- Death/critical HP: stop and report; do not continue.
- Stuck: move/camera adjustment with immediate observation.

## 8. Live practice result for this task

No live Metin2 practice was run in this task because the game process did not appear to be open.

Process inspection saw Metin2-related local files/tools only:

- `WinRAR.exe` opened `C:\Users\blade\Downloads\MT2Portugalia.zip`.
- `notepad++.exe` opened a Metin2 research report.
- No active `pgclient.app`, `MT2Portugalia`, or equivalent game process was found.

Therefore the correct status is: `blocked-on-game-not-open`.

## 9. Evidence artifacts and SHA256

| Artifact | SHA256 |
|---|---|
| `references/metin2-training-playbook-v0.md` | `5e760d0336bb747461123711a3cfd2a6c0247bdc960dc8214c352f3a52fb92e9` |
| `reports/PROJECT_PROGRESS_AUDIT.md` | `c0ad378f2021883eb44fb237f5cfd6b01b3beb5bfefff4b21b03d0d6c98141f7` |
| `configs/local_vision_taxonomy.json` | `4a3c8499c19acf801638a33ea11351b04978c22c7ecd9dd38f0847c8c4d43c8b` |
| `src/metin2_research/win_input.py` | `9c2e4fc71e9be4dacb49b496cf35d2e2bb5c6d9144d625c86f368b31fa7b5df0` |
| `src/metin2_research/local_vision.py` | `bfffc30a37a4bde5fe897d0721255b7c3e90a0bbdbc934ead4b4611f55f91872` |
| `reports/destroy_requested_metin/summary.json` | `8f2d27660cefb06006336f70779049f549a32dc9943164a5fbcf6012327445fa` |
| `reports/destroy_requested_metin/latest_state.json` | `21abe0fe3a37b995286d45c8185f28e4a53e5c3807d6ed0daf44b0181618b00b` |
| `reports/destroy_requested_metin/latest_preview.jpg` | `6480ed7eaa9568043b2e8a6a563aaa30aaea8065fdd11788c7e5b7109e98f082` |
| `reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt` | `cd1ac0be26a66c443da4bf7ad8d14f5fb6397ec11aed00828e8e0039d46aa1fd` |

Fresh verification:

- `PYTHONPATH=src python -m pytest -q`
- Result: `43 passed in 1.17s`

## 10. Exact next task Zeus should queue

Queue this next:

Title: `Metin2 Drill 1: observation-only live screen state when Yoshy opens client`

Instructions:

1. Ask Yoshy to open MT2Portugalia on the private server and place Yoshypt in a safe area.
2. Dionysus must run observation-only capture, no input.
3. Produce the playbook JSON state for 5 consecutive captures.
4. Save screenshots, `latest_state.json`, `latest_preview.jpg`, and `events.jsonl` under a timestamped `reports/player_drill_observation_only_*` directory.
5. If selected-target/minimap OCR is unavailable, mark it explicitly and do not invent text.
6. Return paths and SHA256 to Zeus.

Success criteria:

- 5/5 captures classify screen state without unsafe action.
- UI blockers and HP/death state are identified or explicitly marked unknown.
- No mouse/keyboard input is sent.
