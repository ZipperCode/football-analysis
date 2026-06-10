from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from pydantic import Field

from football_analysis.db import StructuredRepository
from football_analysis.models import AppModel, BetLog, Match, Recommendation
from football_analysis.scoring import _normalized_strategy_selection
from football_analysis.service import _is_paper_platform
from football_analysis.settings import LeagueSettings, Settings, StrategyProfileSettings


class LiveReviewProfile(AppModel):
    profile_id: str
    name: str
    league_code: str
    live_enabled: bool
    settled_bets: int
    total_stake_units: float
    profit_units: float
    roi: float | None
    average_clv: float | None
    recent_consecutive_losses: int
    action: str
    issues: list[str] = Field(default_factory=list)


class LiveReviewLeague(AppModel):
    league_code: str
    league_name: str
    tier: str
    settled_bets: int
    total_stake_units: float
    profit_units: float
    roi: float | None
    average_clv: float | None
    recent_consecutive_losses: int
    action: str
    issues: list[str] = Field(default_factory=list)


class LiveReviewReport(AppModel):
    checked_at: datetime
    status: str
    include_paper: bool
    min_settled_bets: int
    min_roi: float
    min_average_clv: float
    pause_roi: float
    profiles: list[LiveReviewProfile] = Field(default_factory=list)
    leagues: list[LiveReviewLeague] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


def run_live_review(
    repository: StructuredRepository,
    settings: Settings,
    include_paper: bool = True,
    checked_at: datetime | None = None,
) -> LiveReviewReport:
    """Review settled execution evidence and recommend operator actions."""
    now = checked_at or datetime.now(settings.app.tzinfo)
    bets = [
        bet
        for bet in repository.list_models("bets", BetLog)
        if bet.profit_units is not None and (include_paper or not _is_paper_platform(bet.platform))
    ]
    matches_by_id = {match.id: match for match in repository.list_models("matches", Match)}
    recommendations = repository.list_models("recommendations", Recommendation)
    profile_by_id = {profile.id: profile for profile in settings.strategy_profiles}

    bets_by_profile: dict[str, list[BetLog]] = defaultdict(list)
    bets_by_league: dict[str, list[BetLog]] = defaultdict(list)
    league_meta: dict[str, LeagueSettings] = {}

    for bet in bets:
        profile_id = _profile_id_for_bet(bet, recommendations)
        if profile_id:
            bets_by_profile[profile_id].append(bet)
        match = matches_by_id.get(bet.match_id)
        league = _league_settings_for_match(match, settings) if match else None
        league_code = league.code if league else "UNKNOWN"
        if league:
            league_meta[league_code] = league
        bets_by_league[league_code].append(bet)

    profiles = [
        _profile_review(profile, bets_by_profile.get(profile.id, []), settings)
        for profile in settings.strategy_profiles
        if profile.active or profile.id in bets_by_profile
    ]
    leagues = [
        _league_review(league_code, league_meta.get(league_code), league_bets, settings)
        for league_code, league_bets in sorted(bets_by_league.items())
    ]
    issues = _report_issues(profiles, leagues)
    return LiveReviewReport(
        checked_at=now,
        status=_report_status(profiles, leagues),
        include_paper=include_paper,
        min_settled_bets=settings.live_trading.review_min_settled_bets,
        min_roi=settings.live_trading.review_min_roi,
        min_average_clv=settings.live_trading.review_min_average_clv,
        pause_roi=settings.live_trading.review_pause_roi,
        profiles=profiles,
        leagues=leagues,
        issues=issues,
    )


def _profile_review(
    profile: StrategyProfileSettings,
    bets: list[BetLog],
    settings: Settings,
) -> LiveReviewProfile:
    summary = _settled_summary(bets)
    action, issues = _review_action(
        settled_bets=int(summary["settled_bets"]),
        roi=summary["roi"],
        average_clv=summary["average_clv"],
        live_enabled=profile.live_enabled,
        settings=settings,
    )
    return LiveReviewProfile(
        profile_id=profile.id,
        name=profile.name,
        league_code=profile.league_code,
        live_enabled=profile.live_enabled,
        settled_bets=int(summary["settled_bets"]),
        total_stake_units=float(summary["total_stake_units"]),
        profit_units=float(summary["profit_units"]),
        roi=summary["roi"],
        average_clv=summary["average_clv"],
        recent_consecutive_losses=_recent_consecutive_losses(bets),
        action=action,
        issues=issues,
    )


