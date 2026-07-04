# PROJECT KNOWLEDGE BASE

**Generated:** 2026-07-03 · **Commit:** ac7bf82 · **Branch:** main

## OVERVIEW
Pre-match football value-betting analysis backend. Python 3.11+, FastAPI + Typer CLI + MCP server over SQLAlchemy (SQLite local / Postgres in Docker). Ingests fixtures/odds/results, scores value bets, gates real-money staking behind multi-layer risk checks. No auto-placement, no in-play, no ML.

## STRUCTURE
```
football-analysis/
├── src/football_analysis/   # flat package, 43 modules (see its AGENTS.md)
│   └── datasources/         # 8 external-API adapters (see its AGENTS.md)
├── scripts/                 # 42 verify_*.py — the test suite (see its AGENTS.md)
├── config/default.yaml      # single 748-line runtime config, env-overridable
├── data/historical/         # football-data.co.uk CSVs by season (0304..2526)
├── docs/                    # design specs, plans, backtest-results.md
├── Dockerfile               # python:3.12-slim, CMD uvicorn api:app :8000
└── docker-compose.yml       # postgres17 + api + worker
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Add/change a CLI command | [cli.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/cli.py) | 3088 LOC Typer app; `footballctl` |
| Production worker/deploy/broker | [production.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/production.py) | 7067 LOC monolith |
| HTTP routes | [api.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/api.py) | 45 FastAPI routes |
| Business orchestration | [service.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/service.py) | `AnalysisService`, `get_service()` |
| Scoring / value logic | [scoring.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/scoring.py), [strategy.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/strategy.py) | |
| Real-money risk gate | [live_gate.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/live_gate.py) | staking brakes |
| ORM models | [models.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/models.py) | Match, OddsSnapshot, Bet |
| Config load | [settings.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/settings.py) | YAML + env, Pydantic |
| Runtime tuning | [config/default.yaml](file:///home/zipper/Projects/football-analysis/config/default.yaml) | leagues, strategy_profiles, live_trading |
| MCP tools | [mcp_server.py](file:///home/zipper/Projects/football-analysis/src/football_analysis/mcp_server.py) | 8 tools, no broker placement |

## CODE MAP
| Symbol | Type | Location | Refs | Role |
|--------|------|----------|------|------|
| `AnalysisService` | Class | service.py:34 | ~30 files | Central orchestrator; every entry point routes here |
| `get_service` / `get_api_service` | Fn | service.py:834 | api, cli, scripts | Service factory from config |
| `app` (Typer) | Var | cli.py:18 | entry `footballctl` | Root CLI, ~40 subcommands |
| `app` (FastAPI) | Var | api.py:31 | `uvicorn ...api:app` | 45 routes |
| `run_production_worker` | Fn | production.py:456 | cli, worker | Hourly production loop |
| `run_production_cycle` | Fn | production.py:116 | cli, mcp | One production pass |
| `ClientContext` | dataclass | datasources/base.py:11 | all adapters | Shared datasource context |

## ENTRY POINTS
- `footballctl` → `football_analysis.cli:app` (also `python -m football_analysis`)
- `football-analysis-mcp` → `football_analysis.mcp_server:main` (stdio MCP)
- `uvicorn football_analysis.api:app` (Docker CMD, port 8000 → host 18000)
- Docker worker runs `footballctl production-worker-env` in a loop

## CONVENTIONS
- Every `.py` starts with `from __future__ import annotations`.
- Type annotations on all signatures; prefer `@dataclass(frozen=True)` / Pydantic v2 for DTOs.
- Config comes from `config/default.yaml` + env overrides via `settings.py`; never hardcode.
- Datasources subclass the `datasources/base.py` adapter pattern.
- Comments/docs/commit messages in Chinese; code identifiers in English.

## ANTI-PATTERNS (THIS PROJECT)
- No pytest/unittest, no `tests/` dir — tests are `scripts/verify_*.py` with raw `assert` (see scripts/AGENTS.md). Do NOT introduce a test framework without asking.
- No ruff/black/mypy/isort/pre-commit configured despite cache dirs in `.gitignore` — do not assume a formatter runs.
- Do not spend remote API quota by default: `FOOTBALL_VALIDATE_REMOTE` gates datasource probes; worker defaults `dry-run`.
- Broker (Betfair) auto-placement stays disabled; MCP never exposes order placement.
- `.env` contains live secrets — never echo values or commit it.

## COMMANDS
```bash
python -m pip install -e .
# lightweight acceptance (the "test suite")
python -m compileall src scripts
python scripts/verify_scenarios.py
python scripts/verify_datasources.py --no-remote
python scripts/verify_backtest.py
# run surfaces
footballctl picks today --json
uvicorn football_analysis.api:app --reload
docker compose up --build            # postgres + api + worker
```

## NOTES
- `production.py` (7067) and `cli.py` (3088) are monoliths; largest refactor candidates.
- `datasources/qqsd.py` (2809) does anti-scrape reverse-engineering (pycryptodome) — most fragile adapter.
- World Cup is an isolated subsystem (`world_cup.py` + `world_cup_parlay.py`) with its own refresh→research→backtest→recommend(advisory→final)→queue flow; uses `world_cup_high_winrate` profile, not the league profiles.
- Backtest evidence lives in [docs/backtest-results.md](file:///home/zipper/Projects/football-analysis/docs/backtest-results.md).
