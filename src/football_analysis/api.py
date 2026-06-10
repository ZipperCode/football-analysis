from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from football_analysis.backtest import run_historical_backtest
from football_analysis.models import BacktestSummary, BetLog, IngestionResult, MatchAnalysis, PerformanceSummary, PickList, SourceHealth
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


@app.get("/matches/{match_id}/analysis", response_model=MatchAnalysis)
def match_analysis(match_id: str, service: AnalysisService = Depends(get_api_service)) -> MatchAnalysis:
    try:
        return service.analyze_match(match_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/bets", response_model=BetLog)
def create_bet(bet: BetLog, service: AnalysisService = Depends(get_api_service)) -> BetLog:
    return service.record_bet(bet)


@app.get("/performance", response_model=PerformanceSummary)
def performance(service: AnalysisService = Depends(get_api_service)) -> PerformanceSummary:
    return service.performance()


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


@app.post("/jobs/ingest/odds", response_model=IngestionResult)
def ingest_odds(
    date: str | None = None,
    source: str = "api_football",
    league: str | None = None,
    service: AnalysisService = Depends(get_api_service),
) -> IngestionResult:
    return service.ingestion.ingest_odds(date=date, source=source, league_code=league)


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
