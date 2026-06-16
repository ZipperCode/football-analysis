from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import typer
from rich.console import Console
from rich.table import Table

from football_analysis.evaluation import evaluate_finished_matches
from football_analysis.models import BetLog, Match, RecommendationStatus
from football_analysis.research import match_league_filter, research_and_store_match
from football_analysis.service import get_service

app = typer.Typer(help="Football value analysis command line bridge.")
picks_app = typer.Typer(help="Pick commands.")
db_app = typer.Typer(help="Database commands.")
ingest_app = typer.Typer(help="Ingestion commands.")
backtest_app = typer.Typer(help="Backtest commands.")
world_cup_app = typer.Typer(help="World Cup controlled live workflow commands.")
qqsd_app = typer.Typer(help="QQSD datasource utility commands.")
app.add_typer(picks_app, name="picks")
app.add_typer(db_app, name="db")
app.add_typer(ingest_app, name="ingest")
app.add_typer(backtest_app, name="backtest")
app.add_typer(world_cup_app, name="world-cup")
app.add_typer(qqsd_app, name="qqsd")
console = Console()


def _print_json(payload: Any) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def _print_json_line(payload: Any) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def _parse_recommendation_statuses(value: str) -> set[RecommendationStatus]:
    statuses: set[RecommendationStatus] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        statuses.add(RecommendationStatus(item))
    return statuses or {RecommendationStatus.recommended}


def _league_codes_for_option(settings: Any, league: str | None) -> set[str] | None:
    if not league:
        return None
    from football_analysis.live_refresh import _canonical_league_code

    codes = {
        _canonical_league_code(settings, item.strip()) or item.strip()
        for item in league.split(",")
        if item.strip()
    }
    return codes or None


def _telegram_credentials() -> tuple[str | None, str | None]:
    from football_analysis.settings import load_settings

    settings = load_settings()
    return (
        os.getenv(settings.telegram.bot_token_env),
        os.getenv(settings.telegram.chat_id_env),
    )


def _notify_telegram_if_requested(message: str, enabled: bool) -> None:
    if not enabled:
        return
    from football_analysis.production import send_telegram_alert

    token, chat_id = _telegram_credentials()
    result = send_telegram_alert(message, bot_token=token, chat_id=chat_id)
    print(json.dumps({"telegram": result.model_dump(mode="json")}, ensure_ascii=False), flush=True)


ProductionApiGetter = Callable[[str, dict[str, Any]], dict[str, Any]]


def _production_api_base_url(api_url: str | None) -> str:
    return (
        api_url
        or os.getenv("FOOTBALL_PRODUCTION_API_URL")
        or os.getenv("PRODUCTION_API_URL")
        or "http://127.0.0.1:18000"
    ).rstrip("/")


