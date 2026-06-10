from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from football_analysis.db import StructuredRepository
from football_analysis.live_preflight import LivePreflightReport, run_live_preflight
from football_analysis.live_review import LiveReviewReport, run_live_review
from football_analysis.models import AppModel
from football_analysis.odds_readiness import OddsReadinessReport
from football_analysis.settings import Settings, StrategyProfileSettings
from football_analysis.strategy import StrategyProfileAuditItem, StrategyProfileAuditReport, audit_strategy_profiles


DEFAULT_PROFILE_AUDIT_SEASONS = ["2122", "2223", "2324", "2425", "2526"]


class LiveDecisionReport(AppModel):
    checked_at: datetime
    status: str
    ready_to_bet: bool
    action: str
    issues: list[str] = Field(default_factory=list)
    components: dict[str, str] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    reproducibility: dict[str, Any] = Field(default_factory=dict)
    profile_audit: StrategyProfileAuditReport
    odds_readiness: OddsReadinessReport
    live_review: LiveReviewReport
    preflight: LivePreflightReport


def run_live_decision(
    repository: StructuredRepository,
    settings: Settings,
    include_past: bool = False,
    include_paper: bool = True,
    seasons: list[str] | None = None,
    full_profile_audit: bool = False,
    checked_at: datetime | None = None,
) -> LiveDecisionReport:
    """Build one reproducible go/no-go snapshot for real-money operation."""
    now = checked_at or datetime.now(settings.app.tzinfo)
    audit_seasons = seasons or DEFAULT_PROFILE_AUDIT_SEASONS
    profile_audit_mode = "full" if full_profile_audit else "contract"
    profile_audit = (
        audit_strategy_profiles(
            repository,
            configured_profiles=settings.strategy_profiles,
            seasons=audit_seasons,
        )
        if full_profile_audit
        else _contract_profile_audit(settings, audit_seasons)
    )
    live_review = run_live_review(
        repository,
        settings,
        include_paper=include_paper,
        checked_at=now,
    )
    preflight = run_live_preflight(
        repository,
        settings,
        include_past=include_past,
        checked_at=now,
    )
    status, ready_to_bet, action, issues = _decision(profile_audit, live_review, preflight)
    return LiveDecisionReport(
        checked_at=now,
        status=status,
        ready_to_bet=ready_to_bet,
        action=action,
        issues=issues,
        components={
            "profile_audit": "passed" if profile_audit.passed else "failed",
            "odds_readiness": preflight.odds_readiness.status,
            "live_review": live_review.status,
            "preflight": preflight.status,
        },
        thresholds=_thresholds(settings),
        reproducibility={
            "config_timezone": settings.app.timezone,
            "profile_audit_seasons": audit_seasons,
            "include_past": include_past,
            "include_paper": include_paper,
            "profile_audit_mode": profile_audit_mode,
            "active_strategy_profiles": [profile.id for profile in settings.strategy_profiles if profile.active],
        },
        profile_audit=profile_audit,
        odds_readiness=preflight.odds_readiness,
        live_review=live_review,
        preflight=preflight,
    )


def _decision(
    profile_audit: StrategyProfileAuditReport,
    live_review: LiveReviewReport,
    preflight: LivePreflightReport,
) -> tuple[str, bool, str, list[str]]:
    issues: list[str] = []
    if not profile_audit.passed:
        issues.extend(
            f"profile_audit:{item.profile_id}:{item.status}:{item.message}"
            for item in profile_audit.items
            if item.status != "matched"
        )
    if live_review.status == "action_required":
        issues.extend(f"live_review:{issue}" for issue in live_review.issues)
    if preflight.issues:
        issues.extend(f"preflight:{issue}" for issue in preflight.issues)

    if not profile_audit.passed:
        return "blocked", False, "review_strategy_profiles", issues
    if live_review.status == "action_required":
        return "paused", False, "apply_live_review_actions", issues
    if preflight.ready_to_bet:
        return "ready", True, preflight.action, []
    return preflight.status, False, preflight.action, issues or ["preflight_not_ready"]


def _contract_profile_audit(settings: Settings, seasons: list[str]) -> StrategyProfileAuditReport:
    items = [_contract_profile_item(profile, settings) for profile in settings.strategy_profiles if profile.active]
    items.sort(key=lambda item: item.profile_id)
    return StrategyProfileAuditReport(
        seasons=seasons,
        passed=all(item.status == "matched" for item in items),
        items=items,
    )


