# Round-2 Hard-Negative Fine-Tune Comparison

| Metric | Original | Corrected baseline | Smoke 1ep | Fine-tune 30ep | Round2 lowLR 10ep | Round2 vs corrected | Round2 vs 30ep |
|---|---:|---:|---:|---:|---:|---:|---:|
| evaluated_images | 769 | 769 | 769 | 769 | 769 | 0 | 0 |
| true_positives | 714 | 740 | 729 | 741 | 745 | 5 | 4 |
| false_positives | 64 | 38 | 37 | 50 | 44 | 6 | -6 |
| false_negatives | 55 | 29 | 40 | 28 | 24 | -5 | -4 |
| precision | 0.917738 | 0.951157 | 0.951697 | 0.936789 | 0.944233 | -0.006924000000000041 | 0.007444000000000006 |
| recall | 0.928479 | 0.962289 | 0.947984 | 0.963589 | 0.968791 | 0.006502000000000008 | 0.005201999999999929 |
| failure_preview_count | 71 | 46 | 49 | 53 | 48 | 2 | -5 |

## YOLOv5 internal validation

- result rows: 10
- best mAP50 row epoch: 5 mAP50=0.93307 precision=0.92907 recall=0.93913
- final row epoch: 9 mAP50=0.91787 precision=0.93348 recall=0.93913
