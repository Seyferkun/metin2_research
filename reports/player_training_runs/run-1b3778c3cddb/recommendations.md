# Player training analysis

Events: `reports\player_training_runs\run-1b3778c3cddb\events.jsonl`
Samples: 1147
Target locks: 2
Destroy candidates: 1

## Key down counts
- a: 2
- ctrl+g: 1
- d: 1
- e: 2
- f1: 1
- g: 1
- tab: 1
- w: 6

## Mouse down counts
- left: 66
- right: 5

## Learned click coordinates
- Target left-click avg window-relative: [0.4109, 0.4655]
- Target left-click avg window-pos: [658.2273, 479.9091]
  - t=176.249 rel=[0.1423, 0.355] window=[228.0, 366.0] screen=[388.0, 361.0] after_target=23.747
  - t=177.002 rel=[0.1017, 0.0601] window=[163.0, 62.0] screen=[323.0, 57.0] after_target=24.5
  - t=179.618 rel=[0.6049, 0.6479] window=[969.0, 668.0] screen=[1129.0, 663.0] after_target=27.116
  - t=180.667 rel=[0.6773, 0.7187] window=[1085.0, 741.0] screen=[1245.0, 736.0] after_target=28.165
  - t=181.97 rel=[0.7097, 0.7042] window=[1137.0, 726.0] screen=[1297.0, 721.0] after_target=29.468
  - t=184.058 rel=[0.6998, 0.6644] window=[1121.0, 685.0] screen=[1281.0, 680.0] after_target=31.556
  - t=194.503 rel=[0.6361, 0.5975] window=[1019.0, 616.0] screen=[1179.0, 611.0] after_target=42.001
  - t=212.767 rel=[0.6848, 0.4394] window=[1097.0, 453.0] screen=[1257.0, 448.0] after_target=60.265
  - t=251.114 rel=[0.3976, 0.3695] window=[637.0, 381.0] screen=[797.0, 376.0] after_target=2.895
  - t=257.083 rel=[0.3564, 0.3938] window=[571.0, 406.0] screen=[731.0, 401.0] after_target=8.864

## Recommendations
- mouse_targeting: compare left-click positions against target bars/Metin body to learn player-like mouse selection
- camera_search: use player-like short camera sweeps when no Metin is targetable
