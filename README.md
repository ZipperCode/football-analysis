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

v1 不直接对接投注平台自动下单、不做滚球、不训练 ML、不绕反爬；当前已支持生产 worker 的 Telegram 告警开关和实盘执行队列，不做网页后台。

## 快速运行

```powershell
python -m pip install -e .
footballctl picks today --json
footballctl analyze SAMPLE-001 --json
footballctl sources --json
uvicorn football_analysis.api:app --reload
```

本地 API 默认地址：`http://127.0.0.1:8000`。Docker Compose 默认映射到 `http://127.0.0.1:18000`，可用 `API_PORT` 覆盖。

## 配置

复制 `.env.example` 为 `.env` 后按需填写：

```powershell
Copy-Item .env.example .env
```

常用 env：

- `DATABASE_URL`：默认 `sqlite:///./data/football_analysis.db`
- `FOOTBALL_CONFIG`：默认 `config/default.yaml`
- `FOOTBALL_ADMIN_TOKEN`：可选；设置后，API 的所有 POST/PUT/PATCH/DELETE 端点，以及会执行命令或消耗远程配额的 GET 开关，必须带 `X-Football-Admin-Token: <token>` 或 `Authorization: Bearer <token>`；`/healthz`、生产状态和部署巡检等只读 GET 仍可匿名用于监控。
- `FOOTBALL_PRODUCTION_API_URL`：宿主机生产巡检默认 API 地址，默认 `http://127.0.0.1:18000`；`footballctl production-ops-check` 会用它查询正在运行的 Docker/API 生产栈，避免宿主机 CLI 默认读本地 SQLite。
- `API_BIND_HOST`：Docker Compose API 绑定地址，默认 `127.0.0.1`；需要由反向代理或局域网访问时再显式改为目标地址或 `0.0.0.0`。
- `API_PORT`：Docker Compose API 主机端口，默认 `18000`
- `PYTHON_IMAGE`：Docker 构建基础镜像，默认 `python:3.12-slim`
- `PIP_INDEX_URL`：Docker 构建 pip 源，默认 `https://pypi.org/simple`
- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`：Docker Compose Postgres 凭证；默认值只用于本地开发，生产 `.env` 必须替换为独立强密码。
- `POSTGRES_BIND_HOST` / `POSTGRES_PORT`：Docker Compose Postgres 主机绑定，默认 `127.0.0.1:5432`；生产通常不应对外网暴露数据库端口。
- `PRODUCTION_DEPLOY_TARGET`：Docker worker 启动门禁和 healthcheck 目标，默认 `worker`；准备好真实成交记录链路后可升为 `record-only`，准备好合法 broker 后可升为 `broker-live`
- `WORKER_*`：Docker worker 生产参数，`.env.example` 已列出默认值；常用项包括 `WORKER_EXECUTION_MODE`、`WORKER_DATA_APPLY_MODE`、`WORKER_BROKER_DISCOVERY_MODE`、`WORKER_BROKER_EXECUTION_MODE`、`WORKER_REQUIRE_DEPLOY_READY`
- `API_FOOTBALL_KEY`
- `ODDS_API_IO_KEY`
- `FOOTBALL_DATA_ORG_TOKEN`
- `THE_ODDS_API_KEY`：可选，The Odds API 付费历史赔率/补充赔率源，默认禁用。
- `SPORTMONKS_TOKEN`：可选，Sportmonks Football API/Premium Odds Feed，默认禁用；启用具体联赛前还需要在对应 league 配置 `sportmonks_league_id`。
- `QQSD_C_CK`：世界杯/主要杯赛刷新首选 QQSD token；缺失时 `world-cup refresh-data` 会显式返回 `missing_required_env:QQSD_C_CK`。
- `EXA_API_KEY` / `FIRECRAWL_API_KEY` / `TAVILY_API_KEY`：世界杯赛前研究至少需要其中一个；缺失时 final 阶段不会升级到实盘队列。
- `BETFAIR_APP_KEY`：可选，Betfair Exchange API-NG application key，用于授权 broker 对接。
- `BETFAIR_SESSION_TOKEN`：可选，Betfair Exchange API-NG session token，用于授权 broker 对接。
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## API

生产 API 默认可本地无 token 运行；部署到共享网络或公网前应设置 `FOOTBALL_ADMIN_TOKEN`。启用后，状态/health/deploy-check/doctor 等只读 GET 仍可供监控读取，所有写入、执行、远程抓取或下单相关端点需要管理 token。

- `GET /healthz`
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
- `GET /production/status`
- `GET /production/health`
- `GET /production/preflight`
- `GET /production/onboarding`
- `GET /production/onboarding-apply-plan`
- `GET /production/deploy-check`
- `GET /production/runtime-security`
- `GET /production/deployment-doctor`
- `POST /production/config-plan`
- `GET /production/data-plan`
- `POST /production/profile-promotions`
- `POST /production/data-apply`
- `GET /production/execution-queue`
- `GET /production/broker-plan`
- `POST /production/broker-discovery`
- `POST /production/broker-execute`
- `POST /production/execute`
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
footballctl backtest kelly --league I1 --family asian-away --quick --json
footballctl backtest profile-audit --json
footballctl odds-readiness --json
footballctl live-audit --json
footballctl live-preflight --json
footballctl live-review --json
footballctl live-decision --json
footballctl live-decision --full-profile-audit --json
footballctl production-status --json
footballctl production-health --json
footballctl production-preflight --json
footballctl production-onboarding --json
footballctl production-onboarding-apply-plan --json
footballctl production-deploy-check --json
footballctl production-runtime-security --json
footballctl production-runtime-secrets --json
footballctl production-ops-check --json
footballctl production-deployment-doctor --json
footballctl production-config-plan --json
footballctl production-data-plan --json
footballctl production-profile-promote --json
footballctl production-data-apply --json
footballctl production-execution-queue --json
footballctl production-broker-plan --json
footballctl production-broker-discovery --json
footballctl production-broker-execute --json
footballctl production-execute --json
footballctl sources-the-odds-api-sports --json
footballctl ingest historical-odds --source the_odds_api --league EPL --snapshot-time 2027-01-03T12:00:00Z --json
footballctl live-refresh --date 2026-06-10 --dry-run --json
footballctl live-refresh --date 2026-06-10 --scope live-leagues --dry-run --json
footballctl world-cup refresh-data --date 2026-06-14 --json
footballctl world-cup research --hours 48 --provider auto --json
footballctl world-cup backtest --json
footballctl world-cup recommend --date 2026-06-14 --stage advisory --json
footballctl world-cup recommend --date 2026-06-14 --stage final --json
footballctl production-execution-queue --league WORLD_CUP --json
footballctl daily-ops --date 2026-06-10 --json
footballctl picks today --json
footballctl record-bet api_football:12345 1x2 HOME 2.20 0.5 manual --json
footballctl settle-bet <bet-id> --closing-odds 2.05 --json
footballctl settle-open-bets --json
footballctl performance --by-league --json
```

