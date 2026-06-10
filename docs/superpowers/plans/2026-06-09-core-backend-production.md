# Core Backend Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade football-analysis from MVP skeleton to a production-ready core backend without Telegram or web UI.

**Architecture:** Keep FastAPI and Typer as the external surface. Replace the MVP JSON-bucket persistence path with structured SQLAlchemy tables plus raw payload audit records, then add data source clients, ingestion jobs, recommendation versioning, and lightweight production verification.

**Tech Stack:** Python 3.11+, FastAPI, Typer, SQLAlchemy 2.x, Pydantic 2.x, httpx, PyYAML, SQLite local default, Postgres via Docker Compose.

---

## File Structure

- Create `src/football_analysis/db.py`: SQLAlchemy engine/session/table models and database initialization.
- Create `src/football_analysis/contracts.py`: provider-neutral ingest DTOs and score breakdown DTOs.
- Create `src/football_analysis/http_client.py`: HTTP timeout/retry wrapper that records request metadata without secrets.
- Create `src/football_analysis/cache.py`: provider cache, quota windows, raw payload persistence helpers.
- Create `src/football_analysis/datasources/base.py`: common source client protocol and result types.
- Create `src/football_analysis/datasources/api_football.py`: API-FOOTBALL client and mapper.
- Create `src/football_analysis/datasources/odds_api_io.py`: Odds-API.io client and mapper.
- Create `src/football_analysis/datasources/football_data_org.py`: football-data.org client and mapper.
- Create `src/football_analysis/datasources/football_data_uk.py`: football-data.co.uk CSV importer.
- Create `src/football_analysis/ingestion.py`: fixture, odds, standings, injury, historical ingest jobs.
- Create `src/football_analysis/backtest.py`: historical strategy replay and ROI/CLV summary.
- Modify `src/football_analysis/models.py`: add production response models and score breakdown fields.
- Modify `src/football_analysis/settings.py`: add league/date/source/cache/quota settings.
- Modify `src/football_analysis/service.py`: read from structured tables, generate persisted recommendations.
- Modify `src/football_analysis/scoring.py`: produce explainable score breakdown and stricter risk gates.
- Modify `src/football_analysis/sources.py`: use real credential checks, optional remote probes, quota/cache state.
- Modify `src/football_analysis/api.py`: add ingestion/backtest/admin core endpoints, no Telegram endpoints.
- Modify `src/football_analysis/cli.py`: add `ingest`, `backtest`, `db`, `jobs` command groups.
- Modify `config/default.yaml`: add league scope, cache TTLs, quota budgets, date windows, backtest config.
- Modify `README.md`: document production core commands and no-Telegram scope.
- Create `scripts/verify_contracts.py`: machine-readable CLI/API contract checks.
- Create `scripts/verify_datasources.py`: credential and optional remote data source smoke checks.
- Create `scripts/verify_backtest.py`: historical importer/backtest smoke verification.

## Task 1: Runtime Cleanup and Settings Expansion

**Files:**
- Modify: `.gitignore`
- Modify: `config/default.yaml`
- Modify: `src/football_analysis/settings.py`
- Modify: `README.md`

- [ ] Stop any old local `uvicorn` process started from this workspace if it is still listening on port 8000.
- [ ] Add `.run/`, `data/`, and downloaded historical files to `.gitignore` if missing.
- [ ] Extend config with `ingestion`, `cache`, `quota`, `leagues`, and `backtest` sections.
- [ ] Extend settings models for those config sections.
- [ ] Run `python -m compileall src scripts`; expected exit code 0.

## Task 2: Structured Database Layer

**Files:**
- Create: `src/football_analysis/db.py`
- Modify: `src/football_analysis/service.py`
- Modify: `src/football_analysis/repository.py`

- [ ] Create structured SQLAlchemy models for competitions, teams, matches, odds snapshots, findings, recommendations, bets, raw payloads, source requests, quota windows, and job runs.
- [ ] Add session factory helpers and `init_db(database_url)`.
- [ ] Keep `RecordRepository` temporarily for backward compatibility during migration.
- [ ] Add CLI command `footballctl db init`.
- [ ] Verify SQLite table creation in a temp database.

## Task 3: Source Request Audit, Cache, and Quota

**Files:**
- Create: `src/football_analysis/http_client.py`
- Create: `src/football_analysis/cache.py`
- Modify: `src/football_analysis/sources.py`

