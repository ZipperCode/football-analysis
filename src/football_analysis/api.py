from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from football_analysis.backtest import run_historical_backtest
from football_analysis.daily_ops import DailyOpsReport, run_daily_ops
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
from football_analysis.service import AnalysisService, get_api_service

app = FastAPI(
    title="football-analysis",
    version="0.1.0",
    description="Pre-match football value betting analysis backend.",
)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "football-analysis", "status": "ok"}


@app.get("/picks/today", response_model=PickList)
def picks_today(service: AnalysisService = Depends(get_api_service)) -> PickList:
    return service.picks_today()


@app.get("/live/audit", response_model=LiveAuditReport)
def live_audit(
    include_past: bool = False,
    service: AnalysisService = Depends(get_api_service),
) -> LiveAuditReport:
    return audit_live_trading(service.repository, service.settings, include_past=include_past)


@app.get("/live/preflight", response_model=LivePreflightReport)
def live_preflight(
    include_past: bool = False,
    service: AnalysisService = Depends(get_api_service),
) -> LivePreflightReport:
    return run_live_preflight(service.repository, service.settings, include_past=include_past)


@app.get("/live/review", response_model=LiveReviewReport)
def live_review(
    include_paper: bool = True,
    service: AnalysisService = Depends(get_api_service),
) -> LiveReviewReport:
    return run_live_review(service.repository, service.settings, include_paper=include_paper)


@app.get("/live/decision", response_model=LiveDecisionReport)
def live_decision(
    include_past: bool = False,
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


@app.get("/backtest/historical", response_model=BacktestSummary)
def backtest_historical(
    league: str = "E0",
    season: str = "2526",
    min_clv_edge: float = 0.025,
    service: AnalysisService = Depends(get_api_service),
) -> BacktestSummary:
    return run_historical_backtest(service.repository, league=league, season=season, min_clv_edge=min_clv_edge)
