from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from football_analysis.api import app
from football_analysis.db import StructuredRepository
from football_analysis.models import BetLog, Match, Recommendation, RecommendationStatus
from football_analysis.settings import StrategyProfileSettings, load_settings


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'review.db'}"
        settings.strategy_profiles = [_live_i1_profile()]
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            from football_analysis.live_review import run_live_review

            _insert_negative_profile_sample(repository)
            report = run_live_review(repository, settings, include_paper=True)
            assert report.status == "action_required", "negative settled sample must require operator action"
            profile = report.profiles[0]
            assert profile.profile_id == "i1_middle_ah_away_live_long_horizon"
            assert profile.settled_bets == 6
            assert profile.roi is not None and profile.roi < -0.15
            assert profile.average_clv is not None and profile.average_clv < 0.0
            assert profile.action == "pause_live", "lossy negative-CLV live profile must be paused"
            assert "negative_roi" in profile.issues
            assert "negative_clv" in profile.issues
            assert report.leagues[0].action in {"pause_live", "demote_to_paper"}
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'cli.db'}"
        subprocess.check_output(["footballctl", "db", "init", "--json"], env=env, text=True, encoding="utf-8")
        completed = subprocess.run(
            ["footballctl", "live-review", "--json"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        payload = json.loads(completed.stdout)
        assert "profiles" in payload, "CLI live-review JSON must include profile reviews"
        assert "leagues" in payload, "CLI live-review JSON must include league reviews"

        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'api.db'}"
        client = TestClient(app)
        assert client.get("/live/review").status_code == 200
        api_payload = client.get("/live/review").json()
        assert "profiles" in api_payload, "API live review JSON must include profile reviews"
        assert "leagues" in api_payload, "API live review JSON must include league reviews"

    print("live review verification passed")


def _live_i1_profile() -> StrategyProfileSettings:
    return StrategyProfileSettings(
        id="i1_middle_ah_away_live_long_horizon",
        name="I1 middle-season AH away live candidate",
        league_code="I1",
        market_type="asian_handicap",
        selections=["AH_AWAY"],
        season_phases=["all"],
        stability_label="live_candidate",
        roi=0.189,
        settled_bets=231,
        positive_folds=13,
        fold_count=16,
        average_clv=0.0154,
        active=True,
        live_enabled=True,
        max_stake_units=0.4,
        long_horizon_roi=0.189,
        long_horizon_settled_bets=231,
        holdout_roi=0.194,
        holdout_settled_bets=122,
        holdout_positive_seasons=5,
        holdout_season_count=7,
        worst_season_roi=-0.1737,
    )


def _insert_negative_profile_sample(repository: StructuredRepository) -> None:
    kickoff = datetime(2027, 1, 1, 19, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    profits = [-1.0, -1.0, -1.0, -1.0, 0.8, 0.6]
    for index, profit in enumerate(profits):
        match_id = f"review-i1-{index}"
        repository.upsert_model(
            "matches",
            match_id,
            Match(
                id=match_id,
                league="Italy - Serie A",
                home_team=f"Review Home {index}",
                away_team=f"Review Away {index}",
                kickoff_at=kickoff + timedelta(days=index),
                data_completeness=0.92,
                home_score=1,
                away_score=1,
            ),
        )
        repository.upsert_model(
            "recommendations",
            f"{match_id}-asian_handicap-AWAY-v1",
            Recommendation(
                id=f"{match_id}-asian_handicap-AWAY-v1",
                match_id=match_id,
                market_type="asian_handicap",
                selection="AWAY",
                status=RecommendationStatus.recommended,
                value_score=75.0,
                risk_score=30.0,
                confidence=0.7,
                stake_units=0.4,
                odds_basis={
                    "best_price": 2.12,
                    "strategy_profile": {
                        "matched": True,
                        "id": "i1_middle_ah_away_live_long_horizon",
                    },
                },
                score_breakdown={
                    "strategy_profile": {
                        "matched": True,
                        "id": "i1_middle_ah_away_live_long_horizon",
                    }
                },
                reason="review fixture",
                risk_notice="review fixture",
            ),
        )
        repository.upsert_model(
            "bets",
            f"review-bet-{index}",
            BetLog(
                id=f"review-bet-{index}",
                match_id=match_id,
                market_type="asian_handicap",
                selection="AH_AWAY(+0.5)",
                odds=2.10,
                stake_units=1.0,
                platform="paper",
                placed_at=kickoff + timedelta(days=index, hours=-2),
                result="win" if profit > 0 else "loss",
                profit_units=profit,
                closing_odds=2.25,
            ),
        )


if __name__ == "__main__":
    main()
