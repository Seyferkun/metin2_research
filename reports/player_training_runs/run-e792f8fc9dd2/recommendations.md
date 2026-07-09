# Player training analysis

Events: `reports\player_training_runs\run-e792f8fc9dd2\events.jsonl`
Samples: 1111
Target locks: 1
Destroy candidates: 1

## Key down counts
- a: 3
- d: 4
- e: 1
- g: 1
- m: 1
- r: 1
- s: 6
- space: 6
- t: 3
- w: 2
- x: 3
- z: 9

## Mouse down counts
- left: 15
- right: 15

## Learned click coordinates
- Channel/menu follow-up avg window-relative: [176.5541, 1009.1875]
- Channel/menu follow-up avg window-pos: [40784.0, 40367.5]
  - t=103.931 rel=[176.6104, 1009.6] window=[40797.0, 40384.0] screen=[806.0, 384.0] after_x=1.284
  - t=127.943 rel=[176.4978, 1008.775] window=[40771.0, 40351.0] screen=[780.0, 351.0] after_x=1.033

## Recommendations
- pickup_after_destroy: after target disappearance, begin Z pickup spam near this delay
- camera_search: use player-like short camera sweeps when no Metin is targetable
