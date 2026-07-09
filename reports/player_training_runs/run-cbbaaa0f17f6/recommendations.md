# Player training analysis

Events: `reports\player_training_runs\run-cbbaaa0f17f6\events.jsonl`
Samples: 2299
Target locks: 1
Destroy candidates: 1

## Key down counts
- 1: 1
- a: 9
- ctrl+g: 5
- d: 3
- e: 23
- f: 4
- f1: 2
- f2: 1
- g: 9
- m: 3
- q: 29
- r: 2
- s: 7
- space: 20
- t: 11
- w: 8
- x: 7
- z: 32

## Mouse down counts
- left: 22
- right: 3

## Learned click coordinates
- Target left-click avg window-relative: [0.2509, 0.8371]
- Target left-click avg window-pos: [402.0, 863.0]
  - t=408.048 rel=[0.2509, 0.8371] window=[402.0, 863.0] screen=[562.0, 858.0] after_target=6.467
- Channel/menu follow-up avg window-relative: [0.3849, 0.4396]
- Channel/menu follow-up avg window-pos: [616.5, 453.25]
  - t=34.479 rel=[0.4014, 0.4287] window=[643.0, 442.0] screen=[803.0, 437.0] after_x=2.084
  - t=191.903 rel=[0.3664, 0.485] window=[587.0, 500.0] screen=[747.0, 495.0] after_x=2.348
  - t=257.549 rel=[0.3733, 0.5034] window=[598.0, 519.0] screen=[758.0, 514.0] after_x=1.828
  - t=458.481 rel=[0.3983, 0.3414] window=[638.0, 352.0] screen=[798.0, 347.0] after_x=2.317

## Recommendations
- attack_start: after a selected Metin appears, start bounded Space attack pulses near this delay instead of passively monitoring
- mouse_targeting: compare left-click positions against target bars/Metin body to learn player-like mouse selection
- pickup_after_destroy: after target disappearance, begin Z pickup spam near this delay
- camera_search: use player-like short camera sweeps when no Metin is targetable
