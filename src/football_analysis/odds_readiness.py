from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from pydantic import Field

from football_analysis.db import StructuredRepository
from football_analysis.models import AppModel, Match, OddsSnapshot
from football_analysis.scoring import _normalized_strategy_selection
from football_analysis.settings import LeagueSettings, Settings, StrategyProfileSettings


class OddsMarketCoverage(AppModel):
    match_id: str
    league_code: str | None = None
    market_type: str
    line: str | None = None
    snapshot_count: int
    source_count: int
    bookmaker_count: int
    required_bookmakers: int
    selections: list[str] = Field(default_factory=list)
    has_market_average: bool
    has_best_price: bool
    ready: bool
    issues: list[str] = Field(default_factory=list)
    strategy_profile_ids: list[str] = Field(default_factory=list)


class StrategyProfileReadiness(AppModel):
    profile_id: str
    name: str
    league_code: str
    market_type: str
    selections: list[str] = Field(default_factory=list)
    status: str
    matching_matches: int
    ready_matches: int
    ready_market_groups: int
    issues: list[str] = Field(default_factory=list)


class LeagueCoverageReadiness(AppModel):
    code: str
    name: str
    tier: str
    analysis_depth: str
    strategy_mode: str
    paper_only: bool
    min_bookmakers: int
    tier_policy_label: str | None = None
    tier_policy_min_value_score: float | None = None
    tier_policy_max_risk_score: float | None = None
    tier_policy_min_confidence: float | None = None
    tier_policy_max_stake_units: float | None = None
    scoped_matches: int
    odds_snapshots: int
    market_groups: int
    ready_market_groups: int
    status: str
    issues: list[str] = Field(default_factory=list)


class OddsReadinessReport(AppModel):
    checked_at: datetime
    status: str
    min_bookmakers: int
    min_profile_matches: int
    total_matches: int
    scoped_matches: int
    total_odds_snapshots: int
    scoped_odds_snapshots: int
    active_profiles: int
    ready_profiles: int
    partial_profiles: int
    insufficient_profiles: int
    issues: list[str] = Field(default_factory=list)
    profiles: list[StrategyProfileReadiness] = Field(default_factory=list)
    market_coverages: list[OddsMarketCoverage] = Field(default_factory=list)
    league_coverages: list[LeagueCoverageReadiness] = Field(default_factory=list)


