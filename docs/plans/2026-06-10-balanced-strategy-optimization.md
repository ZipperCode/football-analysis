# Balanced Strategy Optimization Report

## Scope

- Optimization profile: balanced high-yield.
- Data source: football-data.co.uk historical CSV.
- League: I1.
- Seasons loaded: 0506 through 2526.
- Warm-up seasons: 0506 through 0910.
- Discovery window: 1011 through 1819.
- Holdout window: 1920 through 2526.

The production candidate still comes from the Asian handicap away-value family. The long-horizon scanner now also covers `asian-home`, `market-home`, and `market-away` so rejected or paper-only families are backed by reproducible evidence instead of manual notes.

## Previous Long-Horizon Baseline

Current I1 AH away fixed parameters over 0506 through 2526:

- Bets: 642
- Profit: +40.30u
- ROI: +6.28%
- Average CLV: +5.97%

## Balanced Candidate

Candidate:

- mode: `asian_value`
- selection bias: `ah_away`
- season phase: `middle`
- min edge: `0.025`
- min odds: `1.80`
- max odds: `2.70`
- min matches: `5`
- min strength: `0.30`
- max bets per season: `35`

Long-horizon result, discovery plus holdout:

- Bets: 274
- Profit: +46.625u
- ROI: +17.02%
- Positive active seasons: 11/16
- Worst active season ROI: -28.60%

Discovery window:

- Bets: 137
- Profit: +17.605u
- ROI: +12.85%
- Positive active seasons: 6/9
- Worst active season ROI: -28.60%

Holdout window:

- Bets: 137
- Profit: +29.020u
- ROI: +21.18%
- Positive active seasons: 5/7
- Average CLV: +1.64%
- Worst active season ROI: -9.78%

## Holdout Season Detail

| Season | Bets | Profit | ROI | CLV |
| --- | ---: | ---: | ---: | ---: |
| 1920 | 19 | +4.265u | +22.45% | +1.06% |
| 2021 | 20 | +1.170u | +5.85% | +1.23% |
| 2122 | 17 | +4.800u | +28.24% | +2.69% |
| 2223 | 16 | -1.565u | -9.78% | +0.40% |
| 2324 | 19 | -1.470u | -7.74% | +1.93% |
| 2425 | 22 | +8.750u | +39.77% | +2.16% |
| 2526 | 24 | +13.070u | +54.46% | +2.04% |

## Interpretation

This candidate materially improves ROI versus the previous long-horizon fixed baseline:

- ROI improves from +6.28% to +17.02%.
- Profit improves from +40.30u to +46.625u despite fewer bets.
- The holdout period is strong, but not perfect: 2 of 7 active holdout seasons are negative.

The full-grid top candidate should remain a research candidate until it is covered by the same fast regression and
profile-audit gates. The live profile is pinned to the quick regression candidate below because it is reproducible in
the default verification path and now appears in `profile-audit` as `source=long_horizon`.

## Reproducible Scan Command

Implemented command:

```powershell
footballctl backtest long-horizon-scan --league I1 --family asian-away --json
```

Fast regression command:

```powershell
footballctl backtest long-horizon-scan --league I1 --family asian-away --quick --json
```

The formal command reports `window_mode=continuous_replay_season_breakdown`: one continuous replay over discovery plus holdout seasons, then grouped window summaries from the per-season breakdown. This avoids hidden differences between independent discovery/holdout replays and one continuous historical run.

Current default scan top result under that formal replay mode:

- Params: `min_edge=0.025`, `min_odds=1.90`, `max_odds=2.70`, `min_strength=0.50`, `max_bets_per_season=20`.
- Total: 188 bets, +37.615u, ROI +20.01%, positive seasons 13/16.
- Discovery: 91 bets, +16.510u, ROI +18.14%, positive seasons 8/9.
- Holdout: 97 bets, +21.105u, ROI +21.76%, positive seasons 5/7, average CLV +2.82%.

Fast regression candidate:

- Params: `min_edge=0.025`, `min_odds=1.80`, `max_odds=2.70`, `min_strength=0.50`, `max_bets_per_season=25`.
- Total: 231 bets, +43.665u, ROI +18.90%, positive seasons 13/16, worst season -17.37%.
- Holdout: 122 bets, +23.665u, ROI +19.40%, positive seasons 5/7, average CLV +1.54%.

## Multi-Family Scan Evidence

Supported scan families:

- `asian-away`
- `asian-home`
- `market-home`
- `market-away`

Regression commands:

```powershell
python -m football_analysis backtest long-horizon-scan --league I1 --family asian-away --quick --json
python -m football_analysis backtest long-horizon-scan --league I1 --family asian-home --quick --min-discovery-roi 0.05 --min-holdout-roi 0.05 --min-holdout-positive-seasons 5 --json
python -m football_analysis backtest long-horizon-scan --league I1 --family market-home --quick --json
python -m football_analysis backtest long-horizon-scan --league I1 --family market-away --quick --json
```

Latest regression results:

| Family | Gate | Total Bets | Total ROI | Holdout Bets | Holdout ROI | Holdout Positive Seasons | Decision |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| `asian-away` | live defaults | 231 | +18.90% | 122 | +19.40% | 5/7 | Keep as the only live-enabled family. |
| `asian-home` | paper research, ROI gate relaxed to 5% | 315 | +6.24% | 139 | +6.20% | 5/7 | Paper-only observation; below the 8% live ROI gate. |
| `market-home` | live defaults | 0 candidates | n/a | 0 candidates | n/a | n/a | Reject for live use. |
| `market-away` | live defaults | 0 candidates | n/a | 0 candidates | n/a | n/a | Reject for live use. |

Operational conclusion: do not promote `asian-home` or any `market-*` family to real-money recommendations. They remain useful as regression guards because they prove the scanner can compare alternate families without weakening the live gate.
