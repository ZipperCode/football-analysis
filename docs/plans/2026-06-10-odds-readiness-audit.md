# Odds Readiness Audit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a lightweight command that explains whether the current live odds data is sufficient for the configured production strategy profiles.

**Architecture:** Implement a read-only audit module that loads `matches`, `odds`, and `strategy_profiles`, groups odds by match, market, and line, then reports whether each active strategy profile has enough upcoming market coverage. Add a CLI wrapper and documentation. Do not commit, do not add unit tests, and do not run large builds because the project AGENTS.md forbids those actions.

**Tech Stack:** Python, Typer CLI, Pydantic models, existing `StructuredRepository`, existing `Settings`.

---

### Task 1: Add Readiness Models And Audit Logic

**Files:**
- Create: `src/football_analysis/odds_readiness.py`

**Steps:**
1. Load `Match` and `OddsSnapshot` records from the repository.
2. Restrict coverage to today/future matches by default.
3. Group odds by `(match_id, market_type, line)`.
4. Count sources, bookmakers, selections, market averages, best prices, and matching strategy profiles.
5. Produce `ready`, `partial`, or `insufficient` status for each active profile and the whole report.

### Task 2: Add CLI Command

**Files:**
- Modify: `src/football_analysis/cli.py`

**Steps:**
1. Add `footballctl odds-readiness`.
2. Support `--json`, `--min-bookmakers`, `--min-profile-matches`, and `--include-past`.
3. Keep text output compact with one row per active strategy profile.

### Task 3: Fix Production Profile Matching Gaps

**Files:**
- Modify: `src/football_analysis/scoring.py`
- Modify: `config/default.yaml`

**Steps:**
1. Normalize live Asian handicap `HOME/AWAY` selections to `AH_HOME/AH_AWAY` when the market is `asian_handicap`.
2. Add the Serie A league mapping needed by active I1 strategy profiles.

### Task 4: Document And Verify

**Files:**
- Modify: `README.md`
- Modify: `docs/backtest-results.md`

**Steps:**
1. Document when to run `footballctl odds-readiness --json`.
2. Verify with `compileall`, `git diff --check`, `footballctl odds-readiness --json`, `footballctl picks today --json`, and the existing scenario/strategy scripts.