def _league_review(
    league_code: str,
    league: LeagueSettings | None,
    bets: list[BetLog],
    settings: Settings,
) -> LiveReviewLeague:
    summary = _settled_summary(bets)
    action, issues = _review_action(
        settled_bets=int(summary["settled_bets"]),
        roi=summary["roi"],
        average_clv=summary["average_clv"],
        live_enabled=bool(league and not league.paper_only),
        settings=settings,
    )
    return LiveReviewLeague(
        league_code=league_code,
        league_name=league.name if league else "Unknown",
        tier=league.tier if league else "unknown",
        settled_bets=int(summary["settled_bets"]),
        total_stake_units=float(summary["total_stake_units"]),
        profit_units=float(summary["profit_units"]),
        roi=summary["roi"],
        average_clv=summary["average_clv"],
        recent_consecutive_losses=_recent_consecutive_losses(bets),
        action=action,
        issues=issues,
    )


def _review_action(
    settled_bets: int,
    roi: float | None,
    average_clv: float | None,
    live_enabled: bool,
    settings: Settings,
) -> tuple[str, list[str]]:
    live = settings.live_trading
    issues: list[str] = []
    if settled_bets < live.review_min_settled_bets:
        issues.append(f"sample_below_min:{settled_bets}/{live.review_min_settled_bets}")
        return "observe_more", issues
    if roi is not None and roi < live.review_min_roi:
        issues.append("negative_roi")
    if average_clv is None:
        issues.append("missing_clv")
    elif average_clv < live.review_min_average_clv:
        issues.append("negative_clv")
    if roi is not None and roi <= live.review_pause_roi and "negative_clv" in issues:
        return "pause_live", issues
    if "negative_roi" in issues or "negative_clv" in issues:
        return "demote_to_paper" if live_enabled else "keep_paper"
    return "keep_live" if live_enabled else "keep_paper"


def _settled_summary(bets: list[BetLog]) -> dict[str, float | int | None]:
    settled = [bet for bet in bets if bet.profit_units is not None]
    stake = sum(bet.stake_units for bet in settled)
    profit = sum(bet.profit_units or 0.0 for bet in settled)
    clv_values = [
        (bet.odds / bet.closing_odds) - 1.0
        for bet in settled
        if bet.closing_odds is not None and bet.odds > 0 and bet.closing_odds > 0
    ]
    roi = profit / stake if stake else None
    average_clv = sum(clv_values) / len(clv_values) if clv_values else None
    return {
        "settled_bets": len(settled),
        "total_stake_units": round(stake, 3),
        "profit_units": round(profit, 3),
        "roi": round(roi, 4) if roi is not None else None,
        "average_clv": round(average_clv, 4) if average_clv is not None else None,
    }


def _recent_consecutive_losses(bets: list[BetLog]) -> int:
    losses = 0
    for bet in sorted(bets, key=lambda item: item.placed_at, reverse=True):
        if bet.profit_units is None:
            continue
        if bet.profit_units < 0:
            losses += 1
            continue
        break
    return losses


def _profile_id_for_bet(bet: BetLog, recommendations: list[Recommendation]) -> str | None:
    normalized_bet_selection = _normalized_strategy_selection(bet.selection, bet.market_type.value)
    for recommendation in recommendations:
        if recommendation.match_id != bet.match_id:
            continue
        if recommendation.market_type != bet.market_type:
            continue
        if _normalized_strategy_selection(recommendation.selection or "", bet.market_type.value) != normalized_bet_selection:
            continue
        strategy_profile = (
            recommendation.score_breakdown.get("strategy_profile")
            or recommendation.odds_basis.get("strategy_profile")
        )
        if isinstance(strategy_profile, dict) and strategy_profile.get("matched"):
            value = strategy_profile.get("id")
            return str(value) if value else None
    return None


def _league_settings_for_match(match: Match | None, settings: Settings) -> LeagueSettings | None:
    if match is None:
        return None
    normalized_league = match.league.strip().lower()
    for league in settings.leagues:
        values = [league.code, league.name, league.football_data_uk_code, league.football_data_org_code]
        if league.country and league.name:
            values.append(f"{league.country} - {league.name}")
        values.extend(league.aliases)
        if normalized_league in {value.strip().lower() for value in values if value}:
            return league
    return None


def _report_status(profiles: list[LiveReviewProfile], leagues: list[LiveReviewLeague]) -> str:
    actions = {item.action for item in [*profiles, *leagues]}
    if actions & {"pause_live", "demote_to_paper"}:
        return "action_required"
    if "observe_more" in actions:
        return "monitoring"
    return "ok"


def _report_issues(profiles: list[LiveReviewProfile], leagues: list[LiveReviewLeague]) -> list[str]:
    issues: list[str] = []
    for profile in profiles:
        if profile.action in {"pause_live", "demote_to_paper"}:
            issues.append(f"profile:{profile.profile_id}:{profile.action}:{','.join(profile.issues)}")
    for league in leagues:
        if league.action in {"pause_live", "demote_to_paper"}:
            issues.append(f"league:{league.league_code}:{league.action}:{','.join(league.issues)}")
    return issues
