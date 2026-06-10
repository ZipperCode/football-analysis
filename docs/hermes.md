# Hermes 集成约定

Hermes 不直接绕过后端给下注结论，只调用后端 REST API 或 JSON CLI，再负责调度、解释和推送。

## 推荐 skill 动作

- `today_picks`：执行 `footballctl picks today --json`
- `analyze_match`：执行 `footballctl analyze <match_id> --json`
- `performance`：执行 `footballctl performance --json`
- `sources_health`：执行 `footballctl sources --json`

## cron 节奏

- T-48h：建立比赛档案，写入 `Match`。
- T-12h：更新赔率、伤停、积分和消息面。
- T-3h：生成推荐版本，输出主推/备选/不推荐。
- T-30m：终检风险，只允许降级，不允许越过风控升级。

## Telegram 报告字段

- 比赛：联赛、主队、客队、开赛时间。
- 推荐：市场、选择项、赔率口径、建议单位。
- 证据：Odds/News/History/Risk Agent 的结构化 finding。
- 风控：数据完整度、风险标签、风险提示。

固定风险提示：仅供赛前分析，不保证收益；请自行承担风险，并严格控制最大仓位。
