from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SimulatedExecutionItem(BaseModel):
    match_id: str
    market_type: str
    selection: str
    requested_odds: float
    simulated_odds: float | None = None
    stake_units: float
    slippage: float | None = None
    accepted: bool
    reason: str


class SimulatedExecutionReport(BaseModel):
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    mode: str = "simulation_only"
    source_status: str | None = None
    source_ready_to_execute: bool = False
    simulated_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    total_requested_stake_units: float = 0.0
    total_accepted_stake_units: float = 0.0
    average_slippage: float | None = None
    real_execution_allowed: bool = False
    items: list[SimulatedExecutionItem] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


def simulate_execution_queue(
    queue: dict[str, Any],
    *,
    odds_slippage: float = 0.01,
    reject_below_minimum: bool = True,
) -> SimulatedExecutionReport:
    items = [
        _simulate_item(item, odds_slippage=odds_slippage, reject_below_minimum=reject_below_minimum)
        for item in queue.get("items", [])
    ]
    accepted = [item for item in items if item.accepted]
    rejected = [item for item in items if not item.accepted]
    slippages = [item.slippage for item in accepted if item.slippage is not None]
    issues = list(queue.get("issues") or [])
    if queue.get("ready_to_execute") is not True:
        issues.append("source_queue_not_ready")
    return SimulatedExecutionReport(
        source_status=queue.get("status"),
        source_ready_to_execute=bool(queue.get("ready_to_execute")),
        simulated_count=len(items),
        accepted_count=len(accepted),
        rejected_count=len(rejected),
        total_requested_stake_units=round(sum(item.stake_units for item in items), 3),
        total_accepted_stake_units=round(sum(item.stake_units for item in accepted), 3),
        average_slippage=round(sum(slippages) / len(slippages), 6) if slippages else None,
        items=items,
        issues=issues,
    )


def _simulate_item(
    item: dict[str, Any],
    *,
    odds_slippage: float,
    reject_below_minimum: bool,
) -> SimulatedExecutionItem:
    requested_odds = float(item.get("odds") or item.get("approved_odds") or item.get("best_price") or 0.0)
    stake_units = float(item.get("remaining_stake_units") or item.get("stake_units") or 0.0)
    simulated_odds = round(requested_odds * (1.0 - odds_slippage), 4) if requested_odds > 0 else None
    slippage = round(requested_odds - simulated_odds, 6) if simulated_odds is not None else None
    minimum_odds = float(item.get("minimum_execution_odds") or 0.0)
    accepted = bool(simulated_odds is not None and stake_units > 0)
    reason = "simulated_fill"
    if reject_below_minimum and minimum_odds and simulated_odds is not None and simulated_odds < minimum_odds:
        accepted = False
        reason = "simulated_reject_below_minimum_odds"
    elif not accepted:
        reason = "invalid_queue_item"
    return SimulatedExecutionItem(
        match_id=str(item.get("match_id") or ""),
        market_type=str(item.get("market_type") or ""),
        selection=str(item.get("selection") or ""),
        requested_odds=requested_odds,
        simulated_odds=simulated_odds,
        stake_units=stake_units,
        slippage=slippage,
        accepted=accepted,
        reason=reason,
    )
