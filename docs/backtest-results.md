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

## Season Phase Sensitivity

The strategy engine now supports an explicit `season_phase` filter:

- `all`: keep the full season eligible for bets.
- `early`: only place bets in the first third of a season.
- `middle`: only place bets in the middle third of a season.
- `late`: only place bets in the final third of a season.

The phase filter controls bet eligibility only. Rolling team form still uses every earlier match in the same replay, so middle and late phases do not lose pre-phase team state.

Fixed-parameter sensitivity on the current walk-forward folds:

### E0 robust profile

The current `all` phase remains the best stable E0 profile.

| Phase | Bets | Profit | ROI | Positive folds | CLV |
| --- | ---: | ---: | ---: | ---: | ---: |
| all | 176 | +8.83u | +5.02% | 3/3 | +1.82% |
| early | 31 | +8.30u | +26.77% | 2/3 | -0.05% |
| middle | 75 | -2.99u | -3.99% | 1/3 | +1.52% |
| late | 82 | +2.99u | +3.65% | 2/3 | +2.36% |

### I1 high-yield profile

The `middle` phase is a useful supplemental candidate: it improves fold stability, but the sample is smaller than the all-season profile.

| Phase | Bets | Profit | ROI | Positive folds | CLV |
| --- | ---: | ---: | ---: | ---: | ---: |
| all | 180 | +21.505u | +11.95% | 2/3 | +5.28% |
| early | 54 | +4.71u | +8.72% | 2/3 | +5.81% |
| middle | 70 | +12.27u | +17.53% | 3/3 | +4.79% |
| late | 71 | +8.905u | +12.54% | 2/3 | +5.01% |

Production interpretation: keep E0 `all` as the robust candidate, keep I1 `all` as the aggressive high-yield candidate, and track I1 `middle` as a smaller-sample stability candidate in paper betting before using real stake.

## Portfolio Report

The current multi-strategy report is available through:

```bash
footballctl backtest portfolio --json
```

This report ranks the active candidates and labels them as `robust`, `high_yield`, `supplemental`, or `reject_unstable` based on settled bets, ROI, positive folds, CLV, and fallback usage. It is a research and deployment-readiness artifact; it does not place bets and does not automatically override live `picks_today` scoring.

For targeted full-data phase overlay scans, use:

```bash
footballctl backtest portfolio --scan-phases --leagues I1 --season-phases middle --json
```

The scan mode can cover all imported leagues and all phases. It trains the all-season walk-forward candidate once per league, then replays each requested phase as an overlay. Use `footballctl backtest walk-forward --season-phases <phase>` when you specifically need slower phase-only retraining evidence.

Latest full overlay scan across E0, SP1, D1, I1, F1 and all four phases:

| League | Phase | Label | Bets | ROI | Positive folds | CLV |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| E0 | all | robust | 176 | +5.02% | 3/3 | +1.82% |
| I1 | all | high_yield | 180 | +11.95% | 2/3 | +5.28% |
| I1 | middle | supplemental | 70 | +17.53% | 3/3 | +4.79% |
| I1 | late | supplemental | 71 | +12.54% | 2/3 | +5.01% |

All other league/phase overlays were labeled `reject_unstable` because they failed sample, fold consistency, ROI, or CLV gates. High-ROI but very small samples, such as E0 early and F1 early, are not production candidates.

## Production Profile Metadata

The validated candidates are mirrored into `config/default.yaml` as `strategy_profiles`. When a match's configured football-data.co.uk league code, market, and selection match a validated profile, the recommendation includes that profile under `odds_basis.strategy_profile` and `score_breakdown.strategy_profile`.

Recommendation scoring also includes `league_profile`, `tier_policy`, and `strategy_confidence_class`. A matched backtest profile is labeled `validated_strategy`. A configured live league without a matched profile can still be labeled `live_scoring` when it is explicitly not paper-only. Smaller professional leagues use `secondary_live_small_stake`: they can enter real-stake picks only when the stricter tier policy passes, and their stake is capped below elite-league limits. Paper-only leagues, unknown leagues, or tier-policy failures are labeled `paper_candidate` with stake forced to `0`.

Before using these profiles in production review, run:

```bash
footballctl backtest profile-audit --json
```

The audit compares configured profiles with the current default portfolio and reports stale, missing, or newly discovered candidates.

Also run:

```bash
footballctl odds-readiness --json
footballctl live-refresh --date 2026-06-10 --dry-run --json
footballctl live-refresh --date 2026-06-10 --scope live-leagues --dry-run --json
footballctl live-decision --json
footballctl live-decision --full-profile-audit --json
```

This live-data audit is separate from backtesting. It checks whether today/future matches have enough stored odds snapshots, odds freshness, bookmaker coverage, market averages, best prices, matching active strategy profiles, and per-league coverage status. The current historical CSV data is sufficient for research backtests, but production picks need fresh multi-bookmaker odds in `odds_snapshots`. Both odds readiness and the live gate reject otherwise valid candidates when the matched market odds are older than `live_trading.max_odds_age_minutes` (default `90`). When profile odds are blocked, `odds_readiness.refresh_requirements` lists the exact active profile, refresh league, market, selection, bookmaker minimum, missing ready-match count, and blocking issues. `live-decision` is the final reproducible go/no-go snapshot: by default it uses a fast profile contract audit, then combines odds readiness, live review, preflight, thresholds, and reproducibility inputs in one JSON report. The non-JSON operator summary stays short and surfaces both odds refresh requirements and the closest blocked candidates. Use `live-decision --full-profile-audit --json` or `backtest profile-audit --json` for the heavier portfolio drift audit before rollout, after strategy changes, or as a scheduled daily control.

