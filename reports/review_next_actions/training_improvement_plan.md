# Metin2 Training Improvement Plan

Source decisions: `C:\Hermes Unreal\metin2_research\reports\failure_review_decisions_continued.csv`

## Final status counts

- fix_label: 29
- label_ok: 23
- hard_negative: 15
- uncertain: 3
- ignore: 1

## Recommended action buckets

- Relabel/fix annotations: 29 rows → `relabel_todo.csv`
- Hard negatives for false positives: 15 rows → `hard_negative_set.csv`
- Oversample/augment difficult valid labels: 22 rows → `oversample_or_augment.csv`
- Manual recheck: 3 rows → `manual_recheck.csv`
- Excluded/ignored: 1 rows → `excluded.csv`

## Category × status

- duplicate_or_extra_detection / fix_label: 2
- duplicate_or_extra_detection / hard_negative: 13
- duplicate_or_extra_detection / label_ok: 1
- localization_below_iou_threshold / fix_label: 22
- localization_below_iou_threshold / hard_negative: 1
- localization_below_iou_threshold / label_ok: 13
- localization_below_iou_threshold / uncertain: 3
- missed_detection / hard_negative: 1
- missed_detection / ignore: 1
- missed_detection / label_ok: 9
- wrong_location_or_label_mismatch / fix_label: 5

## Recommended order

1. Fix the 29 annotation issues first; otherwise detector metrics remain misleading.
2. Add the 15 hard-negative cases to reduce false positives on crystals/effects/terrain.
3. Oversample or augment the 22 difficult valid cases, especially missed/small/low-contrast Metins.
4. Manually recheck the 3 uncertain cases before using them for training.
5. Exclude the ignored case from improvement experiments unless you later decide it is valid.