`footballctl backtest portfolio --json` reports the current fast multi-strategy candidate set. Use `footballctl backtest portfolio --scan-phases --leagues I1 --season-phases middle --json` for targeted league/phase overlay scans. `footballctl backtest optimize` and `footballctl backtest walk-forward` accept `--season-phases all,early,middle,late` for slower phase-filtered optimization runs.

`footballctl backtest kelly --league I1 --family asian-away --quick --json` converts the top long-horizon candidate into a bankroll curve report with target CAGR and max-drawdown checks. It is an audit/report command, not proof of guaranteed annual profit. Under the default 10000u bankroll baseline, the current I1 long-horizon candidate has positive ROI but does not pass the 20% CAGR target because production stake caps keep exposure deliberately small.

Validated strategy profiles are listed under `strategy_profiles` in `config/default.yaml`. Live recommendations include a matched profile in `odds_basis.strategy_profile` and `score_breakdown.strategy_profile` when the current league, market, and selection match the backtested pool. The score also includes `league_profile` and `strategy_confidence_class` so production review can distinguish validated strategies from paper-only candidates.

Run `footballctl backtest profile-audit --json` before production rollout to verify configured strategy profiles still match the current backtest portfolio.

Run `footballctl odds-readiness --json` before paper betting or production review. It checks today/future matches, stored odds snapshots, odds freshness, bookmaker count, market averages, best prices, active strategy profile coverage, and per-league readiness. A status below `ready` means the current live odds store is not strong enough for production-grade picks, even if historical backtests are positive. Use `refresh_requirements` to see the exact active profile, refresh league, market, selection, bookmaker minimum, and missing ready-match count that must be filled before the profile can support real stakes.

Run `footballctl live-preflight --json` immediately before any real-money action. It combines odds readiness and the live trading gate into one machine-readable report. Only `status: "ready"` with `ready_to_bet: true` and `action: "place_approved_live_bets"` allows manually recording a real-platform bet. `paused`, `blocked`, `no_trade`, or `no_matches` means observe only or paper only. `footballctl record-bet` rejects real-platform stakes unless they match an approved live recommendation and stake cap.

Run `footballctl daily-ops --date YYYY-MM-DD --json` as the normal daily operating command after odds/results refresh. It batch-settles open bets, reports performance, runs live review, and includes the same preflight gate used before real-money action. By default it does not call remote result ingestion; add `--ingest-results --source api_football --league <CODE>` only when you intentionally want it to spend API quota refreshing finished scores first.

Run `footballctl live-decision --json` as the final reproducible go/no-go snapshot before real-money action. By default it uses a fast profile contract audit, then combines odds readiness, live review, preflight, live thresholds, and reproducibility inputs in one report. The non-JSON operator summary stays under 30 lines and shows odds refresh requirements plus the closest blocked candidates. The JSON includes `reproducibility.profile_audit_mode: "contract"` for this intraday mode. Run `footballctl live-decision --full-profile-audit --json` or `footballctl backtest profile-audit --json` for the heavier portfolio drift audit before rollout, after strategy changes, or as a scheduled daily control.

When `live-decision` or `live-preflight` returns `action: "refresh_fixtures_and_odds"`, run `footballctl live-refresh --date YYYY-MM-DD --dry-run --json` first to inspect the exact active-profile leagues and sources that would be refreshed without spending quota. Then run the same command without `--dry-run` to refresh fixtures and odds, followed by `footballctl live-decision --json`. By default this targets only leagues mapped from active strategy profiles, currently EPL and Serie A, and uses `auto` source selection so each league resolves to the first configured fixture and odds provider it can use. If the executed default refresh returns `active_profile_refresh_empty:<leagues>` plus `consider_scope_live_leagues`, the active-profile calendar has no usable markets for that date; use `--scope live-leagues` only then, or when intentionally scanning every non-paper live league for low-stake tier-policy opportunities. `live-leagues` scans do not spend fallback odds-source quota by default; add `--allow-odds-fallback` only when you intentionally want `auto` odds refresh to try the next mapped provider after the preferred provider is empty or fails. The dry-run report also flags source mapping gaps such as `fixtures_source_unmapped:<league>:<source>` or `odds_source_unmapped:<league>:<source>` when a fixed source is requested and cannot serve that league.

The live gate also blocks stale market data and pauses real stakes after recent performance deterioration. Defaults in `live_trading` require the matched market odds to be at most `max_odds_age_minutes: 90` minutes old, stop live staking after 3 consecutive settled losses, or when the last 8 settled bets include at least 5 results and reach either `max_rolling_loss_units: 2.0` or `min_rolling_roi: -0.25`. These are account-level brakes; they do not change historical strategy ROI, they only decide whether the next candidate may use real stake.

Real-platform `record-bet` is the final execution guard. It rejects unmatched live recommendations, stakes above the approved cap, cumulative duplicate real stakes on the same match/market/selection, execution odds below the approved recommendation price after `max_execution_odds_slippage`, and real-platform records at or after kickoff. Paper, paper-trading, and simulation records remain allowed for observation.

Run `footballctl production-execution-queue --json` after production status is ready. It rebuilds the live audit, keeps only current live-gate-approved recommendations, subtracts existing real-platform stake on the same match/market/selection, and emits an idempotency key plus a safe `footballctl record-bet ... --json` command for the remaining stake. Queue items now include `kelly_fraction`, `kelly_stake_units`, `portfolio_adjusted`, `correlation_group`, `mutual_exclusion_tag`, and `expires_at` so manual operators can execute a complete betting slip. Automated executors should still use the actual matched odds and must reject execution below `minimum_execution_odds`.