def audit_odds_readiness(
    repository: StructuredRepository,
    settings: Settings,
    min_bookmakers: int = 2,
    min_profile_matches: int = 1,
    include_past: bool = False,
    checked_at: datetime | None = None,
) -> OddsReadinessReport:
    """Audit whether stored live odds can support the configured strategy profiles."""
    now = checked_at or datetime.now(settings.app.tzinfo)
    matches = repository.list_models("matches", Match)
    odds = repository.list_models("odds", OddsSnapshot)
    scoped_matches = [
        match
        for match in matches
        if include_past or _match_local_date(match, settings) >= now.date()
    ]
    scoped_match_ids = {match.id for match in scoped_matches}
    scoped_odds = [snapshot for snapshot in odds if snapshot.match_id in scoped_match_ids]

    league_settings_by_match = {match.id: _league_settings_for_match(match, settings) for match in scoped_matches}
    league_by_match = {
        match_id: _strategy_league_code(league)
        for match_id, league in league_settings_by_match.items()
    }
    required_bookmakers_by_match = {
        match_id: league.min_bookmakers
        for match_id, league in league_settings_by_match.items()
        if league is not None
    }
    market_coverages = _build_market_coverages(
        scoped_odds,
        league_by_match=league_by_match,
        required_bookmakers_by_match=required_bookmakers_by_match,
        default_min_bookmakers=min_bookmakers,
    )
    league_coverages = _build_league_coverages(
        settings=settings,
        scoped_matches=scoped_matches,
        scoped_odds=scoped_odds,
        market_coverages=market_coverages,
    )
    profiles = [
        _profile_readiness(
            profile,
            market_coverages=market_coverages,
            settings=settings,
            min_profile_matches=min_profile_matches,
        )
        for profile in settings.strategy_profiles
        if profile.active
    ]
    profile_ids_by_group = _profile_ids_by_market_group(profiles, market_coverages)
    market_coverages = [
        coverage.model_copy(update={"strategy_profile_ids": profile_ids_by_group.get(_market_key(coverage), [])})
        for coverage in market_coverages
    ]

    ready_profiles = sum(1 for profile in profiles if profile.status == "ready")
    partial_profiles = sum(1 for profile in profiles if profile.status == "partial")
    insufficient_profiles = sum(1 for profile in profiles if profile.status == "insufficient")
    issues = _report_issues(profiles, scoped_matches, scoped_odds)
    if profiles and ready_profiles == len(profiles):
        status = "ready"
    elif ready_profiles > 0 or partial_profiles > 0:
        status = "partial"
    else:
        status = "insufficient"

    return OddsReadinessReport(
        checked_at=now,
        status=status,
        min_bookmakers=min_bookmakers,
        min_profile_matches=min_profile_matches,
        total_matches=len(matches),
        scoped_matches=len(scoped_matches),
        total_odds_snapshots=len(odds),
        scoped_odds_snapshots=len(scoped_odds),
        active_profiles=len(profiles),
        ready_profiles=ready_profiles,
        partial_profiles=partial_profiles,
        insufficient_profiles=insufficient_profiles,
        issues=issues,
        profiles=profiles,
        market_coverages=market_coverages,
        league_coverages=league_coverages,
    )


