from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.db import StructuredRepository
from football_analysis.models import AgentFinding, BetLog, Match, MarketType, OddsSnapshot, RecommendationStatus, StrategySnapshot
from football_analysis.service import AnalysisService
from football_analysis.settings import load_settings


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'strategy-snapshot.db'}"
        settings.app.daily_pick_limit = 5
        settings.thresholds.min_data_quality = 0.5
        settings.thresholds.min_value_score = 50.0
        settings.thresholds.max_risk_score = 80.0
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            match = _match(settings)
            repository.upsert_model("matches", match.id, match)
            for snapshot in _odds(match.id):
                repository.upsert_model("odds", snapshot.id, snapshot)
            repository.upsert_model(
                "findings",
                "snapshot-news",
                AgentFinding(
                    id="snapshot-news",
                    match_id=match.id,
                    agent_name="news-signal",
                    summary="No material injury risk in the fixture fixture.",
                    confidence=0.65,
                    score_delta=3.0,
                ),
            )

            result = service.picks_today()
            assert result.analyses, "picks_today must score the fixture"
            recommendations = repository.list_models("recommendations", type(result.analyses[0].recommendation))
            assert recommendations, "recommendation should be persisted before snapshot audit"
            snapshots = repository.list_models("strategy_snapshots", StrategySnapshot)
            assert snapshots, "strategy snapshot should be persisted for audited recommendation"
            audited = snapshots[0]
            assert audited.match_id == match.id
            assert audited.recommendation_id == recommendations[0].id
            assert audited.recommendation_status in RecommendationStatus
            assert audited.market_odds["selected"]["best_price"] is not None
            assert audited.model_prediction["confidence"] == recommendations[0].confidence
            assert audited.time_to_kickoff_hours is not None
            assert audited.source_recommendation["id"] == recommendations[0].id

            settled_match = match.model_copy(update={"home_score": 2, "away_score": 0})
            repository.upsert_model("matches", settled_match.id, settled_match)
            recommendation = recommendations[0]
            repository.upsert_model(
                "bets",
                "snapshot-paper-bet",
                BetLog(
                    id="snapshot-paper-bet",
                    match_id=match.id,
                    market_type=recommendation.market_type,
                    selection=recommendation.selection or "HOME",
                    odds=2.30,
                    stake_units=1.0,
                    platform="paper",
                ),
            )
            service.settle_bet("snapshot-paper-bet", closing_odds=2.10)
            backfilled = repository.get_model("strategy_snapshots", audited.id, StrategySnapshot)
            assert backfilled is not None
            assert backfilled.clv == round((2.30 / 2.10) - 1.0, 6)
            assert backfilled.settlement_result == "win"
            assert backfilled.profit_units == 1.3
        finally:
            repository.close()

    print("strategy snapshot verification passed")


def _match(settings) -> Match:
    now = datetime.now(settings.app.tzinfo)
    kickoff_at = now + timedelta(hours=1)
    if kickoff_at.date() != now.date():
        kickoff_at = now.replace(hour=23, minute=59, second=0, microsecond=0)
    return Match(
        id="snapshot-match",
        league="England - Premier League",
        home_team="Snapshot Home",
        away_team="Snapshot Away",
        kickoff_at=kickoff_at,
        data_completeness=0.92,
    )


def _odds(match_id: str) -> list[OddsSnapshot]:
    collected_at = datetime.now(timezone.utc)
    return [
        OddsSnapshot(
            id="snapshot-odds-1",
            match_id=match_id,
            market_type=MarketType.one_x_two,
            source="verify",
            bookmaker="Book A",
            collected_at=collected_at,
            outcome_odds={"HOME": 2.30, "DRAW": 3.30, "AWAY": 3.10},
            market_average={"HOME": 2.05, "DRAW": 3.25, "AWAY": 3.20},
            best_price={"HOME": 2.30, "DRAW": 3.30, "AWAY": 3.10},
        ),
        OddsSnapshot(
            id="snapshot-odds-2",
            match_id=match_id,
            market_type=MarketType.one_x_two,
            source="verify",
            bookmaker="Book B",
            collected_at=collected_at,
            outcome_odds={"HOME": 2.02, "DRAW": 3.20, "AWAY": 3.05},
            market_average={"HOME": 2.05, "DRAW": 3.25, "AWAY": 3.20},
            best_price={"HOME": 2.02, "DRAW": 3.20, "AWAY": 3.05},
        ),
    ]


if __name__ == "__main__":
    main()
