# SCRIPTS KNOWLEDGE BASE

## OVERVIEW
This directory IS the test suite. 42 files, ~14.7k LOC. No pytest, no unittest, no `tests/` dir.

## THE PATTERN
Every `verify_*.py` follows this exact skeleton:

```python
from __future__ import annotations
from tempfile import TemporaryDirectory
# ... imports ...

def main() -> None:
    with TemporaryDirectory() as tmp:
        # per-script SQLite DB + seed data
        # raw assert statements
    print("<name> verification passed")

if __name__ == "__main__":
    main()
```

Failure = `AssertionError` = non-zero exit. No fixtures, no mocks, no parametrization.

## SCRIPT GROUPS

| Domain | Scripts |
|--------|---------|
| Core | `verify_scenarios` |
| Infra / Contract | `verify_contracts` |
| Datasources | `verify_datasources`, `verify_qqsd_datasource`, `verify_dongqiudi_datasource`, `verify_leisu_datasource` |
| Odds / Value | `verify_devig`, `verify_devig_backtest`, `verify_edge`, `verify_scoring_devig`, `verify_kelly`, `verify_kelly_backtest` |
| Backtest / Strategy | `verify_backtest`, `verify_strategy`, `verify_strategy_health`, `verify_strategy_snapshot`, `verify_portfolio`, `verify_long_horizon_scan`, `verify_paper_bankroll` |
| Live Trading | `verify_live_preflight`, `verify_live_decision`, `verify_live_review`, `verify_live_gate`, `verify_live_audit`, `verify_live_refresh`, `verify_live_stake_allocator`, `verify_execution_queue`, `verify_record_bet_gate`, `verify_clv_brake`, `verify_simulated_execution` |
| Settlement | `verify_settlement`, `verify_finished_evaluation` |
| Daily Ops | `verify_daily_ops` |
| World Cup | `verify_world_cup_advisory`, `verify_world_cup_parlay`, `verify_world_cup_auto_refresh` |
| Production / Deploy | `verify_production_worker` (6273 LOC, end-to-end), `verify_production_status_runtime`, `verify_mcp_server` |

Three non-verify scripts: `optimize_strategy.py`, `research_strategies.py`, `replay_world_cup_parlay_review.py` (research tools, not tests).

## HOW TO RUN

```bash
python scripts/verify_scenarios.py
python scripts/verify_datasources.py --no-remote
python scripts/verify_backtest.py
python scripts/verify_production_worker.py
```

No test runner. Each script is self-executable.

## ANTI-PATTERNS / GOTCHAS

- `verify_datasources.py` respects `FOOTBALL_VALIDATE_REMOTE`. Pass `--no-remote` to avoid spending API quota. Pass `--remote` only when you intend to probe live sources.
- `verify_production_worker.py` is the largest script by far (6273 LOC). It mocks the full production cycle with `FakeIngestion` / `FakeService`. Changes to `production.py` often require updating this script.
- Adding a new `verify_*.py`? Copy the `main()` / `assert` / `print("... passed")` pattern exactly. Do NOT import pytest or unittest.
