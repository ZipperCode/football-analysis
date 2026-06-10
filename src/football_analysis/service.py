from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from football_analysis.models import (
    AgentFinding,
    BetLog,
    Match,
    MatchAnalysis,
    OddsSnapshot,
    PerformanceSummary,
    PickList,
    Recommendation,
    RecommendationStatus,
    SourceHealth,
)
from football_analysis.db import StructuredRepository
from football_analysis.ingestion import IngestionService
from football_analysis.scoring import score_match
from football_analysis.seed_data import build_seed_dataset
from football_analysis.settings import Settings, load_settings
from football_analysis.sources import SourceHealthChecker


class AnalysisService:
    def __init__(self, settings: Settings, repository: StructuredRepository):
        self.settings = settings
        self.repository = repository
        self.health_checker = SourceHealthChecker(settings, repository)
        self.ingestion = IngestionService(settings, repository)

    @classmethod
    def from_config(cls) -> "AnalysisService":
        settings = load_settings()
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        service = cls(settings, repository)
        service.ensure_seed_data()
        return service

    def ensure_seed_data(self) -> None:
        if not self.settings.app.fixture_mode:
            return
        existing = self.repository.list_models("matches", Match)
        today = datetime.now(self.settings.app.tzinfo).date()
        has_today = any(match.kickoff_at.astimezone(self.settings.app.tzinfo).date() == today for match in existing)
        has_real_data = any(not match.id.startswith("SAMPLE-") for match in existing)
        if has_today or has_real_data:
            return
        matches, odds, findings = build_seed_dataset(self.settings.app.timezone)
        for match in matches:
            self.repository.upsert_model("matches", match.id, match)
        for snapshot in odds:
            self.repository.upsert_model("odds", snapshot.id, snapshot)
        for finding in findings:
            self.repository.upsert_model("findings", finding.id, finding)

    def picks_today(self) -> PickList:
        today = datetime.now(self.settings.app.tzinfo).date()
        analyses = [
            self.analyze_match(match.id)
            for match in self.repository.list_models("matches", Match)
            if match.kickoff_at.astimezone(self.settings.app.tzinfo).date() == today
        ]
        recommended = [
            analysis.recommendation
            for analysis in analyses
            if analysis.recommendation.status is RecommendationStatus.recommended
        ]
        recommended.sort(key=lambda item: (item.value_score, item.confidence), reverse=True)
        picks = recommended[: self.settings.app.daily_pick_limit]
        message = f"今日主推 {len(picks)} 场。" if picks else "今日无满足阈值的主推，建议只复盘观察。"
        return PickList(picks=picks, analyses=analyses, message=message)

    def analyze_match(self, match_id: str) -> MatchAnalysis:
        match = self.repository.get_model("matches", match_id, Match)
        if match is None:
            raise KeyError(f"Match not found: {match_id}")
        odds = [
            snapshot
            for snapshot in self.repository.list_models("odds", OddsSnapshot)
            if snapshot.match_id == match_id
        ]
        findings = [
            finding
            for finding in self.repository.list_models("findings", AgentFinding)
            if finding.match_id == match_id
        ]
        recommendation = score_match(match, odds, findings, self.settings)
        self.repository.upsert_model("recommendations", recommendation.id, recommendation)
        return MatchAnalysis(match=match, odds_snapshots=odds, findings=findings, recommendation=recommendation)

    def record_bet(self, bet: BetLog) -> BetLog:
        if not bet.id:
            bet.id = str(uuid4())
        self.repository.upsert_model("bets", bet.id, bet)
        return bet

    def performance(self) -> PerformanceSummary:
        bets = self.repository.list_models("bets", BetLog)
        settled = [bet for bet in bets if bet.profit_units is not None]
        total_stake = sum(bet.stake_units for bet in settled)
        profit = sum(bet.profit_units or 0.0 for bet in settled)
        clv_values = [
            (bet.closing_odds - bet.odds) / bet.odds
            for bet in settled
            if bet.closing_odds is not None and bet.odds > 0
        ]
        roi = profit / total_stake if total_stake else None
        average_clv = sum(clv_values) / len(clv_values) if clv_values else None
        return PerformanceSummary(
            bets=len(bets),
            settled_bets=len(settled),
            total_stake_units=round(total_stake, 3),
            profit_units=round(profit, 3),
            roi=round(roi, 4) if roi is not None else None,
            average_clv=round(average_clv, 4) if average_clv is not None else None,
        )

    async def sources_health(self) -> list[SourceHealth]:
        return await self.health_checker.check_all()


def get_service() -> AnalysisService:
    return AnalysisService.from_config()


def get_api_service():
    service = AnalysisService.from_config()
    try:
        yield service
    finally:
        service.repository.close()
