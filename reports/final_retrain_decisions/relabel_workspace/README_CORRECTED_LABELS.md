# Draft Corrected Labels

Generated draft corrected YOLO labels for the 29 `fix_label` cases.

Color guide for `corrected_previews`:

- Green = original ground-truth label
- Red = detector prediction
- Cyan = draft corrected label written to `labels_corrected`

Important: these are draft corrections derived from your review decisions plus detector/ground-truth boxes. Review the cyan boxes before treating them as final training labels.

Generated files:

- `labels_corrected/` — 29 corrected YOLO `.txt` labels
- `corrected_previews/` — one preview per corrected label
- `corrected_previews_contact_sheet.jpg` — all corrected previews in one sheet
- `corrected_labels_manifest.csv` — method and output path for each correction
- `corrected_labels_summary.json` — counts by correction method

Validation:

- corrected label files: 29
- validation errors: 0

Next step:

1. Open `corrected_previews_contact_sheet.jpg`.
2. If cyan boxes look acceptable, use `labels_corrected` as corrected labels.
3. Then build the corrected dataset copy and rerun evaluation.
