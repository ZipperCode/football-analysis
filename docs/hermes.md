# Hermes 闭环接入契约

本文档约定 football-analysis 后端与 Hermes Agent（Nous Research hermes-agent）之间的完整闭环接入方式。旧版 26 行简要约定已升级为本契约，覆盖架构原则、MCP 工具面、cron 编排节奏、复盘优化循环与安全边界。

## 1. 架构原则：薄后端 + Hermes 大脑

- **薄后端**：本仓库只做「数据 + 分析 + 干净的机器可读接口」。后端不新增自动调度逻辑，也不自动修改策略配置或越过后台下注。
- **Hermes 是大脑**：调度（cron）、编排、解释、推送、复盘决策都在 Hermes 侧完成。
- **接入方式**：Hermes 通过 MCP 直接调用后端工具（`hermes mcp add`）。后端**不**打包成 Hermes plugin；plugin 层只是 Hermes 侧一个薄薄的 **blueprint skill**，用于 cron 编排配置。
- **风险边界**：所有推荐均为赛前分析，**不自动下注**。真实资金操作必须经由现有多层风控门（`live-decision` / `live-preflight` / `record-bet` 现场门），且需人工确认或人工执行。

## 2. MCP 接入命令

在后端已安装（`python -m pip install -e .`）且 `football-analysis-mcp` 命令可用的前提下，在 Hermes 主机执行：

```bash
hermes mcp add football-analysis --command "football-analysis-mcp"
```

接入后，MCP 工具会自动暴露为 Hermes 的一等公民 tool。进入 Hermes 会话验证工具列表：

```bash
hermes cron
/suggestions
```

看到 `/suggestions` 列表中出现 football-analysis 相关工具即表示接入成功。要启用 blueprint 定时任务，需执行 `/suggestions accept N`（N 为对应编号）才会真正创建 cron。

## 3. 必需环境变量

MCP server 运行时依赖以下环境变量（由 Hermes 侧 `~/.hermes/.env` 或 MCP 配置 `env` 块传入）。以下列出变量名与用途，**不暴露密钥值**：

| 变量名 | 用途 | 敏感度 |
|--------|------|--------|
| `FOOTBALL_CONFIG` | 配置文件路径，默认 `config/default.yaml` | 低 |
| `DATABASE_URL` | 数据库连接串；本地默认 SQLite，生产建议 Postgres | 中 |
| `FOOTBALL_AI_KEY` | 自建/本地 LLM 分析层 API key（OpenAI 兼容端点） | **高** |
| `API_FOOTBALL_KEY` | API-Football 数据源 key | **高** |
| `ODDS_API_IO_KEY` | Odds-API.io 赔率源 key | **高** |
| `FOOTBALL_DATA_ORG_TOKEN` | football-data.org token | **高** |
| `THE_ODDS_API_KEY` | The Odds API 付费赔率源 key（可选） | **高** |
| `SPORTMONKS_TOKEN` | Sportmonks Premium Odds Feed token（可选） | **高** |
| `QQSD_C_CK` | 球球是道 token（杯赛/补充数据，可选） | **高** |
| `EXA_API_KEY` | 赛前研究网页搜索（可选） | **高** |
| `FIRECRAWL_API_KEY` | 赛前研究网页抓取（可选） | **高** |
| `TAVILY_API_KEY` | 赛前研究补充搜索（可选） | **高** |
| `BETFAIR_APP_KEY` | Betfair Exchange app key（broker 预览，可选） | **高** |
| `BETFAIR_SESSION_TOKEN` | Betfair Exchange session token（broker 预览，可选） | **高** |
| `TELEGRAM_BOT_TOKEN` | Telegram 推送 bot token（可选） | **高** |
| `TELEGRAM_CHAT_ID` | Telegram 推送 chat id（可选） | **高** |
| `FOOTBALL_ADMIN_TOKEN` | API 管理 token；设置后写入/执行接口需携带 | **高** |

