# Production Cutover Status - 2026-06-13

## Current State

- Worker target is deployable: `footballctl production-deploy-check --target worker --json` returned `status=ready_with_warnings`, `ready_for_worker=true`, `ready_for_record_execution=true`.
- Final go/no-go gate is functional: `footballctl live-decision --json` returned `status=ready`, `ready_to_bet=true`, `action=place_approved_live_bets`.
- Real data refresh path is functional with existing local credentials:
  - `footballctl live-refresh --date 2026-06-13 --scope live-leagues --fixture-source auto --odds-source auto --allow-odds-fallback --json`
  - `BRA_SERIE_A` via `odds_api_io` inserted `145` odds snapshots.
  - Other active leagues returned successful jobs with `0` odds snapshots, indicating coverage/date/provider-plan gaps rather than pipeline errors.
- Production heartbeat is current:
  - `footballctl production-cycle --date 2026-06-13 --leagues BRA_SERIE_A --fixed-leagues --fixture-source api_football --odds-source odds_api_io --skip-results --skip-daily-ops --execution-mode off --broker-discovery-mode off --broker-execution-mode off --json`
  - latest production cycle ingested `145` odds snapshots and wrote a valid heartbeat.
- Worker dry-run starts under deploy gate:
  - `footballctl production-worker-env --once --json` with `WORKER_REFRESH_DRY_RUN=1`, `WORKER_EXECUTION_MODE=off`, `WORKER_DATA_APPLY_MODE=dry-run`, `WORKER_REQUIRE_DEPLOY_READY=1` returned `status=planned`, `action=refresh_dry_run`.
- Runtime secrets have been applied for the local Compose stack:
  - `FOOTBALL_ADMIN_TOKEN` is present and intentionally fixed to the operator-approved admin value.
  - `POSTGRES_PASSWORD` is no longer the default value.
  - The running Postgres role was rotated with `ALTER USER`, then `postgres`, `api`, and `worker` were restarted.
- Record-only execution is deployable:
  - container `production-execution-queue` returned `status=ready`, `ready_to_execute=true`, `queue_count=2`, `queue_stake_units=1.0`.
  - container `production-execute --json` returned `status=dry_run`, `queue_count=2`, `dry_run_count=2`, `recorded_count=0`.
  - container `production-deploy-check --target record-only --json` returned `ready_for_record_execution=true`.

## Current Hard Stops

- No legal broker-live execution yet:
  - missing `BETFAIR_APP_KEY`
  - missing `BETFAIR_SESSION_TOKEN`
  - broker disabled in config
  - missing `stake_currency_per_unit`
  - no broker market/selection mappings
- Paid/provider expansion not complete:
  - missing `THE_ODDS_API_KEY`
  - missing `SPORTMONKS_TOKEN`
  - The Odds API and Sportmonks are configured but disabled until credentials, plan coverage, and league ids are confirmed.
- Runtime security retains one intentional warning:
  - `runtime_admin_token_too_short`, because the operator-approved admin value is shorter than the generated-secret recommendation.
  - This is not a blocker while API and Postgres remain bound to localhost.

## External Provider Evidence

- The Odds API v4 supports sports discovery, odds, event odds, and historical odds endpoints; use `footballctl sources-the-odds-api-sports --fetch-remote --json` after setting `THE_ODDS_API_KEY`.
- Sportmonks Premium Odds Feed is the intended paid premium odds fallback; each league still needs a verified `sportmonks_league_id`.
- Betfair API-NG is the broker template in config; live order placement must remain blocked until credentials, stake sizing, mappings, and explicit live execution mode are all present.

## Next Operator Actions

1. Set remaining production provider and broker secrets in secret store or `.env`:
   - `THE_ODDS_API_KEY` if The Odds API paid coverage is approved
   - `SPORTMONKS_TOKEN` if Sportmonks Premium Odds coverage is approved
   - `BETFAIR_APP_KEY` and `BETFAIR_SESSION_TOKEN` only after legal broker approval
2. Run provider checks:
   - `footballctl sources-the-odds-api-sports --fetch-remote --json`
   - `footballctl production-data-plan --json`
   - `footballctl production-historical-odds-plan --league <CODE> --start-time <ISO> --end-time <ISO> --max-snapshots 24 --json`
3. Build candidate config, not direct production config:
   - `footballctl production-candidate-check --candidate-config build/production-candidate.yaml --json`
   - for broker readiness add `--broker-stake-currency-per-unit <AMOUNT>` after stake policy approval.
4. Promote only after candidate deploy check passes:
   - `footballctl production-deploy-check --target worker --json`
   - `footballctl production-deploy-check --target record-only --json`
   - `footballctl production-deploy-check --target broker-live --json`

## Verification Passed

- `python -m compileall src scripts`
- `python scripts/verify_datasources.py --no-remote`
- `python scripts/verify_live_decision.py`
- `python scripts/verify_live_preflight.py`
- `python scripts/verify_production_worker.py`
- `python scripts/verify_contracts.py`
- `docker compose config --quiet`
- `docker compose up -d --build api worker`
- `docker compose exec -T api footballctl production-runtime-security --target broker-live --json`
- `docker compose exec -T api footballctl production-execution-queue --json`
- `docker compose exec -T api footballctl production-execute --json`
- `docker compose exec -T api footballctl production-deploy-check --target record-only --json`
