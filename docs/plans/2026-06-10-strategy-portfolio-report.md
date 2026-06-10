# Strategy Portfolio Report Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a reproducible strategy portfolio report that ranks multiple walk-forward candidates by return, stability, sample size, and CLV.

**Architecture:** Keep production match scoring unchanged. Add portfolio summary models and strategy helpers that reuse existing historical `StrategyResult` and `WalkForwardResult` calculations, then expose them through a JSON CLI command. The report is a research and deployment-readiness artifact, not an automatic betting engine.

**Tech Stack:** Python 3.11+, Pydantic 2, Typer CLI, existing SQLAlchemy repository and football-data.co.uk historical rows.

---

### Task 1: Portfolio Result Models

**Files:**
- Modify: `src/football_analysis/strategy.py`

Steps:
- Add lightweight Pydantic models for a strategy portfolio item and portfolio report.
- Include league, strategy name, phase set, fold count, settled bets, profit, ROI, positive folds, average CLV, worst fold ROI, stability label, and parameters.
- Keep models in `strategy.py` to avoid introducing a new module for a small reporting layer.

Verification:
- Run `python -m compileall src scripts`.

### Task 2: Portfolio Builder

**Files:**
- Modify: `src/football_analysis/strategy.py`

Steps:
- Add a builder that runs a fixed list of candidate profiles:
  - E0 robust all-season candidate.
  - I1 high-yield all-season candidate.
  - I1 middle-season supplemental candidate.
- Add an opt-in scan mode for league/phase pairs so the default report stays fast.
- Compute stability labels from current evidence:
  - `robust`: at least 100 settled bets, all folds positive, positive CLV.
  - `high_yield`: at least 150 settled bets, ROI at least 10%, at least 2/3 positive folds, positive CLV.
  - `supplemental`: at least 60 settled bets, ROI at least 8%, at least 2/3 positive folds, positive CLV.
  - `reject_unstable`: everything else.
- Preserve fallback metadata so failed training candidates do not look production-ready.

Verification:
- Run the builder through a small Python one-liner and confirm it returns JSON-serializable Pydantic models.

### Task 3: CLI Surface

**Files:**
- Modify: `src/football_analysis/cli.py`
- Modify: `README.md`

Steps:
- Add `footballctl backtest portfolio --json`.
- Support default seasons `2122,2223,2324,2425,2526`.
- Add `--scan-phases`, `--leagues`, and `--season-phases` for targeted or full league/phase scans.
- Output JSON only when requested, matching existing CLI style.
- Document the command as a research/report command, not an auto-betting command.

Verification:
- Run `footballctl backtest portfolio --json | python -m json.tool`.

### Task 4: Evidence Documentation

**Files:**
- Modify: `docs/backtest-results.md`

Steps:
- Add a short portfolio summary section with current E0 robust, I1 high-yield, and I1 middle supplemental candidates.
- State that production use still requires live paper tracking before real staking.

Verification:
- Run `git diff --check`.

### Task 5: Production Profile Metadata

**Files:**
- Modify: `config/default.yaml`
- Modify: `src/football_analysis/settings.py`
- Modify: `src/football_analysis/scoring.py`
- Modify: `README.md`
- Modify: `docs/backtest-results.md`

Steps:
- Add validated strategy profiles to configuration.
- Parse strategy profile settings without changing recommendation thresholds.
- Attach matched strategy profile metadata to live recommendation `odds_basis` and `score_breakdown`.
- Keep recommendations non-blocking: unmatched profiles remain visible as `matched: false`, not rejected.

Verification:
- Run `python scripts/verify_scenarios.py`.
- Run `footballctl picks today --json` and confirm at least one seeded pick includes `strategy_profile.matched`.
- Run `python scripts/verify_strategy.py` to confirm backtest baselines remain stable.

### Task 6: Strategy Profile Drift Audit

**Files:**
- Modify: `src/football_analysis/strategy.py`
- Modify: `src/football_analysis/cli.py`
- Modify: `README.md`
- Modify: `docs/backtest-results.md`

Steps:
- Add a read-only profile audit report that compares configured profiles with the current default portfolio.
- Report matched, stale, missing-from-config, and missing-from-portfolio entries.
- Add `footballctl backtest profile-audit --json`.
- Keep the command read-only; it must not rewrite config.

Verification:
- Run `footballctl backtest profile-audit --json` and confirm `passed` is true for current data.
- Run `python scripts/verify_strategy.py` to confirm backtest baselines remain stable.

### Final Verification

Run:

```bash
python -m compileall src scripts
python scripts/verify_strategy.py
footballctl backtest portfolio --json
git diff --check
```

Expected:
- Commands exit 0.
- Portfolio JSON parses.
- Current E0 and I1 baseline strategy verification still passes.
- No git commit is performed.