def _build_market_coverages(
    snapshots: list[OddsSnapshot],
    league_by_match: dict[str, str | None],
    required_bookmakers_by_match: dict[str, int],
    default_min_bookmakers: int,
) -> list[OddsMarketCoverage]:
    """Group snapshots by match and market, then apply the league-specific bookmaker gate."""
    groups: dict[tuple[str, str, str | None], list[OddsSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        groups[(snapshot.match_id, snapshot.market_type.value, snapshot.line)].append(snapshot)

    coverages: list[OddsMarketCoverage] = []
    for (match_id, market_type, line), group in groups.items():
        selections = sorted({selection for snapshot in group for selection in snapshot.outcome_odds})
        issues: list[str] = []
        bookmaker_count = len({snapshot.bookmaker for snapshot in group if snapshot.bookmaker})
        required_bookmakers = required_bookmakers_by_match.get(match_id, default_min_bookmakers)
        has_market_average = _has_complete_prices(group, selections, "market_average")
        has_best_price = _has_complete_prices(group, selections, "best_price")
        if bookmaker_count < required_bookmakers:
            issues.append(f"bookmakers_below_min:{bookmaker_count}/{required_bookmakers}")
        if not has_market_average:
            issues.append("missing_market_average")
        if not has_best_price:
            issues.append("missing_best_price")
        coverages.append(
            OddsMarketCoverage(
                match_id=match_id,
                league_code=league_by_match.get(match_id),
                market_type=market_type,
                line=line,
                snapshot_count=len(group),
                source_count=len({snapshot.source for snapshot in group}),
                bookmaker_count=bookmaker_count,
                required_bookmakers=required_bookmakers,
                selections=selections,
                has_market_average=has_market_average,
                has_best_price=has_best_price,
                ready=not issues,
                issues=issues,
            )
        )
    return sorted(coverages, key=lambda item: (item.league_code or "", item.match_id, item.market_type, item.line or ""))


def _build_league_coverages(
    settings: Settings,
    scoped_matches: list[Match],
    scoped_odds: list[OddsSnapshot],
    market_coverages: list[OddsMarketCoverage],
) -> list[LeagueCoverageReadiness]:
    """Summarize readiness by configured league so small leagues can incubate separately."""
    league_code_by_match: dict[str, str] = {}
    matches_by_league: dict[str, list[Match]] = defaultdict(list)
    for match in scoped_matches:
        league = _league_settings_for_match(match, settings)
        if league is None:
            continue
        league_code_by_match[match.id] = league.code
        matches_by_league[league.code].append(match)

    odds_by_league: dict[str, list[OddsSnapshot]] = defaultdict(list)
    for snapshot in scoped_odds:
        if league_code := league_code_by_match.get(snapshot.match_id):
            odds_by_league[league_code].append(snapshot)

    market_groups_by_league: dict[str, list[OddsMarketCoverage]] = defaultdict(list)
    for coverage in market_coverages:
        if league_code := league_code_by_match.get(coverage.match_id):
            market_groups_by_league[league_code].append(coverage)

    reports: list[LeagueCoverageReadiness] = []
    for league in settings.leagues:
        policy = settings.tier_policies.get(league.tier)
        league_matches = matches_by_league.get(league.code, [])
        league_odds = odds_by_league.get(league.code, [])
        league_market_groups = market_groups_by_league.get(league.code, [])
        ready_groups = [coverage for coverage in league_market_groups if coverage.ready]
        issues: list[str] = []

        if ready_groups:
            status = "ready"
        elif league_market_groups:
            status = "partial"
            issues.append("no_ready_market_groups")
        elif league_matches:
            status = "fixtures_only"
            issues.append("no_odds_snapshots")
        else:
            status = "idle"
            issues.append("no_today_or_future_matches")

        reports.append(
            LeagueCoverageReadiness(
                code=league.code,
                name=league.name,
                tier=league.tier,
                analysis_depth=league.analysis_depth,
                strategy_mode=league.strategy_mode,
                paper_only=league.paper_only,
                min_bookmakers=league.min_bookmakers,
                tier_policy_label=policy.label if policy else None,
                tier_policy_min_value_score=policy.min_value_score if policy else None,
                tier_policy_max_risk_score=policy.max_risk_score if policy else None,
                tier_policy_min_confidence=policy.min_confidence if policy else None,
                tier_policy_max_stake_units=policy.max_stake_units if policy else None,
                scoped_matches=len(league_matches),
                odds_snapshots=len(league_odds),
                market_groups=len(league_market_groups),
                ready_market_groups=len(ready_groups),
                status=status,
                issues=issues,
            )
        )

    return reports


def _profile_readiness(
    profile: StrategyProfileSettings,
    market_coverages: list[OddsMarketCoverage],
    settings: Settings,
    min_profile_matches: int,
) -> StrategyProfileReadiness:
    matching_groups = [coverage for coverage in market_coverages if _coverage_matches_profile(coverage, profile)]
    matching_matches = {coverage.match_id for coverage in matching_groups}
    ready_groups = [coverage for coverage in matching_groups if coverage.ready]
    ready_matches = {coverage.match_id for coverage in ready_groups}
    issues: list[str] = []
    configured_leagues = _configured_strategy_leagues(settings)
    if profile.league_code.upper() not in configured_leagues:
        issues.append("league_not_configured")
    if not matching_groups:
        issues.append("no_matching_market_odds")
    if matching_groups and len(ready_matches) < min_profile_matches:
        issues.append(f"ready_matches_below_min:{len(ready_matches)}/{min_profile_matches}")

    if len(ready_matches) >= min_profile_matches:
        status = "ready"
    elif matching_groups:
        status = "partial"
    else:
        status = "insufficient"

    return StrategyProfileReadiness(
        profile_id=profile.id,
        name=profile.name,
        league_code=profile.league_code,
        market_type=profile.market_type,
        selections=profile.selections,
        status=status,
        matching_matches=len(matching_matches),
        ready_matches=len(ready_matches),
        ready_market_groups=len(ready_groups),
        issues=issues,
    )


def _profile_ids_by_market_group(
    profiles: list[StrategyProfileReadiness],
    market_coverages: list[OddsMarketCoverage],
) -> dict[tuple[str, str, str | None], list[str]]:
    result: dict[tuple[str, str, str | None], list[str]] = {}
    for coverage in market_coverages:
        profile_ids = [
            profile.profile_id
            for profile in profiles
            if _coverage_matches_profile_readiness(coverage, profile)
        ]
        if profile_ids:
            result[_market_key(coverage)] = profile_ids
    return result


def _coverage_matches_profile(coverage: OddsMarketCoverage, profile: StrategyProfileSettings) -> bool:
    if coverage.league_code != profile.league_code.upper():
        return False
    if coverage.market_type != profile.market_type:
        return False
    coverage_selections = {
        _normalized_strategy_selection(selection, coverage.market_type) for selection in coverage.selections
    }
    profile_selections = {
        _normalized_strategy_selection(selection, profile.market_type) for selection in profile.selections
    }
    return bool(coverage_selections & profile_selections)


def _coverage_matches_profile_readiness(
    coverage: OddsMarketCoverage,
    profile: StrategyProfileReadiness,
) -> bool:
    if coverage.league_code != profile.league_code.upper():
        return False
    if coverage.market_type != profile.market_type:
        return False
    coverage_selections = {
        _normalized_strategy_selection(selection, coverage.market_type) for selection in coverage.selections
    }
    profile_selections = {
        _normalized_strategy_selection(selection, profile.market_type) for selection in profile.selections
    }
    return bool(coverage_selections & profile_selections)


def _has_complete_prices(group: list[OddsSnapshot], selections: list[str], field_name: str) -> bool:
    if not selections:
        return False
    for selection in selections:
        if not any(getattr(snapshot, field_name).get(selection) for snapshot in group):
            return False
    return True


def _football_data_uk_code(match: Match, settings: Settings) -> str | None:
    league = _league_settings_for_match(match, settings)
    return _strategy_league_code(league)


def _configured_strategy_leagues(settings: Settings) -> set[str]:
    return {
        (league.football_data_uk_code or league.code).upper()
        for league in settings.leagues
        if league.football_data_uk_code or league.code
    }


def _league_settings_for_match(match: Match, settings: Settings) -> LeagueSettings | None:
    """Match provider league names against configured names, aliases, and historical codes."""
    normalized_league = match.league.strip().lower()
    for league in settings.leagues:
        if normalized_league in {value.strip().lower() for value in _league_match_values(league) if value}:
            return league
    return None


def _league_match_values(league: LeagueSettings) -> list[str]:
    """Collect every stable identifier that can appear as Match.league."""
    values = [league.code, league.name, league.football_data_uk_code, league.football_data_org_code]
    if league.country and league.name:
        values.append(f"{league.country} - {league.name}")
    values.extend(league.aliases)
    return [value for value in values if value]


def _strategy_league_code(league: LeagueSettings | None) -> str | None:
    """Return the historical strategy code when available, otherwise the configured code."""
    if league is None:
        return None
    return (league.football_data_uk_code or league.code).upper()


def _match_local_date(match: Match, settings: Settings):
    kickoff = match.kickoff_at
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=settings.app.tzinfo)
    return kickoff.astimezone(settings.app.tzinfo).date()


def _market_key(coverage: OddsMarketCoverage) -> tuple[str, str, str | None]:
    return (coverage.match_id, coverage.market_type, coverage.line)


def _report_issues(
    profiles: list[StrategyProfileReadiness],
    scoped_matches: list[Match],
    scoped_odds: list[OddsSnapshot],
) -> list[str]:
    issues: list[str] = []
    if not profiles:
        issues.append("no_active_strategy_profiles")
    if not scoped_matches:
        issues.append("no_today_or_future_matches")
    if scoped_matches and not scoped_odds:
        issues.append("no_odds_for_today_or_future_matches")
    for profile in profiles:
        if profile.status != "ready":
            issues.append(f"profile_not_ready:{profile.profile_id}:{','.join(profile.issues)}")
    return issues
