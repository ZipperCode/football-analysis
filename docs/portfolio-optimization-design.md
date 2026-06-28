# Kelly Portfolio Optimization

## Goal

This layer improves stake sizing and betting-slip quality for positive-EV recommendations. It does not convert a negative-EV model into a profitable system and does not guarantee stable profit.

Target operating objective:

- Expected CAGR target: 20%.
- Maximum drawdown limit: 20%.
- Execution mode: manual betting slip generation.
- Core source of return: existing strategy edge plus controlled stake sizing.

## Implementation

The live path keeps the existing production safety caps and adds two controls:

- Kelly sizing: `kelly_fraction = edge / (odds - 1)`, then `fraction=0.25`.
- Portfolio control: same-match exclusion, same-league stake penalty, and daily exposure budget.

Default config:

```yaml
bankroll:
  initial_units: 10000
  unit_basis: fixed

kelly:
  mode: fractional
  fraction: 0.25
  min_edge: 0.025
  max_stake_fraction: 0.005

portfolio:
  max_daily_exposure_fraction: 0.00012
  correlation_penalty_same_league: 0.7
  correlation_penalty_same_match: 1.0
  max_correlated_stakes_per_day: 3
```

The production queue exposes the fields required for manual execution:

- `minimum_execution_odds`
- `expires_at`
- `mutual_exclusion_tag`
- `correlation_group`
- `kelly_fraction`
- `kelly_stake_units`
- `portfolio_adjusted`

## Backtest Evidence

Run:

```bash
footballctl backtest kelly --league I1 --family asian-away --quick --json
```

Current result with the default 10000u baseline:

- Top candidate: I1 middle-season AH away long-horizon scan.
- Settled bets: 231.
- Total ROI: 18.90%.
- Holdout ROI: 19.40%.
- Max drawdown: about 0.04% of the 10000u baseline.
- CAGR target check: failed under the default bankroll baseline.

The failed CAGR check is expected because historical `profit_units` are fixed betting units, while the default bankroll baseline is 10000u. This keeps production exposure conservative, but it also means the current safe cap profile does not mathematically produce 20% annualized bankroll growth without raising turnover, raising stake fraction, or finding additional independent edge.

## Operating Boundary

The system should keep real-money execution conservative until paper or recorded real results show that higher exposure is justified. A future promotion should require:

- positive CLV remains visible after live execution,
- enough settled bets for the target market,
- drawdown stays under the configured limit,
- actual executed odds stay above `minimum_execution_odds`,
- no same-match duplicate exposure.
