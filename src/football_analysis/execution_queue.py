from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import Field

from football_analysis.live_audit import LiveAuditItem, audit_live_trading
from football_analysis.models import AppModel, Match, RecommendationStatus
from football_analysis.service import AnalysisService


class ExecutionQueueItem(AppModel):
    rank: int
    state: str
    match_id: str
    league: str
    home_team: str
    away_team: str
    kickoff_at: datetime
    market_type: str | None = None
    selection: str | None = None
    minimum_odds: float | None = None
    stake_units: float = 0.0
    value_score: float
    risk_score: float
    confidence: float
    gates_failed: list[str] = Field(default_factory=list)
    reason: str
    expires_at: datetime


class ExecutionQueueReport(AppModel):
    checked_at: datetime
    status: str
    ready_to_bet: bool
    action: str
    approved_count: int
    blocked_count: int
    items: list[ExecutionQueueItem] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


def build_execution_queue(
    service: AnalysisService,
    include_past: bool = False,
    limit: int = 20,
    checked_at: datetime | None = None,
) -> ExecutionQueueReport:
    now = checked_at or datetime.now(service.settings.app.tzinfo)
    audit = audit_live_trading(service.repository, service.settings, include_past=include_past, checked_at=now)
    matches_by_id = {match.id: match for match in service.repository.list_models("matches", Match)}
    approved = [
        item
        for item in audit.items
        if item.status == RecommendationStatus.recommended.value and item.live_gate_passed and item.stake_units > 0
    ]
    blocked = [item for item in audit.items if item not in approved and (item.market_type or item.gates_failed)]
    ranked = _apply_queue_stake_cap(approved, service.settings.live_trading.max_daily_stake_units) + blocked
    queue_items = [
        _queue_item(index, item, matches_by_id, service.settings.live_trading.max_odds_age_minutes)
        for index, item in enumerate(ranked[:limit], start=1)
    ]
    approved_count = sum(1 for item in queue_items if item.state == "approved")
    blocked_count = len(queue_items) - approved_count
    status = "ready" if approved_count else "blocked"
    return ExecutionQueueReport(
        checked_at=now,
        status=status,
        ready_to_bet=bool(approved_count),
        action="manual_place_approved_queue" if approved_count else "refresh_fixtures_and_odds",
        approved_count=approved_count,
        blocked_count=blocked_count,
        items=queue_items,
        issues=[] if approved_count else audit.issues or ["no_approved_execution_queue_items"],
    )


def _apply_queue_stake_cap(items: list[LiveAuditItem], max_stake_units: float) -> list[LiveAuditItem]:
    planned = 0.0
    capped: list[LiveAuditItem] = []
    for item in items:
        next_planned = round(planned + item.stake_units, 3)
        if next_planned > max_stake_units:
            capped.append(_block_queue_overflow(item, next_planned, max_stake_units))
            continue
        planned = next_planned
        capped.append(item)
    return capped


def _block_queue_overflow(item: LiveAuditItem, planned: float, max_stake_units: float) -> LiveAuditItem:
    gate = f"execution_queue_daily_stake_limit:{planned:.2f}/{max_stake_units:.2f}"
    gates_failed = [*item.gates_failed]
    if gate not in gates_failed:
        gates_failed.append(gate)
    return item.model_copy(
        update={
            "stake_units": 0.0,
            "live_gate_passed": False,
            "gates_failed": gates_failed,
            "reason": item.reason + " 超过本次人工执行队列总仓位上限，保留为候选但不批准下单。",
        }
    )


def _queue_item(
    index: int,
    item: LiveAuditItem,
    matches_by_id: dict[str, Match],
    max_odds_age_minutes: int,
) -> ExecutionQueueItem:
    match = matches_by_id[item.match_id]
    return ExecutionQueueItem(
        rank=index,
        state="approved" if item.live_gate_passed and item.stake_units > 0 else "blocked",
        match_id=match.id,
        league=item.league,
        home_team=item.home_team,
        away_team=item.away_team,
        kickoff_at=match.kickoff_at,
        market_type=item.market_type,
        selection=item.selection,
        minimum_odds=_minimum_odds(item),
        stake_units=item.stake_units if item.live_gate_passed else 0.0,
        value_score=item.value_score,
        risk_score=item.risk_score,
        confidence=item.confidence,
        gates_failed=item.gates_failed,
        reason=item.reason,
        expires_at=datetime.now(match.kickoff_at.tzinfo) + timedelta(minutes=max_odds_age_minutes),
    )


def _minimum_odds(item: LiveAuditItem) -> float | None:
    best_price = item.odds_basis.get("best_price")
    if isinstance(best_price, int | float):
        return round(float(best_price) * 0.99, 3)
    return None