.env.example 已包含全部变量模板与说明，复制后按需填写即可。

## 4. MCP 工具清单（按工作流分组）

当前已暴露 13 个工具；`get_picks_today` 返回的 `ai_analysis` 结构化字段已上线。

### 4.1 数据刷新与研究（Refresh / Research）

| 工具名 | 状态 | 说明 |
|--------|------|------|
| `refresh_live_data` | 已上线 | 刷新 fixtures 与 odds；`dry_run=true` 时不消耗远程配额。 |
| `refresh_research_data` | 已上线 | 搜索赛前情报（伤停、新闻、预览）并存入本地；不下单。 |
| `run_analysis_cycle` | 已上线 | 运行一次 analysis-only 生产周期，内部强制关闭 broker discovery/execution，适合 Hermes 定时拉取分析。 |

### 4.2 分析与推荐（Analyze）

| 工具名 | 状态 | 说明 |
|--------|------|------|
| `get_picks_today` | 已上线 | 评分今日本地比赛并保存 recommendations。返回 item 中包含结构化字段 `ai_analysis`（AI 概率 / 市场隐含概率 / ai_edge / signal_confidence / applied / value_delta / confidence_delta / analysis）。 |
| `get_analysis` | 已上线 | 统一高层入口：`get_analysis(hours=24, limit=8, refresh=false)`。一次调用完成「（可选刷新）-> 分析 -> 带 AI 的推荐」。默认 `refresh=false` 以节省配额；`refresh=true` 时先拉数据再分析。 |
| `get_live_decision` | 已上线 | 返回可复现的 go/no-go 决策快照，包含 odds readiness、live gate、profile audit 等综合结果。 |
| `get_odds_readiness` | 已上线 | 审计当前赔率覆盖是否足够支撑 active strategy profiles。 |

### 4.3 复盘与策略评估（Review）

| 工具名 | 状态 | 说明 |
|--------|------|------|
| `evaluate_finished_matches` | 已上线 | 按当前策略复盘已完赛比赛；只统计指定 recommendation status，返回命中率、ROI 和排除原因；不下单。 |
| `evaluate_ai_quality` | 已上线 | AI 准度校验：返回 Brier score（AI vs 市场）、命中率、`brier_improvement`、逐场明细。 |
| `review_strategies` | 已上线 | 策略评估：返回各 profile 的 ROI/CLV 评估与建议动作（`pause_live` / `demote_to_paper` / 保持）。Hermes 复盘后据此判断是否调整策略。 |

### 4.4 报告与推送（Report）

| 工具名 | 状态 | 说明 |
|--------|------|------|
| `production_status` | 已上线 | 读取生产状态（不调用远程 provider）。 |
| `production_health` | 已上线 | 读取 worker/ingestion 心跳健康状态。 |
| `push_analysis_report` | 已上线 | 格式化并推送今日分析建议到 Telegram；`dry_run=true` 只返回文本不发送。 |

## 5. cron 节奏与 MCP 工具映射（闭环编排）

沿用旧版赛前节奏，补上复盘回路。以下 cron 表达式为参考值，实际由 Hermes blueprint skill 中的 `schedule` 字段控制。

| 时点 | Hermes cron / 动作 | MCP 工具 | 说明 |
|------|-------------------|----------|------|
| 每日 08:00 | 建档 + 刷新 | `refresh_live_data(dry_run=false)` | 拉当天/次日 fixtures + odds。 |
| 每日 09:00 | 研究情报 | `refresh_research_data` | 拉伤停/消息面 findings。 |
| 赛前 T-3h | 生成推荐 | `get_analysis(refresh=true)` 或 `get_picks_today` | 分析 + AI，出主推/备选。 |
| 每日固定 | 推送 | `push_analysis_report` | Telegram 发当日推荐。 |
| 次日 10:00 | 结算复盘 | `evaluate_finished_matches` | 已完赛命中率 / ROI。 |
| 次日 10:05 | AI 准度 | `evaluate_ai_quality` | AI vs 市场 Brier（可 `context_from` 上一步）。 |
| 每周一 | 策略评估 | `review_strategies` | ROI/CLV -> 调整建议。 |

