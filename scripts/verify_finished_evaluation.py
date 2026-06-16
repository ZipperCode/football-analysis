from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from football_analysis.db import StructuredRepository
from football_analysis.evaluation import evaluate_finished_matches
from football_analysis.models import MarketType, Match, MatchStatus, OddsSnapshot, RecommendationStatus
from football_analysis.service import AnalysisService
from football_analysis.settings import load_settings


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'evaluation.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            kickoff_at = datetime.now(settings.app.tzinfo) - timedelta(minutes=30)
            repository.upsert_model(
                "matches",
                "eval-home-win",
                Match(
                    id="eval-home-win",
                    league="BRA_SERIE_A",
                    home_team="Home FC",
                    away_team="Away FC",
                    kickoff_at=kickoff_at,
                    status=MatchStatus.finished,
                    data_completeness=1.0,
                    season=2026,
                    home_score=2,
                    away_score=0,
                ),
            )
            for idx, bookmaker in enumerate(["Book A", "Book B"], start=1):
                repository.upsert_model(
                    "odds",
                    f"eval-home-win-{idx}",
                    OddsSnapshot(
                        id=f"eval-home-win-{idx}",
                        match_id="eval-home-win",
                        market_type=MarketType.one_x_two,
                        source="verification",
                        bookmaker=bookmaker,
                        collected_at=kickoff_at - timedelta(minutes=15),
                        outcome_odds={"HOME": 2.20, "DRAW": 3.20, "AWAY": 3.70},
                        market_average={"HOME": 1.80, "DRAW": 3.10, "AWAY": 3.40},
                        best_price={"HOME": 2.20, "DRAW": 3.20, "AWAY": 3.70},
                    ),
                )

            report = evaluate_finished_matches(
                service,
                target_date=kickoff_at.date(),
                league="BRA_SERIE_A",
                included_statuses={RecommendationStatus.recommended},
            )
            assert report.sample_count == 1, report.model_dump(mode="json")
            assert report.wins == 1
            assert report.hit_rate == 1.0
            assert report.profit_units > 0

            empty_report = evaluate_finished_matches(
                service,
                target_date=kickoff_at.date(),
                league="BRA_SERIE_A",
                included_statuses={RecommendationStatus.paper_candidate},
            )
            assert empty_report.sample_count == 0
            assert empty_report.hit_rate is None
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'cli-evaluation.db'}"
        cli_repository = StructuredRepository(env["DATABASE_URL"])
        cli_repository.initialize()
        try:
            kickoff_at = datetime.now(load_settings().app.tzinfo) - timedelta(minutes=30)
            cli_repository.upsert_model(
                "matches",
                "cli-eval",
                Match(
                    id="cli-eval",
                    league="BRA_SERIE_A",
                    home_team="Cli Home",
                    away_team="Cli Away",
                    kickoff_at=kickoff_at,
                    status=MatchStatus.finished,
                    data_completeness=1.0,
                    season=2026,
                    home_score=2,
                    away_score=0,
                ),
            )
            for idx, bookmaker in enumerate(["Book A", "Book B"], start=1):
                cli_repository.upsert_model(
                    "odds",
                    f"cli-eval-{idx}",
                    OddsSnapshot(
                        id=f"cli-eval-{idx}",
                        match_id="cli-eval",
                        market_type=MarketType.one_x_two,
                        source="verification",
                        bookmaker=bookmaker,
                        collected_at=kickoff_at - timedelta(minutes=15),
                        outcome_odds={"HOME": 2.20, "DRAW": 3.20, "AWAY": 3.70},
                        market_average={"HOME": 1.80, "DRAW": 3.10, "AWAY": 3.40},
                        best_price={"HOME": 2.20, "DRAW": 3.20, "AWAY": 3.70},
                    ),
                )
        finally:
            cli_repository.close()

        completed = subprocess.run(
            [
                "footballctl",
                "evaluate-finished",
                "--date",
                kickoff_at.date().isoformat(),
                "--league",
                "BRA_SERIE_A",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        payload = json.loads(completed.stdout)
        assert payload["sample_count"] == 1
        assert payload["wins"] == 1

    print("finished evaluation verification passed")


if __name__ == "__main__":
    main()
