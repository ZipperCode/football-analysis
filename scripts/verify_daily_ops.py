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
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'daily-ops.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            _insert_finished_match(repository, "daily-service-match", home_score=1, away_score=0)
            repository.upsert_model(
                "bets",
                "daily-service-bet",
                _bet("daily-service-bet", "daily-service-match", "1x2", "HOME", odds=2.1),
            )

            from football_analysis.daily_ops import run_daily_ops

            report = run_daily_ops(service, date="2026-01-18", ingest_results=False, include_past=True)
            assert report.results_ingestion is None, "daily ops must not ingest results unless requested"
            assert report.settlement.settled_count == 1, "daily ops should batch settle open bets"
            assert report.performance.settled_bets == 1, "daily ops should include updated performance"
            assert report.performance.profit_units == 1.1
            assert report.live_review.status in {"monitoring", "ok"}, "daily ops should include live review state"
            assert report.preflight.ready_to_bet is False, "daily ops should include current preflight gate"
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'daily-cli.db'}"
        repository = StructuredRepository(env["DATABASE_URL"])
        repository.initialize()
        try:
            _insert_finished_match(repository, "daily-cli-match", home_score=0, away_score=1)
            repository.upsert_model(
                "bets",
                "daily-cli-bet",
                _bet("daily-cli-bet", "daily-cli-match", "asian_handicap", "AH_AWAY(+0.5)", odds=1.95),
            )
        finally:
            repository.close()
        completed = subprocess.run(
            ["footballctl", "daily-ops", "--date", "2026-01-18", "--include-past", "--json"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        cli_payload = json.loads(completed.stdout)
        assert cli_payload["settlement"]["settled_count"] == 1, "CLI daily ops must settle open bets"
        assert cli_payload["performance"]["settled_bets"] == 1, "CLI daily ops must include performance"
        assert "live_review" in cli_payload, "CLI daily ops must include live review"
        assert cli_payload["preflight"]["ready_to_bet"] is False

    with TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'daily-api.db'}"
        repository = StructuredRepository(os.environ["DATABASE_URL"])
        repository.initialize()
        try:
            _insert_finished_match(repository, "daily-api-match", home_score=2, away_score=1)
            repository.upsert_model(
                "bets",
                "daily-api-bet",
                _bet("daily-api-bet", "daily-api-match", "over_under", "OVER 2.5", odds=1.88),
            )
        finally:
            repository.close()
        from fastapi.testclient import TestClient
        from football_analysis.api import app

        response = TestClient(app).post("/ops/daily", params={"date": "2026-01-18", "include_past": True})
        assert response.status_code == 200
        api_payload = response.json()
        assert api_payload["settlement"]["settled_count"] == 1, "API daily ops must settle open bets"
        assert api_payload["performance"]["settled_bets"] == 1, "API daily ops must include performance"
        assert "live_review" in api_payload, "API daily ops must include live review"
        assert api_payload["preflight"]["ready_to_bet"] is False

    print("daily ops verification passed")


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