def _production_api_get_json(
    base_url: str,
    path: str,
    params: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    import httpx

    headers: dict[str, str] = {}
    admin_token = os.getenv("FOOTBALL_ADMIN_TOKEN", "").strip()
    if admin_token:
        headers["X-Football-Admin-Token"] = admin_token
    clean_params = {key: value for key, value in params.items() if value is not None}
    try:
        response = httpx.get(
            f"{base_url}{path}",
            params=clean_params,
            headers=headers,
            timeout=timeout_seconds,
        )
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "status_code": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    payload: Any
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_text": response.text[:1000]}
    return {
        "ok": 200 <= response.status_code < 300,
        "status_code": response.status_code,
        "payload": payload,
    }


def _payload_status(result: dict[str, Any], key: str = "status") -> str | None:
    payload = result.get("payload")
    if isinstance(payload, dict):
        value = payload.get(key)
        return str(value) if value is not None else None
    return None


def _payload_bool(result: dict[str, Any], key: str) -> bool | None:
    payload = result.get("payload")
    if isinstance(payload, dict):
        value = payload.get(key)
        return value if isinstance(value, bool) else None
    return None


def _payload_list(result: dict[str, Any], key: str) -> list[str]:
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return []
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _build_production_ops_check_report(
    *,
    api_url: str | None = None,
    target: str = "worker",
    broker: str = "betfair_exchange",
    platform: str = "real",
    include_past: bool = False,
    require_execution_queue: bool = False,
    include_doctor: bool = False,
    include_details: bool = False,
    timeout_seconds: float = 20.0,
    get_json: ProductionApiGetter | None = None,
) -> dict[str, Any]:
    base_url = _production_api_base_url(api_url)

    def fetch(
        path: str,
        params: dict[str, Any] | None = None,
        request_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if get_json is not None:
            return get_json(path, params or {})
        return _production_api_get_json(
            base_url,
            path,
            params or {},
            request_timeout_seconds or timeout_seconds,
        )

    healthz = fetch("/healthz")
    production_status = fetch("/production/status", {"include_past": include_past})
    production_health = fetch("/production/health", {"include_past": include_past})
    runtime_security = fetch("/production/runtime-security", {"target": target})
    deploy_check = fetch(
        "/production/deploy-check",
        {
            "target": target,
            "broker": broker,
            "include_past": include_past,
            "platform": platform,
            "require_execution_queue": require_execution_queue,
        },
    )
    deployment_doctor = (
        fetch(
            "/production/deployment-doctor",
            {
                "target": target,
                "broker": broker,
                "include_past": include_past,
                "platform": platform,
                "require_execution_queue": require_execution_queue,
                "execute_candidate_ready": False,
            },
            request_timeout_seconds=max(timeout_seconds, 60.0),
        )
        if include_doctor
        else None
    )

    checks = {
        "healthz": healthz,
        "production_status": production_status,
        "production_health": production_health,
        "runtime_security": runtime_security,
        "deploy_check": deploy_check,
    }
    if deployment_doctor is not None:
        checks["deployment_doctor"] = deployment_doctor

    unreachable = [
        name
        for name, result in checks.items()
        if not result.get("ok")
    ]
    issues: list[str] = []
    warnings: list[str] = []
    for name in unreachable:
        issues.append(f"api_endpoint_unreachable:{name}")
    health_status = _payload_status(production_health)
    health_problems = [f"production_health:{item}" for item in _payload_list(production_health, "issues")]
    if health_status == "unhealthy":
        issues.extend(health_problems)
    else:
        warnings.extend(health_problems)
    warnings.extend(f"production_health:{item}" for item in _payload_list(production_health, "warnings"))
    runtime_problems = [f"runtime_security:{item}" for item in _payload_list(runtime_security, "issues")]
    if _payload_status(runtime_security) == "blocked":
        issues.extend(runtime_problems)
    else:
        warnings.extend(runtime_problems)
    warnings.extend(f"runtime_security:{item}" for item in _payload_list(runtime_security, "warnings"))
    deploy_problems = [f"deploy_check:{item}" for item in _payload_list(deploy_check, "issues")]
    if _payload_status(deploy_check) == "blocked":
        issues.extend(deploy_problems)
    else:
        warnings.extend(deploy_problems)
    warnings.extend(f"deploy_check:{item}" for item in _payload_list(deploy_check, "warnings"))
    if deployment_doctor is not None:
        issues.extend(f"deployment_doctor:{item}" for item in _payload_list(deployment_doctor, "issues"))
        warnings.extend(f"deployment_doctor:{item}" for item in _payload_list(deployment_doctor, "warnings"))

    runtime_ready = _payload_status(runtime_security) in {"ready", "ready_with_warnings"}
    deploy_ready = _payload_status(deploy_check) in {"ready", "ready_with_warnings"}
    doctor_ready = (
        True
        if deployment_doctor is None
        else _payload_bool(deployment_doctor, "ready_for_target") is True
    )
    ready_for_target = not unreachable and runtime_ready and deploy_ready and doctor_ready
    status = "unreachable" if unreachable else "blocked"
    if ready_for_target:
        status = "ready_with_warnings" if warnings else "ready"

    summary = {
        "healthz_status_code": healthz.get("status_code"),
        "production_overall_status": _payload_status(production_status, "overall_status"),
        "production_ready_to_bet": _payload_bool(production_status, "ready_to_bet"),
        "production_health_status": _payload_status(production_health),
        "runtime_security_status": _payload_status(runtime_security),
        "deploy_check_status": _payload_status(deploy_check),
        "deployment_doctor_status": _payload_status(deployment_doctor) if deployment_doctor else None,
        "deployment_doctor_ready_for_target": (
            _payload_bool(deployment_doctor, "ready_for_target") if deployment_doctor else None
        ),
    }
    status_payload = production_status.get("payload")
    if isinstance(status_payload, dict):
        summary["counts"] = status_payload.get("counts", {})
        recent_jobs = status_payload.get("recent_jobs", [])
        if isinstance(recent_jobs, list) and recent_jobs:
            latest_job = recent_jobs[0]
            if isinstance(latest_job, dict):
                summary["latest_job"] = {
                    "id": latest_job.get("id"),
                    "job_type": latest_job.get("job_type"),
                    "status": latest_job.get("status"),
                    "finished_at": latest_job.get("finished_at"),
                }

    report: dict[str, Any] = {
        "status": status,
        "api_url": base_url,
        "target": target,
        "broker_id": broker,
        "platform": platform,
        "include_past": include_past,
        "require_execution_queue": require_execution_queue,
        "include_doctor": include_doctor,
        "ready_for_target": ready_for_target,
        "api_reachable": not unreachable,
        "issues": issues,
        "warnings": warnings,
        "summary": summary,
    }
    if include_details:
        report["checks"] = checks
    return report


@picks_app.command("today")
def picks_today(as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools.")) -> None:
    result = get_service().picks_today()
    if as_json:
        _print_json(result)
        return

    table = Table(title=result.message)
    table.add_column("Match")
    table.add_column("Market")
    table.add_column("Selection")
    table.add_column("Value")
    table.add_column("Risk")
    table.add_column("Stake")
    analyses = {analysis.match.id: analysis for analysis in result.analyses}
    for pick in result.picks:
        match = analyses[pick.match_id].match
        table.add_row(
            f"{match.home_team} vs {match.away_team}",
            pick.market_type.value if pick.market_type else "-",
            pick.selection or "-",
            f"{pick.value_score:.1f}",
            f"{pick.risk_score:.1f}",
            f"{pick.stake_units:.1f}u",
        )
    console.print(table)


@app.command("analyze")
def analyze(match_id: str, as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools.")) -> None:
    analysis = get_service().analyze_match(match_id)
    if as_json:
        _print_json(analysis)
        return
    console.print(f"{analysis.match.home_team} vs {analysis.match.away_team}")
    console.print(analysis.recommendation.reason)
    console.print(analysis.recommendation.risk_notice)


@app.command("research-upcoming")
def research_upcoming(
    league: str | None = typer.Option(None, "--league", help="Configured league code/name filter, e.g. WORLD_CUP."),
    hours: int = typer.Option(24, "--hours", help="Lookahead window in hours."),
    provider: str = typer.Option("auto", "--provider", help="auto/exa/firecrawl."),
    limit: int = typer.Option(5, "--limit", help="Search results per match."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools."),
) -> None:
    service = get_service()
    now = datetime.now(service.settings.app.tzinfo)
    window_end = now + timedelta(hours=max(1, hours))
    researched: list[dict[str, Any]] = []
    for match in service.repository.list_models("matches", Match):
        local_kickoff = match.kickoff_at.astimezone(service.settings.app.tzinfo)
        if not (now <= local_kickoff <= window_end):
            continue
        if not match_league_filter(match, service.settings, league):
            continue
        finding = research_and_store_match(match, service.repository, provider=provider, limit=limit)
        researched.append(
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
    payload = {"provider": provider, "hours": hours, "researched_count": len(researched), "items": researched}
    if as_json:
        _print_json(payload)
        return
    console.print(f"researched={len(researched)} provider={provider} hours={hours}")


@app.command("performance")
def performance(
    by_league: bool = typer.Option(False, "--by-league", help="Group performance by configured league and tier."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools."),
) -> None:
    service = get_service()
    summary = service.performance_by_league() if by_league else service.performance()
    if as_json:
        _print_json(summary)
        return
    console.print(summary.model_dump())


@app.command("live-audit")
def live_audit(
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in the audit."),
    league: str | None = typer.Option(None, "--league", help="Limit audit to a configured league code."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.live_audit import audit_live_trading

    result = audit_live_trading(
        service.repository,
        service.settings,
        include_past=include_past,
        league_codes=_league_codes_for_option(service.settings, league),
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@app.command("live-preflight")
def live_preflight(
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in the audit scope."),
    league: str | None = typer.Option(None, "--league", help="Limit preflight to a configured league code."),
    min_bookmakers: int | None = typer.Option(None, "--min-bookmakers", help="Override live bookmaker minimum."),
    min_profile_matches: int = typer.Option(1, "--min-profile-matches", help="Minimum ready matches per profile."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.live_preflight import run_live_preflight

    league_codes = _league_codes_for_option(service.settings, league)
    result = run_live_preflight(
        service.repository,
        service.settings,
        include_past=include_past,
        min_bookmakers=min_bookmakers,
        min_profile_matches=min_profile_matches,
        league_codes=league_codes,
        require_strategy_profiles=league is None,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@app.command("live-review")
def live_review(
    include_paper: bool = typer.Option(True, "--include-paper/--real-only", help="Include paper observations in review."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.live_review import run_live_review

    result = run_live_review(service.repository, service.settings, include_paper=include_paper)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@app.command("live-decision")
def live_decision(
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in preflight scope."),
    league: str | None = typer.Option(None, "--league", help="Limit preflight to a configured league code."),
    include_paper: bool = typer.Option(True, "--include-paper/--real-only", help="Include paper observations in review."),
    full_profile_audit: bool = typer.Option(False, "--full-profile-audit", help="Run the full backtest profile audit."),
    seasons: str = typer.Option("2122,2223,2324,2425,2526", "--seasons", help="Profile-audit seasons."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.live_decision import run_live_decision

    league_codes = _league_codes_for_option(service.settings, league)
    result = run_live_decision(
        service.repository,
        service.settings,
        include_past=include_past,
        include_paper=include_paper,
        seasons=_split_csv(seasons),
        full_profile_audit=full_profile_audit,
        league_codes=league_codes,
        require_strategy_profiles=league is None,
    )
    if as_json:
        _print_json(result)
        return
    _print_live_decision_summary(result)


@app.command("live-refresh")
def live_refresh(
    date: str = typer.Option(..., "--date", help="Refresh date in YYYY-MM-DD."),
    fixture_source: str = typer.Option("auto", "--fixture-source", help="Fixture ingestion source."),
    odds_source: str = typer.Option("auto", "--odds-source", help="Odds ingestion source."),
    league: str | None = typer.Option(None, "--league", help="Configured league code; defaults to active profiles."),
    scope: str = typer.Option(
        "active-profiles",
        "--scope",
        help="Refresh scope: active-profiles or live-leagues. Ignored when --league is set.",
    ),
    max_events: int | None = typer.Option(None, "--max-events", help="Maximum odds events per league."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in post-refresh preflight."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan the refresh without spending remote quota."),
    allow_odds_fallback: bool = typer.Option(
        False,
        "--allow-odds-fallback",
        help="Allow auto odds refresh to spend fallback source quota after the preferred source is empty or fails.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.live_refresh import run_live_refresh

    result = run_live_refresh(
        service,
        date=date,
        fixture_source=fixture_source,
        odds_source=odds_source,
        league=league,
        scope=scope,
        max_events=max_events,
        include_past=include_past,
        dry_run=dry_run,
        allow_odds_fallback=allow_odds_fallback,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


def _print_live_decision_summary(result: Any) -> None:
    console.print(
        f"Live decision: {result.status} "
        f"ready={str(result.ready_to_bet).lower()} action={result.action}"
    )
    console.print(
        "Components: "
        + " ".join(f"{name}={status}" for name, status in result.components.items())
    )
    live_audit = result.preflight.live_audit
    console.print(
        "Candidates: "
        f"matches={live_audit.total_matches} "
        f"recommended={live_audit.recommended_count} "
        f"paper={live_audit.paper_candidate_count} "
        f"analysis_only={live_audit.analysis_only_count}"
    )
    console.print(
        "Profiles/Odds: "
        f"ready_profiles={result.odds_readiness.ready_profiles}/{result.odds_readiness.active_profiles} "
        f"scoped_odds={result.odds_readiness.scoped_odds_snapshots}"
    )

    refresh_requirements = list(getattr(result.odds_readiness, "refresh_requirements", []) or [])
    if refresh_requirements:
        console.print("Odds refresh requirements:")
        for item in refresh_requirements[:2]:
            console.print(_refresh_requirement_line(item))
        if len(refresh_requirements) > 2:
            console.print(f"- ... {len(refresh_requirements) - 2} more")

    if result.issues:
        console.print("Issues:")
        for issue in result.issues[:4]:
            console.print(f"- {_clip(issue)}")
        if len(result.issues) > 4:
            console.print(f"- ... {len(result.issues) - 4} more")

    closest = _closest_blocked_candidates(live_audit.items)
    if closest:
        console.print("Closest blocked candidates:")
        for item in closest[:2]:
            console.print(_candidate_summary_line(item))

    blockers = sorted(live_audit.gate_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    if blockers:
        console.print("Top live gate blockers:")
        for gate, count in blockers:
            console.print(f"- {count}x {_clip(gate)}")


def _closest_blocked_candidates(items: list[Any], limit: int = 3) -> list[Any]:
    candidates = [
        item
        for item in items
        if getattr(item, "live_gate_passed", False) is False
        and getattr(item, "status", "") in {"paper_candidate", "recommended"}
    ]
    return sorted(
        candidates,
        key=lambda item: (
            getattr(item, "status", "") != "paper_candidate",
            -float(getattr(item, "value_score", 0.0) or 0.0),
            float(getattr(item, "risk_score", 100.0) or 100.0),
            -float(getattr(item, "confidence", 0.0) or 0.0),
        ),
    )[:limit]


def _refresh_requirement_line(item: Any) -> str:
    refresh_league = getattr(item, "refresh_league_code", None) or "-"
    strategy_league = getattr(item, "strategy_league_code", None) or "-"
    market = getattr(item, "market_type", None) or "-"
    selections = ",".join(getattr(item, "selections", []) or ["-"])
    issue = _first_issue(getattr(item, "issues", []) or [])
    return (
        f"- {refresh_league}({strategy_league}) {market} {selections} "
        f"bookmakers>={getattr(item, 'required_bookmakers', '-')} "
        f"ready={getattr(item, 'ready_matches', 0)} need={getattr(item, 'needed_ready_matches', 0)} "
        f"issue={_clip(issue, 24)}"
    )


def _candidate_summary_line(item: Any) -> str:
    market = getattr(item, "market_type", None) or "-"
    selection = getattr(item, "selection", None) or "-"
    home = getattr(item, "home_team", "-")
    away = getattr(item, "away_team", "-")
    gate = _first_issue(getattr(item, "gates_failed", []) or [])
    return (
        f"- {home} vs {away} {market} {selection} "
        f"v={float(getattr(item, 'value_score', 0.0) or 0.0):.1f} "
        f"r={float(getattr(item, 'risk_score', 0.0) or 0.0):.1f} "
        f"c={float(getattr(item, 'confidence', 0.0) or 0.0):.3f} "
        f"gate={_clip(gate, 26)}"
    )


def _first_issue(issues: list[str]) -> str:
    return str(issues[0]) if issues else "-"


def _clip(value: str, max_chars: int = 68) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


@app.command("daily-ops")
def daily_ops(
    date: str = typer.Option(..., "--date", help="Operational date in YYYY-MM-DD."),
    ingest_results: bool = typer.Option(False, "--ingest-results", help="Refresh finished results before settlement."),
    source: str = typer.Option("api_football", "--source", help="Result ingestion source."),
    league: str | None = typer.Option(None, "--league", help="Configured league code for result ingestion."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in preflight scope."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.daily_ops import run_daily_ops

    result = run_daily_ops(
        service,
        date=date,
        ingest_results=ingest_results,
        source=source,
        league=league,
        include_past=include_past,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@world_cup_app.command("refresh-data")
def world_cup_refresh_data(
    match_date: str = typer.Option(..., "--date", help="Local match date in YYYY-MM-DD."),
    research: bool = typer.Option(True, "--research/--skip-research", help="Also run World Cup research after QQSD refresh."),
    provider: str = typer.Option("auto", "--provider", help="Research provider: auto, exa, firecrawl, or tavily."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.world_cup import refresh_world_cup_data

    result = refresh_world_cup_data(
        service,
        match_date=match_date,
        include_research=research,
        research_provider=provider,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@world_cup_app.command("research")
def world_cup_research(
    hours: int = typer.Option(48, "--hours", help="Lookahead window in hours."),
    provider: str = typer.Option("auto", "--provider", help="Research provider: auto, exa, firecrawl, or tavily."),
    limit: int = typer.Option(5, "--limit", help="Search results per match."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.world_cup import research_world_cup

    result = research_world_cup(service, hours=hours, provider=provider, limit=limit)
    if as_json:
        _print_json(result)
        return
    console.print(result)


@world_cup_app.command("backtest")
def world_cup_backtest(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.world_cup import backtest_world_cup_high_winrate

    result = backtest_world_cup_high_winrate(service)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@world_cup_app.command("recommend")
def world_cup_recommend(
    match_date: str = typer.Option(..., "--date", help="Local match date in YYYY-MM-DD."),
    stage: str = typer.Option("advisory", "--stage", help="Recommendation stage: advisory or final."),
    ignore_final_window: bool = typer.Option(
        False,
        "--ignore-final-window",
        help="Do not block World Cup final recommendations solely because they are outside T-90m to T-60m.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.world_cup import recommend_world_cup

    result = recommend_world_cup(
        service,
        match_date=match_date,
        stage=stage,
        ignore_final_window=ignore_final_window,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-cycle")
def production_cycle(
    run_date: str = typer.Option(..., "--date", help="Production cycle date in YYYY-MM-DD."),
    leagues: str = typer.Option("auto", "--leagues", help="Comma-separated configured league codes, or auto."),
    fixture_source: str = typer.Option("auto", "--fixture-source", help="Fixture ingestion source."),
    odds_source: str = typer.Option("auto", "--odds-source", help="Odds ingestion source."),
    result_source: str = typer.Option("api_football", "--result-source", help="Result ingestion source."),
    max_events: int | None = typer.Option(None, "--max-events", help="Maximum odds events per league."),
    include_results: bool = typer.Option(True, "--include-results/--skip-results", help="Refresh results."),
    include_daily_ops: bool = typer.Option(True, "--include-daily-ops/--skip-daily-ops", help="Run daily ops."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in decision scope."),
    auto_refresh: bool = typer.Option(True, "--auto-refresh/--fixed-leagues", help="Use live-refresh planning instead of fixed source loops."),
    refresh_scope: str = typer.Option("active-profiles", "--refresh-scope", help="Auto refresh scope: active-profiles or live-leagues."),
    allow_odds_fallback: bool = typer.Option(False, "--allow-odds-fallback", help="Allow auto odds source fallback."),
    expand_live_leagues_on_empty: bool = typer.Option(True, "--expand-live-leagues-on-empty/--no-expand-live-leagues-on-empty", help="Refresh live leagues when active-profile refresh is empty."),
    refresh_dry_run: bool = typer.Option(False, "--refresh-dry-run", help="Plan refresh/results operations without provider calls or refresh writes."),
    execution_mode: str = typer.Option("dry-run", "--execution-mode", help="Execution stage mode: off, dry-run, or record-only."),
    execution_platform: str = typer.Option("real", "--execution-platform", help="Record-bet platform for execution records."),
    execution_max_items: int | None = typer.Option(None, "--execution-max-items", help="Maximum execution queue items to consume."),
    execution_fills_json: Path | None = typer.Option(None, "--execution-fills-json", help="Execution fills JSON file for record-only mode."),
    require_execution_fills: bool = typer.Option(False, "--require-execution-fills", help="Reject record-only writes when a fill is missing."),
    data_apply_mode: str = typer.Option("off", "--data-apply-mode", help="Data-apply stage: off, dry-run, safe, or remote."),
    data_apply_include_backtests: bool = typer.Option(True, "--data-apply-include-backtests/--data-apply-skip-backtests", help="Include local backtest/profile-audit commands in data-apply."),
    data_apply_include_blocked_prerequisites: bool = typer.Option(False, "--data-apply-include-blocked-prerequisites", help="Allow data-apply commands whose task is blocked by a prerequisite."),
    data_apply_max_commands: int | None = typer.Option(None, "--data-apply-max-commands", help="Maximum data-apply commands to run or preview."),
    data_apply_timeout_seconds: int = typer.Option(1800, "--data-apply-timeout-seconds", help="Timeout for each data-apply command."),
    data_apply_historical_odds_start_time: str | None = typer.Option(None, "--data-apply-historical-odds-start-time", help="ISO start timestamp for historical odds batch data-apply."),
    data_apply_historical_odds_end_time: str | None = typer.Option(None, "--data-apply-historical-odds-end-time", help="ISO end timestamp for historical odds batch data-apply."),
    data_apply_historical_odds_interval_minutes: int = typer.Option(10, "--data-apply-historical-odds-interval-minutes", help="Minutes between historical odds snapshots in data-apply."),
    data_apply_historical_odds_max_snapshots: int = typer.Option(24, "--data-apply-historical-odds-max-snapshots", help="Maximum historical odds snapshots per league in data-apply."),
    data_apply_historical_odds_max_events: int | None = typer.Option(None, "--data-apply-historical-odds-max-events", help="Maximum events to store per historical odds snapshot in data-apply."),
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    broker_discovery_mode: str = typer.Option("off", "--broker-discovery-mode", help="Broker discovery stage: off, dry-run, remote, or apply."),
    broker_discovery_max_items: int | None = typer.Option(None, "--broker-discovery-max-items", help="Maximum queue items to inspect for broker discovery."),
    broker_discovery_max_results: int = typer.Option(20, "--broker-discovery-max-results", help="Maximum Betfair catalogue rows per queue item."),
    broker_discovery_match_window_hours: int = typer.Option(36, "--broker-discovery-match-window-hours", help="Kickoff search window on each side."),
    broker_execution_mode: str = typer.Option("off", "--broker-execution-mode", help="Broker execution stage: off, dry-run, or live."),
    broker_execution_max_items: int | None = typer.Option(None, "--broker-execution-max-items", help="Maximum broker-ready queue items to consume."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
    alert_text: bool = typer.Option(False, "--alert-text", help="Emit compact production alert text."),
    notify_telegram: bool = typer.Option(False, "--notify-telegram", help="Send compact alert via Telegram when credentials are configured."),
) -> None:
    service = get_service()
    from football_analysis.production import format_production_alert, run_production_cycle

    result = run_production_cycle(
        service,
        run_date=date.fromisoformat(run_date),
        leagues=_split_csv(leagues),
        fixture_source=_production_fixture_source(fixture_source, auto_refresh),
        odds_source=_production_odds_source(odds_source, auto_refresh),
        result_source=result_source,
        max_events=max_events,
        include_results=include_results,
        include_daily_ops=include_daily_ops,
        include_past=include_past,
        auto_refresh=auto_refresh,
        refresh_scope=refresh_scope,
        allow_odds_fallback=allow_odds_fallback,
        expand_live_leagues_on_empty=expand_live_leagues_on_empty,
        refresh_dry_run=refresh_dry_run,
        execution_mode=execution_mode,
        execution_platform=execution_platform,
        execution_max_items=execution_max_items,
        execution_fills=_load_execution_fills(execution_fills_json),
        require_execution_fills=require_execution_fills,
        data_apply_mode=data_apply_mode,
        data_apply_include_backtests=data_apply_include_backtests,
        data_apply_include_blocked_prerequisites=data_apply_include_blocked_prerequisites,
        data_apply_max_commands=data_apply_max_commands,
        data_apply_timeout_seconds=data_apply_timeout_seconds,
        data_apply_historical_odds_start_time=data_apply_historical_odds_start_time,
        data_apply_historical_odds_end_time=data_apply_historical_odds_end_time,
        data_apply_historical_odds_interval_minutes=data_apply_historical_odds_interval_minutes,
        data_apply_historical_odds_max_snapshots=data_apply_historical_odds_max_snapshots,
        data_apply_historical_odds_max_events=data_apply_historical_odds_max_events,
        broker_id=broker,
        broker_discovery_mode=broker_discovery_mode,
        broker_discovery_max_items=broker_discovery_max_items,
        broker_discovery_max_results=broker_discovery_max_results,
        broker_discovery_match_window_hours=broker_discovery_match_window_hours,
        broker_execution_mode=broker_execution_mode,
        broker_execution_max_items=broker_execution_max_items,
    )
    message = format_production_alert(result)
    if as_json:
        _print_json(result)
    elif alert_text:
        console.print(message)
    else:
        console.print(result.model_dump())
    _notify_telegram_if_requested(message, notify_telegram)


@app.command("production-worker")
def production_worker(
    leagues: str = typer.Option("auto", "--leagues", help="Comma-separated configured league codes, or auto."),
    fixture_source: str = typer.Option("auto", "--fixture-source", help="Fixture ingestion source."),
    odds_source: str = typer.Option("auto", "--odds-source", help="Odds ingestion source."),
    result_source: str = typer.Option("api_football", "--result-source", help="Result ingestion source."),
    max_events: int | None = typer.Option(None, "--max-events", help="Maximum odds events per league."),
    interval_seconds: int = typer.Option(900, "--interval-seconds", help="Seconds between cycles."),
    once: bool = typer.Option(False, "--once", help="Run one cycle and exit."),
    include_results: bool = typer.Option(True, "--include-results/--skip-results", help="Refresh results."),
    include_daily_ops: bool = typer.Option(True, "--include-daily-ops/--skip-daily-ops", help="Run daily ops."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in decision scope."),
    auto_refresh: bool = typer.Option(True, "--auto-refresh/--fixed-leagues", help="Use live-refresh planning instead of fixed source loops."),
    refresh_scope: str = typer.Option("active-profiles", "--refresh-scope", help="Auto refresh scope: active-profiles or live-leagues."),
    allow_odds_fallback: bool = typer.Option(False, "--allow-odds-fallback", help="Allow auto odds source fallback."),
    expand_live_leagues_on_empty: bool = typer.Option(True, "--expand-live-leagues-on-empty/--no-expand-live-leagues-on-empty", help="Refresh live leagues when active-profile refresh is empty."),
    refresh_dry_run: bool = typer.Option(False, "--refresh-dry-run", help="Plan refresh/results operations without provider calls or refresh writes."),
    execution_mode: str = typer.Option("dry-run", "--execution-mode", help="Execution stage mode: off, dry-run, or record-only."),
    execution_platform: str = typer.Option("real", "--execution-platform", help="Record-bet platform for execution records."),
    execution_max_items: int | None = typer.Option(None, "--execution-max-items", help="Maximum execution queue items to consume."),
    execution_fills_json: Path | None = typer.Option(None, "--execution-fills-json", help="Execution fills JSON file for record-only mode."),
    require_execution_fills: bool = typer.Option(False, "--require-execution-fills", help="Reject record-only writes when a fill is missing."),
    data_apply_mode: str = typer.Option("off", "--data-apply-mode", help="Data-apply stage: off, dry-run, safe, or remote."),
    data_apply_include_backtests: bool = typer.Option(True, "--data-apply-include-backtests/--data-apply-skip-backtests", help="Include local backtest/profile-audit commands in data-apply."),
    data_apply_include_blocked_prerequisites: bool = typer.Option(False, "--data-apply-include-blocked-prerequisites", help="Allow data-apply commands whose task is blocked by a prerequisite."),
    data_apply_max_commands: int | None = typer.Option(None, "--data-apply-max-commands", help="Maximum data-apply commands to run or preview."),
    data_apply_timeout_seconds: int = typer.Option(1800, "--data-apply-timeout-seconds", help="Timeout for each data-apply command."),
    data_apply_historical_odds_start_time: str | None = typer.Option(None, "--data-apply-historical-odds-start-time", help="ISO start timestamp for historical odds batch data-apply."),
    data_apply_historical_odds_end_time: str | None = typer.Option(None, "--data-apply-historical-odds-end-time", help="ISO end timestamp for historical odds batch data-apply."),
    data_apply_historical_odds_interval_minutes: int = typer.Option(10, "--data-apply-historical-odds-interval-minutes", help="Minutes between historical odds snapshots in data-apply."),
    data_apply_historical_odds_max_snapshots: int = typer.Option(24, "--data-apply-historical-odds-max-snapshots", help="Maximum historical odds snapshots per league in data-apply."),
    data_apply_historical_odds_max_events: int | None = typer.Option(None, "--data-apply-historical-odds-max-events", help="Maximum events to store per historical odds snapshot in data-apply."),
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    broker_discovery_mode: str = typer.Option("off", "--broker-discovery-mode", help="Broker discovery stage: off, dry-run, remote, or apply."),
    broker_discovery_max_items: int | None = typer.Option(None, "--broker-discovery-max-items", help="Maximum queue items to inspect for broker discovery."),
    broker_discovery_max_results: int = typer.Option(20, "--broker-discovery-max-results", help="Maximum Betfair catalogue rows per queue item."),
    broker_discovery_match_window_hours: int = typer.Option(36, "--broker-discovery-match-window-hours", help="Kickoff search window on each side."),
    broker_execution_mode: str = typer.Option("off", "--broker-execution-mode", help="Broker execution stage: off, dry-run, or live."),
    broker_execution_max_items: int | None = typer.Option(None, "--broker-execution-max-items", help="Maximum broker-ready queue items to consume."),
    as_json: bool = typer.Option(False, "--json", help="Emit full JSON for tools."),
    compact_json: bool = typer.Option(False, "--compact-json", help="Emit compact single-line JSON for production logs."),
    alert_text: bool = typer.Option(False, "--alert-text", help="Emit compact production alert text."),
    notify_telegram: bool = typer.Option(False, "--notify-telegram", help="Send compact alert via Telegram when credentials are configured."),
) -> None:
    from football_analysis.production import build_production_cycle_log, format_production_alert, run_production_worker

    def emit_report(report: Any) -> None:
        message = format_production_alert(report)
        if as_json:
            _print_json(report)
        elif compact_json:
            _print_json_line(build_production_cycle_log(report))
        elif alert_text:
            console.print(message)
        else:
            console.print(report.model_dump())
        _notify_telegram_if_requested(message, notify_telegram)

    reports = run_production_worker(
        service_factory=get_service,
        leagues=_split_csv(leagues),
        fixture_source=_production_fixture_source(fixture_source, auto_refresh),
        odds_source=_production_odds_source(odds_source, auto_refresh),
        result_source=result_source,
        max_events=max_events,
        interval_seconds=interval_seconds,
        once=once,
        include_results=include_results,
        include_daily_ops=include_daily_ops,
        include_past=include_past,
        auto_refresh=auto_refresh,
        refresh_scope=refresh_scope,
        allow_odds_fallback=allow_odds_fallback,
        expand_live_leagues_on_empty=expand_live_leagues_on_empty,
        refresh_dry_run=refresh_dry_run,
        on_report=None if once else emit_report,
        execution_mode=execution_mode,
        execution_platform=execution_platform,
        execution_max_items=execution_max_items,
        execution_fills=_load_execution_fills(execution_fills_json),
        require_execution_fills=require_execution_fills,
        data_apply_mode=data_apply_mode,
        data_apply_include_backtests=data_apply_include_backtests,
        data_apply_include_blocked_prerequisites=data_apply_include_blocked_prerequisites,
        data_apply_max_commands=data_apply_max_commands,
        data_apply_timeout_seconds=data_apply_timeout_seconds,
        data_apply_historical_odds_start_time=data_apply_historical_odds_start_time,
        data_apply_historical_odds_end_time=data_apply_historical_odds_end_time,
        data_apply_historical_odds_interval_minutes=data_apply_historical_odds_interval_minutes,
        data_apply_historical_odds_max_snapshots=data_apply_historical_odds_max_snapshots,
        data_apply_historical_odds_max_events=data_apply_historical_odds_max_events,
        broker_id=broker,
        broker_discovery_mode=broker_discovery_mode,
        broker_discovery_max_items=broker_discovery_max_items,
        broker_discovery_max_results=broker_discovery_max_results,
        broker_discovery_match_window_hours=broker_discovery_match_window_hours,
        broker_execution_mode=broker_execution_mode,
        broker_execution_max_items=broker_execution_max_items,
    )
    if not reports:
        return
    report = reports[-1]
    message = format_production_alert(report)
    if as_json:
        _print_json(report)
    elif alert_text:
        console.print(message)
    else:
        console.print(report.model_dump())
    _notify_telegram_if_requested(message, notify_telegram)


def _env_value(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    return value if value else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_value(name)
    if raw is None:
        return default
    value = raw.lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise typer.BadParameter(f"{name} must be a boolean value")


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = _env_value(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"{name} must be an integer") from exc


def _env_path(name: str) -> Path | None:
    value = _env_value(name)
    return Path(value) if value is not None else None



def _production_worker_startup_deploy_check(
    target: str,
    broker_id: str,
    platform: str,
) -> dict[str, Any]:
    from football_analysis.production import build_production_deploy_check

    return build_production_deploy_check(
        get_service(),
        target=target,
        broker_id=broker_id,
        platform=platform,
    )

@app.command("production-worker-env")
def production_worker_env(
    once: bool = typer.Option(False, "--once", help="Run one cycle and exit; also accepts WORKER_ONCE=1."),
    as_json: bool = typer.Option(False, "--json", help="Emit full JSON for tools; default is controlled by WORKER_JSON."),
    compact_json: bool = typer.Option(False, "--compact-json", help="Emit compact single-line JSON; default is controlled by WORKER_COMPACT_JSON."),
    alert_text: bool = typer.Option(False, "--alert-text", help="Emit compact production alert text; also accepts WORKER_ALERT_TEXT=1."),
    notify_telegram: bool = typer.Option(False, "--notify-telegram", help="Send compact alert via Telegram; default is controlled by WORKER_NOTIFY_TELEGRAM."),
    require_deploy_ready: bool = typer.Option(False, "--require-deploy-ready", help="Exit before the worker loop when the deploy target is blocked."),
) -> None:
    from football_analysis.production import build_production_cycle_log, format_production_alert, run_production_worker

    worker_once = once or _env_bool("WORKER_ONCE", False)
    if as_json:
        worker_json = True
        worker_compact_json = False
        worker_alert_text = False
    elif compact_json:
        worker_json = False
        worker_compact_json = True
        worker_alert_text = False
    elif alert_text:
        worker_json = False
        worker_compact_json = False
        worker_alert_text = True
    else:
        worker_json = _env_bool("WORKER_JSON", False)
        worker_compact_json = _env_bool("WORKER_COMPACT_JSON", not worker_json)
        worker_alert_text = _env_bool("WORKER_ALERT_TEXT", False)
    worker_notify_telegram = notify_telegram or _env_bool("WORKER_NOTIFY_TELEGRAM", True)
    worker_require_deploy_ready = require_deploy_ready or _env_bool("WORKER_REQUIRE_DEPLOY_READY", False)
    auto_refresh = _env_bool("WORKER_AUTO_REFRESH", True)
    broker_id = _env_value("WORKER_BROKER", "betfair_exchange") or "betfair_exchange"
    execution_platform = _env_value("WORKER_EXECUTION_PLATFORM", "real") or "real"
    deploy_target = _env_value("PRODUCTION_DEPLOY_TARGET", "worker") or "worker"

    if worker_require_deploy_ready:
        deploy_check = _production_worker_startup_deploy_check(
            target=deploy_target,
            broker_id=broker_id,
            platform=execution_platform,
        )
        if deploy_check.get("status") == "blocked":
            _print_json({"status": "blocked", "production_deploy_check": deploy_check})
            raise typer.Exit(code=2)

    def emit_report(report: Any) -> None:
        message = format_production_alert(report)
        if worker_json:
            _print_json(report)
        elif worker_compact_json:
            _print_json_line(build_production_cycle_log(report))
        elif worker_alert_text:
            console.print(message)
        else:
            console.print(report.model_dump())
        _notify_telegram_if_requested(message, worker_notify_telegram)

    reports = run_production_worker(
        service_factory=get_service,
        leagues=_split_csv(_env_value("WORKER_LEAGUES", "auto") or "auto"),
        fixture_source=_production_fixture_source(_env_value("WORKER_FIXTURE_SOURCE", "auto") or "auto", auto_refresh),
        odds_source=_production_odds_source(_env_value("WORKER_ODDS_SOURCE", "auto") or "auto", auto_refresh),
        result_source=_env_value("WORKER_RESULT_SOURCE", "api_football") or "api_football",
        max_events=_env_int("WORKER_MAX_EVENTS"),
        interval_seconds=_env_int("WORKER_INTERVAL_SECONDS", 3600) or 3600,
        once=worker_once,
        include_results=_env_bool("WORKER_INCLUDE_RESULTS", False),
        include_daily_ops=_env_bool("WORKER_INCLUDE_DAILY_OPS", True),
        include_past=_env_bool("WORKER_INCLUDE_PAST", False),
        auto_refresh=auto_refresh,
        refresh_scope=_env_value("WORKER_REFRESH_SCOPE", "active-profiles") or "active-profiles",
        allow_odds_fallback=_env_bool("WORKER_ALLOW_ODDS_FALLBACK", False),
        expand_live_leagues_on_empty=_env_bool("WORKER_EXPAND_LIVE_LEAGUES_ON_EMPTY", True),
        refresh_dry_run=_env_bool("WORKER_REFRESH_DRY_RUN", False),
        on_report=None if worker_once else emit_report,
        execution_mode=_env_value("WORKER_EXECUTION_MODE", "dry-run") or "dry-run",
        execution_platform=execution_platform,
        execution_max_items=_env_int("WORKER_EXECUTION_MAX_ITEMS"),
        execution_fills=_load_execution_fills(_env_path("WORKER_EXECUTION_FILLS_JSON")),
        require_execution_fills=_env_bool("WORKER_REQUIRE_EXECUTION_FILLS", False),
        data_apply_mode=_env_value("WORKER_DATA_APPLY_MODE", "safe") or "safe",
        data_apply_include_backtests=_env_bool("WORKER_DATA_APPLY_INCLUDE_BACKTESTS", False),
        data_apply_include_blocked_prerequisites=_env_bool("WORKER_DATA_APPLY_INCLUDE_BLOCKED_PREREQUISITES", False),
        data_apply_max_commands=_env_int("WORKER_DATA_APPLY_MAX_COMMANDS", 3),
        data_apply_timeout_seconds=_env_int("WORKER_DATA_APPLY_TIMEOUT_SECONDS", 1800) or 1800,
        data_apply_historical_odds_start_time=_env_value("WORKER_DATA_APPLY_HISTORICAL_ODDS_START_TIME"),
        data_apply_historical_odds_end_time=_env_value("WORKER_DATA_APPLY_HISTORICAL_ODDS_END_TIME"),
        data_apply_historical_odds_interval_minutes=_env_int("WORKER_DATA_APPLY_HISTORICAL_ODDS_INTERVAL_MINUTES", 10) or 10,
        data_apply_historical_odds_max_snapshots=_env_int("WORKER_DATA_APPLY_HISTORICAL_ODDS_MAX_SNAPSHOTS", 24) or 24,
        data_apply_historical_odds_max_events=_env_int("WORKER_DATA_APPLY_HISTORICAL_ODDS_MAX_EVENTS"),
        broker_id=broker_id,
        broker_discovery_mode=_env_value("WORKER_BROKER_DISCOVERY_MODE", "off") or "off",
        broker_discovery_max_items=_env_int("WORKER_BROKER_DISCOVERY_MAX_ITEMS"),
        broker_discovery_max_results=_env_int("WORKER_BROKER_DISCOVERY_MAX_RESULTS", 20) or 20,
        broker_discovery_match_window_hours=_env_int("WORKER_BROKER_DISCOVERY_MATCH_WINDOW_HOURS", 36) or 36,
        broker_execution_mode=_env_value("WORKER_BROKER_EXECUTION_MODE", "off") or "off",
        broker_execution_max_items=_env_int("WORKER_BROKER_EXECUTION_MAX_ITEMS"),
    )
    if not reports:
        return
    emit_report(reports[-1])


def _production_fixture_source(source: str, auto_refresh: bool) -> str:
    if auto_refresh or source.strip().lower() != "auto":
        return source
    return "api_football"


def _production_odds_source(source: str, auto_refresh: bool) -> str:
    if auto_refresh or source.strip().lower() != "auto":
        return source
    return "odds_api_io"


@app.command("production-preflight")
def production_preflight(
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in production scope."),
    league: str | None = typer.Option(None, "--league", help="Limit production preflight to configured league code(s)."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform used by queue generation."),
    require_broker: bool = typer.Option(False, "--require-broker", help="Fail preflight unless broker execution is ready."),
    require_execution_queue: bool = typer.Option(False, "--require-execution-queue", help="Fail preflight unless at least one executable queue item is open."),
    recent_limit: int = typer.Option(10, "--recent-limit", help="Recent jobs to include in nested status."),
    max_cycle_age_minutes: int = typer.Option(90, "--max-cycle-age-minutes", help="Maximum age for production-cycle heartbeat."),
    max_data_job_age_minutes: int = typer.Option(180, "--max-data-job-age-minutes", help="Maximum age for fixture/odds ingestion jobs."),
    profile_audit: bool = typer.Option(False, "--profile-audit", help="Run full strategy profile audit for profile-promotion gate."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_preflight

    result = build_production_preflight(
        service,
        broker_id=broker,
        include_past=include_past,
        league_codes=_league_codes_for_option(service.settings, league),
        platform=platform,
        require_broker=require_broker,
        require_execution_queue=require_execution_queue,
        recent_limit=recent_limit,
        max_cycle_age_minutes=max_cycle_age_minutes,
        max_data_job_age_minutes=max_data_job_age_minutes,
        profile_promotion_audit=profile_audit,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-status")
def production_status(
    recent_limit: int = typer.Option(10, "--recent-limit", help="Recent ingestion jobs to include."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in decision scope."),
    league: str | None = typer.Option(None, "--league", help="Limit decision and execution queue scope to configured league code(s)."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_status

    result = build_production_status(
        service,
        recent_limit=recent_limit,
        include_past=include_past,
        league_codes=_league_codes_for_option(service.settings, league),
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-health")
def production_health(
    recent_limit: int = typer.Option(10, "--recent-limit", help="Recent jobs to include in nested status."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in decision scope."),
    league: str | None = typer.Option(None, "--league", help="Limit nested production status to configured league code(s)."),
    max_cycle_age_minutes: int = typer.Option(90, "--max-cycle-age-minutes", help="Maximum age for production-cycle heartbeat."),
    max_data_job_age_minutes: int = typer.Option(180, "--max-data-job-age-minutes", help="Maximum age for fixture/odds ingestion jobs."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_health

    result = build_production_health(
        service,
        recent_limit=recent_limit,
        include_past=include_past,
        league_codes=_league_codes_for_option(service.settings, league),
        max_cycle_age_minutes=max_cycle_age_minutes,
        max_data_job_age_minutes=max_data_job_age_minutes,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-readiness")
def production_readiness(
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in readiness scope."),
    league: str | None = typer.Option(None, "--league", help="Limit readiness to a configured league code."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_readiness

    result = build_production_readiness(
        service,
        include_past=include_past,
        league_codes=_league_codes_for_option(service.settings, league),
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-onboarding")
def production_onboarding(
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in onboarding scope."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform used by queue generation."),
    profile_audit: bool = typer.Option(False, "--profile-audit", help="Run full strategy profile audit for profile-promotion onboarding."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_onboarding

    result = build_production_onboarding(
        service,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        profile_promotion_audit=profile_audit,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-onboarding-checklist")
def production_onboarding_checklist(
    target: str = typer.Option("worker", "--target", help="Deployment target: worker, record-only, broker-live, or full."),
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in onboarding scope."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform used by queue generation."),
    profile_audit: bool = typer.Option(False, "--profile-audit", help="Run full strategy profile audit for profile-promotion onboarding."),
    config_path: Path | None = typer.Option(None, "--config-path", help="Config YAML path to use when rendering apply commands."),
    broker_stake_currency_per_unit: float | None = typer.Option(None, "--broker-stake-currency-per-unit", help="Stake currency value for one strategy stake unit when rendering broker config apply commands."),
    markdown: bool = typer.Option(False, "--markdown", help="Render the checklist as Markdown."),
    output: Path | None = typer.Option(None, "--output", help="Write the rendered checklist to a file."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import (
        build_production_onboarding_checklist,
        format_production_onboarding_checklist_markdown,
    )

    result = build_production_onboarding_checklist(
        service,
        target=target,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        profile_promotion_audit=profile_audit,
        config_path=config_path,
        broker_stake_currency_per_unit=broker_stake_currency_per_unit,
    )
    if markdown:
        rendered = format_production_onboarding_checklist_markdown(result)
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return
    if as_json:
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            _print_json(result)
        return
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(result)


@app.command("production-onboarding-apply-plan")
def production_onboarding_apply_plan(
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in onboarding scope."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform used by queue generation."),
    profile_audit: bool = typer.Option(False, "--profile-audit", help="Run full strategy profile audit for profile-promotion onboarding."),
    config_path: Path | None = typer.Option(None, "--config-path", help="Config YAML path to use in generated apply commands."),
    broker_stake_currency_per_unit: float | None = typer.Option(None, "--broker-stake-currency-per-unit", help="Stake currency value for one strategy stake unit when enabling broker config."),
    execute_ready: bool = typer.Option(False, "--execute-ready", help="Execute ready local apply commands; default only plans."),
    timeout_seconds: int = typer.Option(1800, "--timeout-seconds", help="Timeout for each ready apply command."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_onboarding_apply_plan

    result = build_production_onboarding_apply_plan(
        service,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        profile_promotion_audit=profile_audit,
        config_path=config_path,
        broker_stake_currency_per_unit=broker_stake_currency_per_unit,
        execute_ready=execute_ready,
        timeout_seconds=timeout_seconds,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-deploy-check")
def production_deploy_check(
    target: str = typer.Option("worker", "--target", help="Deployment target: worker, record-only, broker-live, or full."),
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in deploy-check scope."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform used by queue generation."),
    require_execution_queue: bool = typer.Option(False, "--require-execution-queue", help="Block unless an executable queue item is open."),
    profile_audit: bool = typer.Option(False, "--profile-audit", help="Run full strategy profile audit for profile-promotion deploy gate."),
    fail_on_blocked: bool = typer.Option(False, "--fail-on-blocked", help="Exit non-zero when deploy-check status is blocked."),
    fail_on_warnings: bool = typer.Option(False, "--fail-on-warnings", help="Exit non-zero when deploy-check has warnings."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_deploy_check

    result = build_production_deploy_check(
        service,
        target=target,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        require_execution_queue=require_execution_queue,
        profile_promotion_audit=profile_audit,
    )
    if as_json:
        _print_json(result)
    else:
        console.print(result)
    if fail_on_blocked and result.get("status") == "blocked":
        raise typer.Exit(code=2)
    if fail_on_warnings and result.get("warnings"):
        raise typer.Exit(code=1)


@app.command("production-runtime-security")
def production_runtime_security(
    target: str = typer.Option("worker", "--target", help="Deployment target: worker, record-only, broker-live, or full."),
    fail_on_blocked: bool = typer.Option(False, "--fail-on-blocked", help="Exit non-zero when runtime security status is blocked."),
    fail_on_warnings: bool = typer.Option(False, "--fail-on-warnings", help="Exit non-zero when runtime security has warnings."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    from football_analysis.production import build_production_runtime_security

    result = build_production_runtime_security(target=target)
    if as_json:
        _print_json(result)
    else:
        console.print(result)
    if fail_on_blocked and result.get("status") == "blocked":
        raise typer.Exit(code=2)
    if fail_on_warnings and result.get("warnings"):
        raise typer.Exit(code=1)


@app.command("production-runtime-secrets")
def production_runtime_secrets(
    target: str = typer.Option("worker", "--target", help="Deployment target: worker, record-only, broker-live, or full."),
    show_secret_values: bool = typer.Option(False, "--show-secret-values", help="Print generated secret values. Use only in a private terminal or CI secret job."),
    admin_token_bytes: int = typer.Option(32, "--admin-token-bytes", help="Random byte count for FOOTBALL_ADMIN_TOKEN."),
    postgres_password_bytes: int = typer.Option(24, "--postgres-password-bytes", help="Random byte count for POSTGRES_PASSWORD."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    from football_analysis.production import build_production_runtime_secret_bootstrap

    result = build_production_runtime_secret_bootstrap(
        target=target,
        show_secret_values=show_secret_values,
        admin_token_bytes=admin_token_bytes,
        postgres_password_bytes=postgres_password_bytes,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-ops-check")
def production_ops_check(
    api_url: str | None = typer.Option(None, "--api-url", help="Production API base URL. Defaults to FOOTBALL_PRODUCTION_API_URL or http://127.0.0.1:18000."),
    target: str = typer.Option("worker", "--target", help="Deployment target: worker, record-only, broker-live, or full."),
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in production API checks."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform used by queue generation."),
    require_execution_queue: bool = typer.Option(False, "--require-execution-queue", help="Block unless an executable queue item is open."),
    include_doctor: bool = typer.Option(False, "--include-doctor", help="Also call the heavier deployment-doctor API endpoint."),
    include_details: bool = typer.Option(False, "--include-details", help="Include raw API check responses in JSON output."),
    timeout_seconds: float = typer.Option(20.0, "--timeout-seconds", help="HTTP timeout per API call."),
    fail_on_blocked: bool = typer.Option(False, "--fail-on-blocked", help="Exit non-zero when the target API stack is not ready."),
    fail_on_warnings: bool = typer.Option(False, "--fail-on-warnings", help="Exit non-zero when the target API stack has warnings."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = _build_production_ops_check_report(
        api_url=api_url,
        target=target,
        broker=broker,
        include_past=include_past,
        platform=platform,
        require_execution_queue=require_execution_queue,
        include_doctor=include_doctor,
        include_details=include_details,
        timeout_seconds=timeout_seconds,
    )
    if as_json:
        _print_json(result)
    else:
        console.print(result)
    if fail_on_blocked and result.get("status") in {"blocked", "unreachable"}:
        raise typer.Exit(code=2)
    if fail_on_warnings and result.get("warnings"):
        raise typer.Exit(code=1)


@app.command("production-candidate-check")
def production_candidate_check(
    source_config: Path | None = typer.Option(None, "--source-config", help="Source config YAML to copy from; defaults to FOOTBALL_CONFIG or config/default.yaml."),
    candidate_config: Path | None = typer.Option(None, "--candidate-config", help="Candidate config YAML to write; defaults to build/production-candidates/*.yaml."),
    target: str = typer.Option("record-only", "--target", help="Deployment target: worker, record-only, broker-live, or full."),
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in deploy-check scope."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform used by queue generation."),
    require_execution_queue: bool = typer.Option(False, "--require-execution-queue", help="Block unless an executable queue item is open."),
    profile_audit: bool = typer.Option(False, "--profile-audit", help="Run full strategy profile audit for profile-promotion deploy gate."),
    broker_stake_currency_per_unit: float | None = typer.Option(None, "--broker-stake-currency-per-unit", help="Stake currency value for one strategy stake unit when enabling broker config."),
    execute_ready: bool = typer.Option(True, "--execute-ready/--plan-only", help="Execute ready local apply commands against the candidate config."),
    refresh_candidate: bool = typer.Option(True, "--refresh-candidate/--reuse-candidate", help="Copy source config to the candidate before checking."),
    max_apply_passes: int = typer.Option(3, "--max-apply-passes", help="Maximum ready-apply convergence passes for the candidate config."),
    timeout_seconds: int = typer.Option(1800, "--timeout-seconds", help="Timeout for each ready apply command."),
    fail_on_blocked: bool = typer.Option(False, "--fail-on-blocked", help="Exit non-zero when candidate status is blocked or failed."),
    fail_on_warnings: bool = typer.Option(False, "--fail-on-warnings", help="Exit non-zero when candidate has warnings."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_candidate_check

    result = build_production_candidate_check(
        service,
        source_config_path=source_config,
        candidate_config_path=candidate_config,
        target=target,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        require_execution_queue=require_execution_queue,
        profile_promotion_audit=profile_audit,
        broker_stake_currency_per_unit=broker_stake_currency_per_unit,
        execute_ready=execute_ready,
        refresh_candidate=refresh_candidate,
        max_apply_passes=max_apply_passes,
        timeout_seconds=timeout_seconds,
    )
    if as_json:
        _print_json(result)
    else:
        console.print(result)
    if fail_on_blocked and result.get("status") in {"blocked", "failed"}:
        raise typer.Exit(code=2)
    if fail_on_warnings and result.get("warnings"):
        raise typer.Exit(code=1)


@app.command("production-deployment-doctor")
def production_deployment_doctor(
    source_config: Path | None = typer.Option(None, "--source-config", help="Source config YAML to copy from; defaults to FOOTBALL_CONFIG or config/default.yaml."),
    candidate_config: Path | None = typer.Option(None, "--candidate-config", help="Candidate config YAML to write; defaults to build/production-candidates/*.yaml."),
    target: str = typer.Option("worker", "--target", help="Deployment target: worker, record-only, broker-live, or full."),
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in deployment diagnostics."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform used by queue generation."),
    require_execution_queue: bool = typer.Option(False, "--require-execution-queue", help="Block unless an executable queue item is open."),
    profile_audit: bool = typer.Option(False, "--profile-audit", help="Run full strategy profile audit for profile-promotion deploy gate."),
    broker_stake_currency_per_unit: float | None = typer.Option(None, "--broker-stake-currency-per-unit", help="Stake currency value for one strategy stake unit when enabling broker config."),
    execute_candidate_ready: bool = typer.Option(False, "--execute-candidate-ready/--plan-only", help="Execute ready local apply commands against the candidate config."),
    refresh_candidate: bool = typer.Option(True, "--refresh-candidate/--reuse-candidate", help="Copy source config to the candidate before checking."),
    max_apply_passes: int = typer.Option(3, "--max-apply-passes", help="Maximum ready-apply convergence passes for the candidate config."),
    timeout_seconds: int = typer.Option(1800, "--timeout-seconds", help="Timeout for each ready apply command."),
    fail_on_blocked: bool = typer.Option(False, "--fail-on-blocked", help="Exit non-zero when doctor status is blocked."),
    fail_on_warnings: bool = typer.Option(False, "--fail-on-warnings", help="Exit non-zero when doctor has warnings."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_deployment_doctor

    result = build_production_deployment_doctor(
        service,
        source_config_path=source_config,
        candidate_config_path=candidate_config,
        target=target,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        require_execution_queue=require_execution_queue,
        profile_promotion_audit=profile_audit,
        broker_stake_currency_per_unit=broker_stake_currency_per_unit,
        execute_candidate_ready=execute_candidate_ready,
        refresh_candidate=refresh_candidate,
        max_apply_passes=max_apply_passes,
        timeout_seconds=timeout_seconds,
    )
    if as_json:
        _print_json(result)
    else:
        console.print(result)
    if fail_on_blocked and result.get("status") == "blocked":
        raise typer.Exit(code=2)
    if fail_on_warnings and result.get("warnings"):
        raise typer.Exit(code=1)


@app.command("production-profile-promote")
def production_profile_promote(
    profile_id: str = typer.Option("", "--profile-id", help="Comma-separated strategy profile ids to review/promote."),
    strategy_code: str = typer.Option("", "--strategy-code", help="Comma-separated strategy league codes to review/promote."),
    max_stake_units: float | None = typer.Option(None, "--max-stake-units", help="Per-pick stake cap to write when the profile has none."),
    config_path: Path | None = typer.Option(None, "--config-path", help="Config YAML path to patch when --apply is used."),
    apply_changes: bool = typer.Option(False, "--apply", help="Write live_enabled/max_stake_units patches to config."),
    seasons: str = typer.Option("2122,2223,2324,2425,2526", "--seasons", help="Profile audit seasons."),
    roi_tolerance: float = typer.Option(0.002, "--roi-tolerance"),
    clv_tolerance: float = typer.Option(0.002, "--clv-tolerance"),
    require_audit: bool = typer.Option(True, "--require-audit/--skip-audit", help="Require matched profile-audit evidence."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_profile_promotion_plan

    result = build_production_profile_promotion_plan(
        service,
        profile_ids=_split_csv(profile_id),
        strategy_codes=_split_csv(strategy_code),
        max_stake_units=max_stake_units,
        config_path=config_path,
        apply_changes=apply_changes,
        seasons=_split_csv(seasons),
        roi_tolerance=roi_tolerance,
        clv_tolerance=clv_tolerance,
        require_audit=require_audit,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-config-plan")
def production_config_plan(
    source: str = typer.Option("", "--source", help="Comma-separated data source ids to enable."),
    broker: str = typer.Option("", "--broker", help="Comma-separated broker ids to enable or update."),
    stake_currency_per_unit: float | None = typer.Option(None, "--stake-currency-per-unit", help="Broker stake currency amount represented by one stake unit."),
    config_path: Path | None = typer.Option(None, "--config-path", help="Config YAML path to patch when --apply is used."),
    apply_changes: bool = typer.Option(False, "--apply", help="Write ready production config patches to config."),
    allow_missing_credentials: bool = typer.Option(False, "--allow-missing-credentials", help="Allow enabling config before credential env vars are present."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_config_plan

    source_ids = _split_csv(source)
    broker_ids = _split_csv(broker)
    result = build_production_config_plan(
        service,
        source_ids=source_ids or None,
        broker_ids=broker_ids or None,
        stake_currency_per_unit=stake_currency_per_unit,
        config_path=config_path,
        apply_changes=apply_changes,
        allow_missing_credentials=allow_missing_credentials,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-data-plan")
def production_data_plan(
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in readiness scope."),
    league: str | None = typer.Option(None, "--league", help="Limit data planning to a configured league code."),
    historical_odds_start_time: str | None = typer.Option(None, "--historical-odds-start-time", help="ISO start timestamp for The Odds API historical batch planning."),
    historical_odds_end_time: str | None = typer.Option(None, "--historical-odds-end-time", help="ISO end timestamp for The Odds API historical batch planning."),
    historical_odds_interval_minutes: int = typer.Option(10, "--historical-odds-interval-minutes", help="Minutes between planned historical odds snapshots."),
    historical_odds_max_snapshots: int = typer.Option(24, "--historical-odds-max-snapshots", help="Maximum planned historical odds snapshots per league."),
    historical_odds_max_events: int | None = typer.Option(None, "--historical-odds-max-events", help="Maximum events to store per historical odds snapshot."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_data_plan

    result = build_production_data_plan(
        service,
        include_past=include_past,
        league_codes=_league_codes_for_option(service.settings, league),
        historical_odds_start_time=historical_odds_start_time,
        historical_odds_end_time=historical_odds_end_time,
        historical_odds_interval_minutes=historical_odds_interval_minutes,
        historical_odds_max_snapshots=historical_odds_max_snapshots,
        historical_odds_max_events=historical_odds_max_events,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-data-apply")
def production_data_apply(
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in readiness scope."),
    execute: bool = typer.Option(False, "--execute", help="Run selected local data-plan commands. Defaults to dry-run."),
    allow_remote: bool = typer.Option(False, "--allow-remote", help="Allow commands that spend remote fixture/odds provider quota."),
    include_backtests: bool = typer.Option(True, "--include-backtests/--skip-backtests", help="Include local backtest/profile-audit commands."),
    include_blocked_prerequisites: bool = typer.Option(False, "--include-blocked-prerequisites", help="Allow commands whose data-plan task is blocked by a prerequisite."),
    max_commands: int | None = typer.Option(None, "--max-commands", help="Maximum selected commands to run or preview."),
    timeout_seconds: int = typer.Option(1800, "--timeout-seconds", help="Timeout for each executed command."),
    historical_odds_start_time: str | None = typer.Option(None, "--historical-odds-start-time", help="ISO start timestamp for The Odds API historical batch planning."),
    historical_odds_end_time: str | None = typer.Option(None, "--historical-odds-end-time", help="ISO end timestamp for The Odds API historical batch planning."),
    historical_odds_interval_minutes: int = typer.Option(10, "--historical-odds-interval-minutes", help="Minutes between planned historical odds snapshots."),
    historical_odds_max_snapshots: int = typer.Option(24, "--historical-odds-max-snapshots", help="Maximum planned historical odds snapshots per league."),
    historical_odds_max_events: int | None = typer.Option(None, "--historical-odds-max-events", help="Maximum events to store per historical odds snapshot."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_data_apply

    result = build_production_data_apply(
        service,
        include_past=include_past,
        execute=execute,
        allow_remote=allow_remote,
        include_backtests=include_backtests,
        include_blocked_prerequisites=include_blocked_prerequisites,
        max_commands=max_commands,
        timeout_seconds=timeout_seconds,
        historical_odds_start_time=historical_odds_start_time,
        historical_odds_end_time=historical_odds_end_time,
        historical_odds_interval_minutes=historical_odds_interval_minutes,
        historical_odds_max_snapshots=historical_odds_max_snapshots,
        historical_odds_max_events=historical_odds_max_events,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-historical-odds-plan")
def production_historical_odds_plan(
    league: str = typer.Option(..., "--league", help="Comma-separated configured league codes, e.g. EPL,A_LEAGUE."),
    start_time: str = typer.Option(..., "--start-time", help="ISO start timestamp for historical odds snapshots."),
    end_time: str = typer.Option(..., "--end-time", help="ISO end timestamp for historical odds snapshots."),
    interval_minutes: int = typer.Option(10, "--interval-minutes", help="Minutes between planned snapshots."),
    max_snapshots: int = typer.Option(24, "--max-snapshots", help="Maximum planned snapshots per league."),
    max_events: int | None = typer.Option(None, "--max-events", help="Maximum events to store per snapshot."),
    source: str = typer.Option("the_odds_api", "--source", help="Historical odds source. Currently supports the_odds_api."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_historical_odds_plan

    result = build_production_historical_odds_plan(
        service,
        leagues=_split_csv(league),
        start_time=start_time,
        end_time=end_time,
        interval_minutes=interval_minutes,
        max_snapshots=max_snapshots,
        max_events=max_events,
        source_id=source,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-execution-queue")
def production_execution_queue(
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in execution scope."),
    league: str | None = typer.Option(None, "--league", help="Limit execution queue to configured league code(s)."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform for generated commands."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_execution_queue

    result = build_production_execution_queue(
        service,
        include_past=include_past,
        platform=platform,
        league_codes=_league_codes_for_option(service.settings, league),
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-broker-plan")
def production_broker_plan(
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in execution scope."),
    league: str | None = typer.Option(None, "--league", help="Limit broker plan to configured league code(s)."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform used by queue generation."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_production_broker_plan

    result = build_production_broker_plan(
        service,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        league_codes=_league_codes_for_option(service.settings, league),
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-broker-discovery")
def production_broker_discovery(
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in discovery scope."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform used by queue generation."),
    max_items: int | None = typer.Option(None, "--max-items", help="Maximum queue items to inspect."),
    max_results: int = typer.Option(20, "--max-results", help="Maximum Betfair catalogue rows per queue item."),
    match_window_hours: int = typer.Option(36, "--match-window-hours", help="Kickoff search window on each side."),
    fetch_remote: bool = typer.Option(False, "--fetch-remote", help="Run read-only Betfair catalogue requests."),
    apply_mappings: bool = typer.Option(False, "--apply-mappings", help="Persist high-confidence mapping patches to local matches."),
    min_apply_confidence: str = typer.Option("high", "--min-apply-confidence", help="Minimum confidence to persist: high, medium, or low."),
    request_timeout_seconds: float = typer.Option(
        20.0,
        "--request-timeout-seconds",
        help="HTTP timeout for read-only broker discovery.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import run_production_broker_discovery

    result = run_production_broker_discovery(
        service,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        fetch_remote=fetch_remote,
        apply_mappings=apply_mappings,
        max_items=max_items,
        max_results=max_results,
        match_window_hours=match_window_hours,
        min_apply_confidence=min_apply_confidence,
        request_timeout_seconds=request_timeout_seconds,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-broker-execute")
def production_broker_execute(
    broker: str = typer.Option("betfair_exchange", "--broker", help="Execution broker id from config."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in execution scope."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform used by queue generation."),
    max_items: int | None = typer.Option(None, "--max-items", help="Maximum broker-ready queue items to consume."),
    execute_broker_orders: bool = typer.Option(
        False,
        "--execute-broker-orders",
        help="POST live orders to the broker. Omit for a request preview dry-run.",
    ),
    request_timeout_seconds: float = typer.Option(
        20.0,
        "--request-timeout-seconds",
        help="HTTP timeout for broker order placement.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import run_production_broker_execution

    result = run_production_broker_execution(
        service,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        execute_broker_orders=execute_broker_orders,
        max_items=max_items,
        request_timeout_seconds=request_timeout_seconds,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("production-execute")
def production_execute(
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in execution scope."),
    platform: str = typer.Option("real", "--platform", help="Record-bet platform for generated records."),
    max_items: int | None = typer.Option(None, "--max-items", help="Maximum queue items to consume."),
    fills_json: Path | None = typer.Option(None, "--fills-json", help="JSON file keyed by idempotency_key or recommendation_id with execution fills."),
    require_fills: bool = typer.Option(False, "--require-fills", help="Reject record writes when an execution fill is missing."),
    execute_records: bool = typer.Option(False, "--execute-records", help="Write approved queue items to the local bet ledger."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import run_production_execution

    result = run_production_execution(
        service,
        include_past=include_past,
        platform=platform,
        execute_records=execute_records,
        max_items=max_items,
        fills=_load_execution_fills(fills_json),
        require_fills=require_fills,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result)


def _load_execution_fills(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise typer.BadParameter("fills-json must contain an object keyed by idempotency_key or recommendation_id")
    return payload


@app.command("sources")
def sources(as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools.")) -> None:
    import asyncio

    result = asyncio.run(get_service().sources_health())
    if as_json:
        _print_json([item.model_dump(mode="json") for item in result])
        return
    for item in result:
        console.print(f"{item.source_id}: {item.state.value} - {item.detail}")


def _qqsd_normalize_market(market: str) -> str:
    normalized_market = market.strip().lower().replace("-", "_")
    if normalized_market in {"h2h", "europe", "europe_odds"}:
        return "1x2"
    if normalized_market in {"ah", "asian", "handicap"}:
        return "asian_handicap"
    if normalized_market in {"ou", "total", "totals", "overunder"}:
        return "over_under"
    return normalized_market


def _qqsd_company_identity(row: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(row.get("id") or row.get("companyid") or row.get("cid") or ""),
        "name": str(
            row.get("name")
            or row.get("company")
            or row.get("companyname")
            or row.get("companyName")
            or ""
        ),
    }


def _qqsd_compact_history_row(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "time",
        "updatetime",
        "updateTime",
        "date",
        "uptime",
        "modifytime",
        "win",
        "draw",
        "lost",
        "home",
        "same",
        "away",
        "w",
        "d",
        "l",
        "handi",
        "handicap",
        "line",
        "p",
        "pshow",
        "flat",
        "flatodds",
        "winodds",
        "lostodds",
        "homeodds",
        "awayodds",
        "big",
        "small",
        "over",
        "under",
        "pay",
        "kwin",
        "kdraw",
        "klost",
    )
    compact = {key: row[key] for key in keys if row.get(key) not in (None, "", [], {})}
    compact["keys"] = sorted(str(key) for key in row.keys())
    return compact


def _qqsd_compact_current_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    compact = _qqsd_company_identity(row)
    for key in ("first", "end"):
        value = row.get(key)
        if isinstance(value, dict):
            compact[key] = {
                item_key: item_value
                for item_key, item_value in value.items()
                if item_value not in (None, "", [], {})
            }
    compact["keys"] = sorted(str(key) for key in row.keys())
    return compact


def _qqsd_parse_extra_params(items: list[str] | None, *, option_name: str = "history-param") -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise typer.BadParameter(f"{option_name} must use KEY=VALUE format")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter(f"{option_name} key must not be empty")
        parsed[key] = value.strip()
    return parsed


def _qqsd_company_rows(client: Any, fid: str, *, market: str, company_endpoint: str) -> list[dict[str, Any]]:
    from football_analysis.datasources.qqsd import (
        _company_asian_odds_rows,
        _company_odds_rows,
        _company_total_odds_rows,
    )

    company_payload = client._post(company_endpoint, {"fid": fid})
    if market == "asian_handicap":
        return _company_asian_odds_rows(company_payload)
    if market == "over_under":
        return _company_total_odds_rows(company_payload)
    return _company_odds_rows(company_payload)


def _qqsd_select_company(
    company_rows: list[dict[str, Any]],
    *,
    company_id: str | None,
    company_name: str | None,
) -> tuple[dict[str, str] | None, dict[str, Any] | None, list[dict[str, str]]]:
    companies = [_qqsd_company_identity(row) for row in company_rows]
    selected_company: dict[str, str] | None = None
    if company_id:
        selected_company = next((company for company in companies if company["id"] == company_id), None)
    elif company_name:
        needle = company_name.lower()
        selected_company = next(
            (company for company in companies if needle in company["name"].lower()),
            None,
        )
    selected_row = None
    if selected_company:
        selected_row = next(
            (row for row in company_rows if _qqsd_company_identity(row)["id"] == selected_company["id"]),
            None,
        )
    return selected_company, selected_row, companies


def _qqsd_fetch_history_rows(
    client: Any,
    *,
    fid: str,
    market: str,
    company_id: str,
    vsdate: str | None,
    market_param: str | None,
    extra_params: dict[str, str],
) -> list[dict[str, Any]]:
    from football_analysis.datasources.qqsd import (
        _payload_rows,
        map_handicap_totals_odds_history_rows,
    )

    if market == "asian_handicap":
        history_payload = client.asian_odds_history(
            fid,
            company_id=company_id,
            vsdate=vsdate,
            t=market_param,
            extra_params=extra_params,
        )
        return map_handicap_totals_odds_history_rows(history_payload, market=market)
    if market == "over_under":
        history_payload = client.over_under_odds_history(
            fid,
            company_id=company_id,
            vsdate=vsdate,
            t=market_param,
            extra_params=extra_params,
        )
        return map_handicap_totals_odds_history_rows(history_payload, market=market)
    history_payload = client.europe_odds_history(fid, company_id=company_id)
    return _payload_rows(history_payload)


def _qqsd_match_vsdate(match: Match) -> str:
    return match.kickoff_at.strftime("%Y-%m-%d %H:%M:%S")


def _qqsd_build_odds_history_coverage(
    client: Any,
    *,
    date: str,
    markets: list[str],
    company_id: str | None,
    company_name: str | None,
    history_params: dict[str, str] | None = None,
    max_matches: int | None = None,
) -> dict[str, Any]:
    from football_analysis.datasources.qqsd import (
        qqsd_current_odds_available,
        qqsd_history_availability,
        qqsd_odds_timeline_capabilities,
    )

    capabilities = qqsd_odds_timeline_capabilities()
    normalized_markets = [_qqsd_normalize_market(market) for market in markets]
    invalid_markets = [market for market in normalized_markets if market not in capabilities]
    if invalid_markets:
        raise typer.BadParameter("markets must contain only: 1x2, asian_handicap, over_under")

    fixtures = sorted(client.fixtures(date), key=lambda match: match.kickoff_at)[:max_matches]
    checks: list[dict[str, Any]] = []
    for match in fixtures:
        fid = match.external_ids.get("qqsd_fid") or match.id.removeprefix("qqsd:")
        vsdate = _qqsd_match_vsdate(match)
        for market in normalized_markets:
            capability = capabilities[market]
            company_endpoint = str(capability["company_endpoint"])
            check: dict[str, Any] = {
                "fid": fid,
                "match_id": match.id,
                "league": match.league,
                "home_team": match.home_team,
                "away_team": match.away_team,
                "kickoff_at": match.kickoff_at.isoformat(),
                "vsdate": vsdate,
                "market": market,
                "company_endpoint": company_endpoint,
                "history_endpoint": capability.get("timeline_endpoint"),
                "status": "ok",
            }
            try:
                company_rows = _qqsd_company_rows(client, fid, market=market, company_endpoint=company_endpoint)
                selected_company, selected_row, companies = _qqsd_select_company(
                    company_rows,
                    company_id=company_id,
                    company_name=company_name,
                )
                check.update(
                    {
                        "available_company_count": len(companies),
                        "company": selected_company,
                        "current": _qqsd_compact_current_row(selected_row),
                        "history_request": {
                            "vsdate": vsdate,
                            "market_param": capability.get("market_param_value"),
                            "extra_params": history_params or {},
                        },
                    }
                )
                if not selected_company:
                    current_available = False
                    check.update(
                        {
                            "status": "company_required" if not (company_id or company_name) else "company_not_found",
                            "current_available": current_available,
                            "history_row_count": 0,
                            "first_row": None,
                            "last_row": None,
                            "sample_companies": companies[:10],
                            **qqsd_history_availability(0, current_available=current_available),
                        }
                    )
                    checks.append(check)
                    continue

                current_available = qqsd_current_odds_available(selected_row, market=market)
                history_rows = _qqsd_fetch_history_rows(
                    client,
                    fid=fid,
                    market=market,
                    company_id=selected_company["id"],
                    vsdate=vsdate,
                    market_param=None,
                    extra_params=history_params or {},
                )
                check.update(
                    {
                        "current_available": current_available,
                        "history_row_count": len(history_rows),
                        "first_row": _qqsd_compact_history_row(history_rows[0]) if history_rows else None,
                        "last_row": _qqsd_compact_history_row(history_rows[-1]) if history_rows else None,
                        **qqsd_history_availability(len(history_rows), current_available=current_available),
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive batch diagnostics
                check.update(
                    {
                        "status": "error",
                        "current_available": False,
                        "history_row_count": 0,
                        "history_availability": "history_request_error",
                        "history_issue": f"{type(exc).__name__}: {exc}",
                        "history_note": "QQSD history coverage check failed for this match and market.",
                    }
                )
            checks.append(check)

    def market_summary(market: str) -> dict[str, Any]:
        market_checks = [check for check in checks if check["market"] == market]
        count = len(market_checks)
        history_available = sum(1 for check in market_checks if check.get("history_availability") == "history_available")
        current_available = sum(1 for check in market_checks if check.get("current_available"))
        return {
            "market_checks": count,
            "history_available_count": history_available,
            "history_empty_current_available_count": sum(
                1 for check in market_checks if check.get("history_availability") == "history_empty_current_available"
            ),
            "current_missing_count": sum(
                1 for check in market_checks if check.get("history_availability") == "history_missing_current_missing"
            ),
            "company_missing_count": sum(1 for check in market_checks if check.get("status") == "company_not_found"),
            "history_request_error_count": sum(1 for check in market_checks if check.get("status") == "error"),
            "history_coverage_rate": round(history_available / count, 4) if count else 0.0,
            "current_coverage_rate": round(current_available / count, 4) if count else 0.0,
        }

    market_checks = len(checks)
    history_available_total = sum(1 for check in checks if check.get("history_availability") == "history_available")
    current_available_total = sum(1 for check in checks if check.get("current_available"))
    return {
        "source": "qqsd",
        "date": date,
        "company_filter": {"company_id": company_id, "company_name": company_name},
        "markets": normalized_markets,
        "matches_checked": len(fixtures),
        "market_checks": market_checks,
        "history_available_count": history_available_total,
        "history_empty_current_available_count": sum(
            1 for check in checks if check.get("history_availability") == "history_empty_current_available"
        ),
        "current_missing_count": sum(
            1 for check in checks if check.get("history_availability") == "history_missing_current_missing"
        ),
        "company_missing_count": sum(1 for check in checks if check.get("status") == "company_not_found"),
        "history_request_error_count": sum(1 for check in checks if check.get("status") == "error"),
        "history_coverage_rate": round(history_available_total / market_checks, 4) if market_checks else 0.0,
        "current_coverage_rate": round(current_available_total / market_checks, 4) if market_checks else 0.0,
        "by_market": {market: market_summary(market) for market in normalized_markets},
        "note": (
            "history_empty_current_available means the selected company has usable current odds but QQSD returned no "
            "timeline rows. For major matches this should be a confidence downgrade or configurable gate, not a "
            "datasource failure."
        ),
        "checks": checks,
    }


@qqsd_app.command("inspect-match")
def qqsd_inspect_match(
    fid: str = typer.Option(..., "--fid", help="QQSD fixture id."),
    vsdate: str | None = typer.Option(None, "--vsdate", help="QQSD fixture kickoff time, e.g. 2026-06-15 01:00:00."),
    company_id: str | None = typer.Option(None, "--company-id", help="QQSD company id for timeline markets."),
    company_name: str | None = typer.Option("Pinnacle", "--company-name", help="Company name substring, default Pinnacle."),
    markets_value: str = typer.Option("1x2,asian_handicap,over_under", "--markets", help="Comma-separated markets."),
    include_context: bool = typer.Option(True, "--context/--no-context", help="Fetch QQSD match detail, standings and analysis context."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    from football_analysis.datasources.qqsd import QQSDClient

    service = get_service()
    client = QQSDClient(service.ingestion._context("qqsd"))
    markets = [_qqsd_normalize_market(item) for item in _split_csv(markets_value)]
    payload: dict[str, Any] = {
        "source": "qqsd",
        "fid": fid,
        "vsdate": vsdate,
        "company_filter": {"company_id": company_id, "company_name": company_name},
        "odds_timeline": client.match_odds_timeline_bundle(
            fid,
            vsdate=vsdate,
            company_name=company_name,
            company_id=company_id,
            markets=markets,
        ),
    }
    if include_context:
        payload["context"] = client.match_analysis_bundle(fid)
    if as_json:
        _print_json(payload)
        return

    timeline = payload["odds_timeline"]
    summary = timeline.get("summary", {}) if isinstance(timeline, dict) else {}
    console.print(
        f"QQSD inspect fid={fid} history_rate={summary.get('history_coverage_rate', 0):.2%} "
        f"current_rate={summary.get('current_coverage_rate', 0):.2%}"
    )
    table = Table("market", "company", "current", "history_rows", "availability")
    for market, item in (timeline.get("markets", {}) or {}).items():
        company = item.get("company") if isinstance(item, dict) else {}
        table.add_row(
            str(market),
            str((company or {}).get("name") or ""),
            "yes" if item.get("current_available") else "no",
            str(item.get("history_row_count") or 0),
            str(item.get("history_availability") or item.get("status") or ""),
        )
    console.print(table)


@qqsd_app.command("odds-history")
def qqsd_odds_history(
    fid: str = typer.Option(..., "--fid", help="QQSD fixture id."),
    market: str = typer.Option("1x2", "--market", help="Market: 1x2, asian_handicap, or over_under."),
    company_id: str | None = typer.Option(None, "--company-id", help="QQSD company id from the market company endpoint."),
    company_name: str | None = typer.Option(None, "--company-name", help="Company name substring, e.g. Pinnacle."),
    vsdate: str | None = typer.Option(None, "--vsdate", help="Optional QQSD fixture kickoff time for company timeline, e.g. 2026-06-15 01:00:00."),
    market_param: str | None = typer.Option(None, "--market-param", help="Override QQSD timeline t parameter."),
    history_params: list[str] | None = typer.Option(None, "--history-param", help="Extra QQSD timeline body param as KEY=VALUE; repeatable."),
    limit: int = typer.Option(20, "--limit", min=1, max=500, help="Maximum history rows to print."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    from football_analysis.datasources.qqsd import (
        QQSDClient,
        qqsd_current_odds_available,
        qqsd_history_availability,
        qqsd_odds_timeline_capabilities,
    )

    service = get_service()
    client = QQSDClient(service.ingestion._context("qqsd"))
    capabilities = qqsd_odds_timeline_capabilities()
    normalized_market = _qqsd_normalize_market(market)
    if normalized_market not in capabilities:
        raise typer.BadParameter("market must be one of: 1x2, asian_handicap, over_under")

    capability = capabilities[normalized_market]
    company_endpoint = str(capability["company_endpoint"])
    company_rows = _qqsd_company_rows(client, fid, market=normalized_market, company_endpoint=company_endpoint)
    selected_company, selected_row, companies = _qqsd_select_company(
        company_rows,
        company_id=company_id,
        company_name=company_name,
    )
    extra_params = _qqsd_parse_extra_params(history_params)

    payload: dict[str, Any] = {
        "source": "qqsd",
        "fid": fid,
        "market": normalized_market,
        "capability": capability,
        "company_endpoint": company_endpoint,
        "history_endpoint": capability.get("timeline_endpoint"),
        "available_company_count": len(companies),
        "available_companies": companies,
        "history_request": {
            "vsdate": vsdate,
            "market_param": market_param or capability.get("market_param_value"),
            "extra_params": extra_params,
        },
    }
    if not selected_company:
        payload["status"] = "company_required" if not (company_id or company_name) else "company_not_found"
        if as_json:
            _print_json(payload)
            return
        console.print(f"status: {payload['status']}")
        table = Table("company_id", "company_name")
        for company in companies:
            table.add_row(company["id"], company["name"])
        console.print(table)
        return

    current_available = qqsd_current_odds_available(selected_row, market=normalized_market)
    if capability.get("status") != "supported":
        payload.update(
            {
                "status": "current_only",
                "company": selected_company,
                "current_available": current_available,
                "timeline_status": "unsupported_unverified",
                "timeline_issue": (
                    f"QQSD {normalized_market} company timeline endpoint is not confirmed; "
                    f"{company_endpoint} provides company first/end odds only."
                ),
                "current": _qqsd_compact_current_row(selected_row),
                **qqsd_history_availability(0, current_available=current_available),
            }
        )
        if as_json:
            _print_json(payload)
            return
        console.print(f"status: current_only market={normalized_market}")
        console.print(payload["timeline_issue"])
        console.print(payload.get("current") or {})
        return

    history_rows = _qqsd_fetch_history_rows(
        client,
        fid=fid,
        market=normalized_market,
        company_id=selected_company["id"],
        vsdate=vsdate,
        market_param=market_param,
        extra_params=extra_params,
    )
    history_state = qqsd_history_availability(len(history_rows), current_available=current_available)
    payload.update(
        {
            "status": "ok",
            "company": selected_company,
            "current_available": current_available,
            "current": _qqsd_compact_current_row(selected_row),
            "history_row_count": len(history_rows),
            "history_rows_returned": min(len(history_rows), limit),
            "rows": [_qqsd_compact_history_row(row) for row in history_rows[:limit]],
            "first_row": _qqsd_compact_history_row(history_rows[0]) if history_rows else None,
            "last_row": _qqsd_compact_history_row(history_rows[-1]) if history_rows else None,
            **history_state,
        }
    )
    if as_json:
        _print_json(payload)
        return

    console.print(
        f"QQSD {fid} {selected_company['name']}({selected_company['id']}) "
        f"history rows: {len(history_rows)} availability: {history_state['history_availability']}"
    )
    if history_state.get("history_issue"):
        console.print(str(history_state.get("history_note") or ""))
    if normalized_market == "asian_handicap":
        table = Table("time", "home", "line", "away")
    elif normalized_market == "over_under":
        table = Table("time", "over", "line", "under")
    else:
        table = Table("time", "win", "draw", "lost", "pay")
    for row in history_rows[:limit]:
        compact = _qqsd_compact_history_row(row)
        row_time = str(compact.get("time") or compact.get("updatetime") or compact.get("date") or "")
        if normalized_market == "asian_handicap":
            table.add_row(
                row_time,
                str(compact.get("home") or compact.get("win") or compact.get("w") or compact.get("winodds") or ""),
                str(compact.get("line") or compact.get("handi") or compact.get("flat") or compact.get("flatodds") or ""),
                str(compact.get("away") or compact.get("lost") or compact.get("l") or compact.get("lostodds") or ""),
            )
        elif normalized_market == "over_under":
            table.add_row(
                row_time,
                str(compact.get("over") or compact.get("big") or compact.get("home") or compact.get("win") or compact.get("winodds") or ""),
                str(compact.get("line") or compact.get("handi") or compact.get("flat") or compact.get("flatodds") or ""),
                str(compact.get("under") or compact.get("small") or compact.get("away") or compact.get("lost") or compact.get("lostodds") or ""),
            )
        else:
            table.add_row(
                row_time,
                str(compact.get("win") or compact.get("home") or compact.get("w") or ""),
                str(compact.get("draw") or compact.get("same") or compact.get("d") or ""),
                str(compact.get("lost") or compact.get("away") or compact.get("l") or ""),
                str(compact.get("pay") or ""),
            )
    console.print(table)


@qqsd_app.command("odds-history-coverage")
def qqsd_odds_history_coverage(
    date_value: str = typer.Option(..., "--date", help="QQSD fixture date YYYY-MM-DD."),
    markets_value: str = typer.Option(
        "asian_handicap,over_under",
        "--markets",
        help="Comma-separated markets: 1x2, asian_handicap, over_under.",
    ),
    company_id: str | None = typer.Option(None, "--company-id", help="QQSD company id from the market company endpoint."),
    company_name: str | None = typer.Option(
        "Pinnacle",
        "--company-name",
        help="Company name substring, default Pinnacle.",
    ),
    history_params: list[str] | None = typer.Option(
        None,
        "--history-param",
        help="Extra QQSD timeline body param as KEY=VALUE; repeatable.",
    ),
    max_matches: int | None = typer.Option(None, "--max-matches", min=1, help="Limit fixtures scanned."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    from football_analysis.datasources.qqsd import QQSDClient

    markets = [_qqsd_normalize_market(item) for item in _split_csv(markets_value)]
    if not markets:
        raise typer.BadParameter("markets must not be empty")
    service = get_service()
    client = QQSDClient(service.ingestion._context("qqsd"))
    result = _qqsd_build_odds_history_coverage(
        client,
        date=date_value,
        markets=markets,
        company_id=company_id,
        company_name=company_name,
        history_params=_qqsd_parse_extra_params(history_params),
        max_matches=max_matches,
    )
    if as_json:
        _print_json(result)
        return

    console.print(
        f"QQSD odds timeline coverage date={date_value} matches={result['matches_checked']} "
        f"checks={result['market_checks']} history_rate={result['history_coverage_rate']:.2%} "
        f"current_rate={result['current_coverage_rate']:.2%}"
    )
    summary_table = Table("market", "checks", "history", "empty+current", "current_missing", "errors", "history_rate")
    for market, summary in result["by_market"].items():
        summary_table.add_row(
            market,
            str(summary["market_checks"]),
            str(summary["history_available_count"]),
            str(summary["history_empty_current_available_count"]),
            str(summary["current_missing_count"]),
            str(summary["history_request_error_count"]),
            f"{summary['history_coverage_rate']:.2%}",
        )
    console.print(summary_table)

    detail_table = Table("kickoff", "league", "match", "market", "company", "current", "history_rows", "availability")
    for check in result["checks"]:
        company = check.get("company") or {}
        detail_table.add_row(
            str(check.get("vsdate") or ""),
            str(check.get("league") or ""),
            f"{check.get('home_team') or ''} vs {check.get('away_team') or ''}",
            str(check.get("market") or ""),
            str(company.get("name") or ""),
            "yes" if check.get("current_available") else "no",
            str(check.get("history_row_count") or 0),
            str(check.get("history_availability") or check.get("status") or ""),
        )
    console.print(detail_table)


@app.command("sources-the-odds-api-sports")
def sources_the_odds_api_sports(
    fetch_remote: bool = typer.Option(False, "--fetch-remote", help="Fetch /sports from The Odds API."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.production import build_the_odds_api_sports_report

    result = build_the_odds_api_sports_report(service, fetch_remote=fetch_remote)
    if as_json:
        _print_json(result)
        return
    console.print(result)


@app.command("odds-readiness")
def odds_readiness(
    min_bookmakers: int = typer.Option(2, "--min-bookmakers", help="Minimum bookmakers per match/market group."),
    min_profile_matches: int = typer.Option(1, "--min-profile-matches", help="Minimum ready matches per profile."),
    include_past: bool = typer.Option(False, "--include-past", help="Include past matches in the audit scope."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.odds_readiness import audit_odds_readiness

    result = audit_odds_readiness(
        service.repository,
        service.settings,
        min_bookmakers=min_bookmakers,
        min_profile_matches=min_profile_matches,
        include_past=include_past,
    )
    if as_json:
        _print_json(result)
        return

    table = Table(title=f"Odds readiness: {result.status}")
    table.add_column("Profile")
    table.add_column("Status")
    table.add_column("Ready")
    table.add_column("Matching")
    table.add_column("Issues")
    for profile in result.profiles:
        table.add_row(
            profile.profile_id,
            profile.status,
            str(profile.ready_matches),
            str(profile.matching_matches),
            ", ".join(profile.issues) or "-",
        )
    console.print(table)


@app.command("evaluate-finished")
def evaluate_finished(
    target_date: str | None = typer.Option(None, "--date", help="Local match date in YYYY-MM-DD. Defaults to today."),
    league: str | None = typer.Option(None, "--league", help="Configured league code, e.g. WORLD_CUP."),
    include_statuses: str = typer.Option(
        "recommended",
        "--include-statuses",
        help="Comma-separated recommendation statuses to settle. Default: recommended.",
    ),
    result_overrides: list[str] = typer.Option(
        [],
        "--result",
        help='Final score override, e.g. "Home vs Away=2-1" or "match_id=2-1".',
    ),
    save_results: bool = typer.Option(False, "--save-results", help="Persist --result overrides to local matches."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    local_date = date.fromisoformat(target_date) if target_date else datetime.now(service.settings.app.tzinfo).date()
    report = evaluate_finished_matches(
        service,
        target_date=local_date,
        league=league,
        included_statuses=_parse_recommendation_statuses(include_statuses),
        result_overrides=result_overrides,
        save_results=save_results,
    )
    if as_json:
        _print_json(report)
        return

    table = Table(title=f"Finished evaluation: {report.date} sample={report.sample_count} hit={report.hit_rate}")
    table.add_column("Match")
    table.add_column("Score")
    table.add_column("Status")
    table.add_column("Market")
    table.add_column("Selection")
    table.add_column("Result")
    table.add_column("Excluded")
    for item in report.results:
        table.add_row(
            f"{item.home_team} vs {item.away_team}",
            item.final_score or "-",
            item.recommendation_status,
            item.market_type or "-",
            item.normalized_selection or item.selection or "-",
            item.result or "-",
            item.excluded_reason or "-",
        )
    console.print(table)
    console.print(
        {
            "sample_count": report.sample_count,
            "wins": report.wins,
            "losses": report.losses,
            "voids": report.voids,
            "hit_rate": report.hit_rate,
            "roi_units": report.roi_units,
            "excluded_by_reason": report.excluded_by_reason,
        }
    )


@db_app.command("init")
def db_init(as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools.")) -> None:
    service = get_service()
    service.repository.initialize()
    payload = {"status": "ok", "database_url": service.settings.storage.database_url}
    if as_json:
        _print_json(payload)
        return
    console.print(payload)


@ingest_app.command("fixtures")
def ingest_fixtures(
    date: str = typer.Option(..., "--date", help="Date in YYYY-MM-DD."),
    source: str = typer.Option("api_football", "--source"),
    league: str | None = typer.Option(None, "--league", help="Configured league code, e.g. EPL."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_fixtures(date=date, source=source, league_code=league)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@ingest_app.command("results")
def ingest_results(
    date: str = typer.Option(..., "--date", help="Date in YYYY-MM-DD."),
    source: str = typer.Option("api_football", "--source"),
    league: str | None = typer.Option(None, "--league", help="Configured league code, e.g. EPL."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_results(date=date, source=source, league_code=league)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@ingest_app.command("odds")
def ingest_odds(
    date: str | None = typer.Option(None, "--date", help="Date in YYYY-MM-DD."),
    source: str = typer.Option("api_football", "--source"),
    league: str | None = typer.Option(None, "--league", help="Configured league code, e.g. EPL."),
    max_events: int | None = typer.Option(
        None,
        "--max-events",
        help="Maximum Odds-API.io events to price. Defaults to the configured league limit.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_odds(
        date=date,
        source=source,
        league_code=league,
        max_events=max_events,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@ingest_app.command("standings")
def ingest_standings(
    league: str = typer.Option(..., "--league", help="Configured league code, e.g. EPL."),
    season: int | None = typer.Option(None, "--season"),
    source: str = typer.Option("api_football", "--source"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_standings(league_code=league, season=season, source=source)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@ingest_app.command("historical-odds")
def ingest_historical_odds(
    league: str = typer.Option(..., "--league", help="Configured league code, e.g. EPL."),
    snapshot_time: str = typer.Option(..., "--snapshot-time", help="ISO timestamp for the historical odds snapshot."),
    source: str = typer.Option("the_odds_api", "--source"),
    max_events: int | None = typer.Option(None, "--max-events"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_historical_odds(
        league_code=league,
        snapshot_time=snapshot_time,
        source=source,
        max_events=max_events,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@ingest_app.command("historical")
def ingest_historical(
    league: str = typer.Option("E0", "--league"),
    season: str = typer.Option("2526", "--season"),
    source: str = typer.Option("football_data_uk", "--source", help="Historical data source."),
    path: str | None = typer.Option(None, "--path", help="Local CSV path."),
    download: bool = typer.Option(False, "--download", help="Download from football-data.co.uk."),
    start_date: str | None = typer.Option(None, "--start-date", help="YYYY-MM-DD start date for QQSD archive import."),
    end_date: str | None = typer.Option(None, "--end-date", help="YYYY-MM-DD end date for QQSD archive import."),
    max_pages: int | None = typer.Option(None, "--max-pages", help="Maximum QQSD archive pages per date."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    result = get_service().ingestion.ingest_historical(
        league=league,
        season=season,
        source=source,
        path=path,
        download=download,
        start_date=start_date,
        end_date=end_date,
        max_pages=max_pages,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("historical")
def backtest_historical(
    league: str = typer.Option("E0", "--league"),
    season: str = typer.Option("2526", "--season"),
    min_clv_edge: float = typer.Option(0.025, "--min-clv-edge"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.backtest import run_historical_backtest

    result = run_historical_backtest(service.repository, league=league, season=season, min_clv_edge=min_clv_edge)
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("optimize")
def backtest_optimize(
    league: str = typer.Option("E0", "--league"),
    train_seasons: str = typer.Option("2122,2223,2324,2425", "--train-seasons"),
    test_seasons: str = typer.Option("2526", "--test-seasons"),
    min_test_bets: int = typer.Option(80, "--min-test-bets"),
    season_phases: str = typer.Option("all", "--season-phases", help="Comma-separated phases: all, early, middle, late."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.strategy import optimize_strategy

    result = optimize_strategy(
        service.repository,
        league=league,
        train_seasons=_split_csv(train_seasons),
        test_seasons=_split_csv(test_seasons),
        min_test_bets=min_test_bets,
        season_phases=_split_csv(season_phases),
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("walk-forward")
def backtest_walk_forward(
    league: str = typer.Option("E0", "--league"),
    seasons: str = typer.Option("2122,2223,2324,2425,2526", "--seasons"),
    min_train_seasons: int = typer.Option(2, "--min-train-seasons"),
    min_test_bets: int = typer.Option(30, "--min-test-bets"),
    season_phases: str = typer.Option("all", "--season-phases", help="Comma-separated phases: all, early, middle, late."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.strategy import walk_forward_optimize

    result = walk_forward_optimize(
        service.repository,
        league=league,
        seasons=_split_csv(seasons),
        min_train_seasons=min_train_seasons,
        min_test_bets=min_test_bets,
        season_phases=_split_csv(season_phases),
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("portfolio")
def backtest_portfolio(
    seasons: str = typer.Option("2122,2223,2324,2425,2526", "--seasons"),
    leagues: str = typer.Option("E0,SP1,D1,I1,F1", "--leagues", help="Comma-separated league codes for phase scans."),
    season_phases: str = typer.Option("all,early,middle,late", "--season-phases", help="Comma-separated phases for phase scans."),
    scan_phases: bool = typer.Option(False, "--scan-phases", help="Run league/phase optimization scans."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.strategy import build_strategy_portfolio

    result = build_strategy_portfolio(
        service.repository,
        seasons=_split_csv(seasons),
        leagues=_split_csv(leagues),
        season_phases=_split_csv(season_phases),
        scan_phases=scan_phases,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("long-horizon-scan")
def backtest_long_horizon_scan(
    league: str = typer.Option("I1", "--league"),
    family: str = typer.Option(
        "asian-away",
        "--family",
        help="Strategy family: asian-away, asian-home, market-home, or market-away.",
    ),
    seasons: str = typer.Option("", "--seasons", help="Comma-separated seasons. Empty means all loaded seasons."),
    discovery_start: str = typer.Option("1011", "--discovery-start"),
    discovery_end: str = typer.Option("1819", "--discovery-end"),
    holdout_start: str = typer.Option("1920", "--holdout-start"),
    quick: bool = typer.Option(False, "--quick", help="Run the fixed regression candidate only."),
    limit: int = typer.Option(10, "--limit"),
    min_total_bets: int = typer.Option(180, "--min-total-bets"),
    min_discovery_bets: int = typer.Option(80, "--min-discovery-bets"),
    min_holdout_bets: int = typer.Option(80, "--min-holdout-bets"),
    min_discovery_roi: float = typer.Option(0.08, "--min-discovery-roi"),
    min_holdout_roi: float = typer.Option(0.08, "--min-holdout-roi"),
    min_holdout_positive_seasons: int = typer.Option(4, "--min-holdout-positive-seasons"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.strategy import long_horizon_scan

    result = long_horizon_scan(
        service.repository,
        league=league,
        family=family,
        seasons=_split_csv(seasons) or None,
        discovery_start=discovery_start,
        discovery_end=discovery_end,
        holdout_start=holdout_start,
        quick=quick,
        limit=limit,
        min_total_bets=min_total_bets,
        min_discovery_bets=min_discovery_bets,
        min_holdout_bets=min_holdout_bets,
        min_discovery_roi=min_discovery_roi,
        min_holdout_roi=min_holdout_roi,
        min_holdout_positive_seasons=min_holdout_positive_seasons,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


@backtest_app.command("profile-audit")
def backtest_profile_audit(
    seasons: str = typer.Option("2122,2223,2324,2425,2526", "--seasons"),
    roi_tolerance: float = typer.Option(0.002, "--roi-tolerance"),
    clv_tolerance: float = typer.Option(0.002, "--clv-tolerance"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for tools."),
) -> None:
    service = get_service()
    from football_analysis.strategy import audit_strategy_profiles

    result = audit_strategy_profiles(
        service.repository,
        configured_profiles=service.settings.strategy_profiles,
        seasons=_split_csv(seasons),
        roi_tolerance=roi_tolerance,
        clv_tolerance=clv_tolerance,
    )
    if as_json:
        _print_json(result)
        return
    console.print(result.model_dump())


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@app.command("record-bet")
def record_bet(
    match_id: str,
    market: str,
    selection: str,
    odds: float,
    stake_units: float,
    platform: str,
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools."),
) -> None:
    bet = BetLog(
        id="",
        match_id=match_id,
        market_type=market,
        selection=selection,
        odds=odds,
        stake_units=stake_units,
        platform=platform,
    )
    try:
        recorded = get_service().record_bet(bet)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if as_json:
        _print_json(recorded)
        return
    console.print(f"Recorded bet {recorded.id}")


@app.command("settle-bet")
def settle_bet(
    bet_id: str,
    result: str | None = typer.Option(
        None,
        "--result",
        help="Explicit settlement result: win, loss, void, half_win, or half_loss.",
    ),
    closing_odds: float | None = typer.Option(None, "--closing-odds", help="Closing odds for CLV tracking."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools."),
) -> None:
    try:
        settled = get_service().settle_bet(bet_id, result=result, closing_odds=closing_odds)
    except (KeyError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if as_json:
        _print_json(settled)
        return
    console.print(f"Settled bet {settled.id}: {settled.result} {settled.profit_units:+.2f}u")


@app.command("settle-open-bets")
def settle_open_bets(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for Hermes/tools."),
) -> None:
    report = get_service().settle_open_bets()
    if as_json:
        _print_json(report)
        return
    console.print(
        f"Settled {report.settled_count}/{report.scanned_count} open bets; "
        f"skipped={report.skipped_count}, errors={report.error_count}"
    )


if __name__ == "__main__":
    app()
