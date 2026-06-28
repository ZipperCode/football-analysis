from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from football_analysis.backtest import run_historical_backtest
from football_analysis.daily_ops import DailyOpsReport, run_daily_ops
from football_analysis.execution_queue import ExecutionQueueReport, build_execution_queue
from football_analysis.live_audit import LiveAuditReport, audit_live_trading
from football_analysis.live_decision import LiveDecisionReport, run_live_decision
from football_analysis.live_preflight import LivePreflightReport, run_live_preflight
from football_analysis.live_refresh import LiveRefreshReport, run_live_refresh
from football_analysis.live_review import LiveReviewReport, run_live_review
from football_analysis.models import (
    BacktestSummary,
    BetLog,
    BetSettlementReport,
    IngestionResult,
    MatchAnalysis,
    PerformanceByLeagueReport,
    PerformanceSummary,
    PickList,
    SourceHealth,
)
from football_analysis.production import build_production_status
from football_analysis.service import AnalysisService, get_api_service

app = FastAPI(
    title="football-analysis",
    version="0.1.0",
    description="Pre-match football value betting analysis backend.",
)


def _admin_token() -> str:
    return os.getenv("FOOTBALL_ADMIN_TOKEN", "").strip()


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _league_codes_for_query(service: AnalysisService, league: str | None) -> set[str] | None:
    if not league:
        return None
    from football_analysis.live_refresh import _canonical_league_code

    codes = {
        _canonical_league_code(service.settings, item.strip()) or item.strip()
        for item in league.split(",")
        if item.strip()
    }
    return codes or None


def _requires_admin_token(request: Request) -> bool:
    if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    guarded_get_params = {
        "/production/onboarding-apply-plan": {"execute_ready"},
        "/production/deployment-doctor": {"execute_candidate_ready"},
        "/sources/the-odds-api/sports": {"fetch_remote"},
    }
    return any(
        _truthy(request.query_params.get(param))
        for param in guarded_get_params.get(request.url.path, set())
    )


def _request_has_admin_token(request: Request, expected_token: str) -> bool:
    candidates: list[str] = []
    header_token = request.headers.get("x-football-admin-token")
    if header_token:
        candidates.append(header_token.strip())
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        candidates.append(value.strip())
    return any(hmac.compare_digest(candidate, expected_token) for candidate in candidates)


@app.middleware("http")
async def require_admin_token(request: Request, call_next):
    expected_token = _admin_token()
    if expected_token and _requires_admin_token(request):
        if not _request_has_admin_token(request, expected_token):
            return JSONResponse(
                status_code=401,
                content={"detail": "admin_token_required"},
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "football-analysis", "status": "ok"}


@app.get("/healthz")
def healthz(service: AnalysisService = Depends(get_api_service)) -> dict[str, str]:
    try:
        service.repository.count("jobs")
    except Exception as exc:  # pragma: no cover - defensive for container probes
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {"service": "football-analysis", "status": "ok", "database": "ok"}


