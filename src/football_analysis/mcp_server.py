from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from football_analysis.evaluation import evaluate_finished_matches as build_finished_evaluation
from football_analysis.live_decision import run_live_decision as build_live_decision
from football_analysis.live_refresh import run_live_refresh
from football_analysis.odds_readiness import audit_odds_readiness
from football_analysis.production import (
    build_analysis_advice_report,
    build_production_health,
    build_production_status,
    format_analysis_advice_alert,
    run_production_cycle,
    send_telegram_alert,
)
from football_analysis.models import Match, RecommendationStatus
from football_analysis.research import match_league_filter, research_and_store_match
from football_analysis.service import get_service


mcp = FastMCP("football-analysis")


def _dump(payload: Any) -> Any:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return {key: _dump(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_dump(value) for value in payload]
    return payload


def _telegram_credentials() -> tuple[str | None, str | None]:
    service = get_service()
    return (
        os.getenv(service.settings.telegram.bot_token_env),
        os.getenv(service.settings.telegram.chat_id_env),
    )


def _parse_recommendation_statuses(value: str) -> set[RecommendationStatus]:
    statuses: set[RecommendationStatus] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        statuses.add(RecommendationStatus(item))
    return statuses or {RecommendationStatus.recommended}


@mcp.tool()
def production_status(recent_limit: int = 10, include_past: bool = False) -> dict[str, Any]:
    """Read the current production pipeline status from the configured database."""
    service = get_service()
    return _dump(build_production_status(service, recent_limit=recent_limit, include_past=include_past))


@mcp.tool()
def production_health(
    recent_limit: int = 10,
    include_past: bool = False,
    max_cycle_age_minutes: int = 90,
    max_data_job_age_minutes: int = 180,
) -> dict[str, Any]:
    """Read production health and heartbeat freshness without remote provider calls."""
    service = get_service()
    return _dump(
        build_production_health(
            service,
            recent_limit=recent_limit,
            include_past=include_past,
            max_cycle_age_minutes=max_cycle_age_minutes,
            max_data_job_age_minutes=max_data_job_age_minutes,
        )
    )


@mcp.tool()
def get_picks_today(limit: int = 5) -> dict[str, Any]:
    """Score upcoming locally stored matches and return analysis advice. Saves recommendations locally."""
    service = get_service()
    report = build_analysis_advice_report(service, limit=limit)
    return _dump(report)


@mcp.tool()
def evaluate_finished_matches(
    date_text: str,
    league: str | None = None,
    include_statuses: str = "recommended",
    result_overrides: list[str] | None = None,
    save_results: bool = False,
) -> dict[str, Any]:
    """Settle already finished stored matches against current strategy analysis. No broker orders."""
    service = get_service()
    report = build_finished_evaluation(
        service,
        target_date=date.fromisoformat(date_text),
        league=league,
        included_statuses=_parse_recommendation_statuses(include_statuses),
        result_overrides=result_overrides or [],
        save_results=save_results,
    )
    return _dump(report)


@mcp.tool()
def get_live_decision(include_past: bool = False, full_profile_audit: bool = False) -> dict[str, Any]:
    """Return the reproducible go/no-go decision snapshot for analysis operations."""
    service = get_service()
    return _dump(
        build_live_decision(
            service.repository,
            service.settings,
            include_past=include_past,
            full_profile_audit=full_profile_audit,
        )
    )


@mcp.tool()
def get_odds_readiness(
    min_bookmakers: int = 2,
    min_profile_matches: int = 1,
    include_past: bool = False,
) -> dict[str, Any]:
    """Audit whether locally stored odds can support active strategy profiles."""
    service = get_service()
    return _dump(
        audit_odds_readiness(
            service.repository,
            service.settings,
            min_bookmakers=min_bookmakers,
            min_profile_matches=min_profile_matches,
            include_past=include_past,
        )
    )


@mcp.tool()
def refresh_live_data(
    run_date: str | None = None,
    fixture_source: str = "auto",
    odds_source: str = "auto",
    league: str | None = None,
    scope: str = "active-profiles",
    max_events: int | None = None,
    include_past: bool = False,
    dry_run: bool = False,
    allow_odds_fallback: bool = False,
) -> dict[str, Any]:
    """Refresh fixtures and odds through configured data sources; use dry_run to avoid remote quota."""
    service = get_service()
    target_date = run_date or date.today().isoformat()
    return _dump(
        run_live_refresh(
            service,
            date=target_date,
            fixture_source=fixture_source,
            odds_source=odds_source,
            league=league,
            scope=scope,
            max_events=max_events,
            include_past=include_past,
            dry_run=dry_run,
            allow_odds_fallback=allow_odds_fallback,
        )
    )


@mcp.tool()
def run_analysis_cycle(
    run_date: str | None = None,
    leagues: str = "auto",
    fixture_source: str = "auto",
    odds_source: str = "auto",
    result_source: str = "api_football",
    max_events: int | None = None,
    include_results: bool = False,
    include_daily_ops: bool = True,
    include_past: bool = False,
    auto_refresh: bool = True,
    refresh_scope: str = "active-profiles",
    allow_odds_fallback: bool = False,
    refresh_dry_run: bool = False,
    data_apply_mode: str = "off",
) -> dict[str, Any]:
    """Run one analysis-only production cycle and save its heartbeat; broker execution is always disabled."""
    service = get_service()
    day = date.fromisoformat(run_date) if run_date else date.today()
    report = run_production_cycle(
        service,
        run_date=day,
        leagues=[item.strip() for item in leagues.split(",") if item.strip()] or ["auto"],
        fixture_source=fixture_source,
        odds_source=odds_source,
        result_source=result_source,
        max_events=max_events,
        include_results=include_results,
        include_daily_ops=include_daily_ops,
        include_past=include_past,
        auto_refresh=auto_refresh,
        refresh_scope=refresh_scope,
        allow_odds_fallback=allow_odds_fallback,
        refresh_dry_run=refresh_dry_run,
        execution_mode="off",
        data_apply_mode=data_apply_mode,
        broker_discovery_mode="off",
        broker_execution_mode="off",
    )
    return _dump(report)


@mcp.tool()
def refresh_research_data(
    league: str | None = None,
    hours: int = 24,
    provider: str = "auto",
    limit: int = 5,
) -> dict[str, Any]:
    """Search external previews for upcoming matches and store research findings locally; no betting orders."""
    service = get_service()
    now = datetime.now(service.settings.app.tzinfo)
    window_end = now + timedelta(hours=max(1, hours))
    items: list[dict[str, Any]] = []
    for match in service.repository.list_models("matches", Match):
        local_kickoff = match.kickoff_at.astimezone(service.settings.app.tzinfo)
        if not (now <= local_kickoff <= window_end):
            continue
        if not match_league_filter(match, service.settings, league):
            continue
        finding = research_and_store_match(match, service.repository, provider=provider, limit=limit)
        items.append(
            {
                "match_id": match.id,
                "home_team": match.home_team,
                "away_team": match.away_team,
                "kickoff_at": local_kickoff.isoformat(),
                "finding_id": finding.id if finding else None,
                "evidence_count": len(finding.evidence_sources) if finding else 0,
                "selection": finding.payload.get("selection") if finding else None,
            }
        )
    return {"status": "ready", "provider": provider, "hours": hours, "researched_count": len(items), "items": items}


@mcp.tool()
def push_analysis_report(limit: int = 5, hours: int = 24, dry_run: bool = False) -> dict[str, Any]:
    """Format and optionally send upcoming analysis advice to Telegram; does not place or record bets."""
    service = get_service()
    report = build_analysis_advice_report(service, limit=limit, hours=hours)
    message = format_analysis_advice_alert(report)
    if dry_run:
        return {"status": "dry_run", "sent": False, "message": message, "report": _dump(report)}
    token, chat_id = _telegram_credentials()
    result = send_telegram_alert(message, bot_token=token, chat_id=chat_id)
    return {
        "status": "sent" if result.sent else "skipped_or_failed",
        "sent": result.sent,
        "telegram": result.model_dump(mode="json"),
        "message": message,
        "report": _dump(report),
    }


def main() -> None:
    load_dotenv(override=False)
    mcp.run()


if __name__ == "__main__":
    main()
