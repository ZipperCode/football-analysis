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
- `GET /performance`
- `GET /sources/health`
- `POST /jobs/ingest/fixtures`
- `POST /jobs/ingest/odds`
- `POST /jobs/ingest/standings`
- `GET /backtest/historical`

## 生产核心命令

```powershell
footballctl db init --json
footballctl sources --json
footballctl ingest fixtures --date 2026-06-09 --source api_football --league EPL --json
footballctl ingest odds --date 2026-06-09 --source api_football --league EPL --json
footballctl ingest standings --league EPL --season 2025 --source api_football --json
footballctl ingest historical --league E0 --season 2526 --path data/historical/2526/E0.csv --json
footballctl backtest historical --league E0 --season 2526 --json
footballctl picks today --json
```

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

Current real backtest evidence is summarized in `docs/backtest-results.md`. The current high-yield candidate is I1 Asian handicap away value: walk-forward ROI `+11.95%` over 180 bets, with 2/3 positive folds and `+5.28%` average CLV.
