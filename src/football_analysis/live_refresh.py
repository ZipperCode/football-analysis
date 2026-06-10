from __future__ import annotations

from datetime import datetime

from pydantic import Field

from football_analysis.live_preflight import LivePreflightReport, run_live_preflight
from football_analysis.models import AppModel, IngestionResult
from football_analysis.odds_readiness import OddsRefreshRequirement
from football_analysis.service import AnalysisService
from football_analysis.settings import LeagueSettings, Settings


class LiveRefreshOperation(AppModel):
    kind: str
    source: str
    league_code: str
    date: str | None = None
    max_events: int | None = None
    executed: bool = False
    result_job_id: str | None = None
    error_count: int = 0


class LiveRefreshReport(AppModel):
    checked_at: datetime
    date: str
    dry_run: bool
    scope: str
    fixture_source: str
    odds_source: str
    requested_league: str | None = None
    leagues: list[str]
    max_events: int | None = None
    allow_odds_fallback: bool = False
    operations: list[LiveRefreshOperation] = Field(default_factory=list)
    fixture_results: list[IngestionResult] = Field(default_factory=list)
    odds_results: list[IngestionResult] = Field(default_factory=list)
    preflight: LivePreflightReport
    refresh_requirements: list[OddsRefreshRequirement] = Field(default_factory=list)
    status: str
    ready_to_bet: bool
    action: str
    issues: list[str] = Field(default_factory=list)


def run_live_refresh(
    service: AnalysisService,
    date: str,
    fixture_source: str = "auto",
    odds_source: str = "auto",
    league: str | None = None,
    scope: str = "active-profiles",
    max_events: int | None = None,
    include_past: bool = False,
    dry_run: bool = False,
    allow_odds_fallback: bool = False,
) -> LiveRefreshReport:
    checked_at = datetime.now(service.settings.app.tzinfo)
    leagues = _target_leagues(service.settings, requested_league=league, scope=scope)
    operations: list[LiveRefreshOperation] = []
    fixture_results: list[IngestionResult] = []
    odds_results: list[IngestionResult] = []
    issues: list[str] = []
    empty_fixture_leagues: set[str] = set()
    empty_odds_leagues: set[str] = set()

    if not leagues:
        issues.append("no_refreshable_strategy_profile_leagues")

    for league_code in leagues:
        resolved_source, mapping_issue = _resolve_source(
            service.settings,
            league_code,
            requested_source=fixture_source,
            kind="fixtures",
        )
        operation = LiveRefreshOperation(
            kind="fixtures",
            source=resolved_source,
            league_code=league_code,
            date=date,
            executed=not dry_run and mapping_issue is None,
        )
        if mapping_issue is not None:
            issues.append(mapping_issue)
        elif not dry_run:
            result = service.ingestion.ingest_fixtures(date=date, source=resolved_source, league_code=league_code)
            fixture_results.append(result)
            operation = _operation_with_result(operation, result)
            issues.extend(f"fixtures:{league_code}:{error}" for error in result.errors)
            if _empty_ingestion(result):
                issues.append(f"fixtures_refresh_empty:{league_code}")
                empty_fixture_leagues.add(league_code)
        operations.append(operation)

    for league_code in leagues:
        odds_operations, league_odds_results, league_issues, odds_empty = _run_odds_refresh_operations(
            service=service,
            league_code=league_code,
            date=date,
            requested_source=odds_source,
            max_events=max_events,
            dry_run=dry_run,
            allow_fallback=_allow_odds_fallback(
                scope=scope,
                requested_league=league,
                explicit_allow=allow_odds_fallback,
            ),
        )
        operations.extend(odds_operations)
        odds_results.extend(league_odds_results)
        issues.extend(league_issues)
        if odds_empty:
            empty_odds_leagues.add(league_code)

    issues.extend(
        _active_profile_empty_refresh_issues(
            leagues=leagues,
            scope=scope,
            requested_league=league,
            dry_run=dry_run,
            empty_fixture_leagues=empty_fixture_leagues,
            empty_odds_leagues=empty_odds_leagues,
        )
    )

    preflight = run_live_preflight(
        service.repository,
        service.settings,
        include_past=include_past,
        checked_at=checked_at,
    )
    issues.extend(f"preflight:{issue}" for issue in preflight.issues)
    return LiveRefreshReport(
        checked_at=checked_at,
        date=date,
        dry_run=dry_run,
        scope=scope,
        fixture_source=fixture_source,
        odds_source=odds_source,
        requested_league=league,
        leagues=leagues,
        max_events=max_events,
        allow_odds_fallback=allow_odds_fallback,
        operations=operations,
        fixture_results=fixture_results,
        odds_results=odds_results,
        preflight=preflight,
        refresh_requirements=preflight.odds_readiness.refresh_requirements,
        status="planned" if dry_run else preflight.status,
        ready_to_bet=False if dry_run else preflight.ready_to_bet,
        action="execute_live_refresh" if dry_run else preflight.action,
        issues=issues,
    )


