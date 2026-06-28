from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from football_analysis.api import app as api_app
from football_analysis.cli import app as cli_app
from football_analysis.db import StructuredRepository
from football_analysis.db import RecommendationRow
from sqlalchemy.orm import Session
from football_analysis.models import Match, Recommendation
from football_analysis.settings import load_settings


def main() -> None:
    runner = CliRunner()
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'runtime.db'}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        kickoff = datetime.now(settings.app.tzinfo) + timedelta(hours=4)
        match = Match(
            id="legacy:1",
            league="FIFA World Cup",
            home_team="Home",
            away_team="Away",
            kickoff_at=kickoff,
            status="scheduled",
            data_completeness=0.9,
        )
        repository.upsert_model("matches", match.id, match)
        legacy_payload = {
                "id": "legacy-rec:1",
                "match_id": match.id,
                "market_type": "1x2",
                "selection": "HOME",
                "status": "advisory_recommended",
                "value_score": 72.0,
                "risk_score": 30.0,
                "confidence": 0.66,
                "stake_units": 0.0,
                "odds_basis": {},
                "score_breakdown": {},
                "risk_tags": [],
                "reason": "legacy advisory output",
                "risk_notice": "paper only",
                "created_at": kickoff.isoformat(),
                "version": "v1",
        }
        with Session(repository.engine) as session:
            session.add(
                RecommendationRow(
                    id="legacy-rec:1",
                    match_id=match.id,
                    status="advisory_recommended",
                    value_score=72.0,
                    risk_score=30.0,
                    confidence=0.66,
                    created_at=kickoff,
                    payload=legacy_payload,
                )
            )
            session.commit()

        parsed = repository.list_models("recommendations", Recommendation)
        assert parsed[0].status.value == "paper_candidate"

        previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = settings.storage.database_url
        try:
            cli_result = runner.invoke(cli_app, ["production-status", "--json"])
        finally:
            if previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database_url
        assert cli_result.exit_code == 0, cli_result.output
        assert '"overall_status"' in cli_result.output
        assert '"ready_to_bet"' in cli_result.output

        from football_analysis.service import get_api_service

        api_app.dependency_overrides[get_api_service] = lambda: __import__(
            "football_analysis.service", fromlist=["AnalysisService"]
        ).AnalysisService(settings, repository)
        try:
            response = TestClient(api_app).get("/production/status")
        finally:
            api_app.dependency_overrides.clear()
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ready_to_bet"] is False
        assert payload["counts"]["matches"] == 1
        repository.close()

    print("production status runtime verification passed")


if __name__ == "__main__":
    main()