世界杯使用独立的 `world_cup_high_winrate` profile，不复用 EPL/Serie A 长赛季 profile 作为实盘依据。`WORLD_CUP` 配置为受控 live，但只有 `footballctl world-cup recommend --stage final --json` 写入了 `world_cup_high_winrate.passed: true` 的 `1x2` 推荐后，`production-execution-queue --league WORLD_CUP --json` 才会生成队列；普通评分、AH/大小球或未过 final gate 的候选都会保持 0 仓观察。操作顺序：

```powershell
footballctl world-cup refresh-data --date YYYY-MM-DD --json
footballctl world-cup research --hours 48 --provider auto --json
footballctl world-cup backtest --json
footballctl world-cup recommend --date YYYY-MM-DD --stage advisory --json
footballctl world-cup recommend --date YYYY-MM-DD --stage advisory --parlays --parlay-stake-units 5 --json
footballctl world-cup recommend --date YYYY-MM-DD --stage final --json
footballctl production-execution-queue --league WORLD_CUP --json
footballctl live-decision --league WORLD_CUP --json
```

`advisory --parlays` 会基于新鲜 QQSD/赔率证据生成 3 注 2 串 1，默认每注 5u，支持亚盘和大小球混合串，并输出中文球队名、组合赔率、预计返还和错一腿容错测算；它仍是 advisory-only，不进入生产执行队列。`advisory` 阶段是 T-12h 到 T-6h 的观察建议，始终 `stake_units: 0`；不在窗口内会返回 `world_cup_advisory_window:*` 作为审计原因。`final` 阶段只在 T-90m 到 T-60m 内尝试升级，并要求历史命中率至少 65%、当前 1x2 赔率新鲜且至少 2 个 bookmaker、至少 2 个研究来源交叉验证、包含首发/伤停/球队新闻上下文、存在 Exa/Firecrawl/Tavily 之一的搜索凭证。通过后 A 级 0.5u、B 级 0.25u、世界杯单日上限 1.0u，并仍要经过 `live-decision` 的最终 go/no-go；队列只用于人工按 `minimum_execution_odds` 执行，不自动下单。

Run `footballctl production-execute --json` for the production executor dry-run. It consumes the same queue and returns the exact records it would write without changing the database. After the external placement or operator confirmation is done, pass an execution fill file and add `--execute-records --require-fills --json`; it writes approved queue items into the local real-platform bet ledger through the same `record-bet` live gate, using the queue idempotency key as the bet id. This is record-only automation, not bookmaker/exchange order placement. Fill files are JSON objects keyed by `idempotency_key` or `recommendation_id`, for example `{"production-execution:...": {"odds": 8.95, "stake_units": 0.3, "platform": "real", "external_bet_id": "book:123"}}`.

Run `footballctl production-broker-plan --json` to check whether the current queue can be handed to a legal execution broker. The default disabled template is `betfair_exchange`; it requires an approved Betfair developer account, `BETFAIR_APP_KEY`, `BETFAIR_SESSION_TOKEN`, a configured `stake_currency_per_unit`, and broker market/selection mappings such as `betfair_market_id` plus `betfair_selection_id_AH_HOME` in match external IDs. Until these are present, broker execution remains blocked and the system stays in dry-run/record-only mode.

Run `footballctl production-broker-discovery --json` to build the read-only Betfair `SportsAPING/v1.0/listMarketCatalogue` request preview for each approved execution-queue item. Add `--fetch-remote` only after `BETFAIR_APP_KEY` and `BETFAIR_SESSION_TOKEN` are configured; this performs catalogue reads only, never order placement. Add `--apply-mappings` to persist high-confidence `betfair_market_id`, `betfair_selection_id_<selection>`, and optional `betfair_handicap_<selection>` patches into local `Match.external_ids`. API callers use `POST /production/broker-discovery?fetch_remote=true&apply_mappings=true`.

Run `footballctl production-broker-execute --json` for the broker execution request preview. For Betfair it builds the official API-NG JSON-RPC `SportsAPING/v1.0/placeOrders` request with redacted `X-Application` / `X-Authentication` headers and does not send anything. Live broker POSTs require all broker-plan gates to be ready plus the explicit `--execute-broker-orders` flag; API callers must pass the equivalent `POST /production/broker-execute?execute_broker_orders=true`. Broker responses are returned as raw execution reports for operator/audit review, while local bet-ledger writes still happen through `production-execute --execute-records --require-fills`.

Run `footballctl live-review --json` after settlement or use the `live_review` block inside `daily-ops`. It reviews settled profile and league evidence without changing config automatically. Defaults require at least `review_min_settled_bets: 6`; negative ROI or negative CLV recommends demotion, and ROI at or below `review_pause_roi: -0.15` with negative CLV recommends `pause_live`. Profile actions `pause_live` and `demote_to_paper` are also consumed by the live gate, so the next matching recommendation is forced back to `paper_candidate` until the review evidence recovers or config is changed intentionally.

The live ingestion list now uses league tiers. Elite club leagues and major tournaments are configured for deeper analysis, while smaller professional leagues use a stricter low-stake live policy. Current configured coverage includes EPL, La Liga, Serie A, Bundesliga, Ligue 1, UEFA Champions League, FIFA World Cup, Euro Championship, Copa America, J1, A-League, K League 1, MLS, Brazil Serie A, Argentina Liga Profesional, and Liga MX.

For Odds-API.io, `footballctl ingest odds` uses each league's configured `max_events` by default and batches event odds through `/odds/multi` in groups of up to 10 events. Use `--max-events` to raise or lower that cap for a one-off run; this prevents a league with many future fixtures from exhausting the free hourly quota. Bookmaker coverage is configured in `data_sources.odds_api_io.bookmakers`; increase that list only with bookmaker names supported by the account/API plan, then rerun `footballctl live-refresh --date YYYY-MM-DD --dry-run --json` to confirm the active-profile refresh requirements.

Football-Data extra-league CSVs are supported through `/new/{code}.csv`. Current mappings include `BRA_SERIE_A -> BRA`, `MLS -> USA`, and `J1 -> JPN`; import them with `footballctl ingest historical --league BRA --season 2526 --download --json` and the equivalent `USA`/`JPN` commands. The parser preserves the CSV `Season` column, so these multi-year files populate many backtest seasons in `historical_matches`.

