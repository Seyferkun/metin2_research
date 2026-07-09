# Player training analysis

Events: `reports\player_training_runs\run-a6d380a03695\events.jsonl`
Samples: 1909
Target locks: 2
Destroy candidates: 1

## Key down counts
- d: 1
- e: 1
- w: 1

## Mouse down counts
- left: 250
- right: 2

## Learned click coordinates
- Target left-click avg window-relative: [0.6982, 0.6486]
- Target left-click avg window-pos: [1118.468, 668.668]
  - t=10.013 rel=[0.6998, 0.6566] window=[1121.0, 677.0] screen=[1281.0, 672.0] after_target=9.955
  - t=12.113 rel=[0.6998, 0.6566] window=[1121.0, 677.0] screen=[1281.0, 672.0] after_target=12.055
  - t=18.4 rel=[0.6998, 0.6566] window=[1121.0, 677.0] screen=[1281.0, 672.0] after_target=18.342
  - t=35.165 rel=[0.6998, 0.6566] window=[1121.0, 677.0] screen=[1281.0, 672.0] after_target=35.107
  - t=36.7 rel=[0.6998, 0.6566] window=[1121.0, 677.0] screen=[1281.0, 672.0] after_target=36.642
  - t=37.5 rel=[0.6998, 0.6566] window=[1121.0, 677.0] screen=[1281.0, 672.0] after_target=37.442
  - t=38.283 rel=[0.6998, 0.6566] window=[1121.0, 677.0] screen=[1281.0, 672.0] after_target=38.225
  - t=41.916 rel=[0.6998, 0.6566] window=[1121.0, 677.0] screen=[1281.0, 672.0] after_target=41.858
  - t=50.928 rel=[0.6998, 0.6566] window=[1121.0, 677.0] screen=[1281.0, 672.0] after_target=50.87
  - t=56.65 rel=[0.6998, 0.6566] window=[1121.0, 677.0] screen=[1281.0, 672.0] after_target=56.592

## Recommendations
- mouse_targeting: compare left-click positions against target bars/Metin body to learn player-like mouse selection
- camera_search: use player-like short camera sweeps when no Metin is targetable
