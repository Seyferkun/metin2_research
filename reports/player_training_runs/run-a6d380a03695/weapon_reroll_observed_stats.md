# Weapon reroll observed stats

Source recording: `run-a6d380a03695`

Evidence files:
- `weapon_reroll_montage.jpg`
- `weapon_tooltip_crops_contact.jpg`
- `weapon_tooltip_zoom_contact.jpg`
- `weapon_tooltip_crops_manifest.json`

Important caveats:
- This is visual extraction from screenshots, not a server/drop-table proof.
- The recording has 250 left-clicks, but screenshots were sampled periodically and some frames are black, so very fast intermediate rolls may be missed.
- Text is small/anti-aliased; uncertain values are marked approximate/uncertain in the chat summary.
- Base weapon/sockets are ignored for roll analysis.

Weapon seen:
- Lança Fénix+9
- Nível mínimo: 30
- Valor de Ataque: 93 - 107
- Rapidez de Ataque: +15%

Observed rerollable stat families and observed ranges from readable crops:

| Stat | Observed values | Observed min | Observed max | Confidence |
|---|---:|---:|---:|---|
| Dano Médio | observed readable positives include about +3, +4, +5, +8, +10, +11, +13, +15, +16, +20, +22, +23; observed negatives include about -49, -22, -17, -15, -10, -1 | about -49 | about +23 observed | medium; earlier +49 was a visual/OCR misread of a +4% frame |
| Dano de Habilidade | about -29, -19, -6, -5, -3, -1, +1, +2, +5, +10, +12 | about -29 | about +12 observed | medium; not authoritative max |
| Forte contra Demónios | +6, +10, +20 | +6 | +20 | medium-high |
| Forte contra Mortos-Vivos | +6, +10, +20 | +6 | +20 | medium-high |
| Forte contra Orcs | +6, +10 | +6 | +10 observed | medium |
| Forte contra Esotéricos | +6, +10 | +6 | +10 observed | medium |
| Forte contra Animais | +10, +20 | +10 | +20 | medium |
| Forte contra Semi-Humanos | +5 | +5 | +5 observed | medium |
| Chance de Golpes Críticos | +3, +10 | +3 | +10 | medium |
| Chance de Golpes Perfurantes | +5, +10 | +5 | +10 | medium |
| Probabilidade de Atordoamento | +3, +5, +8 | +3 | +8 | medium |
| Força | +6, +8 | +6 | +8 | medium |
| Destreza | +4, +6, +12 | +4 | +12 | medium |
| Inteligência | +6 | +6 | +6 observed | medium |
| Vitalidade | +8 | +8 | +8 observed | medium |
| Rapidez de Feitiço | +10 | +10 | +10 observed | medium-low |

Recommended next step:
- Add dedicated weapon-reroll tracking mode to the recorder that crops the tooltip area on every click, keeps a screenshot per roll, and optionally instruments the client tooltip text directly. That would make min/max much more reliable than visual reading from periodic screenshots.