`footballctl production-data-plan --json` turns readiness gaps into tasks. It prefers local/public Football-Data CSV imports when available; otherwise it lists provider applications such as The Odds API historical odds snapshots (`THE_ODDS_API_KEY`) or Sportmonks Premium Odds Feed (`SPORTMONKS_TOKEN`). The Odds API v4 live/upcoming odds adapter is implemented for `h2h`, `spreads`, and `totals`, and historical odds snapshots can be ingested with `footballctl ingest historical-odds --source the_odds_api --league <CODE> --snapshot-time <ISO> --json`; use `footballctl production-historical-odds-plan --league <CODE> --start-time <ISO> --end-time <ISO> --max-snapshots 24 --json` to generate a bounded batch plan with request and credit estimates before spending quota. Sportmonks v3 fixture + pre-match odds ingestion is also implemented through `footballctl ingest fixtures --source sportmonks --league <CODE> --date YYYY-MM-DD --json` and `footballctl ingest odds --source sportmonks --league <CODE> --date YYYY-MM-DD --max-events 20 --json`; it requires `SPORTMONKS_TOKEN`, enabled `data_sources.sportmonks`, and per-league `sportmonks_league_id` verified from the Sportmonks league catalogue. The Odds API stays disabled until a suitable plan, sport key coverage, bookmaker regions, quota, and `THE_ODDS_API_KEY` are confirmed. Use `footballctl sources-the-odds-api-sports --json` for local config review; after setting `THE_ODDS_API_KEY`, add `--fetch-remote` to query `/sports?all=true` and compare configured sport keys. API callers use `GET /sources/the-odds-api/sports?fetch_remote=true`, `GET /production/historical-odds-plan`, and `POST /jobs/ingest/historical-odds`.

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

## Agent / MCP 调用

项目提供 stdio MCP server，供 Codex、Claude Desktop、Hermes 或其他支持 MCP 的 agent 调用当前生产库和分析流水线。

### 接入 Hermes（NousResearch hermes-agent）

架构：**本后端是薄工具层，Hermes 是大脑**。后端通过 MCP 暴露分析/复盘工具，Hermes 侧用 blueprint skill 做 cron 编排、解释、推送、复盘决策。后端不自动下单、不自动改策略配置。完整闭环契约见 `docs/hermes.md`。

按以下三步接入（命令在 Hermes 主机执行；跨机部署时最简单是让 Hermes 与后端跑在同一台机器）。

#### 第 1 步：注册 MCP server

先确认后端已安装、命令可用：

```bash
cd /home/zipper/Projects/football-analysis
python -m pip install -e .
football-analysis-mcp   # 能启动（stdio 挂起不退出）即 OK，Ctrl-C 退出
```

在 Hermes 主机注册：

```bash
hermes mcp add football-analysis --command "football-analysis-mcp"
```

#### 第 2 步：配置环境变量

MCP server 运行时依赖以下环境变量。写入 Hermes 的 `~/.hermes/.env`（不要写真实值到 skill 文件里）：

```bash
FOOTBALL_CONFIG=/home/zipper/Projects/football-analysis/config/default.yaml
DATABASE_URL=sqlite:////home/zipper/Projects/football-analysis/data/football_analysis.db
FOOTBALL_AI_KEY=<你的 OpenAI 兼容 LLM key>
API_FOOTBALL_KEY=<...>
ODDS_API_IO_KEY=<...>
# Telegram 推送（可选，配置后 blueprint 才能发消息）
TELEGRAM_BOT_TOKEN=<...>
TELEGRAM_CHAT_ID=<...>
```

- `FOOTBALL_CONFIG`：配置文件路径，默认 `config/default.yaml`
- `DATABASE_URL`：默认本地 SQLite；Docker Compose 生产栈用 `postgresql+psycopg://football:<POSTGRES_PASSWORD>@127.0.0.1:5432/football_analysis`
- `FOOTBALL_AI_KEY`：AI 概率分析层凭证
- 其余数据源/推送变量的完整清单见 `docs/hermes.md` 第 3 节

验证工具已挂上：进入 Hermes 会话执行 `/suggestions` 或 `hermes cron`，能看到 `football-analysis` 的工具（`get_analysis`、`evaluate_ai_quality`、`review_strategies` 等）即接入成功。

#### 第 3 步：安装 blueprint skill 并启用定时

把两个示例 skill 复制到 Hermes skills 目录：

```bash
cp -r /home/zipper/Projects/football-analysis/docs/hermes-skills/football-daily-picks ~/.hermes/skills/
cp -r /home/zipper/Projects/football-analysis/docs/hermes-skills/football-review-loop ~/.hermes/skills/
```

- `football-daily-picks`：每天 18:00 调 `get_analysis(refresh=true)` 生成推荐并推送 Telegram。
- `football-review-loop`：每天 10:00 依次调 `evaluate_finished_matches` → `evaluate_ai_quality` → `review_strategies` 做赛后复盘与策略评估；策略调整建议需人工确认后方可执行。

**blueprint 装进去不会自动跑**。必须进入 Hermes 的 `/suggestions`，对每个 skill 执行一次 `/suggestions accept <序号>`，才会真正创建 cron。想改触发时点，直接改对应 `SKILL.md` 里 `metadata.hermes.blueprint.schedule` 的 cron 表达式，再重新 `accept`。

#### 接入后的闭环

```
每天 18:00  →  get_analysis(refresh=true)     →  拉数据 + AI 分析 → 推荐推送 Telegram
次日 10:00  →  evaluate_finished_matches      →  已完赛命中率 / ROI
            →  evaluate_ai_quality            →  AI vs 市场 Brier 对比
            →  review_strategies              →  各 profile ROI/CLV 与调整建议（仅建议，人工确认）
```

后端不自动下单、不自动改 `config/default.yaml`；策略调整建议只作为消息呈现，由人拍板。完整 MCP 工具契约、cron 映射与安全边界详见 `docs/hermes.md`。

### 每日推荐与 AI 质量复盘

当 Agent/MCP 不可用时，可直接用 CLI 作为 fallback：

```powershell
footballctl picks today --json
footballctl ai-eval --date YYYY-MM-DD --json
footballctl evaluate-finished --date YYYY-MM-DD --league WORLD_CUP --json
footballctl evaluate-finished --date YYYY-MM-DD --league WORLD_CUP --result "Home vs Away=2-1" --json
```

