# Boss farm recording quality/fix analysis: run-f5c8d0c5c386

## Bottom line

The screenshot/mouse capture fix worked. This run has full gameplay/menu screenshots and usable mouse window-relative coordinates. The remaining gap is the JSON selected-target feed: it still reported zero target locks even though screenshots visibly show combat/target bars.

## Capture quality

- samples: 941
- screenshots: 236
- foreground samples: 680
- background samples: 261
- mouse clicks inside window: 17; outside window: 1
- contact sheet confirms full gameplay/menu frames, not title-bar-only frames.

## Boss/loot evidence

- Chefe Orc box count started at 38
- Chefe Orc box count ended at 41
- confirmed loot delta: 3

| t seconds | Cofre do Chefe Orc count | delta |
|---:|---:|---:|
| 52.801 | 38 → 39 | +1 |
| 69.196 | 39 → 40 | +1 |
| 86.193 | 40 → 41 | +1 |

## Remaining issue

- `target_lock_count` is still 0 and target sightings are 0 in JSON state.
- This means the target/client-state logger is not reliably exposing selected Chefe Orc target data during this workflow.
- Do not use target HP/name as the boss kill proof yet; use `Cofre do Chefe Orc` vnum 50070 deltas for confirmed counters.

## Next engineering fix

- Add a visual target-bar diagnostic or OCR fallback from the now-good screenshots.
- Keep mouse-learning from this run: most click coordinates are now inside-window and usable.
- Continue boss farm tracker counters from loot deltas.

Artifacts:
- quality JSON: `C:\Hermes Unreal\metin2_research\reports\player_training_runs\run-f5c8d0c5c386\boss_farm_fix_quality_analysis.json`
- contact sheet: `C:\Hermes Unreal\metin2_research\reports\player_training_runs\run-f5c8d0c5c386\boss_farm_fixed_capture_contact_sheet.jpg`
