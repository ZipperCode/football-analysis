from __future__ import annotations

from datetime import datetime

from pydantic import Field

from football_analysis.db import StructuredRepository
from football_analysis.live_audit import LiveAuditReport, audit_live_trading
from football_analysis.models import AppModel
from football_analysis.odds_readiness import OddsReadinessReport, audit_odds_readiness
from football_analysis.settings import Settings


class LivePreflightReport(AppModel):
    checked_at: datetime
    status: str
    ready_to_bet: bool
    action: str
    issues: list[str] = Field(default_factory=list)
    odds_readiness: OddsReadinessReport
    live_audit: LiveAuditReport


def run_live_preflight(
    repository: StructuredRepository,
    settings: Settings,
    include_past: bool = False,
    min_bookmakers: int | None = None,
    min_profile_matches: int = 1,
    checked_at: datetime | None = None,
) -> LivePreflightReport:
    now = checked_at or datetime.now(settings.app.tzinfo)
    odds_readiness = audit_odds_readiness(
        repository,
        settings,
        min_bookmakers=min_bookmakers or settings.live_trading.min_bookmakers,
        min_profile_matches=min_profile_matches,
        include_past=include_past,
        checked_at=now,
    )
    live_audit = audit_live_trading(
        repository,
        settings,
        include_past=include_past,
        checked_at=now,
    )
    status, action, issues = _preflight_decision(odds_readiness, live_audit)
    return LivePreflightReport(
        checked_at=now,
        status=status,
        ready_to_bet=status == "ready",
        action=action,
        issues=issues,
        odds_readiness=odds_readiness,
        live_audit=live_audit,
    )


def _preflight_decision(
    odds_readiness: OddsReadinessReport,
    live_audit: LiveAuditReport,
) -> tuple[str, str, list[str]]:
    issues = _prefixed_issues("odds", odds_readiness.issues) + _prefixed_issues("live", live_audit.issues)
    if live_audit.status == "paused":
        return "paused", "do_not_bet_loss_pause", issues
    if live_audit.status == "ready":
        return "ready", "place_approved_live_bets", []
    if live_audit.status == "no_matches":
        return "no_matches", "wait_for_fixtures", issues
    if odds_readiness.status == "insufficient" or _requires_odds_refresh(odds_readiness):
        return "blocked", "refresh_fixtures_and_odds", issues or ["odds_readiness_insufficient"]
    return "no_trade", "paper_or_observe_only", issues or ["no_live_gate_passed_candidates"]


def _prefixed_issues(prefix: str, issues: list[str]) -> list[str]:
    return [f"{prefix}:{issue}" for issue in issues]


def _requires_odds_refresh(odds_readiness: OddsReadinessReport) -> bool:
    refresh_markers = (
        "no_odds",
        "no_matching_market_odds",
        "odds_older_than_max_minutes",
    )
    return any(marker in issue for issue in odds_readiness.issues for marker in refresh_markers)
