# Docker Production Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Docker Compose production worker that runs real data refresh and decision checks as an auditable production MVP.

**Architecture:** Add a small orchestration module over existing ingestion, live decision, and daily ops code. Expose one-shot and looping Typer commands. Run the worker as a second Compose service sharing the API image and Postgres database.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, SQLAlchemy repository, Docker Compose, Postgres.

---

### Task 1: Production Cycle Core

**Files:**
- Create: `src/football_analysis/production.py`
- Test: `scripts/verify_production_worker.py`

- [x] Write a failing verification script that imports `run_production_cycle`.
- [x] Implement `ProductionCycleReport`.
- [x] Implement fixture, odds, optional result ingestion, live decision, and daily ops orchestration.
- [x] Compact the embedded decision output for production logs.
- [x] Run `python scripts/verify_production_worker.py`.

### Task 2: CLI Commands

**Files:**
- Modify: `src/football_analysis/cli.py`

- [x] Add `footballctl production-cycle`.
- [x] Add `footballctl production-worker`.
- [x] Default production cycle/worker to auto refresh through `live_refresh`.
- [x] Add `--fixed-leagues` compatibility path for the older fixed source loop.
- [x] Add `--expand-live-leagues-on-empty` so empty active-profile refreshes automatically populate live leagues.
- [x] Ensure long-running worker emits one JSON report per cycle.
- [x] Run `footballctl production-cycle --date 2026-06-11 --leagues EPL --skip-results --skip-daily-ops --json`.

### Task 3: Docker Compose Worker

**Files:**
- Modify: `docker-compose.yml`

- [x] Add `.env` to API and worker services.
- [x] Add a worker service using the same image.
- [x] Set conservative defaults: hourly cadence and `--skip-results`.
- [x] Add restart policy, API healthcheck, configurable API port, Docker build image override, pip index override, and unbuffered worker logs.
- [x] Add optional Telegram notification hook using `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- [x] Run `docker compose config`.

### Task 4: Documentation And Acceptance

**Files:**
- Modify: `README.md`

- [x] Document production Compose commands.
- [x] Document quota-safe defaults and result ingestion.
- [x] Run baseline verification commands.

### Task 5: Production Status Snapshot

**Files:**
- Modify: `src/football_analysis/production.py`
- Modify: `src/football_analysis/cli.py`
- Modify: `src/football_analysis/api.py`
- Modify: `scripts/verify_contracts.py`

- [x] Add local-only `build_production_status` for live decision, table counts, recent jobs, provider quota/cache counters, credential presence, missing/empty required ingestion jobs, odds readiness, profile refresh requirements, and live league coverage.
- [x] Add `footballctl production-status --json`.
- [x] Add `GET /production/status`.
- [x] Add the CLI/API status entrypoints to contract verification.

**Verification evidence:**
- `python -m compileall src scripts` passed.
- `python scripts/verify_production_worker.py` passed.
- `python scripts/verify_contracts.py` passed after adding `footballctl production-status --json` and `GET /production/status`.
- `python scripts/verify_datasources.py --no-remote` passed.
- `python scripts/verify_contracts.py` passed.
- `python scripts/verify_daily_ops.py` passed.
- `python scripts/verify_live_refresh.py` passed.
- `python scripts/verify_live_decision.py` passed.
- `footballctl production-worker --once --skip-results --skip-daily-ops --json` ran a real remote auto-refresh cycle; active-profile refresh expanded through `live_refresh`, ingestion jobs succeeded, but the production gate correctly stayed `blocked` because no matching fresh market odds were available.
- `docker compose config` passed.
- Docker Desktop was started and `docker info` became ready.
- Initial `docker compose build` / `docker pull python:3.13-slim` were blocked by Docker Hub network/auth transport failures: `unexpected EOF` and `TLS handshake timeout`.
- Dockerfile now defaults to `python:3.12-slim` and accepts `PYTHON_IMAGE`; Compose exposes `PYTHON_IMAGE` for registry fallback.
- `docker compose build --build-arg PYTHON_IMAGE=mcr.microsoft.com/devcontainers/python:1-3.13-bookworm --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` passed.
- `docker compose up -d postgres api` passed; API ran on `http://127.0.0.1:18000`.
- `Invoke-RestMethod http://127.0.0.1:18000/` returned `status: ok`.
- `Invoke-RestMethod http://127.0.0.1:18000/sources/health` returned source health JSON.
- `docker compose run --rm worker footballctl production-worker --once --leagues EPL --skip-results --skip-daily-ops --json` passed with successful ingestion jobs and a correctly blocked production gate.
- `docker compose up -d worker` passed; `docker compose logs --tail 160 worker` showed the first live cycle in real time.
- Worker report keeps `daily_ops` as a compact summary and does not emit full candidate lists.
- `footballctl production-worker --once --leagues EPL --skip-results --skip-daily-ops --alert-text --notify-telegram` passed with empty Telegram credentials and returned `missing_credentials` without sending externally.
- Rebuilt and restarted Compose worker with `--notify-telegram`; `docker compose logs --tail 220 worker` showed a real production cycle followed by one-line JSON Telegram status: `sent=false`, `skipped_reason=missing_credentials`.
