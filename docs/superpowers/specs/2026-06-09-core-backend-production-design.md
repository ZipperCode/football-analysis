# Core Backend Production Design

## Scope

This iteration upgrades the current MVP skeleton into a production-ready core backend. Telegram, web UI, live betting, automated betting, machine learning training, and multi-user permissions are outside this iteration.

## Success Criteria

- Env variables are loaded without echoing secrets.
- Data source health checks can verify credentials, run remote smoke probes, and report quota/cache state.
- Real data ingestion can fetch fixtures, odds, standings/injuries where available, historical CSV rows, and store normalized records plus raw payloads.
- Recommendations are generated from persisted data, not seed-only fixtures.
- Each recommendation records versioned scoring inputs, odds basis, data quality, risk tags, stake units, and audit reason.
- Backtest and simulation flows can calculate ROI and CLV from stored recommendations and settled results.
- API and CLI remain JSON-stable for future Hermes integration.
- Verification stays lightweight: compile, scenario checks, JSON contract checks, API smoke checks, data source smoke checks, and backtest smoke checks.

## Architecture

The backend remains a Python FastAPI app with Typer CLI commands. The storage layer changes from a generic JSON bucket repository to structured SQLAlchemy tables for matches, teams, competitions, odds snapshots, findings, recommendations, bets, raw payloads, source requests, and quota/cache records. SQLite remains the local default, while Docker Compose uses Postgres.

Data source clients live behind small interfaces. Each client handles auth, retries, timeouts, response capture, and provider-specific endpoints; mapper modules convert provider payloads into internal models. Ingestion jobs call clients, write raw payloads first, then normalized records, then recommendation versions.

## Data Sources

- API-FOOTBALL/API-SPORTS: primary fixtures, odds, injuries, standings source. Base URL: `https://v3.football.api-sports.io`; credential env: `API_FOOTBALL_KEY`; auth header: `x-apisports-key`.
- Odds-API.io: odds backup source. Base URL: `https://api.odds-api.io/v3`; credential env: `ODDS_API_IO_KEY`; auth query parameter: `apiKey`.
- football-data.org: competitions, matches, standings fallback. Base URL: `https://api.football-data.org/v4`; credential env: `FOOTBALL_DATA_ORG_TOKEN`; auth header: `X-Auth-Token`.
- football-data.co.uk: historical CSV source for backtesting; no secret required.

Official docs checked before implementation:
- https://www.api-football.com/documentation-v3
- https://docs.odds-api.io/api-reference/introduction
- https://www.football-data.org/documentation/quickstart
- https://www.football-data.co.uk/data

## Data Model

The structured schema stores:

- `competitions`: provider-neutral competition identity and country.
- `teams`: provider-neutral team identity.
- `matches`: fixture identity, provider IDs, kickoff, league, season, teams, status, completeness.
- `odds_snapshots`: provider, bookmaker, market, line, selection odds, averages, best price, collected time.
- `agent_findings`: odds, news, history, and risk findings with evidence.
- `recommendations`: versioned recommendation outputs and score breakdown.
- `bets`: real or simulated bet records, closing odds, result, profit, CLV.
- `raw_payloads`: provider, endpoint, request key, response body, status, captured time.
- `source_requests`: endpoint calls, cache hit/miss, duration, error, quota bucket.
- `quota_windows`: provider request counters by day/hour.
- `job_runs`: ingestion/backtest job state and summary.

## Data Flow

1. `footballctl ingest fixtures --date YYYY-MM-DD` fetches fixture candidates.
2. Raw provider responses are stored before mapping.
3. Mappers upsert competitions, teams, matches, and completeness fields.
4. `footballctl ingest odds --date YYYY-MM-DD` fetches odds for candidate fixtures.
5. Odds snapshots are aggregated into market averages and best prices.
6. Agent rules generate findings.
7. Recommendation rules produce versioned recommendations.
8. `footballctl picks today --json` returns only persisted recommendations that pass risk gates.
9. `footballctl backtest historical --source football-data-uk` imports CSV and reports ROI/CLV baselines.

## Error Handling

- Missing credentials produce `missing_credentials`, not crashes.
- Provider failures store source request errors and keep the last valid cached payload when available.
- Rate limit and quota exhaustion degrade jobs to partial success.
- Invalid provider payloads are stored as raw payloads and reported as mapper errors without deleting prior normalized data.
- Scoring refuses to recommend when odds, data quality, or risk gates are insufficient.

## Verification

- `python -m compileall src scripts`
- `python scripts/verify_scenarios.py`
- `python scripts/verify_contracts.py`
- `python scripts/verify_datasources.py --no-remote`
- `footballctl sources --json`
- `footballctl picks today --json`
- API smoke via FastAPI TestClient.

No commit is performed unless the user explicitly asks for git commit/push.
