# football-analysis

足球赛前价值投注多 Agent 分析系统的 v1 骨架实现。

## 当前范围

- FastAPI 后端：比赛分析、今日精选、下注回写、表现统计、数据源健康。
- JSON CLI：给 Hermes skill/tool/cron 调用。
- 配置：`config/default.yaml` + env 覆盖。
- 存储：本地默认 SQLite；Docker Compose 使用 Postgres；结构化表 + raw payload 审计。
- 数据源：免费源优先，支持缓存、配额计数、脱敏 source request 记录。
- 采集：fixtures、odds、standings、football-data.co.uk historical CSV。
- 评分：赔率价值、消息面/历史信号、数据质量、风险阈值、score breakdown 合并。
- 回测：历史 CSV 导入后计算 ROI/CLV。

v1 不自动下注、不做滚球、不训练 ML、不绕反爬。本轮暂不接入 Telegram，不做网页后台。

## 快速运行

```powershell
python -m pip install -e .
footballctl picks today --json
footballctl analyze SAMPLE-001 --json
footballctl sources --json
uvicorn football_analysis.api:app --reload
```

API 默认地址：`http://127.0.0.1:8000`

## 配置

复制 `.env.example` 为 `.env` 后按需填写：

```powershell
Copy-Item .env.example .env
```

常用 env：

- `DATABASE_URL`：默认 `sqlite:///./data/football_analysis.db`
- `FOOTBALL_CONFIG`：默认 `config/default.yaml`
- `API_FOOTBALL_KEY`
- `ODDS_API_IO_KEY`
- `FOOTBALL_DATA_ORG_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## API

- `GET /picks/today`
- `GET /matches/{match_id}/analysis`
- `POST /bets`
- `POST /bets/{bet_id}/settle`
- `GET /performance`
- `GET /performance/by-league`
- `GET /sources/health`
- `POST /jobs/ingest/fixtures`
- `POST /jobs/ingest/results`
- `POST /jobs/ingest/odds`
- `POST /jobs/ingest/standings`
- `GET /backtest/historical`

## 生产核心命令

```powershell
footballctl db init --json
footballctl sources --json
footballctl ingest fixtures --date 2026-06-09 --source api_football --league EPL --json
footballctl ingest odds --date 2026-06-09 --source api_football --league EPL --json
footballctl ingest fixtures --date 2026-06-09 --source api_football --league J1 --json
footballctl ingest odds --source odds_api_io --league K_LEAGUE_1 --max-events 20 --json
footballctl ingest results --date 2026-06-10 --source api_football --league K_LEAGUE_1 --json
footballctl ingest standings --league EPL --season 2025 --source api_football --json
footballctl ingest historical --league E0 --season 2526 --path data/historical/2526/E0.csv --json
footballctl backtest historical --league E0 --season 2526 --json
footballctl backtest portfolio --json
footballctl backtest profile-audit --json
footballctl odds-readiness --json
footballctl picks today --json
footballctl record-bet api_football:12345 1x2 HOME 2.20 0.5 manual --json
footballctl settle-bet <bet-id> --closing-odds 2.05 --json
footballctl performance --by-league --json
```

`footballctl backtest portfolio --json` reports the current fast multi-strategy candidate set. Use `footballctl backtest portfolio --scan-phases --leagues I1 --season-phases middle --json` for targeted league/phase overlay scans. `footballctl backtest optimize` and `footballctl backtest walk-forward` accept `--season-phases all,early,middle,late` for slower phase-filtered optimization runs.

Validated strategy profiles are listed under `strategy_profiles` in `config/default.yaml`. Live recommendations include a matched profile in `odds_basis.strategy_profile` and `score_breakdown.strategy_profile` when the current league, market, and selection match the backtested pool. The score also includes `league_profile` and `strategy_confidence_class` so production review can distinguish validated strategies from paper-only candidates.

Run `footballctl backtest profile-audit --json` before production rollout to verify configured strategy profiles still match the current backtest portfolio.

Run `footballctl odds-readiness --json` before paper betting or production review. It checks today/future matches, stored odds snapshots, bookmaker count, market averages, best prices, active strategy profile coverage, and per-league readiness. A status below `ready` means the current live odds store is not strong enough for production-grade picks, even if historical backtests are positive.

The live ingestion list now uses league tiers. Elite club leagues and major tournaments are configured for deeper analysis, while smaller professional leagues use a stricter low-stake live policy. Current configured coverage includes EPL, La Liga, Serie A, Bundesliga, Ligue 1, UEFA Champions League, FIFA World Cup, Euro Championship, Copa America, J1, A-League, K League 1, MLS, Brazil Serie A, Argentina Liga Profesional, and Liga MX.

For Odds-API.io, `footballctl ingest odds` uses each league's configured `max_events` by default. Use `--max-events` to raise or lower that cap for a one-off run; this prevents a league with many future fixtures from exhausting the free hourly quota.

Small-league picks that pass both the base score and the `secondary_professional` tier policy can enter `picks_today` as `recommended` with `strategy_confidence_class: secondary_live_small_stake` and a capped stake. If they pass the base score but fail the stricter tier policy, they are returned as `paper_candidate` with `stake_units: 0`.

After enabling small-league live picks, the next production step is settled-result tracking by league: record outcomes and closing odds, then review ROI, CLV, hit rate, and downgrade/upgrade decisions for each small league separately.

Result ingestion and settlement are now available for the first review loop. `footballctl ingest results` refreshes finished match status and scores through the configured fixture provider. `footballctl settle-bet` can infer `win/loss` for `1x2` bets from stored final scores and can store `closing_odds` for CLV. Asian handicap and totals should be settled with an explicit `--result win|loss|void` until line-specific settlement rules are added.

远程数据源验证默认关闭，避免误耗免费配额：

```powershell
python scripts/verify_datasources.py --no-remote
```

需要真实探测时再显式开启：

```powershell
$env:FOOTBALL_VALIDATE_REMOTE = "1"
python scripts/verify_datasources.py --remote
```

## Docker

```powershell
docker compose up --build
```

服务会使用 compose 中的 Postgres：

`postgresql+psycopg://football:football@postgres:5432/football_analysis`

## 轻量验收

```powershell
python -m compileall src scripts
python scripts/verify_scenarios.py
python scripts/verify_contracts.py
python scripts/verify_datasources.py --no-remote
python scripts/verify_backtest.py
python scripts/verify_strategy.py
footballctl picks today --json
```

Current real backtest evidence is summarized in `docs/backtest-results.md`. The current robust candidate is E0 all-season home value: walk-forward ROI `+5.02%` over 176 bets, with 3/3 positive folds and `+1.82%` average CLV. The current high-yield candidate is I1 Asian handicap away value: walk-forward ROI `+11.95%` over 180 bets, with 2/3 positive folds and `+5.28%` average CLV. Fixed-parameter I1 middle-season filtering is a smaller-sample supplemental candidate: ROI `+17.53%` over 70 bets, with 3/3 positive folds and `+4.79%` average CLV.