def _contract_profile_item(profile: StrategyProfileSettings, settings: Settings) -> StrategyProfileAuditItem:
    issues: list[str] = []
    if profile.roi is None:
        issues.append("missing_roi")
    if profile.settled_bets <= 0:
        issues.append("missing_settled_bets")
    if profile.average_clv is None:
        issues.append("missing_average_clv")

    if profile.live_enabled:
        live = settings.live_trading
        long_bets = profile.long_horizon_settled_bets or profile.settled_bets
        long_roi = profile.long_horizon_roi if profile.long_horizon_roi is not None else profile.roi
        if long_bets < live.min_long_horizon_bets:
            issues.append(f"live_min_long_horizon_bets:{long_bets}/{live.min_long_horizon_bets}")
        if long_roi is None or long_roi < live.min_long_horizon_roi:
            observed = "none" if long_roi is None else f"{long_roi:.4f}"
            issues.append(f"live_min_long_horizon_roi:{observed}/{live.min_long_horizon_roi:.4f}")
        if profile.holdout_settled_bets < live.min_holdout_bets:
            issues.append(f"live_min_holdout_bets:{profile.holdout_settled_bets}/{live.min_holdout_bets}")
        if profile.holdout_roi is None or profile.holdout_roi < live.min_holdout_roi:
            observed = "none" if profile.holdout_roi is None else f"{profile.holdout_roi:.4f}"
            issues.append(f"live_min_holdout_roi:{observed}/{live.min_holdout_roi:.4f}")
        positive_rate = (
            profile.holdout_positive_seasons / profile.holdout_season_count
            if profile.holdout_season_count
            else 0.0
        )
        if positive_rate < live.min_holdout_positive_rate:
            issues.append(
                f"live_min_holdout_positive_rate:{positive_rate:.3f}/{live.min_holdout_positive_rate:.3f}"
            )
        if profile.average_clv is None or profile.average_clv < live.min_average_clv:
            observed = "none" if profile.average_clv is None else f"{profile.average_clv:.4f}"
            issues.append(f"live_min_average_clv:{observed}/{live.min_average_clv:.4f}")
        if profile.worst_season_roi is not None and profile.worst_season_roi < live.max_worst_season_roi:
            issues.append(f"live_worst_season_roi:{profile.worst_season_roi:.4f}/{live.max_worst_season_roi:.4f}")

    return StrategyProfileAuditItem(
        profile_id=profile.id,
        status="stale" if issues else "matched",
        message="; ".join(issues) if issues else "profile contract accepted; run full profile-audit for drift check",
        configured=profile.model_dump(mode="json"),
        portfolio={"source": "configured_contract"},
    )


def _thresholds(settings: Settings) -> dict[str, Any]:
    live = settings.live_trading
    return {
        "min_bookmakers": live.min_bookmakers,
        "max_odds_age_minutes": live.max_odds_age_minutes,
        "max_execution_odds_slippage": live.max_execution_odds_slippage,
        "min_data_quality": live.min_data_quality,
        "min_value_score": live.min_value_score,
        "max_risk_score": live.max_risk_score,
        "min_confidence": live.min_confidence,
        "min_edge": live.min_edge,
        "min_long_horizon_bets": live.min_long_horizon_bets,
        "min_long_horizon_roi": live.min_long_horizon_roi,
        "min_holdout_bets": live.min_holdout_bets,
        "min_holdout_roi": live.min_holdout_roi,
        "min_holdout_positive_rate": live.min_holdout_positive_rate,
        "min_average_clv": live.min_average_clv,
        "max_worst_season_roi": live.max_worst_season_roi,
        "max_recent_consecutive_losses": live.max_recent_consecutive_losses,
        "rolling_window_settled_bets": live.rolling_window_settled_bets,
        "min_rolling_settled_bets": live.min_rolling_settled_bets,
        "max_rolling_loss_units": live.max_rolling_loss_units,
        "min_rolling_roi": live.min_rolling_roi,
        "review_min_settled_bets": live.review_min_settled_bets,
        "review_min_roi": live.review_min_roi,
        "review_min_average_clv": live.review_min_average_clv,
        "review_pause_roi": live.review_pause_roi,
        "max_stake_units_per_pick": live.max_stake_units_per_pick,
        "max_daily_stake_units": live.max_daily_stake_units,
    }
