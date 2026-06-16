# Docker Production Pipeline Design

## Goal

Run the project as a Docker Compose production MVP that refreshes football fixtures and odds, evaluates live readiness, performs operational checks, and produces compact JSON audit output without automatic betting.

## Scope

- Add a reusable production cycle around existing ingestion, live decision, and daily ops services.
- Add a long-running worker command for Docker Compose.
- Keep live money execution guarded by the existing `record-bet` and live gate rules.
- Keep default remote API usage conservative enough for free-tier development credentials.

## Architecture

- `football_analysis.production.run_production_cycle` is the core orchestration boundary.
- `footballctl production-cycle` runs one explicit cycle for a date and league list.
- `footballctl production-worker` loops over the same cycle and emits one compact JSON report per cycle.
- Production cycle/worker default to `--auto-refresh`, using `live_refresh` planning instead of fixed source loops.
- When active-profile refresh returns empty, `--expand-live-leagues-on-empty` runs a second `live-leagues` refresh to keep the production store populated with currently active leagues.
- `footballctl production-status` and `GET /production/status` expose the local production state without remote provider calls.
- `--alert-text` emits a compact operator alert suitable for log forwarding or a notification bridge.
- `--notify-telegram` sends that compact alert through Telegram only when credentials are configured; missing credentials are skipped without failing the production cycle.
- Docker Compose runs `api` and `worker` from the same image and stores durable state in Postgres.

## Data Flow

1. Resolve refresh targets from active strategy profiles, or explicit `--leagues`.
2. Ingest fixtures and odds through `live_refresh` with source `auto`.
3. If active profiles are empty and expansion is enabled, refresh live leagues.
4. Optionally ingest results when explicitly enabled.
5. Run `live_decision` to produce go/no-go state.
6. Optionally run daily ops without spending result-ingestion quota.
7. Emit a compact report containing refresh operations, job summaries, decision status, action, and issues.

## Production Defaults

- Compose worker targets `auto`, matching active strategy refresh needs and expanding to live leagues when active profiles are empty.
- Compose worker interval is 3600 seconds.
- Compose worker skips remote results by default to avoid burning API-FOOTBALL quota.
- Compose API defaults to host port `18000` to avoid common local `8000` collisions.
- Compose services use `restart: unless-stopped`, and API has an HTTP healthcheck.
- Docker builds default to `python:3.12-slim` and accept `PYTHON_IMAGE` for registry fallback.
- Docker builds accept `PIP_INDEX_URL` for pip mirror fallback and install dependencies before project code for better layer reuse.
- Compose sets `PYTHONUNBUFFERED=1` so long-running worker JSON reports appear in Docker logs immediately.
- Compose worker includes `--notify-telegram`; empty Telegram env values skip sending safely, filled env values enable production alerts.
- Telegram send status is emitted as one-line JSON so log collectors can parse it.
- `ready_to_bet=false` and `status=blocked` are valid production outcomes when odds readiness is insufficient.
- Production status includes live decision, table counts, recent ingestion jobs, provider quota/cache counters, credential presence flags, missing/empty required ingestion job issues, odds readiness, profile refresh requirements, and live league coverage.

## Verification

- `python scripts/verify_production_worker.py`
- `python -m compileall src scripts`
- `footballctl production-cycle --date YYYY-MM-DD --leagues auto --auto-refresh --refresh-scope active-profiles --skip-results --skip-daily-ops --json`
- `footballctl production-status --json`
- `docker compose config`
- `docker compose build --build-arg PYTHON_IMAGE=mcr.microsoft.com/devcontainers/python:1-3.13-bookworm --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`
- `docker compose up -d postgres api`
- `Invoke-RestMethod http://127.0.0.1:18000/`
- `Invoke-RestMethod http://127.0.0.1:18000/sources/health`
- `Invoke-RestMethod http://127.0.0.1:18000/production/status`
- `docker compose run --rm worker footballctl production-worker --once --skip-results --skip-daily-ops --json`
- `docker compose up -d worker`
- `docker compose logs --tail 160 worker`
