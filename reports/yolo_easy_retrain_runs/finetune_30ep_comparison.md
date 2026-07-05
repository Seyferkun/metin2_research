# YOLO Easy Retrain Evaluation Comparison

| Metric | Original labels | Corrected baseline | Smoke 1ep | Fine-tune 30ep | 30ep vs corrected |
|---|---:|---:|---:|---:|---:|
| evaluated_images | 769 | 769 | 769 | 769 | 0 |
| true_positives | 714 | 740 | 729 | 741 | 1 |
| false_positives | 64 | 38 | 37 | 50 | 12 |
| false_negatives | 55 | 29 | 40 | 28 | -1 |
| precision | 0.917738 | 0.951157 | 0.951697 | 0.936789 | -0.014368000000000047 |
| recall | 0.928479 | 0.962289 | 0.947984 | 0.963589 | 0.0013000000000000789 |
| failure_preview_count | 71 | 46 | 49 | 53 | 7 |

## YOLOv5 internal validation

- result rows: 43
- best mAP50 row epoch: 17 mAP50=0.94294 precision=0.95447 recall=0.94783
- final row epoch: 29 mAP50=0.92459 precision=0.93097 recall=0.93824
