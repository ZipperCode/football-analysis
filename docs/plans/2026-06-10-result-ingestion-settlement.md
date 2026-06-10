# Result Ingestion And Settlement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add result ingestion, 1x2 bet settlement, and league/tier performance reporting for live recommendation review.

**Architecture:** Extend the existing `Match` payload with final-score fields and map provider finished fixtures into those fields. Reuse `BetLog` settlement fields, adding service methods and CLI/API wrappers for manual settlement and grouped performance. Keep the first settlement implementation limited to 1x2 because it is deterministic from final score.

**Tech Stack:** Python, Pydantic models, Typer CLI, FastAPI, SQLite-backed structured repository.

---

### Task 1: Add Score Fields To Match Mapping

**Files:**
- Modify: `src/football_analysis/models.py`
- Modify: `src/football_analysis/datasources/api_football.py`
- Modify: `src/football_analysis/datasources/football_data_org.py`

**Steps:**
1. Add `home_score` and `away_score` to `Match`.
2. Map API-Football `goals.home` and `goals.away`.
3. Map football-data.org full-time score when present.
4. Keep scores optional so scheduled matches remain valid.

**Verification:**
- One-off Python assertion that API-Football fixture payload with `FT` maps to `MatchStatus.finished` and scores.

### Task 2: Add Result Ingestion Entry Point

**Files:**
- Modify: `src/football_analysis/ingestion.py`
- Modify: `src/football_analysis/cli.py`
- Modify: `src/football_analysis/api.py`

**Steps:**
1. Add `ingest_results(date, source, league_code)` as a thin wrapper over fixture ingestion.
2. Add CLI command `footballctl ingest results`.
3. Add API route `POST /jobs/ingest/results`.
4. Count finished matches in job metadata when possible.

**Verification:**
- Run CLI with seeded/no-remote-safe path only if it does not call remote; otherwise validate function shape with in-memory mapper assertions.

### Task 3: Add 1x2 Settlement Logic

**Files:**
- Modify: `src/football_analysis/service.py`
- Modify: `src/football_analysis/cli.py`
- Modify: `src/football_analysis/api.py`

**Steps:**
1. Add a helper that derives 1x2 winning selection from final score.
2. Add `settle_bet(bet_id, result=None, closing_odds=None)` to service.
3. For 1x2 bets, infer win/loss from stored match score unless explicit result is provided.
4. Calculate profit: win = `(odds - 1) * stake`, loss = `-stake`, void = `0`.
5. Keep unsupported markets explicit: return clear error unless result is supplied.

**Verification:**
- One-off Python assertion that a HOME 1x2 bet on a 2-1 finished match settles as win and profit is correct.

### Task 4: Add League/Tier Performance Reporting

**Files:**
- Modify: `src/football_analysis/models.py`
- Modify: `src/football_analysis/service.py`
- Modify: `src/football_analysis/cli.py`
- Modify: `src/football_analysis/api.py`

**Steps:**
1. Add grouped performance models.
2. Group settled bets by match league and configured tier.
3. Reuse existing total ROI/CLV calculations.
4. Add CLI option `footballctl performance --by-league --json`.
5. Add API route `GET /performance/by-league`.

**Verification:**
- One-off Python assertion that one settled K League bet appears under `K_LEAGUE_1` / `secondary_professional`.

### Task 5: Documentation And Lightweight Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/backtest-results.md`

**Commands:**
- `.venv/bin/python -m compileall src scripts`
- `git diff --check`
- `.venv/bin/python scripts/verify_scenarios.py`
- focused in-memory mapper/settlement/grouped-performance assertions

No commits, no unit tests, no large batch tests.
