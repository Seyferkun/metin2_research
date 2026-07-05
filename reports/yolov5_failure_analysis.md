# YOLOv5 Failure Analysis

- Evaluated images: 769
- Precision: 0.917738
- Recall: 0.928479
- Failure images: 71
- Failure best-IoU min/avg/max: 0.0 / 0.397681 / 0.927978

## Category counts

- localization_below_iou_threshold: 39
- duplicate_or_extra_detection: 16
- missed_detection: 11
- wrong_location_or_label_mismatch: 5

## FP/FN pattern counts

- fp1_fn1: 41
- fp1_fn0: 15
- fp0_fn1: 11
- fp2_fn1: 3
- fp2_fn0: 1

## Worst examples by category

### duplicate_or_extra_detection

- metin1693253953.jpg: FP=1 FN=0 best_iou=0.552546 preview=reports\yolov5_failures_full\metin1693253953_fp1_fn0_iou0.553.jpg
- metin1693299631.jpg: FP=1 FN=0 best_iou=0.642375 preview=reports\yolov5_failures_full\metin1693299631_fp1_fn0_iou0.642.jpg
- metin1693248269.jpg: FP=1 FN=0 best_iou=0.751202 preview=reports\yolov5_failures_full\metin1693248269_fp1_fn0_iou0.751.jpg
- metin1693195508.jpg: FP=1 FN=0 best_iou=0.76577 preview=reports\yolov5_failures_full\metin1693195508_fp1_fn0_iou0.766.jpg
- metin1693301960.jpg: FP=1 FN=0 best_iou=0.790339 preview=reports\yolov5_failures_full\metin1693301960_fp1_fn0_iou0.790.jpg
- metin1693223156.jpg: FP=1 FN=0 best_iou=0.794618 preview=reports\yolov5_failures_full\metin1693223156_fp1_fn0_iou0.795.jpg
- metin1693253898.jpg: FP=2 FN=0 best_iou=0.814247 preview=reports\yolov5_failures_full\metin1693253898_fp2_fn0_iou0.814.jpg
- metin1693196934.jpg: FP=1 FN=0 best_iou=0.824068 preview=reports\yolov5_failures_full\metin1693196934_fp1_fn0_iou0.824.jpg
- metin1693253042.jpg: FP=1 FN=0 best_iou=0.8323 preview=reports\yolov5_failures_full\metin1693253042_fp1_fn0_iou0.832.jpg
- metin1693298128.jpg: FP=1 FN=0 best_iou=0.845972 preview=reports\yolov5_failures_full\metin1693298128_fp1_fn0_iou0.846.jpg

### localization_below_iou_threshold

- metin1693217365.jpg: FP=1 FN=1 best_iou=0.018432 preview=reports\yolov5_failures_full\metin1693217365_fp1_fn1_iou0.018.jpg
- metin1693311180.jpg: FP=1 FN=1 best_iou=0.090774 preview=reports\yolov5_failures_full\metin1693311180_fp1_fn1_iou0.091.jpg
- metin1693243178.jpg: FP=1 FN=1 best_iou=0.147742 preview=reports\yolov5_failures_full\metin1693243178_fp1_fn1_iou0.148.jpg
- metin1693256029.jpg: FP=1 FN=1 best_iou=0.179319 preview=reports\yolov5_failures_full\metin1693256029_fp1_fn1_iou0.179.jpg
- metin1693219651.jpg: FP=1 FN=1 best_iou=0.182241 preview=reports\yolov5_failures_full\metin1693219651_fp1_fn1_iou0.182.jpg
- metin1693254177.jpg: FP=1 FN=1 best_iou=0.188597 preview=reports\yolov5_failures_full\metin1693254177_fp1_fn1_iou0.189.jpg
- metin1693310940.jpg: FP=1 FN=1 best_iou=0.281021 preview=reports\yolov5_failures_full\metin1693310940_fp1_fn1_iou0.281.jpg
- metin1693298978.jpg: FP=1 FN=1 best_iou=0.292374 preview=reports\yolov5_failures_full\metin1693298978_fp1_fn1_iou0.292.jpg
- metin1693189711.jpg: FP=1 FN=1 best_iou=0.308187 preview=reports\yolov5_failures_full\metin1693189711_fp1_fn1_iou0.308.jpg
- metin1693189322.jpg: FP=1 FN=1 best_iou=0.322404 preview=reports\yolov5_failures_full\metin1693189322_fp1_fn1_iou0.322.jpg

### missed_detection

- metin1693198447.jpg: FP=0 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693198447_fp0_fn1_iou0.000.jpg
- metin1693199801.jpg: FP=0 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693199801_fp0_fn1_iou0.000.jpg
- metin1693217007.jpg: FP=0 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693217007_fp0_fn1_iou0.000.jpg
- metin1693217490.jpg: FP=0 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693217490_fp0_fn1_iou0.000.jpg
- metin1693217694.jpg: FP=0 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693217694_fp0_fn1_iou0.000.jpg
- metin1693220779.jpg: FP=0 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693220779_fp0_fn1_iou0.000.jpg
- metin1693223784.jpg: FP=0 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693223784_fp0_fn1_iou0.000.jpg
- metin1693246927.jpg: FP=0 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693246927_fp0_fn1_iou0.000.jpg
- metin1693248503.jpg: FP=0 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693248503_fp0_fn1_iou0.000.jpg
- metin1693304309.jpg: FP=0 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693304309_fp0_fn1_iou0.000.jpg

### wrong_location_or_label_mismatch

- metin1693192762.jpg: FP=2 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693192762_fp2_fn1_iou0.000.jpg
- metin1693195194.jpg: FP=1 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693195194_fp1_fn1_iou0.000.jpg
- metin1693195880.jpg: FP=1 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693195880_fp1_fn1_iou0.000.jpg
- metin1693224621.jpg: FP=1 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693224621_fp1_fn1_iou0.000.jpg
- metin1693305670.jpg: FP=1 FN=1 best_iou=0.0 preview=reports\yolov5_failures_full\metin1693305670_fp1_fn1_iou0.000.jpg
