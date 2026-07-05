# Metin2 Research Sandbox Harness

This is a safe, read-only research harness for studying Metin2 screenshot perception and advisory decision policies.

It does not run the original bot, does not launch a game client, and does not inject mouse/keyboard input.

## What it does

- Scans YOLO-style image/annotation datasets from the cloned `metin2bot` repo.
- Parses YOLO annotation rows into pixel-space bounding boxes.
- Produces a JSON report with dataset counts and sample symbolic policy decisions.
- Provides a simple advisory policy that returns action names such as `USE_HEAL`, `SEARCH`, or `APPROACH_OR_ATTACK`.

## Run tests

```bash
python -m pytest tests -q
```

## Generate report

Either run with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python -m metin2_research.report /tmp/metin2bot --out reports/metin2bot_summary.json
```

Or install the local package and use the console script:

```bash
python -m pip install -e .
metin2-report /tmp/metin2bot --out reports/metin2bot_summary.json
```

On this Windows/Git-Bash Hermes session, `/tmp/metin2bot` resolves to:

`C:/Users/blade/AppData/Local/Temp/metin2bot`

## Screenshot -> state -> recommendation

Generate state JSON and an annotated preview from one YOLO-annotated screenshot:

```bash
PYTHONPATH=src python -m metin2_research.screenshot_state \
  /tmp/metin2bot/metin_farm_bot/classifier/ervelia/metin120/images/metin1693185355.jpg \
  --state-out reports/sample_state.json \
  --preview-out reports/sample_annotated.jpg
```

This outputs a symbolic state with fields like `target_visible`, `target_xy`, `target_box`, and `recommended_action`.

## Detector evaluation

Run the repo's local YOLOv5 checkpoint on one annotated screenshot and compare it to ground truth:

```bash
PYTHONPATH=src python -m metin2_research.detector \
  /tmp/metin2bot/metin_farm_bot/classifier/ervelia/metin120/images/metin1693185355.jpg \
  --yolov5-dir /tmp/metin2bot/yolov5 \
  --weights /tmp/metin2bot/metin_farm_bot/ml/data/yolo/best.pt \
  --trust-checkpoint \
  --state-out reports/detector_sample_state.json \
  --preview-out reports/detector_sample_annotated.jpg \
  --eval-out reports/detector_sample_eval.json
```

Run a small dataset evaluation:

```bash
PYTHONPATH=src python -m metin2_research.detector \
  /tmp/metin2bot/metin_farm_bot/classifier/ervelia/metin120/images \
  --yolov5-dir /tmp/metin2bot/yolov5 \
  --weights /tmp/metin2bot/metin_farm_bot/ml/data/yolo/best.pt \
  --trust-checkpoint \
  --limit 10 \
  --eval-out reports/yolov5_eval_limit10.json
```

Run the full annotated dataset and save FP/FN preview images:

```bash
PYTHONPATH=src python -m metin2_research.detector \
  /tmp/metin2bot/metin_farm_bot/classifier/ervelia/metin120/images \
  --yolov5-dir /tmp/metin2bot/yolov5 \
  --weights /tmp/metin2bot/metin_farm_bot/ml/data/yolo/best.pt \
  --trust-checkpoint \
  --failure-dir reports/yolov5_failures_full \
  --eval-out reports/yolov5_eval_full.json
```

Failure previews use green boxes for ground truth and red boxes for detector predictions.

Group the failure previews by likely cause and create contact sheets:

```bash
PYTHONPATH=src python -m metin2_research.failure_analysis \
  reports/yolov5_eval_full.json \
  --json-out reports/yolov5_failure_analysis.json \
  --markdown-out reports/yolov5_failure_analysis.md \
  --contact-sheet-dir reports/yolov5_failure_contact_sheets
```

Create a human-review manifest and categorized review folders:

```bash
PYTHONPATH=src python -m metin2_research.review_manifest \
  reports/yolov5_eval_full.json \
  --json-out reports/failure_review_manifest.json \
  --csv-out reports/failure_review_manifest.csv \
  --review-dir reports/review
```

Open the CSV in a spreadsheet and update `status` / `notes` while reviewing the preview images. Suggested statuses: `label_ok`, `fix_label`, `hard_negative`, `ignore`, `uncertain`.

`--trust-checkpoint` is required because old YOLOv5 `.pt` files use PyTorch pickle loading. Only use it with local weights you trust inside the research sandbox.

## Build the retraining dataset

After correcting labels and reviewing hard negatives, build a YOLO training dataset:

```bash
PYTHONPATH=src python -m metin2_research.training_dataset \
  reports/corrected_dataset_draft/images \
  --out reports/yolo_easy_retrain_dataset \
  --corrected-labels-dir reports/final_retrain_decisions/relabel_workspace/labels_corrected \
  --hard-negative-manifest reports/final_retrain_decisions/hard_negative_manifest.csv \
  --oversample-manifest reports/final_retrain_decisions/oversample_augment_manifest.csv \
  --eval-report reports/corrected_dataset_draft/yolov5_eval_corrected_draft.json \
  --oversample-copies 1
```

This creates `dataset.yaml`, train/val image-label folders, corrected labels, hard-negative crops with empty labels, and oversampled hard-but-valid examples.

## Client Python TSV state source

For Yoshy's controlled MT2Portugalia benchmark, the preferred live feedback source is the local read-only client Python logger:

`D:/Games/MT2Portugalia/app/hermes_state.tsv`

Probe normalized state from it:

```bash
PYTHONPATH='src;.' python scripts/probe_client_state.py --no-process --out reports/client_state_probe/latest_client_state.json
```

The resulting `ClientState.game` contains map name, player x/y/z, HP/SP, player name, target VID, and target name when selected. TSV v2 parsing also accepts optional buff state, buff remaining seconds, and a JSON `nearby_entities` list when the client logger exports them. This source is local-file read-only and should be preferred over OCR/memory fallbacks when present and fresh.

Run the conservative Metin combat state machine in dry-run mode:

```bash
PYTHONPATH='src;.' python scripts/combat_metin_client_state.py \
  --metin-name 'Metin da Batalha' \
  --metin-vid 2752330 \
  --metin-x 82327 \
  --metin-y 70834 \
  --max-cycles 5
```

Pass `--live` only in Yoshy's controlled sandbox after confirming the dry-run decisions. The combat policy treats target switching to spawned mobs as `KILL_ADDS`, refreshes/potions before attacking, returns/reacquires when the Metin still exists, and only reports success when the Metin entity is absent after reward evidence.

Learn movement using only the client TSV coordinate feedback path:

```bash
PYTHONPATH='src;.' python scripts/learn_runaround_client_tsv.py \
  --cycles 2 \
  --seconds 0.5 \
  --pause 0.6 \
  --out-jsonl reports/client_tsv_runaround/live_movement_observations_2cycle.jsonl \
  --model-out reports/client_tsv_runaround/live_navigation_model_2cycle.json
```

This bounded run-around calibration presses only WASD, reads `ClientState.game.player_coord` after each move, and writes per-key movement deltas/navigation model JSON. It intentionally does not use screenshots, target clicks, Metin detection, memory label scans, or combat logic.

## Safety boundary

Keep this harness perception/advisory-first. Do not wire it to public-server automation or anti-cheat bypass. If action execution is needed, restrict it to a private research sandbox you own/control.