- [ ] Implement sanitized request metadata so API keys never appear in persisted URLs or logs.
- [ ] Store raw payloads before mapper execution.
- [ ] Add cache lookup/write by provider, endpoint, params hash, and TTL.
- [ ] Add quota counters by provider and time window.
- [ ] Verify repeated requests can hit cache without increasing quota counters.

## Task 4: Data Source Clients and Mappers

**Files:**
- Create: `src/football_analysis/datasources/base.py`
- Create: `src/football_analysis/datasources/api_football.py`
- Create: `src/football_analysis/datasources/odds_api_io.py`
- Create: `src/football_analysis/datasources/football_data_org.py`
- Create: `src/football_analysis/datasources/football_data_uk.py`
- Modify: `src/football_analysis/models.py`

- [ ] Implement API-FOOTBALL fixtures, odds, injuries, and standings fetch methods.
- [ ] Implement Odds-API.io events and odds fetch methods.
- [ ] Implement football-data.org matches and standings fetch methods.
- [ ] Implement football-data.co.uk CSV download/import parser.
- [ ] Map provider payloads into internal match, odds, team, competition, and historical result records.
- [ ] Verify mapper behavior with small local fixture payloads, not live calls.

## Task 5: Ingestion Jobs and CLI/API Surface

**Files:**
- Create: `src/football_analysis/ingestion.py`
- Modify: `src/football_analysis/cli.py`
- Modify: `src/football_analysis/api.py`
- Modify: `src/football_analysis/service.py`

- [ ] Add `footballctl ingest fixtures --date YYYY-MM-DD --source api_football`.
- [ ] Add `footballctl ingest odds --date YYYY-MM-DD --source api_football`.
- [ ] Add `footballctl ingest standings --league CODE --season YYYY`.
- [ ] Add `footballctl ingest historical --season 2526 --league E0`.
- [ ] Add API endpoints for job execution and job status.
- [ ] Store job_runs with started/finished/error/summary fields.

## Task 6: Production Recommendation and Risk Logic

**Files:**
- Modify: `src/football_analysis/scoring.py`
- Modify: `src/football_analysis/models.py`
- Modify: `src/football_analysis/service.py`

- [ ] Add score breakdown fields for odds edge, data quality, historical signal, news signal, risk penalty, and final status.
- [ ] Persist recommendation versions instead of recalculating silently on read.
- [ ] Reject recommendations when odds are stale, source conflict is high, data completeness is low, or risk score exceeds threshold.
- [ ] Keep stake units limited to `0.5u`, `1u`, or `1.5u`.
- [ ] Verify full-data, missing-odds, conflicting-source, stale-odds, and high-risk scenarios.

## Task 7: Backtest and Simulation

**Files:**
- Create: `src/football_analysis/backtest.py`
- Modify: `src/football_analysis/cli.py`
- Modify: `src/football_analysis/api.py`
- Create: `scripts/verify_backtest.py`

- [ ] Import football-data.co.uk CSV rows into historical match and odds records.
- [ ] Replay a deterministic value-edge strategy against historical closing/result columns.
- [ ] Calculate bets, settled bets, stake, profit, ROI, and average CLV.
- [ ] Add `footballctl backtest historical --league E0 --season 2526 --json`.
- [ ] Verify backtest smoke with a local two-row CSV fixture.

## Task 8: Production Verification Scripts and Documentation

**Files:**
- Create: `scripts/verify_contracts.py`
- Create: `scripts/verify_datasources.py`
- Modify: `scripts/verify_scenarios.py`
- Modify: `README.md`

- [ ] Verify CLI JSON outputs parse with `json.loads`.
- [ ] Verify API smoke with FastAPI TestClient.
- [ ] Verify data source credential states without printing secret values.
- [ ] Verify optional remote smoke only when `--remote` is passed.
- [ ] Update README with production-core command sequence and Telegram exclusion.
- [ ] Run final verification commands and record exact outputs.

## Final Verification

Run:

```powershell
python -m compileall src scripts
python scripts/verify_scenarios.py
python scripts/verify_contracts.py
python scripts/verify_datasources.py --no-remote
python scripts/verify_backtest.py
footballctl sources --json
footballctl picks today --json
```

Expected:
- All commands exit 0.
- CLI JSON outputs parse as valid JSON.
- Data source checks report configured/missing states without secret values.
- No Telegram endpoint or command is required.

No git commit, push, branch creation, or PR creation is part of this plan.