If either preflight or live-decision returns `action: "refresh_fixtures_and_odds"`, use `live-refresh` as the auditable response. `--dry-run` reports the active-profile league plan without quota use; removing `--dry-run` executes fixture refresh first, odds refresh second, and returns the post-refresh preflight in the same JSON payload. Source selection defaults to `auto`, so each league resolves to the first configured fixture and odds provider it can use. If the executed default refresh returns `active_profile_refresh_empty:<leagues>` plus `consider_scope_live_leagues`, the active-profile calendar has no usable markets for that date; `--scope live-leagues` then expands the plan to every non-paper live league and is useful for finding low-stake tier-policy candidates when the elite active-profile leagues have no near-term markets. `live-leagues` scans do not spend fallback odds-source quota by default; add `--allow-odds-fallback` only when you intentionally want `auto` odds refresh to try the next mapped provider after the preferred provider is empty or fails. Dry-run issues such as `fixtures_source_unmapped:<league>:<source>` or `odds_source_unmapped:<league>:<source>` mean that a requested fixed source cannot refresh that league under the current config and should be handled before spending quota.

## League Tier Coverage

The production ingestion config uses three practical league groups:

- `elite_club`: major European domestic leagues with deeper historical data and richer market coverage.
- `major_tournament`: international or continental tournaments with broad news coverage, but usually paper-only until event-specific evidence exists.
- `secondary_professional`: smaller professional leagues such as J1, A-League, K League 1, MLS, Brazil Serie A, Argentina Liga Profesional, and Liga MX.

The smaller professional leagues are live-coverage leagues, not validated strategy profiles. They can produce real low-stake recommendations when fresh odds and match data pass the `secondary_professional` tier policy:

- data quality at least `0.75`,
- value score at least `68.0`,
- risk score at most `45.0`,
- confidence at least `0.58`,
- at least two real bookmaker prices,
- stake capped at `0.5u`.

Candidates that fail these stricter gates stay as `paper_candidate`. This keeps the calendar useful while still separating low-liquidity small-league risk from elite-league strategy profiles.

Odds-API.io can list many future fixtures for these leagues. The ingestion command now defaults to each league's configured event cap and batches event odds through `/odds/multi` in groups of up to 10 events:

```bash
footballctl ingest odds --source odds_api_io --league K_LEAGUE_1 --json
```

Use `--max-events` only when a one-off run needs a tighter or wider cap and there is enough quota headroom. The requested bookmaker set comes from `data_sources.odds_api_io.bookmakers`; adjust it only to names supported by the account/API plan, then confirm the result through `odds_readiness.refresh_requirements` and the live gate.

The first production settlement loop is now in place:

```bash
footballctl ingest results --date 2026-06-10 --source api_football --league K_LEAGUE_1 --json
footballctl live-refresh --date 2026-06-10 --dry-run --json
footballctl live-refresh --date 2026-06-10 --scope live-leagues --dry-run --json
footballctl daily-ops --date 2026-06-10 --json
footballctl live-review --json
footballctl live-decision --json
footballctl settle-open-bets --json
footballctl settle-bet <bet-id> --closing-odds 2.05 --json
footballctl performance --by-league --json
```

This loop stores finished scores, then uses `daily-ops` to batch-settle open bets, refresh performance, attach live review, and attach the current live preflight state in one JSON report. `settle-open-bets`, `settle-bet`, and `live-review` remain available for narrower settlement, manual correction, or independent promotion/demotion review. `1x2`, Asian handicap, and totals can be inferred from final score. Asian handicap selections such as `AH_AWAY(+0.5)` / `AH_HOME(-0.25)` and totals such as `OVER 2.5` / `UNDER:2.5` can now be auto-settled, including `half_win` and `half_loss` outcomes.

Use `live-decision` after `daily-ops` when deciding whether to place any real stake. The default mode is optimized for intraday operation and reports `reproducibility.profile_audit_mode: "contract"`; add `--full-profile-audit` when you need the heavier strategy profile audit to detect configured profiles that drift, go missing from the current portfolio audit, or otherwise fail reproducibility checks. If the action is `refresh_fixtures_and_odds`, use `odds_readiness.refresh_requirements` before spending quota so the refresh targets the missing active-profile league and market rather than broad scanning.

Real-platform bet recording is guarded separately from strategy scoring. A real-platform `record-bet` must match a passed live recommendation, stay within the approved stake cap, be recorded before kickoff, avoid cumulative duplicate stake, and keep execution odds within `live_trading.max_execution_odds_slippage` of the approved recommendation price.

Promotion/demotion policy is now explicit in `live-review`: after at least `live_trading.review_min_settled_bets` settled bets, negative ROI or negative CLV recommends demotion, and ROI at or below `live_trading.review_pause_roi` with negative CLV recommends `pause_live`. The command does not automatically edit `config/default.yaml`, but profile actions `pause_live` and `demote_to_paper` are consumed by the live gate so the next matching recommendation is forced back to paper until evidence recovers or config is changed intentionally.
