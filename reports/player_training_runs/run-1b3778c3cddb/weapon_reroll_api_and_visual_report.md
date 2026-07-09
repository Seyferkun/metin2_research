# Weapon reroll API + visual report

Recording: `run-1b3778c3cddb`

## What was captured

- Samples: 1147
- Screenshots: 172 non-black usable frames found from `events.jsonl`
- Left clicks: 66
- Contact sheets generated:
  - `weapon_api_visual_analysis/weapon_reroll_sheet_1.jpg`
  - `weapon_api_visual_analysis/weapon_reroll_sheet_2.jpg`
  - `weapon_api_visual_analysis/weapon_reroll_sheet_3.jpg`
  - `weapon_api_visual_analysis/weapon_reroll_sheet_4.jpg`

## API status

The live client API instrumentation is now active in the current `hermes_state.json` and exports `inventory` with raw attr type/value pairs.

However, this recording's `events.jsonl` does **not** contain those inventory fields because the recorder sanitizer was still stripping `inventory` / `equipped_weapon` at record time. I fixed the recorder after analyzing this run so future recordings preserve raw API item attrs.

Current live API state contains actual item attr values, but it no longer contains the hovered `Lança Fénix+9`; the latest raw item with attrs when I snapped the API was:

```json
{
  "slot": 0,
  "vnum": 13000,
  "name": "Escudo de Batalha+0",
  "attrs": [
    {"index": 0, "type": 19, "value": 20},
    {"index": 1, "type": 18, "value": 10},
    {"index": 2, "type": 22, "value": 4},
    {"index": 3, "type": 43, "value": 6}
  ],
  "sockets": []
}
```

Saved raw snapshot:
`current_api_inventory_snapshot.json`

Likely decoded as:

| raw type | value | likely meaning |
|---:|---:|---|
| 19 | 20 | Forte contra Orcs +20% |
| 18 | 10 | Forte contra Animais +10% |
| 22 | 4 | Forte contra Demónios +4% |
| 43 | 6 | another bonus type, not yet mapped confidently |

## Visual analysis of this recording

The hovered weapon visible in the reroll section is again:

- `Lança Fénix+9`
- Nível mínimo: 30
- Valor de Ataque: 93 - 107
- Rapidez de Ataque: +15%

Because raw API inventory was stripped from this recording, the values below are visual/observed, not authoritative API rows.

### Readable observed roll families

| Stat family | Values clearly/partly observed in this run | Confidence |
|---|---|---|
| Dano Médio | negative values around -26, -24, -20, -16, -15, -10, -7; positive values including about +5, +6, +10, +11, +15, +24, +34, and high positives around +43 | medium; some signs/digits are small |
| Dano de Habilidade | several negative and positive values, commonly around -29 to +14 visually | low-medium; red text is hard to read |
| Forte contra Demónios | +6, +10, +20 appear | medium |
| Forte contra Mortos-Vivos | +6, +10, +20 appear | medium |
| Forte contra Orcs | +6, +10 and possibly +20 appear | medium |
| Forte contra Esotéricos | +6, +10 appear | medium |
| Forte contra Animais | +10, +20 appear | medium |
| Forte contra Semi-Humanos | +5 and +10 appear | medium |
| Chance de Golpes Críticos | +3 and +10 appear | medium |
| Chance de Golpes Perfurantes | +3, +5, +10 appear | medium |
| Probabilidade de Atordoamento | +3, +5, +8 appear | medium |
| Destreza | +6/+8-ish and +12-like values appear | low-medium |
| Inteligência | +6/+8-ish values appear | low-medium |
| Vitalidade | +8 appears | low-medium |
| Rapidez de Feitiço | +10 appears | low-medium |

### Best visual Dano Médio seen

This new recording gives much better evidence for high positive `Dano Médio` than the previous run. I saw multiple frames around sheet 2 crops 40-42 with `Dano Médio` looking around `+34%` to `+43%`.

I still do **not** want to claim true max/min from screenshots. The next recording will preserve API item attrs, which should give exact type/value rows per sample.

## Fix made after this run

Patched `scripts/player_training_recorder.py` so future recordings keep sanitized item API fields:

- `inventory[].slot`
- `inventory[].vnum`
- `inventory[].count`
- `inventory[].name`
- `inventory[].attrs[].index/type/value`
- `inventory[].sockets`
- `equipped_weapon` with the same fields

This means next reroll recording should allow an actual API table instead of OCR/vision guessing.