`picks today` 生成当日评分和 recommendations。
`ai-eval` 对指定日期的已完赛推荐做 Brier score 复盘，比较 AI 概率与市场隐含概率。
`evaluate-finished` 只统计 `recommended` 样本；`analysis_only` / `paper_candidate` / `rejected` 会进入 `excluded_by_reason`，避免把未给出的建议算进成功率。

### MCP 暴露的工具

MCP 默认只暴露分析和数据工具，不暴露真实 broker 下单：

- `production_status`：读取生产状态，不调用远程 provider。
- `production_health`：读取 worker/ingestion heartbeat 健康状态。
- `get_picks_today`：评分今日本地比赛并保存 recommendations。
- `get_analysis`：统一高层入口，可选刷新后返回带 AI 概率的推荐；详见 `docs/hermes.md`。
- `evaluate_finished_matches`：按当前策略复盘已完赛比赛，只统计指定 recommendation status，返回命中率、ROI 和排除原因；不下单。
- `evaluate_ai_quality`：AI 准度校验，返回 Brier score（AI vs 市场）、命中率、brier_improvement；详见 `docs/hermes.md`。
- `review_strategies`：策略评估，返回各 profile 的 ROI/CLV 与建议动作（pause_live / demote_to_paper / 保持）；详见 `docs/hermes.md`。
- `get_live_decision`：读取 reproducible go/no-go 决策快照。
- `get_odds_readiness`：审计当前赔率覆盖。
- `refresh_live_data`：刷新 fixtures/odds；`dry_run=true` 时不消耗远程配额。
- `run_analysis_cycle`：运行一次 analysis-only 生产周期，强制关闭 broker discovery/execution。
- `push_analysis_report`：格式化并推送今日分析建议到 Telegram；`dry_run=true` 只返回文本。

Agent 默认先调 `production_health` 和 `production_status`；需要新数据时再调 `refresh_live_data` 或 `run_analysis_cycle`；需要复盘命中率调 `evaluate_finished_matches`；需要发给人的结果调 `push_analysis_report`。真实下单、Betfair、broker order placement 不通过 MCP 暴露。

生产自动推送分析建议继续用 worker：

```powershell
docker compose up -d postgres api worker
docker compose logs --tail 120 worker
```

worker 的 Telegram 文本包含 `football-analysis advice` 分析建议区块；没有满足阈值的主推时会输出 `advice=none`，不会触发真实下单。

## Docker

```powershell
docker compose up --build
```

服务会使用 Compose 中的 Postgres；连接串由 `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` 生成。本地开发默认值为：

`postgresql+psycopg://football:football@postgres:5432/football_analysis`

生产部署前必须在 `.env` 中替换 `POSTGRES_PASSWORD`。如果密码包含 `@`、`:`、`/` 等 URL 特殊字符，需要先 URL encode，或按部署平台的 secrets/connection-string 机制覆盖容器 `DATABASE_URL`。Compose 默认只把 API 和 Postgres 端口绑定到 `127.0.0.1`；需要外部访问 API 时优先通过反向代理暴露，确实要直接暴露时再设置 `API_BIND_HOST=0.0.0.0`。Postgres 通常保持 localhost 或完全不发布到公网。

Compose 默认启动两个生产进程：

- `api`：FastAPI 服务，默认仅绑定 `http://127.0.0.1:18000`
- `worker`：运行 `footballctl production-worker-env`，由 `.env` 中的 `WORKER_*` 和 `PRODUCTION_DEPLOY_TARGET` 控制生产模式；默认先执行启动前 deploy gate，再进入每小时 auto refresh、跳过 results、`execution_mode=dry-run`、`data_apply_mode=safe`、跳过 backtests、最多执行 3 条安全 data-apply 命令，并输出单行紧凑 JSON/Telegram 告警状态。

如果 Docker Hub 网络不可用，可临时切换到可访问的镜像源：

```powershell
$env:PYTHON_IMAGE = "mcr.microsoft.com/devcontainers/python:1-3.13-bookworm"
$env:PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
docker compose build --build-arg PYTHON_IMAGE=$env:PYTHON_IMAGE --build-arg PIP_INDEX_URL=$env:PIP_INDEX_URL
```

Dockerfile 会先安装依赖层，再安装项目 wheel；常规代码变更不会重新解析全量依赖。Compose 默认设置 `PYTHONUNBUFFERED=1`，worker 的 JSON 报告会实时进入 `docker compose logs worker`。

Telegram 告警默认随 Compose worker 打开，但只有 `.env` 同时配置了 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 才会外发；凭证为空时会在日志中返回一行标准 JSON，例如 `{"telegram": {"sent": false, "skipped_reason": "missing_credentials"}}`，并跳过发送。单次验证：

```powershell
footballctl production-worker --once --skip-results --skip-daily-ops --alert-text --notify-telegram
```

这个 worker 默认先按 active strategy profiles 自动刷新 fixtures 和 odds；如果 active-profile 联赛刷新为空，会自动扩展到 `live-leagues` 补当前可交易联赛数据。执行阶段默认由 `WORKER_EXECUTION_MODE=dry-run` 控制，会在每轮 JSON/告警里给出待执行记录但不写库；改成 `WORKER_EXECUTION_MODE=record-only` 并配置成交 fill 文件后，才会按实际成交回报幂等写入本地真实 bet ledger。Data-apply 阶段由 `WORKER_DATA_APPLY_MODE=dry-run|safe|remote` 控制；Compose 默认使用 `safe`，限制 `WORKER_DATA_APPLY_MAX_COMMANDS=3`，并通过 `WORKER_DATA_APPLY_INCLUDE_BACKTESTS=0` 跳过 backtest/profile-audit 这类重复重任务，只保留公共 historical CSV 下载和其他纯本地安全命令。Broker 阶段默认关闭；需要把 Betfair 映射发现串入同一轮生产流水线时设置 `WORKER_BROKER_DISCOVERY_MODE=dry-run|remote|apply`，需要 broker 订单预览或真实发送时设置 `WORKER_BROKER_EXECUTION_MODE=dry-run|live`。`live` 仍会经过 broker plan、凭证、stake、market/selection mapping 和显式模式多重门控。默认跳过远程 results ingestion，避免免费配额被无意义消耗；赛后需要结算时再显式执行：

