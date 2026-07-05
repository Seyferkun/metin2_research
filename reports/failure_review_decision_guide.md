# Metin2 Failure Review Decision Guide

Use this guide while reviewing `failure_review_dashboard.html` or `failure_review_manifest.csv`.

Green box = ground-truth annotation.
Red box = YOLOv5 detector prediction.

## Primary decision rule

For each case, answer this in order:

1. Is the green box correct?
2. Is the red box detecting a real Metin stone?
3. Is the red box close enough to the green box for gameplay usefulness?
4. Would this image help the next training run?

Then choose one status:

- `label_ok`
- `fix_label`
- `hard_negative`
- `ignore`
- `uncertain`

---

## Status definitions

### `label_ok`

Use when the annotation is correct and the failure is mainly the detector/model's fault.

Choose this if:

- green box correctly surrounds the Metin stone
- red box misses the stone
- red box is too shifted or too loose/tight
- the object is small/dark/partially hidden but still genuinely labeled correctly
- this is a useful example for improving the detector

Typical next action:

- keep image in training/evaluation
- oversample if missed or difficult
- augment if dark/low contrast/small object

Good for categories:

- `missed_detection`
- `localization_below_iou_threshold`

---

### `fix_label`

Use when the green box is wrong, unclear, missing, or inconsistent.

Choose this if:

- green box is not around the Metin stone
- green box marks only a tiny/odd part of the object
- green box is much too large or too small compared with the labeling style
- there are multiple valid Metin stones but only the wrong one is labeled
- red box looks correct but is punished because the annotation is wrong

Typical next action:

- relabel the image manually
- correct the `.txt` annotation
- rerun evaluation after fixing labels

Good for categories:

- `wrong_location_or_label_mismatch`
- suspicious `localization_below_iou_threshold`

---

### `hard_negative`

Use when the red box detects something that is NOT a Metin stone but looks confusingly similar.

Choose this if:

- red box is on a crystal, spell effect, pillar, UI-like decoration, terrain shape, mob, or glow
- green box is correct, but the extra red detection is clearly not a target
- the false positive would cause bad gameplay decisions

Typical next action:

- add image/region as a hard negative
- include more examples of confusing non-target objects
- tune confidence/NMS after retraining

Good for categories:

- `duplicate_or_extra_detection`

---

### `ignore`

Use when the case should not influence training.

Choose this if:

- screenshot is corrupted or visually unusable
- the object is impossible to judge
- UI/menu covers the important region
- the annotation is ambiguous and not worth fixing
- the frame is not representative of real target selection

Typical next action:

- exclude from improvement set
- optionally keep out of evaluation too

---

### `uncertain`

Use when you cannot confidently decide yet.

Choose this if:

- you need to zoom in
- it is unclear whether the object is a Metin stone
- both label and detector seem plausible
- the scene contains multiple candidates and you need game knowledge

Typical next action:

- add a note explaining the doubt
- revisit after reviewing similar cases

---

## Category-specific rules

### 1. `localization_below_iou_threshold`

This means the detector and annotation overlap, but IoU is below the threshold.

Decision flow:

1. If green box is obviously wrong or inconsistent → `fix_label`
2. Else if red box is on the correct stone but shifted/tall/loose → `label_ok`
3. Else if red box is on a different object → `hard_negative` or `fix_label`, depending on whether green is correct
4. If both boxes seem acceptable for gameplay → `label_ok`, and note `maybe_lower_iou_threshold`

Most likely statuses:

- `label_ok`
- `fix_label`
- `uncertain`

What to write in notes:

- `red too low`
- `red too tall`
- `green too tight`
- `green labels only core`
- `acceptable for gameplay despite IoU`

---

### 2. `duplicate_or_extra_detection`

This means the correct target was found, but there is at least one extra red box.

Decision flow:

1. Is the extra red box on a real unlabeled Metin stone?
   - yes → `fix_label`
2. Is the extra red box on a non-target object/effect?
   - yes → `hard_negative`
3. Is the extra red box harmless and far away?
   - maybe `label_ok`, note `minor duplicate`
4. Is the scene too ambiguous?
   - `uncertain`

Most likely statuses:

- `hard_negative`
- `fix_label`
- `label_ok`

What to write in notes:

- `extra red on blue crystal`
- `extra red on spell effect`
- `possible unlabeled target`
- `duplicate same target`

---

### 3. `missed_detection`

This means there is a green label but no matching red detection.

Decision flow:

1. Is green box a real Metin stone and correctly placed?
   - yes → `label_ok`
2. Is the object tiny/dark/occluded but valid?
   - yes → `label_ok`, note `oversample small/dark`
3. Is the green box not a real target?
   - `fix_label` or `ignore`
4. Is the screenshot/menu/UI making detection unrealistic?
   - `ignore` or `uncertain`

Most likely statuses:

- `label_ok`
- `ignore`
- `fix_label`

What to write in notes:

- `small distant target`
- `low contrast`
- `occluded`
- `near screen edge`
- `covered by menu/UI`

---

### 4. `wrong_location_or_label_mismatch`

This means red and green have zero overlap.

Decision flow:

1. Is green box on the correct Metin stone?
   - yes → red is wrong; choose `hard_negative` if red is on a confusing non-target, otherwise `label_ok`
2. Is red box on the correct Metin stone and green is elsewhere?
   - choose `fix_label`
3. Are there multiple Metin-like objects and only one is labeled?
   - choose `fix_label` if the red one should also be labeled
4. If unclear → `uncertain`

Most likely statuses:

- `fix_label`
- `hard_negative`
- `uncertain`

What to write in notes:

- `green wrong location`
- `red on plausible unlabeled stone`
- `red on non-target`
- `multiple candidates`

---

## Fast-review workflow

Review in this order:

1. `wrong_location_or_label_mismatch`
   - only 5 cases
   - highest chance of annotation problems

2. `duplicate_or_extra_detection`
   - 16 cases
   - mark hard negatives or missing labels

3. `missed_detection`
   - 11 cases
   - decide whether to oversample or ignore

4. `localization_below_iou_threshold`
   - 39 cases
   - decide whether labels need fixing or IoU threshold is too strict

---

## Recommended target outcome

After review, aim for these buckets:

- `fix_label`: annotation corrections before retraining
- `hard_negative`: confusing false-positive examples
- `label_ok`: difficult true examples for oversampling/augmentation
- `ignore`: remove from improvement loop
- `uncertain`: manually revisit later

If most localization cases look gameplay-acceptable, consider evaluating at IoU 0.4 in addition to 0.5.
If many duplicate cases are real unlabeled stones, fix annotations before retraining.
If many missed cases are small/dark, oversample and add contrast/scale augmentation.