旧版保留的赛前微节奏（供 CLI skill 或人工检查参考）：

- T-48h：建立比赛档案，写入 `Match`。
- T-12h：更新赔率、伤停、积分和消息面。
- T-3h：生成推荐版本，输出主推/备选/不推荐。
- T-30m：终检风险，只允许降级，不允许越过风控升级。

## 6. 复盘与优化循环

闭环流程如下：

1. **生成**：cron 触发 `get_analysis` 或 `get_picks_today`，输出带 AI 概率的推荐。
2. **推送**：`push_analysis_report` 将推荐发到 Telegram。
3. **结算**：赛后 `evaluate_finished_matches` 计算命中率和 ROI。
4. **AI 准度校验**：`evaluate_ai_quality` 对比 AI 概率与市场隐含概率的 Brier score（通过 `context_from` 链接上一步结果）。
5. **策略评估**：`review_strategies` 输出各 profile 的 ROI/CLV 与建议动作。
6. **人工确认**：`review_strategies` 的输出仅为**建议**；是否修改 `config/default.yaml` 或调整 profile 需要**人工批准**。Hermes 可将建议整理成 Telegram 消息等用户拍板，后端不会自动改配置。

## 7. 安全边界与风险声明

- **不自动下注**：MCP 不暴露真实 broker 下单。`get_picks_today`、`get_analysis`、`push_analysis_report` 均为分析与推送，不产生订单。
- **不自动改配置**：`review_strategies` 输出建议动作，后端不自动写入 `config/default.yaml`；策略调整需人工确认。
- **prompt 注入防护**：不可信的比赛新闻/情报文本仅由后端 LLM 做分析输入，推荐结果始终附带固定风险提示；不会将新闻文本作为命令执行。
- **真实资金门控**：任何真实平台记录必须通过 `live-decision` / `live-preflight` 返回 `ready_to_bet: true` 且 `action: "place_approved_live_bets"`，再经 `record-bet` 现场门人工执行。MCP 层不越过此门。
- **配额保护**：`refresh_live_data` 和 `run_analysis_cycle` 均支持 `dry_run=true`，可在不消耗远程 provider 配额的情况下演练。

## 8. 旧版 CLI Skill 动作（保留备用）

在 Hermes 侧不便使用 MCP 时，以下 JSON CLI 命令仍可作为 skill/tool/cron 的备用调用方式：

- `today_picks`：执行 `footballctl picks today --json`
- `analyze_match`：执行 `footballctl analyze <match_id> --json`
- `performance`：执行 `footballctl performance --json`
- `sources_health`：执行 `footballctl sources --json`

## 9. Telegram 报告字段规范

`push_analysis_report` 推送的 Telegram 消息包含以下字段：

- **比赛**：联赛、主队、客队、开赛时间。
- **推荐**：市场、选择项、赔率口径、建议单位。
- **证据**：Odds / News / History / Risk Agent 的结构化 finding。
- **AI 分析**：`ai_analysis` 字段提供的 AI 概率 vs 市场隐含概率、ai_edge、confidence、value_delta。
- **风控**：数据完整度、风险标签、风险提示。

固定风险提示：仅供赛前分析，不保证收益；请自行承担风险，并严格控制最大仓位。

---

**文档版本**：v2（2026-07-04）
**适用范围**：football-analysis 后端 v1 + Hermes Agent（Nous Research hermes-agent）
**变更说明**：由旧版 26 行简要约定扩展为完整闭环契约；新增 MCP 工具分组、cron 映射、复盘优化循环、AI 结构化字段说明、安全边界与风险声明。
