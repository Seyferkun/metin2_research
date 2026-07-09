# Boss farm recording analysis: run-e792f8fc9dd2

## Bottom line

The recording confirms the user-reported 7-boss result via inventory loot deltas: `Cofre do Chefe Orc` increased from 31 to 38 during the run. This is stronger than the target-state evidence because the selected-target feed was short/stale, while the inventory count changed seven times.

## Evidence

- samples: 1111
- screenshots: 278 (mostly partial/window-chrome captures; not reliable visual proof)
- key_down_counts: `{'a': 3, 's': 6, 'd': 4, 'w': 2, 'z': 9, 'x': 3, 't': 3, 'r': 1, 'g': 1, 'space': 6, 'm': 1, 'e': 1}`
- mouse_down_counts: `{'left': 15, 'right': 15}`
- selected target samples: 8; visible state target was `Chefe Orc*` for a short window around 123.845s

### Loot-count increments

| t seconds | Cofre do Chefe Orc count | delta | nearby operator actions |
|---:|---:|---:|---|
| 44.779 | 31 → 32 | +1 | 38.2s key z; 45.5s right click; 46.6s left click |
| 60.312 | 32 → 33 | +1 | 53.2s key z; 61.1s right click |
| 77.528 | 33 → 34 | +1 | 70.6s key z; 78.8s right click; 80.6s left click |
| 95.212 | 34 → 35 | +1 | 87.5s key z; 95.5s right click; 98.8s right click |
| 109.464 | 35 → 36 | +1 | 101.9s key z; 102.6s key x; 103.9s left click; 105.0s right click; 110.7s left click; 111.8s right click; 112.8s left click |
| 126.910 | 36 → 37 | +1 | 126.1s key z; 126.9s key x; 127.9s left click |
| 157.693 | 37 → 38 | +1 | 150.4s left click; 151.7s key x; 159.0s right click |

Total confirmed boss loot delta: **7**.
Intervals between loot increments: [15.533, 17.216, 17.684, 14.252, 17.446, 30.783] seconds. Median interval: 17.3s.

## Learned loop hints

- Boss identity/loot: Chefe Orc; tracked loot item is `Cofre do Chefe Orc`, vnum `50070`.
- Practical kill confirmation: inventory count delta of `Cofre do Chefe Orc` is the cleanest counter for this farm.
- Pickup behavior: each confirmed count increment is usually preceded by `Z` within about 6–8 seconds; there were 9 total `Z` presses for 7 confirmed boxes.
- Channel/menu behavior: `X` appears at 102.647s, 126.910s, and 151.743s, with left-click followups after the first two; keep X→left-click as a channel/menu-change candidate, but needs better screenshots/window-relative capture before automation.
- Combat/action burst after 244s includes T/R/G/Space/M/E/W keys, but no additional Chefe Orc box increment followed in this recording segment.

## Caveats

- Screenshot capture was bad/partial: most frames show only the MT2Portugalia title/chrome or a thin strip, so visual UI/menu learning is limited.
- Target state only showed `Chefe Orc*` for 8 samples around 123.845–125.620s and reported hp=0/max=24618, so target HP is not reliable enough for kill timing.
- Mouse `window_relative` values are unusable here (`inside_window=false`, huge/incorrect window positions), so do not automate clicks from this run alone.

## Panel/tracker update

Update boss farm tracking to count positive deltas in `Cofre do Chefe Orc` / vnum 50070 as `boss_kills_confirmed`, `channels_cleared`, and `loot_pickups_observed`.