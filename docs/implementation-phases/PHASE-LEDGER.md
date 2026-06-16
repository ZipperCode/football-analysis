# 足球投注量化分析 分阶段实施账本

**目标**: 按阶段递进构建长期正期望证据闭环
**创建时间**: 2026-06-16
**时区**: Asia/Shanghai
**当前状态**: 阶段 1-5 已完成，第四阶段仅模拟与回溯

---

## 阶段 0：基线与账本初始化

### 目标
- 建立分阶段实施账本
- 验证当前系统基线
- 确认补充机制清单

### 完成状态
- [x] 创建账本文件
- [x] 运行基线验证
- [x] 记录当前环境状态

### 验收标准
1. 账本文档建立完毕
2. 基线验证通过 (compile + backtest + live-decision)
3. 环境变量、DB、配置加载无误

---

## 阶段 1：策略审计快照机制

### 目标
实现 StrategySnapshot 模型与持久化，每次推荐/决策保存完整审计上下文。

### 关键交付物
- StrategySnapshot Pydantic 模型（包含 13 个必需字段）
- StrategySnapshotRow SQLAlchemy 表
- 在 live_decision.py / scoring.py 中挂钩自动落盘
- 回填逻辑（closing_odds, CLV, settlement_result）

### 完成证据
- [x] 模型定义通过 `python -m compileall src scripts`
- [x] 表创建通过 `repository.initialize()`
- [x] 快照插入通过集成测试
- [x] 脚本验证 `verify_strategy_snapshot.py` 通过

### 下一阶段调整点
见文末阶段完成记录。

---

## 阶段 2：回测与评估增强

### 目标
补齐 Brier Score、校准桶、正 CLV 比例、最大回撤等关键指标。

### 关键交付物
- Brier Score 计算（概率预测准确度）
- 校准桶（胜率预测 vs 实际）
- 正 CLV 比例门槛
- 分层回测报告（联赛/市场/赔率/时间）

### 完成证据
（待阶段 1 完成后记录）

---

## 阶段 3：影子盘虚拟资金池

### 目标
为每个策略 profile 建虚拟资金池，自动结算和晋级/降级。

### 关键交付物
- PaperBankroll 模型（虚拟初始资金、当前余额、ROI）
- Paper settlement 自动流程
- 晋级条件：样本 ≥300，正 CLV ≥60%，ROI ≥2%，回撤 ≤1.5x 回测
- 降级条件：100 场后 ROI < -5% 或正 CLV < 40%

### 完成证据
（待阶段 2 完成后记录）

---

## 阶段 4：模拟实盘与回溯

### 目标
生成 dry-run 执行队列、模拟成交、滑点、拒单，禁用真实下注。

### 关键交付物
- Execution queue 生成（不执行）
- 模拟成交价、滑点记录
- Replay report（如真实成交会如何）
- 硬断言：禁用 broker live、禁用真实平台写入

### 完成证据
（待阶段 3 完成后记录）

---

## 阶段 5：优化与监控

### 目标
基于阶段 1-4 数据调整策略参数，引入淘汰/暂停规则。

### 关键交付物
- Fractional Kelly（仅在 Brier Score 稳定后）
- 策略淘汰规则（CLV 消失、回撤破界、概率失真）
- 实时监控告警

### 完成证据
（待阶段 4 完成后记录）

---

## 当前环境快照 (2026-06-16)

### 基线验证结果

| 验证项 | 命令 | 结果 |
|---|---|---|
| 编译检查 | python -m compileall src scripts | PASS |
| 数据源本地验证 | python scripts/verify_datasources.py --no-remote | PASS |
| 回测验证 | python scripts/verify_backtest.py | PASS |
| live-decision 验证 | python scripts/verify_live_decision.py | PASS |

### 阶段 0 结论
当前代码库可进入阶段 1。阶段 1 不重建已有回测/生产门禁能力，只补充独立策略快照审计链路。

---


---

## 阶段完成记录 (2026-06-16)

### 阶段 1：策略审计快照机制 - DONE