注意：`production-worker` 的 fixtures/odds refresh 阶段本身会调用已启用的数据源；`--data-apply-mode dry-run` 只控制 data-apply 阶段。要整轮离线演练 refresh/results/execution/broker 且不消耗 provider 配额，加 `--refresh-dry-run`；该演练仍会写审计 job，但 `production-health` 的关键生产心跳会忽略 `summary.refresh_dry_run=true` 的 cycle，避免演练覆盖真实 worker 健康状态。要只验证 data-apply 命令编排，用 `footballctl production-data-apply --json`。要把 The Odds API 历史赔率快照回补串入 worker/cycle，必须同时给出 `--data-apply-historical-odds-start-time`、`--data-apply-historical-odds-end-time`、`--data-apply-historical-odds-max-snapshots`，并且只有 `--data-apply-mode remote` 才会执行这些付费快照命令。

```powershell
docker compose run --rm api footballctl ingest results --date YYYY-MM-DD --source api_football --league EPL --json
docker compose run --rm api footballctl daily-ops --date YYYY-MM-DD --json
```

需要单次生产巡检而不是常驻 worker 时：

```powershell
footballctl production-cycle --date YYYY-MM-DD --leagues auto --auto-refresh --refresh-scope active-profiles --refresh-dry-run --skip-results --execution-mode off --data-apply-mode dry-run --broker-discovery-mode off --broker-execution-mode off --json
footballctl production-status --json
footballctl production-health --json
footballctl production-readiness --json
footballctl production-data-plan --json
footballctl production-execution-queue --json
footballctl production-broker-plan --json
footballctl production-broker-discovery --json
footballctl production-broker-execute --json
footballctl production-execute --json
footballctl sources-the-odds-api-sports --json
footballctl ingest historical-odds --source the_odds_api --league EPL --snapshot-time 2027-01-03T12:00:00Z --json
footballctl production-worker --once --refresh-dry-run --skip-results --execution-mode off --data-apply-mode dry-run --broker-discovery-mode off --broker-execution-mode off --json
footballctl production-worker --once --leagues EPL --fixed-leagues --fixture-source api_football --odds-source odds_api_io --skip-results --skip-daily-ops --execution-mode off --alert-text
```

`production-status` 只读取本地状态，不主动调用远程数据源；它汇总 live decision、数据表计数、最近 production cycle / ingestion jobs、provider 配额计数、缓存条目、凭证是否存在、缺失/空结果关键生产 job、赔率 readiness、profile 刷新需求和 `production_readiness` 联赛覆盖矩阵。每次 `production-cycle` / `production-worker` 运行都会写入一条 `production_cycle` heartbeat job，包含刷新数量、决策状态、执行 dry-run/record-only 摘要和队列规模；`--refresh-dry-run` 产生的 heartbeat 保留在 `recent_jobs` 中用于审计，但不会被 required job health 当作真实生产心跳。

`production-health` 是监控入口，同样只读本地状态。它在 `production-status` 之上检查关键 job freshness：默认要求 `production_cycle` heartbeat 不超过 90 分钟，fixtures/odds ingestion 不超过 180 分钟；返回 `healthy`、`degraded` 或 `unhealthy`，并在 `job_health` 中给出每类 job 的年龄和最新摘要。最新 job 仍在 freshness 窗口内运行时标记为 `running` 且不触发健康告警，超过窗口仍未完成才标记为 `stale`。如果最近 fixtures/odds 抓取返回空，但当前数据库已有对应 matches/odds 数据且决策仍 ready，该空结果会进入 `warnings`，不会把健康状态降级；对应数据桶也为空时才进入 `issues`。常驻 worker 每小时运行时，这个默认值可以覆盖正常调度抖动。

`production-preflight` 是上线/调度门禁入口，同样只读本地状态。它聚合 health、execution queue、broker plan、data plan 和 profile promotion gate；默认用快速检查确认本地 worker、dry-run/record-only 执行链路是否可运行，并把 broker/data/profile 缺口作为 `warnings`。需要把合法 broker 自动下单作为硬门槛时加 `--require-broker`；需要确认当前必须存在待执行项时加 `--require-execution-queue`；上线前需要验证 profile audit 证据时加 `--profile-audit`，这会运行较重的 strategy profile audit。

`production-preflight.execution_queue` 会额外暴露 `profile_matched_queue_count`、`profileless_queue_count` 和 `tier_policy_queue_count`。`profileless_queue_count > 0` 表示当前待执行项来自已通过的小仓 tier policy，而不是已匹配回测 profile；这不是默认阻断项，但生产执行器应把它作为风控分层信号记录。

`production-onboarding` 是外部生产依赖的只读申请/配置清单。它聚合 data plan、broker plan 和 profile promotion gate，把 Betfair、The Odds API、Sportmonks、stake cap、market/selection mapping 等缺口转换成标准 `actions[]`，包含 `official_url`、缺失 env、受影响联赛和可执行命令提示。它不会调用远程 provider，不会写配置，不会暴露密钥值；API 调用为 `GET /production/onboarding`。

`footballctl production-onboarding-apply-plan --json` 会把 onboarding action 转成上线前可执行/阻断清单。它默认只 dry-run，不执行命令；`ready_commands[]` 只包含本地可审计且仍需 operator 明确批准的写配置动作，例如已经通过 profile audit 的 `production-profile-promote ... --apply --json`。显式加 `--execute-ready` 才会执行这些 ready 命令，并在 `executions[]` 中记录 argv、return code 和 stdout/stderr tail。上线前建议先复制 `config/default.yaml` 到候选文件，再用 `--config-path candidate.yaml --execute-ready --json` 验证补丁效果；只有候选配置通过 deploy-check 后再决定是否对真实配置执行。数据源和 broker 配置动作会复用 `production-config-plan` 的 readiness 规则：对应 env 已存在后，`enable_data_source:*` 可进入 ready；broker stake 需要显式传入 `--broker-stake-currency-per-unit <AMOUNT>`，随后 `set_broker_stake_currency_per_unit:*` 会生成带金额的 ready apply 命令。需要外部账号、密钥、远程 Betfair mapping 或 stake 金额确认的动作会留在 `blocked_reasons` / `manual_required_count` 中；API 调用为 `GET /production/onboarding-apply-plan`。

