from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from pydantic import BaseModel, Field

from football_analysis.db import StructuredRepository
from football_analysis.models import StrategySnapshot


class StrategyHealthItem(BaseModel):
    strategy_name: str
    settled_bets: int
    profit_units: float
    roi: float | None
    positive_clv_rate: float | None
    max_drawdown_units: float | None
    brier_score: float | None
    status: str
    action: str
    issues: list[str] = Field(default_factory=list)


class StrategyHealthReport(BaseModel):
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    items: list[StrategyHealthItem] = Field(default_factory=list)


def review_strategy_health(
    repository: StructuredRepository,
    *,
    clv_window: int = 100,
    min_positive_clv_rate: float = 0.40,
    brier_warning_threshold: float = 0.30,
    baseline_max_drawdown_units: float | None = None,
) -> StrategyHealthReport:
    groups: dict[str, list[StrategySnapshot]] = defaultdict(list)
    for snapshot in repository.list_models("strategy_snapshots", StrategySnapshot):
        groups[snapshot.strategy_name].append(snapshot)
    return StrategyHealthReport(
        items=[
            _health_item(
                strategy_name,
                snapshots,
                clv_window=clv_window,
                min_positive_clv_rate=min_positive_clv_rate,
                brier_warning_threshold=brier_warning_threshold,
                baseline_max_drawdown_units=baseline_max_drawdown_units,
            )
            for strategy_name, snapshots in sorted(groups.items())
        ]
    )


def _health_item(
    strategy_name: str,
    snapshots: list[StrategySnapshot],
    *,
    clv_window: int,
    min_positive_clv_rate: float,
    brier_warning_threshold: float,
    baseline_max_drawdown_units: float | None,
) -> StrategyHealthItem:
    settled = [item for item in sorted(snapshots, key=lambda item: item.decision_time) if item.profit_units is not None]
    profit = sum(item.profit_units or 0.0 for item in settled)
    stake = sum(item.stake_units for item in settled)
    clv_values = [item.clv for item in settled if item.clv is not None]
    positive_clv_rate = round(sum(1 for value in clv_values if value > 0) / len(clv_values), 4) if clv_values else None
    max_drawdown = _max_drawdown(settled)
    brier_score = _snapshot_brier_score(settled)
    issues: list[str] = []
    action = "continue_monitoring"
    status = "active"
    if len(clv_values) >= clv_window and positive_clv_rate is not None and positive_clv_rate < min_positive_clv_rate:
        issues.append(f"clv_disappeared:{positive_clv_rate}/{min_positive_clv_rate}")
        action = "retire_or_rebuild"
        status = "failed"
    if baseline_max_drawdown_units is not None and max_drawdown is not None:
        if max_drawdown > baseline_max_drawdown_units * 1.5:
            issues.append(f"drawdown_breached:{max_drawdown}/{baseline_max_drawdown_units}")
            action = "demote_to_paper"
            status = "degraded"
    if brier_score is not None and brier_score > brier_warning_threshold:
        issues.append(f"brier_warning:{brier_score}/{brier_warning_threshold}")
        if status == "active":
            action = "calibration_review"
            status = "watch"
    return StrategyHealthItem(
        strategy_name=strategy_name,
        settled_bets=len(settled),
        profit_units=round(profit, 3),
        roi=round(profit / stake, 4) if stake else None,
        positive_clv_rate=positive_clv_rate,
        max_drawdown_units=max_drawdown,
        brier_score=brier_score,
        status=status,
        action=action,
        issues=issues,
    )


def _max_drawdown(snapshots: list[StrategySnapshot]) -> float | None:
    if not snapshots:
        return None
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for snapshot in snapshots:
        equity += snapshot.profit_units or 0.0
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 3)


def _snapshot_brier_score(snapshots: list[StrategySnapshot]) -> float | None:
    values = []
    for snapshot in snapshots:
        probability = snapshot.model_prediction.get("calibrated_probability")
        if probability is None:
            probability = snapshot.model_prediction.get("implied_probability")
        target = _settlement_target(snapshot.settlement_result)
        if probability is None or target is None:
            continue
        values.append((float(probability) - target) ** 2)
    return round(sum(values) / len(values), 4) if values else None


def _settlement_target(result: str | None) -> float | None:
    if result == "win":
        return 1.0
    if result == "half_win":
        return 0.5
    if result in {"loss", "half_loss"}:
        return 0.0
    return None
