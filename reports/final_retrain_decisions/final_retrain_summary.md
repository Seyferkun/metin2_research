# Final Easy Retrain Decisions

Source: `C:\Users\blade\Downloads\metin2_easy_retrain_answers.csv`

## Final buckets

- fix_label: 29
- hard_negative: 16
- label_ok: 25
- ignore: 1

## Main outputs

- `relabel_todo_with_instructions.csv`: C:\Hermes Unreal\metin2_research\reports\final_retrain_decisions\relabel_todo_with_instructions.csv
- `hard_negative_manifest.csv`: C:\Hermes Unreal\metin2_research\reports\final_retrain_decisions\hard_negative_manifest.csv
- `oversample_augment_manifest.csv`: C:\Hermes Unreal\metin2_research\reports\final_retrain_decisions\oversample_augment_manifest.csv
- `exclude_manifest.csv`: C:\Hermes Unreal\metin2_research\reports\final_retrain_decisions\exclude_manifest.csv
- `manual_recheck_manifest.csv`: C:\Hermes Unreal\metin2_research\reports\final_retrain_decisions\manual_recheck_manifest.csv

## Workspaces

- Relabel workspace: `C:\Hermes Unreal\metin2_research\reports\final_retrain_decisions\relabel_workspace`
- Hard-negative workspace: `C:\Hermes Unreal\metin2_research\reports\final_retrain_decisions\hard_negative_workspace`

## Recommended next order

1. Use `relabel_workspace` to correct the 29 labels.
2. Keep the 16 hard-negative cases for false-positive reduction.
3. Use the 25 label_ok cases for oversampling/augmentation.
4. Exclude the 1 ignored case.
5. Rerun evaluation after labels are corrected.
