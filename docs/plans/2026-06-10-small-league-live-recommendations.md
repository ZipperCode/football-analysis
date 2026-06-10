# Small League Live Recommendations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let configured smaller professional leagues produce real-stake recommendations through a stricter low-stake tier policy.

**Architecture:** Add a tier policy configuration layer and use it inside `score_match` after the base value/risk score is calculated. Small leagues can be marked `live`, but their recommendations must pass a stricter policy and receive a capped stake. Existing validated strategy behavior stays unchanged.

**Tech Stack:** Python, Pydantic settings, Typer CLI, SQLite-backed repository.

---

### Task 1: Add Tier Policy Settings

**Files:**
- Modify: `src/football_analysis/settings.py`
- Modify: `config/default.yaml`

**Steps:**
1. Add `TierPolicySettings` with `min_data_quality`, `min_value_score`, `max_risk_score`, `min_confidence`, `max_stake_units`, `min_bookmakers`, and `label`.
2. Add `tier_policies` to `Settings`.
3. Configure `secondary_professional` with stricter gates and low stake.
4. Change selected secondary leagues to `strategy_mode: live` and `paper_only: false`.

**Verification:**
- Run an in-memory config assertion that secondary policy loads and selected leagues are live.

### Task 2: Apply Tier Policy In Scoring

**Files:**
- Modify: `src/football_analysis/scoring.py`

**Steps:**
1. Count unique bookmakers from the odds snapshots passed to `score_match`.
2. After base score gates, evaluate the league tier policy.
3. If a secondary live candidate passes the stricter policy, keep `recommended`, cap stake, and mark `strategy_confidence_class` as `secondary_live_small_stake`.
4. If it fails the stricter policy but passed base scoring, downgrade to `paper_candidate` with `stake_units: 0`.
5. Include policy details in `odds_basis` and `score_breakdown`.

**Verification:**
- Run a focused in-memory assertion where K League passes policy and returns `recommended` with capped stake.
- Run a second assertion where K League has too few bookmakers and returns `paper_candidate`.

### Task 3: Keep Main Picks Compatible

**Files:**
- Read: `src/football_analysis/service.py`
- No change expected unless verification shows `picks_today` mishandles new labels.

**Steps:**
1. Confirm `picks_today` still selects only `RecommendationStatus.recommended`.
2. Verify small-league live candidates enter the same list only when their status is `recommended`.

**Verification:**
- Run `footballctl picks today --json` and assert the seeded EPL pick remains `validated_strategy`.

### Task 4: Update Readiness And Documentation

**Files:**
- Modify: `src/football_analysis/odds_readiness.py`
- Modify: `README.md`
- Modify: `docs/backtest-results.md`

**Steps:**
1. Surface each league's live/paper mode and tier policy in readiness output where useful.
2. Update docs to state that smaller professional leagues can produce real low-stake recommendations only after stricter tier gates pass.
3. Document the next phase: settled-result tracking and promotion/demotion by league.

**Verification:**
- Run `footballctl odds-readiness --json` and assert league coverage still reports 16 leagues.

### Task 5: Lightweight Final Verification

**Commands:**
- `.venv/bin/python -m compileall src scripts`
- `git diff --check`
- `.venv/bin/python scripts/verify_scenarios.py`
- focused in-memory scoring assertions
- `footballctl odds-readiness --json`
- `footballctl picks today --json`

No commits, no unit tests, no large batch backtests.