完成内容：
- 新增 `StrategySnapshot` 模型。
- 新增 `strategy_snapshots` 持久化 bucket 和 `StrategySnapshotRow`。
- recommendation 分配并落库后自动写入策略快照。
- `settle_bet()` 结算后回填 `closing_odds`, `clv`, `settlement_result`, `profit_units`。
- 修复 `verify_settlement.py` 在本机 `.env` 存在 `FOOTBALL_ADMIN_TOKEN` 时的 API 测试隔离问题。

验证：
- `python -m compileall src scripts` PASS
- `python scripts/verify_strategy_snapshot.py` PASS
- `python scripts/verify_settlement.py` PASS
- `python scripts/verify_live_decision.py` PASS

阶段 2 调整：
- `expected_value` 当前仍是市场 edge 代理值，阶段 2 不把它解释成已校准模型 EV。
- 回测增强优先使用现有历史行，不引入新数据源或远程调用。

### 阶段 2：回测与评估增强 - DONE

完成内容：
- 扩展 `BacktestSummary`：`hit_rate`, `positive_clv_rate`, `max_drawdown_units`, `brier_score`, `calibration_buckets`, `segment_breakdown`。
- `run_historical_backtest()` 增加权益曲线、最大回撤、赔率隐含概率 baseline Brier Score、校准桶和赔率区间分层。
- 明确保持 look-ahead 防护：收盘赔率只用于事后 CLV，不用于选择候选。

验证：
- `python scripts/verify_backtest.py` PASS
- `python scripts/verify_strategy.py` PASS

阶段 3 调整：
- 影子盘从现有 `BetLog(platform=paper)` 聚合，不新建独立订单事实表。
- 晋级/降级先用服务层可调默认阈值，避免直接修改生产配置。

### 阶段 3：影子盘虚拟资金池 - DONE

完成内容：
- 新增 `PaperBankrollReport`。
- 新增 `AnalysisService.paper_bankroll()`，按 profile 聚合 paper/simulation bet。
- 支持虚拟余额、ROI、平均 CLV、正 CLV 比例、最大回撤、连续亏损、晋级/早停建议。
- 使用 `BetLog.notes` 的 `profile_id=<id>` 作为当前最小可验证 profile 绑定方式。

验证：
- `python scripts/verify_paper_bankroll.py` PASS
- `python scripts/verify_live_review.py` PASS

阶段 4 调整：
- 第四阶段严格 simulation-only，不调用 `record_bet()`，不写真实 ledger。

### 阶段 4：模拟实盘与回溯 - DONE

完成内容：
- 新增 `simulation.py`。
- 新增 `simulate_execution_queue()`，基于 execution queue 生成模拟成交、滑点和拒单报告。
- `SimulatedExecutionReport.real_execution_allowed` 固定为 `False`。
- 不写 `bets` 表，不触发 broker live，不执行真实下注。

验证：
- `python scripts/verify_simulated_execution.py` PASS
- `python scripts/verify_record_bet_gate.py` PASS

阶段 5 调整：
- 监控/淘汰只输出建议，不自动改配置。

### 阶段 5：策略优化与监控 - DONE

完成内容：
- 新增 `strategy_health.py`。
- 新增 `review_strategy_health()`，按策略快照聚合 ROI、正 CLV 比例、最大回撤、Brier Score。
- 支持 `retire_or_rebuild`, `demote_to_paper`, `calibration_review`, `continue_monitoring` 等只读建议。

验证：
- `python scripts/verify_strategy_health.py` PASS
- `python scripts/verify_strategy_snapshot.py` PASS

### 最终验证集合

- `python -m compileall src scripts` PASS
- `python scripts/verify_strategy_snapshot.py` PASS
- `python scripts/verify_paper_bankroll.py` PASS
- `python scripts/verify_simulated_execution.py` PASS
- `python scripts/verify_backtest.py` PASS
- `python scripts/verify_strategy_health.py` PASS
- `python scripts/verify_live_gate.py` PASS
- `python scripts/verify_record_bet_gate.py` PASS
- `python scripts/verify_live_decision.py` PASS
- `python scripts/verify_settlement.py` PASS
- `python scripts/verify_strategy.py` PASS

### 当前结论

五个阶段的最小可用闭环已经完成：策略快照 -> 回测增强 -> 影子盘聚合 -> 模拟执行回溯 -> 策略健康监控。
第四阶段保持模拟和回溯，不进行真实下注。