`footballctl production-candidate-check --json` 会自动完成上面这一步的安全闭环：从 `FOOTBALL_CONFIG` 或 `config/default.yaml` 复制出候选配置（默认写到已忽略的 `build/production-candidates/*.yaml`），只在候选文件上执行 ready apply，然后用候选 settings 重新跑 `production-deploy-check`。返回值包含 `source_config_changed`、`apply_plan`、`apply_passes`、`deploy_check` 和 `config_diff`，用于确认真实源配置未被改动、profile/data source/broker 哪些字段在候选里变化。默认会执行候选文件上的 ready 本地命令，并最多跑 3 轮收敛，让“先写 broker stake，再启用 broker”这类二阶段配置可以在同一个候选检查中完成；需要只看计划时加 `--plan-only`，需要固定候选路径时加 `--candidate-config build/production-candidate.yaml`。如果已经设置 Betfair env 且要测试 broker 配置补丁，加 `--broker-stake-currency-per-unit <AMOUNT>`；需要调整收敛轮数时加 `--max-apply-passes <N>`。API 调用为 `POST /production/candidate-check`。

`footballctl production-onboarding-checklist --markdown` 会把当前 onboarding 状态整理成可执行的生产准备清单，按 provider access、secrets、data source/broker config、stake unit、profile promotion 分组，直接列出官方链接、必需 env、候选 apply 命令和后续验证命令。默认输出 JSON，适合自动化系统消费；需要人工协同时加 `--markdown`，加 `--output` 可以直接写到文件。若要提前渲染 broker stake apply 命令，加 `--broker-stake-currency-per-unit <AMOUNT>`。API 调用为 `GET /production/onboarding-checklist`。

`footballctl production-config-plan --json` 会把 onboarding 中“启用数据源 / broker / 设置 stake 单位金额”的动作转换成可审计配置补丁计划。默认只 dry-run，不写 `config/default.yaml`；只有显式加 `--apply` 才会写入配置。生产启用前默认要求相关凭证已存在，例如 `footballctl production-config-plan --source the_odds_api --json` 需要 `THE_ODDS_API_KEY`，`footballctl production-config-plan --broker betfair_exchange --stake-currency-per-unit <AMOUNT> --json` 需要 `BETFAIR_APP_KEY` 和 `BETFAIR_SESSION_TOKEN`；只有在离线准备临时配置时才使用 `--allow-missing-credentials`。API 调用为 `POST /production/config-plan`。

`production-deploy-check` 是 CI/CD 或服务器启动前的硬部署门。默认目标是 `worker`，只要求 worker/health 可运行；`--target record-only` 还要求本地真实成交记录链路可用；`--target broker-live` 要求 broker 自动下单链路、凭证、stake 和 market/selection mapping 全部就绪；`--target full` 还要求 onboarding actions 全部清零。Compose 默认用 `PRODUCTION_DEPLOY_TARGET=worker` 支持新生产库先跑 dry-run worker；准备好本地真实成交链路后再升为 `record-only`，准备好 Betfair/映射/stake 后再升为 `broker-live`。加 `--fail-on-blocked` 可让 blocked 结果退出码非 0；加 `--fail-on-warnings` 可让任何 warnings 也退出码非 0。API 调用为 `GET /production/deploy-check`，升级门槛时显式传 `?target=record-only` 或 `?target=broker-live`。

`production-runtime-security` 是只读运行环境安全巡检，检查 `FOOTBALL_ADMIN_TOKEN` 是否配置、API/Postgres 是否绑定公网、Postgres 是否仍使用默认密码。默认 `worker` 目标会把本地开发默认值作为 warnings；`broker-live` / `full` 目标会把缺 admin token、默认 Postgres 密码和公网数据库绑定作为 blocked。API 调用为 `GET /production/runtime-security?target=worker`。

`footballctl production-runtime-secrets --json` 会为 `FOOTBALL_ADMIN_TOKEN` 和 `POSTGRES_PASSWORD` 生成运行时 secret bootstrap 计划，但默认隐藏生成值且不写任何文件。需要真正落地时，在私密终端或 CI secret job 里加 `--show-secret-values`，把输出的 env lines 写入生产 secret store 或 `.env`。如果 `postgres-data` volume 已经存在，不能只改 `.env`；返回的 `Rotate existing Postgres password` 步骤会给出 `ALTER USER` 轮换命令，轮换后再 `docker compose up -d postgres api worker`。完成后用 `footballctl production-ops-check --api-url http://127.0.0.1:18000 --target worker --json` 验证真实生产栈。

`footballctl production-ops-check --json` 是宿主机巡检正在运行生产 API/Compose 栈的推荐入口。它默认查询 `FOOTBALL_PRODUCTION_API_URL` 或 `http://127.0.0.1:18000`，聚合 `/healthz`、`/production/status`、`/production/health`、`/production/runtime-security` 和 `/production/deploy-check`，因此检查的是容器里的 Postgres 生产库，而不是宿主机默认 SQLite。需要把较重的候选配置 doctor 一并跑上时加 `--include-doctor`；需要部署脚本硬失败时加 `--fail-on-blocked`，需要 warnings 也失败时再加 `--fail-on-warnings`。

`production-deployment-doctor` 是上线前和服务器巡检的一键聚合入口。它只读当前生产状态、health、runtime-security、deploy-check、onboarding checklist，并默认在已忽略的 `build/production-candidates` 下生成候选配置做 plan-only candidate check；不调用远程 provider，不改真实 `config/default.yaml`。默认目标是 `worker`，需要验证候选配置可以自动收敛时再加 `--execute-candidate-ready`，需要 record-only 或 broker-live 预检时配合 `--target record-only` / `--target broker-live --broker-stake-currency-per-unit <AMOUNT>`。API 调用为 `GET /production/deployment-doctor?target=worker`。

Docker Compose 的 `api` healthcheck 会调用 `GET /healthz`，该端点会执行轻量 DB/repository 读以确认 API 与数据库同时可用。`worker` 启动时默认受 `WORKER_REQUIRE_DEPLOY_READY=1` 保护，会先运行 `production-deploy-check`；目标 blocked 时直接以退出码 2 停止并输出 blocked JSON。`worker` healthcheck 也会运行 `footballctl production-deploy-check --target ${PRODUCTION_DEPLOY_TARGET:-worker} --fail-on-blocked --json`。因此新生产库可以先让 dry-run worker 保持 healthy；把目标升到 record-only 或 broker-live 后，对应链路缺口才会成为硬阻断。

