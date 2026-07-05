# Metin2 Research Progress Audit

Date: 2026-06-26
Project path: `C:/Hermes Unreal/metin2_research`

## Safety / scope

- User confirmed the live test target is a private server.
- The harness has been kept explicit and safety-gated.
- Live control uses normal Windows OS mouse input only.
- No packet manipulation, memory reading, injection, anti-cheat bypass, or hidden automation was added.

## Current tested model

Best practical live model so far:

`reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt`

Round-2 comparison report:

`reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr_comparison.md`

Key corrected-dataset metrics:

| Metric | Original | Corrected baseline | Fine-tune 30ep | Round2 lowLR 10ep |
|---|---:|---:|---:|---:|
| evaluated_images | 769 | 769 | 769 | 769 |
| true_positives | 714 | 740 | 741 | 745 |
| false_positives | 64 | 38 | 50 | 44 |
| false_negatives | 55 | 29 | 28 | 24 |
| precision | 0.917738 | 0.951157 | 0.936789 | 0.944233 |
| recall | 0.928479 | 0.962289 | 0.963589 | 0.968791 |
| failure_preview_count | 71 | 46 | 53 | 48 |

Interpretation: Round2 has the best recall and a better balance than the 30-epoch fine-tune, though corrected baseline still has the best precision.

## Implemented modules

- `src/metin2_research/training_dataset.py`
  - Builds YOLO retraining datasets from corrected labels, hard negatives, and augmentation candidates.

- `src/metin2_research/predict.py`
  - Runs annotation-free prediction on any screenshot/image.
  - Outputs state/report JSON and annotated preview.

- `src/metin2_research/live_try.py`
  - Screenshot-only live game testing.
  - Captures full screen or a crop region.
  - Runs detector and outputs advisory state.

- `src/metin2_research/actuator.py`
  - Safety-gated private-server actuator.
  - Dry-run by default.
  - Requires `--private-server-confirmed`.
  - Requires `--execute` to move/click.
  - Uses `pyautogui` if available, otherwise Windows `ctypes.user32` fallback.

## Policy behavior

`src/metin2_research/policy.py` now has three target-confidence levels:

- `confidence >= 0.60`: `APPROACH_OR_ATTACK`
- `0.35 <= confidence < 0.60`: `INVESTIGATE_TARGET`
- below candidate threshold / no target: `SEARCH` or `ROTATE_CAMERA`

## Live testing performed

Live screenshot-only detection correctly found a visible `Lv 5 Metin da Dor` in the private-server game window.

Representative live state:

`reports/live_game_test_policy_update/live_state.json`

Result:

- target_visible: true
- confidence: 0.533905
- target_xy: `[509.384, 320.982]`
- recommended_action: `INVESTIGATE_TARGET`

## Live click performed

A fresh live screenshot was captured and a target was clicked after user explicitly requested it.

Executed action plan:

`reports/live_game_click_attempt/action_plan_executed_ctypes.json`

Result:

- mode: execute
- recommended_action: `APPROACH_OR_ATTACK`
- intended_action: `CLICK_TARGET`
- screen_xy: `[1070, 337]`
- executed: true
- backend: `ctypes.user32`
- safety: `private_server_confirmed; standard OS input only; no bypass`

After-click verification capture:

`reports/live_game_click_attempt_after/live_preview.jpg`

After-click state:

`reports/live_game_click_attempt_after/live_state.json`

After-click detector result:

- target_visible: true
- boxes: 3
- best confidence: 0.659814
- recommended_action: `APPROACH_OR_ATTACK`
- target_xy: `[1135.483, 426.728]`

## Common commands

Live screenshot-only test:

```bash
cd "C:/Hermes Unreal/metin2_research"
PYTHONPATH=src python -m metin2_research.live_try \
  --yolov5-dir /tmp/metin2bot/yolov5 \
  --weights reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt \
  --trust-checkpoint \
  --out-dir reports/live_game_test \
  --region 0,0,1220,1020 \
  --count 1
```

Actuator dry run:

```bash
PYTHONPATH=src python -m metin2_research.actuator \
  reports/live_game_test/live_state.json \
  --private-server-confirmed \
  --out reports/live_game_test/action_plan_dry_run.json
```

Actuator execute:

```bash
PYTHONPATH=src python -m metin2_research.actuator \
  reports/live_game_test/live_state.json \
  --private-server-confirmed \
  --execute \
  --out reports/live_game_test/action_plan_executed.json
```

## Verification

Latest test command:

```bash
python -m pytest tests -q
```

Latest result:

`20 passed in 0.34s`