@app.get("/production/preflight")
def production_preflight(
    broker: str = "betfair_exchange",
    include_past: bool = False,
    league: str | None = None,
    platform: str = "real",
    require_broker: bool = False,
    require_execution_queue: bool = False,
    recent_limit: int = 10,
    max_cycle_age_minutes: int = 90,
    max_data_job_age_minutes: int = 180,
    profile_audit: bool = False,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_preflight

    return build_production_preflight(
        service,
        broker_id=broker,
        include_past=include_past,
        league_codes=_league_codes_for_query(service, league),
        platform=platform,
        require_broker=require_broker,
        require_execution_queue=require_execution_queue,
        recent_limit=recent_limit,
        max_cycle_age_minutes=max_cycle_age_minutes,
        max_data_job_age_minutes=max_data_job_age_minutes,
        profile_promotion_audit=profile_audit,
    )


@app.get("/production/status")
def production_status(
    recent_limit: int = 10,
    include_past: bool = False,
    league: str | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_status

    return build_production_status(
        service,
        recent_limit=recent_limit,
        include_past=include_past,
        league_codes=_league_codes_for_query(service, league),
    )


@app.get("/production/health")
def production_health(
    recent_limit: int = 10,
    include_past: bool = False,
    league: str | None = None,
    max_cycle_age_minutes: int = 90,
    max_data_job_age_minutes: int = 180,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_health

    return build_production_health(
        service,
        recent_limit=recent_limit,
        include_past=include_past,
        league_codes=_league_codes_for_query(service, league),
        max_cycle_age_minutes=max_cycle_age_minutes,
        max_data_job_age_minutes=max_data_job_age_minutes,
    )


@app.get("/production/readiness")
def production_readiness(
    include_past: bool = False,
    league: str | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_readiness

    return build_production_readiness(
        service,
        include_past=include_past,
        league_codes=_league_codes_for_query(service, league),
    )


@app.get("/production/onboarding")
def production_onboarding(
    broker: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    profile_audit: bool = False,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_onboarding

    return build_production_onboarding(
        service,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        profile_promotion_audit=profile_audit,
    )


@app.get("/production/onboarding-checklist")
def production_onboarding_checklist(
    target: str = "worker",
    broker: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    profile_audit: bool = False,
    config_path: str | None = None,
    broker_stake_currency_per_unit: float | None = None,
    include_markdown: bool = False,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
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
    if include_markdown:
        result = {**result, "markdown": format_production_onboarding_checklist_markdown(result)}
    return result


@app.get("/production/onboarding-apply-plan")
def production_onboarding_apply_plan(
    broker: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    profile_audit: bool = False,
    config_path: str | None = None,
    broker_stake_currency_per_unit: float | None = None,
    execute_ready: bool = False,
    timeout_seconds: int = 1800,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_onboarding_apply_plan

    return build_production_onboarding_apply_plan(
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


@app.get("/production/deploy-check")
def production_deploy_check(
    target: str = "worker",
    broker: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    require_execution_queue: bool = False,
    profile_audit: bool = False,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_deploy_check

    return build_production_deploy_check(
        service,
        target=target,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        require_execution_queue=require_execution_queue,
        profile_promotion_audit=profile_audit,
    )


@app.get("/production/runtime-security")
def production_runtime_security(
    target: str = "worker",
) -> dict[str, object]:
    from football_analysis.production import build_production_runtime_security

    return build_production_runtime_security(target=target)


@app.get("/production/deployment-doctor")
def production_deployment_doctor(
    source_config_path: str | None = None,
    candidate_config_path: str | None = None,
    target: str = "worker",
    broker: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    require_execution_queue: bool = False,
    profile_audit: bool = False,
    broker_stake_currency_per_unit: float | None = None,
    execute_candidate_ready: bool = False,
    refresh_candidate: bool = True,
    max_apply_passes: int = 3,
    timeout_seconds: int = 1800,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_deployment_doctor

    return build_production_deployment_doctor(
        service,
        source_config_path=source_config_path,
        candidate_config_path=candidate_config_path,
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


@app.post("/production/candidate-check")
def production_candidate_check(
    source_config_path: str | None = None,
    candidate_config_path: str | None = None,
    target: str = "record-only",
    broker: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    require_execution_queue: bool = False,
    profile_audit: bool = False,
    broker_stake_currency_per_unit: float | None = None,
    execute_ready: bool = True,
    refresh_candidate: bool = True,
    max_apply_passes: int = 3,
    timeout_seconds: int = 1800,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_candidate_check

    return build_production_candidate_check(
        service,
        source_config_path=source_config_path,
        candidate_config_path=candidate_config_path,
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


@app.post("/production/profile-promotions")
def production_profile_promotions(
    profile_id: str = "",
    strategy_code: str = "",
    max_stake_units: float | None = None,
    config_path: str | None = None,
    apply_changes: bool = False,
    seasons: str = "2122,2223,2324,2425,2526",
    roi_tolerance: float = 0.002,
    clv_tolerance: float = 0.002,
    require_audit: bool = True,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_profile_promotion_plan

    return build_production_profile_promotion_plan(
        service,
        profile_ids=[item.strip() for item in profile_id.split(",") if item.strip()],
        strategy_codes=[item.strip() for item in strategy_code.split(",") if item.strip()],
        max_stake_units=max_stake_units,
        config_path=config_path,
        apply_changes=apply_changes,
        seasons=[item.strip() for item in seasons.split(",") if item.strip()],
        roi_tolerance=roi_tolerance,
        clv_tolerance=clv_tolerance,
        require_audit=require_audit,
    )


@app.post("/production/config-plan")
def production_config_plan(
    source: str = "",
    broker: str = "",
    stake_currency_per_unit: float | None = None,
    config_path: str | None = None,
    apply_changes: bool = False,
    allow_missing_credentials: bool = False,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_config_plan

    source_ids = [item.strip() for item in source.split(",") if item.strip()]
    broker_ids = [item.strip() for item in broker.split(",") if item.strip()]
    return build_production_config_plan(
        service,
        source_ids=source_ids or None,
        broker_ids=broker_ids or None,
        stake_currency_per_unit=stake_currency_per_unit,
        config_path=config_path,
        apply_changes=apply_changes,
        allow_missing_credentials=allow_missing_credentials,
    )


@app.get("/production/data-plan")
def production_data_plan(
    include_past: bool = False,
    league: str | None = None,
    historical_odds_start_time: str | None = None,
    historical_odds_end_time: str | None = None,
    historical_odds_interval_minutes: int = 10,
    historical_odds_max_snapshots: int = 24,
    historical_odds_max_events: int | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_data_plan

    return build_production_data_plan(
        service,
        include_past=include_past,
        league_codes=_league_codes_for_query(service, league),
        historical_odds_start_time=historical_odds_start_time,
        historical_odds_end_time=historical_odds_end_time,
        historical_odds_interval_minutes=historical_odds_interval_minutes,
        historical_odds_max_snapshots=historical_odds_max_snapshots,
        historical_odds_max_events=historical_odds_max_events,
    )


@app.post("/production/data-apply")
def production_data_apply(
    include_past: bool = False,
    execute: bool = False,
    allow_remote: bool = False,
    include_backtests: bool = True,
    include_blocked_prerequisites: bool = False,
    max_commands: int | None = None,
    timeout_seconds: int = 1800,
    historical_odds_start_time: str | None = None,
    historical_odds_end_time: str | None = None,
    historical_odds_interval_minutes: int = 10,
    historical_odds_max_snapshots: int = 24,
    historical_odds_max_events: int | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_data_apply

    return build_production_data_apply(
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


@app.get("/production/historical-odds-plan")
def production_historical_odds_plan(
    league: str,
    start_time: str,
    end_time: str,
    interval_minutes: int = 10,
    max_snapshots: int = 24,
    max_events: int | None = None,
    source: str = "the_odds_api",
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_historical_odds_plan

    return build_production_historical_odds_plan(
        service,
        leagues=[item.strip() for item in league.split(",") if item.strip()],
        start_time=start_time,
        end_time=end_time,
        interval_minutes=interval_minutes,
        max_snapshots=max_snapshots,
        max_events=max_events,
        source_id=source,
    )


@app.get("/production/execution-queue")
def production_execution_queue(
    include_past: bool = False,
    league: str | None = None,
    platform: str = "real",
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_execution_queue

    return build_production_execution_queue(
        service,
        include_past=include_past,
        platform=platform,
        league_codes=_league_codes_for_query(service, league),
    )


@app.get("/production/broker-plan")
def production_broker_plan(
    broker: str = "betfair_exchange",
    include_past: bool = False,
    league: str | None = None,
    platform: str = "real",
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import build_production_broker_plan

    return build_production_broker_plan(
        service,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        league_codes=_league_codes_for_query(service, league),
    )


@app.post("/production/broker-discovery")
def production_broker_discovery(
    broker: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    fetch_remote: bool = False,
    apply_mappings: bool = False,
    max_items: int | None = None,
    max_results: int = 20,
    match_window_hours: int = 36,
    min_apply_confidence: str = "high",
    request_timeout_seconds: float = 20.0,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import run_production_broker_discovery

    return run_production_broker_discovery(
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


@app.post("/production/broker-execute")
def production_broker_execute(
    broker: str = "betfair_exchange",
    include_past: bool = False,
    platform: str = "real",
    execute_broker_orders: bool = False,
    max_items: int | None = None,
    request_timeout_seconds: float = 20.0,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import run_production_broker_execution

    return run_production_broker_execution(
        service,
        broker_id=broker,
        include_past=include_past,
        platform=platform,
        execute_broker_orders=execute_broker_orders,
        max_items=max_items,
        request_timeout_seconds=request_timeout_seconds,
    )


@app.post("/production/execute")
def production_execute(
    include_past: bool = False,
    platform: str = "real",
    execute_records: bool = False,
    require_fills: bool = False,
    max_items: int | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> dict[str, object]:
    from football_analysis.production import run_production_execution

    return run_production_execution(
        service,
        include_past=include_past,
        platform=platform,
        execute_records=execute_records,
        require_fills=require_fills,
        max_items=max_items,
    )


@app.get("/picks/today", response_model=PickList)
def picks_today(service: AnalysisService = Depends(get_api_service)) -> PickList:
    return service.picks_today()


@app.get("/live/audit", response_model=LiveAuditReport)
def live_audit(
    include_past: bool = False,
    league: str | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> LiveAuditReport:
    return audit_live_trading(
        service.repository,
        service.settings,
        include_past=include_past,
        league_codes=_league_codes_for_query(service, league),
    )


@app.get("/live/preflight", response_model=LivePreflightReport)
def live_preflight(
    include_past: bool = False,
    league: str | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> LivePreflightReport:
    return run_live_preflight(
        service.repository,
        service.settings,
        include_past=include_past,
        league_codes=_league_codes_for_query(service, league),
        require_strategy_profiles=league is None,
    )


@app.get("/live/review", response_model=LiveReviewReport)
def live_review(
    include_paper: bool = True,
    service: AnalysisService = Depends(get_api_service),
) -> LiveReviewReport:
    return run_live_review(service.repository, service.settings, include_paper=include_paper)


@app.get("/live/decision", response_model=LiveDecisionReport)
def live_decision(
    include_past: bool = False,
    league: str | None = None,
    include_paper: bool = True,
    full_profile_audit: bool = False,
    seasons: str = "2122,2223,2324,2425,2526",
    service: AnalysisService = Depends(get_api_service),
) -> LiveDecisionReport:
    return run_live_decision(
        service.repository,
        service.settings,
        include_past=include_past,
        include_paper=include_paper,
        full_profile_audit=full_profile_audit,
        seasons=[item.strip() for item in seasons.split(",") if item.strip()],
        league_codes=_league_codes_for_query(service, league),
        require_strategy_profiles=league is None,
    )


@app.post("/live/refresh", response_model=LiveRefreshReport)
def live_refresh(
    date: str,
    fixture_source: str = "auto",
    odds_source: str = "auto",
    league: str | None = None,
    scope: str = "active-profiles",
    max_events: int | None = None,
    include_past: bool = False,
    dry_run: bool = False,
    allow_odds_fallback: bool = False,
    service: AnalysisService = Depends(get_api_service),
) -> LiveRefreshReport:
    return run_live_refresh(
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




@app.get("/production/queue", response_model=ExecutionQueueReport)
def production_queue(
    include_past: bool = False,
    limit: int = 20,
    service: AnalysisService = Depends(get_api_service),
) -> ExecutionQueueReport:
    return build_execution_queue(service, include_past=include_past, limit=limit)


@app.post("/ops/daily", response_model=DailyOpsReport)
def daily_ops(
    date: str,
    ingest_results: bool = False,
    source: str = "api_football",
    league: str | None = None,
    include_past: bool = False,
    service: AnalysisService = Depends(get_api_service),
) -> DailyOpsReport:
    return run_daily_ops(
        service,
        date=date,
        ingest_results=ingest_results,
        source=source,
        league=league,
        include_past=include_past,
    )


@app.get("/matches/{match_id}/analysis", response_model=MatchAnalysis)
def match_analysis(match_id: str, service: AnalysisService = Depends(get_api_service)) -> MatchAnalysis:
    try:
        return service.analyze_match(match_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/bets", response_model=BetLog)
def create_bet(bet: BetLog, service: AnalysisService = Depends(get_api_service)) -> BetLog:
    try:
        return service.record_bet(bet)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/bets/{bet_id}/settle", response_model=BetLog)
def settle_bet(
    bet_id: str,
    result: str | None = None,
    closing_odds: float | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> BetLog:
    try:
        return service.settle_bet(bet_id, result=result, closing_odds=closing_odds)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/bets/settle-open", response_model=BetSettlementReport)
def settle_open_bets(service: AnalysisService = Depends(get_api_service)) -> BetSettlementReport:
    return service.settle_open_bets()


@app.get("/performance", response_model=PerformanceSummary)
def performance(service: AnalysisService = Depends(get_api_service)) -> PerformanceSummary:
    return service.performance()


@app.get("/performance/by-league", response_model=PerformanceByLeagueReport)
def performance_by_league(service: AnalysisService = Depends(get_api_service)) -> PerformanceByLeagueReport:
    return service.performance_by_league()


@app.get("/sources/health", response_model=list[SourceHealth])
async def sources_health(service: AnalysisService = Depends(get_api_service)) -> list[SourceHealth]:
    return await service.sources_health()


@app.get("/sources/the-odds-api/sports")
def the_odds_api_sports(
    fetch_remote: bool = False,
    service: AnalysisService = Depends(get_api_service),
) -> dict:
    from football_analysis.production import build_the_odds_api_sports_report

    return build_the_odds_api_sports_report(service, fetch_remote=fetch_remote)


@app.post("/jobs/ingest/fixtures", response_model=IngestionResult)
def ingest_fixtures(
    date: str,
    source: str = "api_football",
    league: str | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> IngestionResult:
    return service.ingestion.ingest_fixtures(date=date, source=source, league_code=league)


@app.post("/jobs/ingest/results", response_model=IngestionResult)
def ingest_results(
    date: str,
    source: str = "api_football",
    league: str | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> IngestionResult:
    return service.ingestion.ingest_results(date=date, source=source, league_code=league)


@app.post("/jobs/ingest/odds", response_model=IngestionResult)
def ingest_odds(
    date: str | None = None,
    source: str = "api_football",
    league: str | None = None,
    max_events: int | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> IngestionResult:
    return service.ingestion.ingest_odds(date=date, source=source, league_code=league, max_events=max_events)


@app.post("/jobs/ingest/standings", response_model=IngestionResult)
def ingest_standings(
    league: str,
    season: int | None = None,
    source: str = "api_football",
    service: AnalysisService = Depends(get_api_service),
) -> IngestionResult:
    return service.ingestion.ingest_standings(league_code=league, season=season, source=source)


@app.post("/jobs/ingest/historical-odds", response_model=IngestionResult)
def ingest_historical_odds(
    league: str,
    snapshot_time: str,
    source: str = "the_odds_api",
    max_events: int | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> IngestionResult:
    return service.ingestion.ingest_historical_odds(
        league_code=league,
        snapshot_time=snapshot_time,
        source=source,
        max_events=max_events,
    )


@app.get("/backtest/historical", response_model=BacktestSummary)
def backtest_historical(
    league: str = "E0",
    season: str = "2526",
    min_clv_edge: float = 0.025,
    service: AnalysisService = Depends(get_api_service),
) -> BacktestSummary:
    return run_historical_backtest(service.repository, league=league, season=season, min_clv_edge=min_clv_edge)
