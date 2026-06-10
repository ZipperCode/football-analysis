# Result Ingestion And Settlement Design

**Goal:** Build the first settlement loop after small-league live recommendations: ingest finished match results, settle recorded bets, and review performance by league and tier.

## Design

The current model already has `BetLog.result`, `BetLog.profit_units`, and `BetLog.closing_odds`, but `Match` does not store scores and there is no command to settle bets. The next step adds score fields to `Match`, maps provider result payloads into those fields, and adds local settlement commands. This gives a reliable local workflow without depending on a full scheduler or broad remote scans.

## Data Flow

1. `footballctl ingest results --date YYYY-MM-DD --source api_football --league K_LEAGUE_1 --json` refreshes finished fixtures and stores scores on matches.
2. `footballctl settle bet <bet-id> --json` settles a specific recorded bet from the stored match score.
3. `footballctl performance --by-league --json` reports ROI and CLV grouped by league and configured tier.

## Boundary

This phase supports 1x2 settlement first because it is unambiguous from final score. Asian handicap and over/under stay explicit/manual until line parsing and push/half-win rules are added. The API-Football result mapper can store final scores for all matches, even when settlement cannot yet infer a market result.

## Next Step After This

Once 1x2 settlement is reliable, extend settlement to Asian handicap and totals, then add a small daily command that refreshes yesterday's results and settles eligible bets automatically.
