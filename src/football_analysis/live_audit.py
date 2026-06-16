from __future__ import annotations

from collections import Counter
from datetime import datetime

from pydantic import Field

from football_analysis.db import StructuredRepository
from football_analysis.live_gate import _recent_consecutive_losses
from football_analysis.models import AppModel, BetLog, Match, RecommendationStatus
from football_analysis.odds_readiness import _match_in_league_codes
from football_analysis.service import AnalysisService
from football_analysis.settings import Settings


class LiveAuditItem(AppModel):
    match_id: str
    league: str
    home_team: str
    away_team: str
    kickoff_at: datetime
    status: str
    market_type: str | None = None
    selection: str | None = None
    value_score: float
    risk_score: float
    confidence: float
    stake_units: float
    live_gate_passed: bool
    gates_failed: list[str] = Field(default_factory=list)
    reason: str


class LiveAuditReport(AppModel):
    checked_at: datetime
    status: str
    total_matches: int
    recommended_count: int
    paper_candidate_count: int
    rejected_count: int
    analysis_only_count: int
    total_live_stake_units: float
    recent_consecutive_losses: int
    max_recent_consecutive_losses: int
    gate_counts: dict[str, int] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    items: list[LiveAuditItem] = Field(default_factory=list)


def audit_live_trading(
    repository: StructuredRepository,
    settings: Settings,
    include_past: bool = False,
    checked_at: datetime | None = None,
    league_codes: set[str] | None = None,
) -> LiveAuditReport:
    now = _sortable_kickoff_at(checked_at or datetime.now(settings.app.tzinfo), settings)
    scoped_league_codes = {code.upper() for code in league_codes or set()}
    service = AnalysisService(settings, repository)
    matches = [
        match
        for match in repository.list_models("matches", Match)
        if include_past or _sortable_kickoff_at(match.kickoff_at, settings) > now
    ]
    if scoped_league_codes:
        matches = [
            match
            for match in matches
            if _match_in_league_codes(match, settings, scoped_league_codes)
        ]
    analyses = [service._score_analysis(match.id) for match in matches]
    analyses = service._allocate_analysis_recommendations(analyses)
    items = [_audit_item(analysis.recommendation, analysis.match) for analysis in analyses]
    items.sort(
        key=lambda item: (
            not item.live_gate_passed,
            -item.value_score,
            _sortable_kickoff_at(item.kickoff_at, settings),
        )
    )
    bet_logs = repository.list_models("bets", BetLog)
    recent_losses = _recent_consecutive_losses(bet_logs)
    gate_counts = Counter(gate for item in items for gate in item.gates_failed)
    issues: list[str] = []
    if recent_losses >= settings.live_trading.max_recent_consecutive_losses:
        issues.append(
            f"live_recent_consecutive_losses:{recent_losses}/{settings.live_trading.max_recent_consecutive_losses}"
        )
    for gate in gate_counts:
        if gate.startswith("live_rolling_loss_units:") or gate.startswith("live_rolling_roi:"):
            issues.append(gate)
    if not any(item.live_gate_passed for item in items) and not issues:
        issues.append("no_live_gate_passed_candidates")
    status = _audit_status(items, issues)
    return LiveAuditReport(
        checked_at=now,
        status=status,
        total_matches=len(matches),
        recommended_count=sum(1 for item in items if item.status == RecommendationStatus.recommended.value),
        paper_candidate_count=sum(1 for item in items if item.status == RecommendationStatus.paper_candidate.value),
        rejected_count=sum(1 for item in items if item.status == RecommendationStatus.rejected.value),
        analysis_only_count=sum(1 for item in items if item.status == RecommendationStatus.analysis_only.value),
        total_live_stake_units=round(
            sum(item.stake_units for item in items if item.status == RecommendationStatus.recommended.value),
            3,
        ),
        recent_consecutive_losses=recent_losses,
        max_recent_consecutive_losses=settings.live_trading.max_recent_consecutive_losses,
        gate_counts=dict(sorted(gate_counts.items())),
        issues=issues,
        items=items,
    )


def _audit_item(recommendation, match: Match) -> LiveAuditItem:
    live_gate = recommendation.score_breakdown.get("live_gate", {})
    market_type = recommendation.market_type.value if recommendation.market_type else None
    return LiveAuditItem(
        match_id=match.id,
        league=match.league,
        home_team=match.home_team,
        away_team=match.away_team,
        kickoff_at=match.kickoff_at,
        status=recommendation.status.value,
        market_type=market_type,
        selection=recommendation.selection,
        value_score=recommendation.value_score,
        risk_score=recommendation.risk_score,
        confidence=recommendation.confidence,
        stake_units=recommendation.stake_units,
        live_gate_passed=bool(live_gate.get("passed")),
        gates_failed=list(live_gate.get("gates_failed", [])),
        reason=recommendation.reason,
    )


def _sortable_kickoff_at(value: datetime, settings: Settings) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=settings.app.tzinfo)
    return value.astimezone(settings.app.tzinfo)


def _audit_status(items: list[LiveAuditItem], issues: list[str]) -> str:
    if any(issue.startswith("live_recent_consecutive_losses:") for issue in issues):
        return "paused"
    if any(issue.startswith("live_rolling_loss_units:") or issue.startswith("live_rolling_roi:") for issue in issues):
        return "paused"
    if any(item.live_gate_passed for item in items):
        return "ready"
    if items:
        return "no_trade"
    return "no_matches"
