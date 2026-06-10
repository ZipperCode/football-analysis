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
- `GET /live/audit`
- `GET /live/preflight`
- `GET /live/review`
- `GET /live/decision`
- `POST /ops/daily`
- `GET /matches/{match_id}/analysis`
- `POST /bets`
- `POST /bets/{bet_id}/settle`
- `POST /bets/settle-open`
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
footballctl live-audit --json
footballctl live-preflight --json
footballctl live-review --json
footballctl live-decision --json
footballctl live-decision --full-profile-audit --json
footballctl live-refresh --date 2026-06-10 --dry-run --json
footballctl live-refresh --date 2026-06-10 --scope live-leagues --dry-run --json
footballctl daily-ops --date 2026-06-10 --json
footballctl picks today --json
footballctl record-bet api_football:12345 1x2 HOME 2.20 0.5 manual --json
footballctl settle-bet <bet-id> --closing-odds 2.05 --json
footballctl settle-open-bets --json
footballctl performance --by-league --json
```

`footballctl backtest portfolio --json` reports the current fast multi-strategy candidate set. Use `footballctl backtest portfolio --scan-phases --leagues I1 --season-phases middle --json` for targeted league/phase overlay scans. `footballctl backtest optimize` and `footballctl backtest walk-forward` accept `--season-phases all,early,middle,late` for slower phase-filtered optimization runs.

Validated strategy profiles are listed under `strategy_profiles` in `config/default.yaml`. Live recommendations include a matched profile in `odds_basis.strategy_profile` and `score_breakdown.strategy_profile` when the current league, market, and selection match the backtested pool. The score also includes `league_profile` and `strategy_confidence_class` so production review can distinguish validated strategies from paper-only candidates.

Run `footballctl backtest profile-audit --json` before production rollout to verify configured strategy profiles still match the current backtest portfolio.

Run `footballctl odds-readiness --json` before paper betting or production review. It checks today/future matches, stored odds snapshots, odds freshness, bookmaker count, market averages, best prices, active strategy profile coverage, and per-league readiness. A status below `ready` means the current live odds store is not strong enough for production-grade picks, even if historical backtests are positive. Use `refresh_requirements` to see the exact active profile, refresh league, market, selection, bookmaker minimum, and missing ready-match count that must be filled before the profile can support real stakes.

Run `footballctl live-preflight --json` immediately before any real-money action. It combines odds readiness and the live trading gate into one machine-readable report. Only `status: "ready"` with `ready_to_bet: true` and `action: "place_approved_live_bets"` allows manually recording a real-platform bet. `paused`, `blocked`, `no_trade`, or `no_matches` means observe only or paper only. `footballctl record-bet` rejects real-platform stakes unless they match an approved live recommendation and stake cap.

Run `footballctl daily-ops --date YYYY-MM-DD --json` as the normal daily operating command after odds/results refresh. It batch-settles open bets, reports performance, runs live review, and includes the same preflight gate used before real-money action. By default it does not call remote result ingestion; add `--ingest-results --source api_football --league <CODE>` only when you intentionally want it to spend API quota refreshing finished scores first.

Run `footballctl live-decision --json` as the final reproducible go/no-go snapshot before real-money action. By default it uses a fast profile contract audit, then combines odds readiness, live review, preflight, live thresholds, and reproducibility inputs in one report. The non-JSON operator summary stays under 30 lines and shows odds refresh requirements plus the closest blocked candidates. The JSON includes `reproducibility.profile_audit_mode: "contract"` for this intraday mode. Run `footballctl live-decision --full-profile-audit --json` or `footballctl backtest profile-audit --json` for the heavier portfolio drift audit before rollout, after strategy changes, or as a scheduled daily control.

When `live-decision` or `live-preflight` returns `action: "refresh_fixtures_and_odds"`, run `footballctl live-refresh --date YYYY-MM-DD --dry-run --json` first to inspect the exact active-profile leagues and sources that would be refreshed without spending quota. Then run the same command without `--dry-run` to refresh fixtures and odds, followed by `footballctl live-decision --json`. By default this targets only leagues mapped from active strategy profiles, currently EPL and Serie A, and uses `auto` source selection so each league resolves to the first configured fixture and odds provider it can use. If the executed default refresh returns `active_profile_refresh_empty:<leagues>` plus `consider_scope_live_leagues`, the active-profile calendar has no usable markets for that date; use `--scope live-leagues` only then, or when intentionally scanning every non-paper live league for low-stake tier-policy opportunities. `live-leagues` scans do not spend fallback odds-source quota by default; add `--allow-odds-fallback` only when you intentionally want `auto` odds refresh to try the next mapped provider after the preferred provider is empty or fails. The dry-run report also flags source mapping gaps such as `fixtures_source_unmapped:<league>:<source>` or `odds_source_unmapped:<league>:<source>` when a fixed source is requested and cannot serve that league.

The live gate also blocks stale market data and pauses real stakes after recent performance deterioration. Defaults in `live_trading` require the matched market odds to be at most `max_odds_age_minutes: 90` minutes old, stop live staking after 3 consecutive settled losses, or when the last 8 settled bets include at least 5 results and reach either `max_rolling_loss_units: 2.0` or `min_rolling_roi: -0.25`. These are account-level brakes; they do not change historical strategy ROI, they only decide whether the next candidate may use real stake.

Real-platform `record-bet` is the final execution guard. It rejects unmatched live recommendations, stakes above the approved cap, cumulative duplicate real stakes on the same match/market/selection, execution odds below the approved recommendation price after `max_execution_odds_slippage`, and real-platform records at or after kickoff. Paper, paper-trading, and simulation records remain allowed for observation.

Run `footballctl live-review --json` after settlement or use the `live_review` block inside `daily-ops`. It reviews settled profile and league evidence without changing config automatically. Defaults require at least `review_min_settled_bets: 6`; negative ROI or negative CLV recommends demotion, and ROI at or below `review_pause_roi: -0.15` with negative CLV recommends `pause_live`. Profile actions `pause_live` and `demote_to_paper` are also consumed by the live gate, so the next matching recommendation is forced back to `paper_candidate` until the review evidence recovers or config is changed intentionally.

The live ingestion list now uses league tiers. Elite club leagues and major tournaments are configured for deeper analysis, while smaller professional leagues use a stricter low-stake live policy. Current configured coverage includes EPL, La Liga, Serie A, Bundesliga, Ligue 1, UEFA Champions League, FIFA World Cup, Euro Championship, Copa America, J1, A-League, K League 1, MLS, Brazil Serie A, Argentina Liga Profesional, and Liga MX.

For Odds-API.io, `footballctl ingest odds` uses each league's configured `max_events` by default and batches event odds through `/odds/multi` in groups of up to 10 events. Use `--max-events` to raise or lower that cap for a one-off run; this prevents a league with many future fixtures from exhausting the free hourly quota. Bookmaker coverage is configured in `data_sources.odds_api_io.bookmakers`; increase that list only with bookmaker names supported by the account/API plan, then rerun `footballctl live-refresh --date YYYY-MM-DD --dry-run --json` to confirm the active-profile refresh requirements.

Small-league picks that pass both the base score and the `secondary_professional` tier policy can enter `picks_today` as `recommended` with `strategy_confidence_class: secondary_live_small_stake` and a capped stake. If they pass the base score but fail the stricter tier policy, they are returned as `paper_candidate` with `stake_units: 0`.

After enabling small-league live picks, the next production step is settled-result tracking by league: record outcomes and closing odds, then review ROI, CLV, hit rate, and downgrade/upgrade decisions for each small league separately.

Result ingestion and settlement are now available for the first review loop. `footballctl ingest results` refreshes finished match status and scores through the configured fixture provider. `footballctl settle-open-bets --json` settles every open bet that has a final score and skips unfinished matches; `footballctl settle-bet` remains available for a single bet or manual correction. Settlement can infer results from stored final scores for `1x2`, Asian handicap selections such as `AH_AWAY(+0.5)` or `AH_HOME(-0.25)`, and totals such as `OVER 2.5` or `UNDER:2.5`. It records `win`, `loss`, `void`, `half_win`, or `half_loss`, and can store `closing_odds` for CLV. Use explicit `--result win|loss|void|half_win|half_loss` only for manual correction or unsupported selection notation.

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
python scripts/verify_live_preflight.py
python scripts/verify_live_review.py
python scripts/verify_live_decision.py
python scripts/verify_settlement.py
python scripts/verify_daily_ops.py
python scripts/verify_datasources.py --no-remote
python scripts/verify_backtest.py
python scripts/verify_strategy.py
footballctl live-preflight --json
footballctl live-decision --json
footballctl daily-ops --date 2026-06-10 --json
footballctl picks today --json
```

Current real backtest evidence is summarized in `docs/backtest-results.md`. The current robust candidate is E0 all-season home value: walk-forward ROI `+5.02%` over 176 bets, with 3/3 positive folds and `+1.82%` average CLV. The current high-yield candidate is I1 Asian handicap away value: walk-forward ROI `+11.95%` over 180 bets, with 2/3 positive folds and `+5.28%` average CLV. Fixed-parameter I1 middle-season filtering is a smaller-sample supplemental candidate: ROI `+17.53%` over 70 bets, with 3/3 positive folds and `+4.79%` average CLV.
