from __future__ import annotations

from datetime import datetime

from pydantic import Field

from football_analysis.live_preflight import LivePreflightReport, run_live_preflight
from football_analysis.live_review import LiveReviewReport, run_live_review
from football_analysis.models import (
    AppModel,
    BetSettlementReport,
    IngestionResult,
    PerformanceByLeagueReport,
    PerformanceSummary,
)
from football_analysis.service import AnalysisService


class DailyOpsReport(AppModel):
    checked_at: datetime
    date: str
    results_ingestion: IngestionResult | None = None
    settlement: BetSettlementReport
    performance: PerformanceSummary
    performance_by_league: PerformanceByLeagueReport
    live_review: LiveReviewReport
    preflight: LivePreflightReport
    issues: list[str] = Field(default_factory=list)


def run_daily_ops(
    service: AnalysisService,
    date: str,
    ingest_results: bool = False,
    source: str = "api_football",
    league: str | None = None,
    include_past: bool = False,
) -> DailyOpsReport:
    checked_at = datetime.now(service.settings.app.tzinfo)
    results_ingestion = (
        service.ingestion.ingest_results(date=date, source=source, league_code=league)
        if ingest_results
        else None
    )
    settlement = service.settle_open_bets()
    performance = service.performance()
    performance_by_league = service.performance_by_league()
    live_review = run_live_review(service.repository, service.settings, checked_at=checked_at)
    preflight = run_live_preflight(
        service.repository,
        service.settings,
        include_past=include_past,
        checked_at=checked_at,
    )
    issues: list[str] = []
    if results_ingestion and results_ingestion.errors:
        issues.extend(f"results_ingestion:{error}" for error in results_ingestion.errors)
    if settlement.errors:
        issues.extend(f"settlement:{error}" for error in settlement.errors)
    issues.extend(f"live_review:{issue}" for issue in live_review.issues)
    issues.extend(f"preflight:{issue}" for issue in preflight.issues)
    return DailyOpsReport(
        checked_at=checked_at,
        date=date,
        results_ingestion=results_ingestion,
        settlement=settlement,
        performance=performance,
        performance_by_league=performance_by_league,
        live_review=live_review,
        preflight=preflight,
        issues=issues,
    )
