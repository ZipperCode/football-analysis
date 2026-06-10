# Backtest Results

## Data

- Source: football-data.co.uk historical CSV files.
- Leagues: E0, SP1, D1, I1, F1.
- Seasons: 2122, 2223, 2324, 2425, 2526.
- Imported rows:
  - E0: 5 x 380 matches.
  - SP1: 5 x 380 matches.
  - D1: 5 x 306 matches.
  - I1: 5 x 380 matches.
  - F1: 380, 380, 306, 306, 305 matches.

## Method

The optimizer uses only pre-match available fields:

- opening / market odds fields,
- max odds and average odds from football-data.co.uk,
- rolling team form before the match date,
- league and season history before the tested fold.

Closing odds are not used for selection. They are used only after the decision for CLV reporting.

Walk-forward folds:

1. Train 2122-2223, test 2324.
2. Train 2122-2324, test 2425.
3. Train 2122-2425, test 2526.

## Current Robust Candidate

Only E0 passed the current robustness filter.

Strategy family:

- mode: `market_value`
- selection bias: `home`
- draw excluded
- min odds: `1.75`
- max odds: `3.25` or `4.50` depending on fold
- min edge: `0.015` to `0.025`
- rolling team history required

Walk-forward result:

- Bets: 176
- Profit: +8.83 units
- ROI: +5.02%
- Positive folds: 3/3
- Average CLV: +1.82%

Fold detail:

| Test season | Bets | Profit | ROI | CLV |
| --- | ---: | ---: | ---: | ---: |
| 2324 | 67 | +4.62u | +6.90% | +2.79% |
| 2425 | 29 | +2.00u | +6.90% | +1.97% |
| 2526 | 80 | +2.21u | +2.76% | +0.69% |

## Rejected Leagues

Same walk-forward method on other leagues did not satisfy robustness:

| League | Bets | Profit | ROI | Positive folds | CLV |
| --- | ---: | ---: | ---: | ---: | ---: |
| SP1 | 349 | -25.29u | -7.25% | 0/3 | +1.05% |
| D1 | 240 | -31.54u | -13.14% | 0/3 | +1.20% |
| I1 | 176 | -19.87u | -11.29% | 0/3 | +3.25% |
| F1 | 236 | -22.56u | -9.56% | 1/3 | +1.06% |

## Interpretation

The current strategy is not yet a high-return system. The honest robust result is a small positive E0-only edge with positive CLV and enough bets to be meaningful. Further ROI improvement should come from stronger pre-match features, not from loosening the optimizer until it overfits.

Next useful features:

- line movement before kickoff, captured from live snapshots,
- injury/news structured signals,
- market closing-distance labels for calibration only,
- bookmaker-specific availability and liquidity filters,
- season phase filters.

## Current High-Yield Candidate

After adding Asian handicap opening odds, the best higher-return candidate is I1 Asian handicap away value.

Selection rules are trained fold by fold, then tested out of sample:

- market: Asian handicap
- side: away handicap
- value signal: `max Asian away odds / average Asian away odds - 1`
- min value edge: `0.015`
- min odds: `1.55` to `2.00` depending on the training fold
- max odds: `3.00`
- rolling form required before match
- closing Asian handicap odds are not used for selection; they are used only for CLV reporting

Walk-forward result:

- Bets: 180
- Profit: +21.505 units
- ROI: +11.95%
- Positive folds: 2/3
- Average CLV: +5.28%

Fold detail:

| Test season | Bets | Profit | ROI | CLV |
| --- | ---: | ---: | ---: | ---: |
| 2324 | 80 | -6.98u | -8.72% | +1.73% |
| 2425 | 63 | +20.35u | +32.30% | +6.02% |
| 2526 | 37 | +8.135u | +21.99% | +8.08% |

Interpretation: this satisfies the current high-yield backtest target, but it is less stable than the E0 candidate because one of three folds is negative. It should be treated as an aggressive candidate and monitored with live paper betting before real staking.
