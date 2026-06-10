from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from football_analysis.db import StructuredRepository
from football_analysis.models import BetLog, Match
from football_analysis.service import AnalysisService
from football_analysis.settings import load_settings


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'settlement.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            _insert_finished_match(repository, "ah-away-win", home_score=1, away_score=2)
            _insert_finished_match(repository, "ah-home-half-loss", home_score=1, away_score=1)
            _insert_finished_match(repository, "over-win", home_score=2, away_score=1)
            _insert_finished_match(repository, "under-loss", home_score=2, away_score=1)

            repository.upsert_model(
                "bets",
                "ah-away-win-bet",
                _bet("ah-away-win-bet", "ah-away-win", "asian_handicap", "AH_AWAY(+0.5)", odds=1.95),
            )
            settled_away = service.settle_bet("ah-away-win-bet")
            assert settled_away.result == "win"
            assert settled_away.profit_units == 0.95

            repository.upsert_model(
                "bets",
                "ah-home-half-loss-bet",
                _bet("ah-home-half-loss-bet", "ah-home-half-loss", "asian_handicap", "AH_HOME(-0.25)", odds=1.9),
            )
            settled_half = service.settle_bet("ah-home-half-loss-bet")
            assert settled_half.result == "half_loss"
            assert settled_half.profit_units == -0.5

            repository.upsert_model(
                "bets",
                "over-win-bet",
                _bet("over-win-bet", "over-win", "over_under", "OVER 2.5", odds=1.88),
            )
            settled_over = service.settle_bet("over-win-bet")
            assert settled_over.result == "win"
            assert settled_over.profit_units == 0.88

            repository.upsert_model(
                "bets",
                "under-loss-bet",
                _bet("under-loss-bet", "under-loss", "over_under", "UNDER:2.5", odds=1.91),
            )
            settled_under = service.settle_bet("under-loss-bet")
            assert settled_under.result == "loss"
            assert settled_under.profit_units == -1.0

            repository.upsert_model(
                "bets",
                "negative-clv-bet",
                _bet("negative-clv-bet", "ah-away-win", "asian_handicap", "AH_AWAY(+0.5)", odds=2.10),
            )
            service.settle_bet("negative-clv-bet", result="loss", closing_odds=2.25)
            performance = service.performance()
            assert performance.average_clv == -0.0667, (
                "CLV must be bet odds divided by closing odds minus one; worse-than-close entries are negative"
            )

            _insert_finished_match(repository, "batch-ah", home_score=0, away_score=1)
            _insert_finished_match(repository, "batch-total", home_score=1, away_score=1)
            _insert_unfinished_match(repository, "batch-pending")
            repository.upsert_model(
                "bets",
                "batch-ah-bet",
                _bet("batch-ah-bet", "batch-ah", "asian_handicap", "AH_AWAY(+0.25)", odds=1.9),
            )
            repository.upsert_model(
                "bets",
                "batch-total-bet",
                _bet("batch-total-bet", "batch-total", "over_under", "UNDER 2.5", odds=1.8),
            )
            repository.upsert_model(
                "bets",
                "batch-pending-bet",
                _bet("batch-pending-bet", "batch-pending", "1x2", "HOME", odds=2.2),
            )
            batch = service.settle_open_bets()
            assert batch.scanned_count == 3, "batch settlement should scan only unsettled bets"
            assert batch.settled_count == 2, "batch settlement should settle finished open bets"
            assert batch.skipped_count == 1, "batch settlement should skip unfinished matches"
            assert batch.error_count == 0, "batch settlement should not classify missing scores as errors"
            settled_by_id = {bet.id: bet for bet in batch.settled_bets}
            assert settled_by_id["batch-ah-bet"].result == "win"
            assert settled_by_id["batch-total-bet"].result == "win"
            assert "batch-pending-bet:missing_final_score:batch-pending" in batch.skipped

            second_batch = service.settle_open_bets()
            assert second_batch.scanned_count == 1, "settled bets must not be reprocessed"
            assert second_batch.settled_count == 0
            assert second_batch.skipped_count == 1
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'cli-settlement.db'}"
        cli_repository = StructuredRepository(env["DATABASE_URL"])
        cli_repository.initialize()
        try:
            _insert_finished_match(cli_repository, "cli-batch", home_score=1, away_score=0)
            cli_repository.upsert_model(
                "bets",
                "cli-batch-bet",
                _bet("cli-batch-bet", "cli-batch", "asian_handicap", "AH_HOME(-0.5)", odds=1.9),
            )
        finally:
            cli_repository.close()
        completed = subprocess.run(
            ["footballctl", "settle-open-bets", "--json"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        cli_payload = json.loads(completed.stdout)
        assert cli_payload["settled_count"] == 1, "CLI batch settlement should settle open finished bets"
        assert cli_payload["settled_bets"][0]["id"] == "cli-batch-bet"

    with TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'api-settlement.db'}"
        api_repository = StructuredRepository(os.environ["DATABASE_URL"])
        api_repository.initialize()
        try:
            _insert_finished_match(api_repository, "api-batch", home_score=0, away_score=1)
            api_repository.upsert_model(
                "bets",
                "api-batch-bet",
                _bet("api-batch-bet", "api-batch", "asian_handicap", "AH_AWAY(+0.5)", odds=1.95),
            )
        finally:
            api_repository.close()
        from fastapi.testclient import TestClient
        from football_analysis.api import app

        response = TestClient(app).post("/bets/settle-open")
        assert response.status_code == 200
        api_payload = response.json()
        assert api_payload["settled_count"] == 1, "API batch settlement should settle open finished bets"
        assert api_payload["settled_bets"][0]["id"] == "api-batch-bet"

    print("settlement verification passed")


def _insert_finished_match(repository: StructuredRepository, match_id: str, home_score: int, away_score: int) -> None:
    repository.upsert_model(
        "matches",
        match_id,
        Match(
            id=match_id,
            league="Italy - Serie A",
            home_team=f"{match_id} Home",
            away_team=f"{match_id} Away",
            kickoff_at=datetime(2026, 1, 17, 19, 45, tzinfo=ZoneInfo("Asia/Shanghai")),
            data_completeness=0.95,
            home_score=home_score,
            away_score=away_score,
        ),
    )


def _insert_unfinished_match(repository: StructuredRepository, match_id: str) -> None:
    repository.upsert_model(
        "matches",
        match_id,
        Match(
            id=match_id,
            league="Italy - Serie A",
            home_team=f"{match_id} Home",
            away_team=f"{match_id} Away",
            kickoff_at=datetime(2026, 1, 17, 19, 45, tzinfo=ZoneInfo("Asia/Shanghai")),
            data_completeness=0.95,
        ),
    )


def _bet(
    bet_id: str,
    match_id: str,
    market_type: str,
    selection: str,
    odds: float,
) -> BetLog:
    return BetLog(
        id=bet_id,
        match_id=match_id,
        market_type=market_type,
        selection=selection,
        odds=odds,
        stake_units=1.0,
        platform="paper",
        placed_at=datetime(2026, 1, 17, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


if __name__ == "__main__":
    main()
