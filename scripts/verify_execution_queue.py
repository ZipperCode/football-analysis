from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.db import StructuredRepository
from football_analysis.execution_queue import build_execution_queue
from football_analysis.models import MarketType, Match, OddsSnapshot
from football_analysis.service import AnalysisService
from football_analysis.settings import load_settings


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'queue.db'}"
        settings.live_trading.max_daily_stake_units = 0.6
        profile = settings.strategy_profiles[0]
        profile.league_code = "I1"
        profile.market_type = "asian_handicap"
        profile.selections = ["AH_AWAY"]
        profile.season_phases = ["all", "early", "middle", "late"]
        profile.live_enabled = True
        profile.max_stake_units = 0.4
        profile.long_horizon_roi = 0.2
        profile.long_horizon_settled_bets = 200
        profile.holdout_roi = 0.2
        profile.holdout_settled_bets = 100
        profile.holdout_positive_seasons = 3
        profile.holdout_season_count = 3
        profile.average_clv = 0.02
        profile.worst_season_roi = 0.01
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            now = datetime.now(settings.app.tzinfo)
            match = Match(
                id="queue:approved",
                league="Italy - Serie A",
                home_team="Home",
                away_team="Away",
                kickoff_at=now + timedelta(hours=3),
                data_completeness=0.95,
            )
            repository.upsert_model("matches", match.id, match)
            for bookmaker in ("book-a", "book-b"):
                snapshot = OddsSnapshot(
                    id=f"queue:approved:{bookmaker}",
                    match_id=match.id,
                    market_type=MarketType.asian_handicap,
                    line="-0.5",
                    source="test",
                    bookmaker=bookmaker,
                    collected_at=now,
                    outcome_odds={"AH_AWAY": 2.2, "AH_HOME": 1.8},
                    market_average={"AH_AWAY": 1.9, "AH_HOME": 1.9},
                    best_price={"AH_AWAY": 2.2, "AH_HOME": 1.8},
                )
                repository.upsert_model("odds", snapshot.id, snapshot)
            report = build_execution_queue(AnalysisService(settings, repository), checked_at=now)
            assert report.status == "ready"
            assert report.ready_to_bet is True
            assert report.approved_count == 1
            assert report.items[0].state == "approved"
            assert report.items[0].minimum_odds == 2.178
            assert report.items[0].stake_units == 0.4

            second = match.model_copy(
                update={
                    "id": "queue:overflow",
                    "home_team": "Home Overflow",
                    "away_team": "Away Overflow",
                    "kickoff_at": now + timedelta(days=1, hours=5),
                }
            )
            repository.upsert_model("matches", second.id, second)
            for bookmaker in ("book-a", "book-b"):
                snapshot = OddsSnapshot(
                    id=f"queue:overflow:{bookmaker}",
                    match_id=second.id,
                    market_type=MarketType.asian_handicap,
                    line="-0.5",
                    source="test",
                    bookmaker=bookmaker,
                    collected_at=now,
                    outcome_odds={"AH_AWAY": 2.2, "AH_HOME": 1.8},
                    market_average={"AH_AWAY": 1.9, "AH_HOME": 1.9},
                    best_price={"AH_AWAY": 2.2, "AH_HOME": 1.8},
                )
                repository.upsert_model("odds", snapshot.id, snapshot)
            capped_report = build_execution_queue(AnalysisService(settings, repository), checked_at=now)
            assert capped_report.approved_count == 1
            assert sum(item.stake_units for item in capped_report.items if item.state == "approved") <= 0.6
            assert any(
                "execution_queue_daily_stake_limit:0.80/0.60" in item.gates_failed
                for item in capped_report.items
            ), "manual execution queue must block candidates above the execution batch stake cap"

            longshot = Match(
                id="queue:longshot",
                league="Italy - Serie A",
                home_team="Home Longshot",
                away_team="Away Longshot",
                kickoff_at=now + timedelta(hours=4),
                data_completeness=0.95,
            )
            repository.upsert_model("matches", longshot.id, longshot)
            for bookmaker, away_price in (("book-a", 50.0), ("book-b", 1.5)):
                snapshot = OddsSnapshot(
                    id=f"queue:longshot:{bookmaker}",
                    match_id=longshot.id,
                    market_type=MarketType.over_under,
                    line="2.5",
                    source="test",
                    bookmaker=bookmaker,
                    collected_at=now,
                    outcome_odds={"OVER": away_price, "UNDER": 1.05},
                    market_average={"OVER": 25.75, "UNDER": 1.05},
                    best_price={"OVER": 50.0, "UNDER": 1.05},
                )
                repository.upsert_model("odds", snapshot.id, snapshot)
            longshot_report = build_execution_queue(AnalysisService(settings, repository), checked_at=now)
            assert all(item.match_id != longshot.id for item in longshot_report.items if item.state == "approved"), (
                "execution queue must not approve longshot prices outside the configured executable odds range"
            )
        finally:
            repository.close()
    print("execution queue verification passed")


if __name__ == "__main__":
    main()