def _operation_with_result(operation: LiveRefreshOperation, result: IngestionResult) -> LiveRefreshOperation:
    return operation.model_copy(
        update={
            "result_job_id": result.job.id,
            "error_count": len(result.errors),
        }
    )


def _empty_ingestion(result: IngestionResult) -> bool:
    return result.inserted == 0 and result.updated == 0 and not result.errors


def _run_odds_refresh_operations(
    service: AnalysisService,
    league_code: str,
    date: str,
    requested_source: str,
    max_events: int | None,
    dry_run: bool,
    allow_fallback: bool,
) -> tuple[list[LiveRefreshOperation], list[IngestionResult], list[str], bool]:
    operations: list[LiveRefreshOperation] = []
    results: list[IngestionResult] = []
    issues: list[str] = []
    normalized_source = requested_source.strip().lower()
    candidates = _mapped_sources(
        service.settings,
        league_code=league_code,
        requested_source=requested_source,
        kind="odds",
    )

    if not candidates:
        issues.append(f"odds_source_unmapped:{league_code}:{requested_source}")
        operations.append(
            LiveRefreshOperation(
                kind="odds",
                source=requested_source,
                league_code=league_code,
                date=_odds_date_for_source(requested_source, date),
                max_events=max_events,
                executed=False,
            )
        )
        return operations, results, issues, False

    attempted_empty = False
    for index, source in enumerate(candidates):
        odds_date = _odds_date_for_source(source, date)
        operation = LiveRefreshOperation(
            kind="odds",
            source=source,
            league_code=league_code,
            date=odds_date,
            max_events=max_events,
            executed=not dry_run,
        )
        if dry_run:
            operations.append(operation)
            break

        result = service.ingestion.ingest_odds(
            date=odds_date,
            source=source,
            league_code=league_code,
            max_events=max_events,
        )
        results.append(result)
        operation = _operation_with_result(operation, result)
        operations.append(operation)
        issues.extend(f"odds:{league_code}:{error}" for error in result.errors)

        if not _empty_ingestion(result) and not result.errors:
            return operations, results, issues, False

        attempted_empty = attempted_empty or _empty_ingestion(result)
        if normalized_source != "auto" or index + 1 >= len(candidates):
            if _empty_ingestion(result):
                issues.append(f"odds_refresh_empty:{league_code}")
            return operations, results, issues, attempted_empty

        if not allow_fallback:
            issues.append(f"odds_fallback_skipped:{league_code}:{candidates[index + 1]}")
            if _empty_ingestion(result):
                issues.append(f"odds_refresh_empty:{league_code}")
            return operations, results, issues, attempted_empty

        issues.append(f"odds_source_fallback:{league_code}:{source}->{candidates[index + 1]}")

    return operations, results, issues, attempted_empty


def _allow_odds_fallback(scope: str, requested_league: str | None, explicit_allow: bool) -> bool:
    if explicit_allow:
        return True
    if requested_league:
        return True
    return scope.strip().lower() != "live-leagues"


