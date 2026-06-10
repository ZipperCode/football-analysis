# 足球赛前价值投注多 Agent 分析系统设计文档

## 摘要
- 目标：构建一个免费源优先、可自动化、可复盘的足球赛前分析系统，输出高价值投注建议，但第一版不自动下注。
- 边界：覆盖主流赛事的赛前胜平负、亚洲让球、大小球；不做滚球、不做全自动下注、不训练 ML 模型。
- 核心形态：独立 Python 后端负责数据、评分、回测和审计；Hermes Agent 负责调度、解释、Telegram 推送和交互式追问。
- 主要指标：长期以 `ROI + CLV` 评估推荐质量；短期通过模拟盘和真实下注回写验证。

## 已确认设计决策
- 自动化边界：系统自动抓取、分析、排序、推送，最终下注由人工确认。
- 数据策略：免费源优先，允许注册免费 API key，只使用公开、无需绕反爬的数据源。
- 主数据源：API-FOOTBALL/API-SPORTS 免费档作为赛程、赔率、伤停、历史战绩主源；其官方页面显示免费档 100 请求/天，且全端点可用。
- 赔率备源：接入 Odds-API.io 免费档作为赔率备源；其免费档标注 100 请求/小时、覆盖 soccer、支持 pre-match/live odds。
- 历史回测：football-data.co.uk 用于历史 CSV、赛果和赔率数据；上线后持续采集真实赔率做滚动回测。
- 辅助数据：football-data.org、TheSportsDB 作为赛程、积分、队伍资料兜底源。
- 消息面：RSS + 搜索 API，覆盖中英来源；LLM 可深度参与消息面评级，但只能有限影响最终分数。
- 风控：稳健价值路线，单关优先，低相关二串一仅作为备选；每天最多 3-5 个主推，允许空推荐。
- 存储与部署：Postgres + Docker Compose + VPS；网页后台单用户登录；配置用 YAML/env。
- Hermes 集成：后端提供 REST API 和 JSON CLI；Hermes 通过 skill/tool/cron 调用后端，并通过 Telegram gateway 推送报告。

## 系统架构
- 后端服务：Python + FastAPI，提供比赛、推荐、回测、实盘回写、数据源状态接口。
- Worker/调度：定时执行 T-48h 建档、T-12h 更新、T-3h 推荐、T-30m 终检；免费配额下采用“广筛后深挖”。
- 数据库：Postgres 保存比赛、赔率快照、消息面摘要、agent 输出、评分拆解、推荐版本、模拟盘和实盘记录。
- Hermes 层：新增 Hermes skill，例如 `football-value-analyst`，提供四个核心动作：今日精选、分析单场、解释推荐、查看回测/模拟盘。
- 网页后台：研究看板，包括比赛列表、推荐详情、证据链、模拟盘/回测表现、数据源健康、实际下注回写表单。
- 通知：后端产出结构化推荐，Hermes 生成中文报告并推送 Telegram。

## Agent 分工
- Odds Agent：抓取赔率、计算市场均值、最高价、隐含概率、盘口变化和异常。
- News Agent：汇总中英消息面，识别伤停、轮换、赛程压力、教练言论、动机因素，并输出带来源的评级。
- History Agent：分析近期状态、主客场、排名、休息天数、直接交锋；直接交锋低权重。
- Risk Agent：检查数据缺失、来源冲突、赔率不可用、过高波动、相关投注和仓位上限。
- Recommendation Agent：合并规则分、LLM 消息面信号和风险结果，输出主推、备选或“不推荐”。
- Hermes Reviewer：把结构化结果转成可追问报告，不允许绕过后端风控直接给下注结论。

## 核心数据契约
- `Match`: 比赛 ID、联赛、主客队、开赛时间、状态、数据完整度。
- `OddsSnapshot`: 比赛、市场类型、盘口、赔率源、博彩公司、采集时间、市场均值、最高价。
- `AgentFinding`: agent 名称、结构化结论、证据来源、置信度、风险标签。
- `Recommendation`: 推荐市场、选择项、价值分、风险分、置信度、建议单位、赔率口径、理由、状态。
- `BetLog`: 实际下注赔率、单位、平台、备注、赛果、收益、CLV。
- API 默认接口：`GET /picks/today`、`GET /matches/{id}/analysis`、`POST /bets`、`GET /performance`、`GET /sources/health`。
- CLI 默认命令：`footballctl picks today --json`、`footballctl analyze <match_id> --json`、`footballctl performance --json`。

## 评分与风控
- 推荐只在数据质量、价值分、风险分同时过阈值时输出；关键数据冲突或缺失时降级为“仅分析，不推荐”。
- 价值分来自市场隐含概率、赔率偏差、盘口变化、历史基础特征和消息面结构化信号。
- LLM 最多影响消息面相关分，不直接覆盖赔率价值、仓位或风控结论。
- 仓位采用单位分档：`0.5u / 1u / 1.5u`，早期不用凯利公式。
- 每条推荐必须显示风险提示：仅供分析、无保证收益、需自行承担风险、建议最大仓位。
- 原始快照和 agent 证据保留 180 天，聚合统计长期保留。

## 验证方案
- 不写单元测试，不做大模块编译测试；只做轻量验证和场景验收。
- 数据源冒烟：验证 API key、配额、主源/备源返回结构、缓存命中、限流降级。
- 推荐场景：完整数据给出主推；赔率缺失只出分析；消息面冲突降低置信度；高风险比赛不推荐。
- Hermes 场景：今日精选推送 Telegram；用户追问单场时 Hermes 能引用后端证据解释。
- 回测/模拟盘：记录推荐、收盘赔率、赛果、ROI、CLV；实际下注通过网页表单回写。
- 配置验收：修改 YAML 后能调整联赛范围、每日推荐数量、仓位上限和数据源优先级。

## 数据源依据
- API-FOOTBALL/API-SPORTS：免费档 100 请求/天，全端点/全赛事付费档说明见 [API-Sports Football](https://api-sports.io/sports/football) 和 [API-Football 文档](https://www.api-football.com/documentation?source=post_page---------------------------)。
- Odds-API.io：免费档 100 请求/小时、覆盖 soccer 和 pre-match/live odds，见 [Odds-API.io Free Tier](https://odds-api.io/pricing/free)。
- football-data.org：提供 competitions、matches、standings 等接口，见 [football-data.org Quickstart](https://www.football-data.org/documentation/quickstart)。
- football-data.co.uk：提供历史赛果、统计和 betting odds CSV，见 [football-data.co.uk data](https://www.football-data.co.uk/data)。
- Hermes Agent：支持 skills、cron、gateway、MCP 和多平台消息，见 [Hermes Agent Docs](https://hermes-agent.nousresearch.com/docs/)。

## 明确不做
- v1 不自动下注、不接投注平台下单 API。
- v1 不做滚球、不做高频盘口交易。
- v1 不训练机器学习模型，只积累后续训练所需数据。
- v1 不做多用户权限系统、不做网页可视化调参后台。
- 不提交代码、不写单测、不做大模块编译测试。