生产就绪需要同一联赛同时具备当前 fixtures、live odds、可回测历史数据、active strategy profile，并且至少一个 profile 通过 `live_enabled: true` 进入实盘风控门。`footballctl production-readiness --json` 可单独输出每个联赛的 `status`、fixtures/odds/history/profile 计数和 `next_actions`，例如 `need_historical_data:BRA`、`need_strategy_profile:USA`、`need_live_odds:SERIE_A`。`footballctl production-data-plan --json` 会把这些缺口转换为可执行命令、provider 候选、凭证 env 和需要人工申请的官方数据源。`paper_only` 或 `blocked` 联赛只能观察或补数据，不能自动下注。

`footballctl production-profile-promote --strategy-code E0 --max-stake-units 0.2 --json` 会生成实盘 profile promotion 补丁计划：先跑 profile audit，要求目标 profile 为 `matched`，并要求每个 profile 有明确 `max_stake_units` 且不超过全局 `live_trading.max_stake_units_per_pick`。`production-data-plan` / `production-preflight` 会在缺 stake cap 时给出保守建议值 `min(live_trading.max_stake_units_per_pick, 0.2)`，并把 onboarding action 转成可直接审计的命令，例如 `footballctl production-profile-promote --strategy-code E0 --max-stake-units 0.2 --apply --json`；默认不写配置，只有显式加 `--apply` 才会把 `live_enabled: true` 和 stake cap 写入 config。API 调用为 `POST /production/profile-promotions`。

`footballctl production-data-apply --json` 会把 `production-data-plan` 中的本地命令整理成可执行批次，默认 dry-run。返回的 `command_summary` 会按 `by_category`、`selected_by_category`、`skipped_by_reason` 和 `next_actions` 汇总为什么选中或跳过，例如远程赔率命令会给出 `remote_command_requires_allow_remote`，未配置 stake 的 profile promotion 会给出 `manual_risk_config_required`。`footballctl production-data-apply --execute --json` 只执行 public historical download 和前置条件满足的本地 backtest/profile-audit；`ingest odds`、fixtures、results 等会消耗远程 provider 配额的命令默认跳过，必须显式加 `--allow-remote`。需要只补公共历史 CSV 时加 `--skip-backtests`；确实要跑前置条件未满足的诊断命令时再加 `--include-blocked-prerequisites`。

真实下注仍只允许在 `production-cycle`、`production-status` 或 `live-decision` 返回 `ready_to_bet: true` 且 `action: "place_approved_live_bets"` 后，通过 `footballctl production-execution-queue --json`、`footballctl production-execute --json`，或 worker/cycle 内置的 `execution` 阶段生成待执行队列，再由人工或外部执行器按队列中的 `record_bet_argv` / `record_bet_command` 执行。外部成交完成后，`footballctl production-execute --execute-records --require-fills --fills-json fills.json --json` 或 `production-cycle/worker --execution-mode record-only --execution-fills-json fills.json --require-execution-fills --json` 会通过同一套 live gate 幂等写入本地真实 bet ledger。`production-cycle --json` 在 ready 时会内嵌 `execution_queue` 和 `execution`，方便 worker 日志或外部编排器直接消费。其他状态都视为观察、补数据或纸面跟踪。

## 轻量验收

```powershell
python -m compileall src scripts
python scripts/verify_scenarios.py
python scripts/verify_contracts.py
python scripts/verify_live_preflight.py
python scripts/verify_live_review.py
python scripts/verify_live_decision.py
python scripts/verify_world_cup_advisory.py
python scripts/verify_settlement.py
python scripts/verify_daily_ops.py
python scripts/verify_production_worker.py
python scripts/verify_mcp_server.py
python scripts/verify_finished_evaluation.py
python scripts/verify_datasources.py --no-remote
python scripts/verify_backtest.py
python scripts/verify_strategy.py
docker compose config
docker compose build --build-arg PYTHON_IMAGE=mcr.microsoft.com/devcontainers/python:1-3.13-bookworm --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
docker compose up -d postgres api
Invoke-RestMethod -Uri http://127.0.0.1:18000/ -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:18000/sources/health -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/status -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/health -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/preflight -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/onboarding -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/onboarding-apply-plan -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/deploy-check -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/deployment-doctor -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/config-plan -Method Post
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/profile-promotions -Method Post
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/data-apply -Method Post
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/execution-queue -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/broker-plan -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/broker-discovery -Method Post
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/broker-execute -Method Post
Invoke-RestMethod -Uri http://127.0.0.1:18000/production/execute -Method Post
docker compose run --rm worker footballctl production-worker-env --once --compact-json
docker compose run --rm worker footballctl production-worker-env --once --json
docker compose up -d worker
docker compose logs --tail 160 worker
footballctl live-preflight --json
footballctl live-decision --json
footballctl production-status --json
footballctl production-health --json
footballctl production-preflight --json
footballctl production-readiness --json
footballctl production-runtime-secrets --target worker --json
footballctl production-ops-check --api-url http://127.0.0.1:18000 --target worker --json
footballctl production-deployment-doctor --json
footballctl production-onboarding-apply-plan --json
footballctl production-config-plan --json
footballctl production-data-plan --json
footballctl production-profile-promote --json
footballctl production-data-apply --json
footballctl production-execution-queue --json
footballctl production-broker-plan --json
footballctl production-broker-discovery --json
footballctl production-broker-execute --json
footballctl production-execute --json
footballctl production-worker --once --skip-results --skip-daily-ops --json
footballctl daily-ops --date 2026-06-10 --json
footballctl picks today --json
```

Current real backtest evidence is summarized in `docs/backtest-results.md`. The current robust candidate is E0 all-season home value: walk-forward ROI `+5.02%` over 176 bets, with 3/3 positive folds and `+1.82%` average CLV. The current high-yield candidate is I1 Asian handicap away value: walk-forward ROI `+11.95%` over 180 bets, with 2/3 positive folds and `+5.28%` average CLV. Fixed-parameter I1 middle-season filtering is a smaller-sample supplemental candidate: ROI `+17.53%` over 70 bets, with 3/3 positive folds and `+4.79%` average CLV.