def _active_profile_empty_refresh_issues(
    leagues: list[str],
    scope: str,
    requested_league: str | None,
    dry_run: bool,
    empty_fixture_leagues: set[str],
    empty_odds_leagues: set[str],
) -> list[str]:
    if dry_run or requested_league or scope.strip().lower() != "active-profiles":
        return []
    empty_profile_leagues = [
        league_code
        for league_code in leagues
        if league_code in empty_fixture_leagues and league_code in empty_odds_leagues
    ]
    if not empty_profile_leagues:
        return []
    return [
        f"active_profile_refresh_empty:{','.join(empty_profile_leagues)}",
        "consider_scope_live_leagues",
    ]


def _resolve_source(
    settings: Settings,
    league_code: str,
    requested_source: str,
    kind: str,
) -> tuple[str, str | None]:
    normalized = requested_source.strip().lower()
    if normalized != "auto":
        return requested_source, _source_mapping_issue(settings, league_code, requested_source, kind=kind)
    for source in _source_preferences(kind):
        if _source_mapping_issue(settings, league_code, source, kind=kind) is None:
            return source, None
    return "auto", f"{kind}_source_unmapped:{league_code}:auto"


def _mapped_sources(
    settings: Settings,
    league_code: str,
    requested_source: str,
    kind: str,
) -> list[str]:
    normalized = requested_source.strip().lower()
    if normalized != "auto":
        return [] if _source_mapping_issue(settings, league_code, requested_source, kind=kind) else [requested_source]
    return [
        source
        for source in _source_preferences(kind)
        if _source_mapping_issue(settings, league_code, source, kind=kind) is None
    ]


def _source_preferences(kind: str) -> list[str]:
    if kind == "fixtures":
        return ["api_football", "football_data_org", "odds_api_io"]
    if kind == "odds":
        return ["odds_api_io", "api_football"]
    return []


def _source_mapping_issue(settings: Settings, league_code: str, source: str, kind: str) -> str | None:
    league = _league_by_code(settings, league_code)
    if league is None:
        return f"{kind}_source_unmapped:{league_code}:{source}"
    if source == "api_football":
        if league.api_football_league_id and league.season:
            return None
        return f"{kind}_source_unmapped:{league_code}:{source}"
    if source == "football_data_org" and kind == "fixtures":
        if league.football_data_org_code:
            return None
        return f"{kind}_source_unmapped:{league_code}:{source}"
    if source == "odds_api_io":
        if league.odds_api_slug:
            return None
        return f"{kind}_source_unmapped:{league_code}:{source}"
    return None


def _target_leagues(settings: Settings, requested_league: str | None, scope: str) -> list[str]:
    if requested_league:
        return [_canonical_league_code(settings, requested_league) or requested_league]
    normalized_scope = scope.strip().lower()
    if normalized_scope == "live-leagues":
        return sorted(
            league.code
            for league in settings.leagues
            if league.strategy_mode == "live" and not league.paper_only
        )
    if normalized_scope != "active-profiles":
        raise ValueError(f"unsupported_live_refresh_scope:{scope}")
    mapped: set[str] = set()
    for profile in settings.strategy_profiles:
        if not profile.active:
            continue
        league_code = _canonical_league_code(settings, profile.league_code)
        if league_code:
            mapped.add(league_code)
    return sorted(mapped)


def _canonical_league_code(settings: Settings, code: str) -> str | None:
    normalized = code.strip().upper()
    league = _league_by_identifier(settings, normalized)
    return league.code if league else None


def _league_by_code(settings: Settings, code: str) -> LeagueSettings | None:
    normalized = code.strip().upper()
    for league in settings.leagues:
        if league.code.upper() == normalized:
            return league
    return None


def _league_by_identifier(settings: Settings, normalized: str) -> LeagueSettings | None:
    for league in settings.leagues:
        if normalized in _league_identifiers(league):
            return league
    return None


def _league_identifiers(league: LeagueSettings) -> set[str]:
    values = {
        league.code,
        league.football_data_uk_code,
        league.football_data_org_code,
        league.name,
    }
    values.update(league.aliases)
    return {value.strip().upper() for value in values if value}


def _odds_date_for_source(source: str, date: str) -> str | None:
    if source == "api_football":
        return date
    return None
