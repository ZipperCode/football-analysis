from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from football_analysis.api import app
from football_analysis.db import StructuredRepository
from football_analysis.models import AgentFinding, BetLog, Match, OddsSnapshot
from football_analysis.settings import StrategyProfileSettings, load_settings


def main() -> None:
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "verify.db"
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{db_path}"
        settings.strategy_profiles = [_live_i1_profile()]
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            from football_analysis.live_preflight import run_live_preflight

            _insert_live_candidate(repository, "preflight-live-pass")
            report = run_live_preflight(repository, settings, include_past=True)
            assert report.status == "ready", "qualified live candidate should make preflight ready"
            assert report.ready_to_bet is True, "ready preflight must explicitly approve betting"
            assert report.action == "place_approved_live_bets", "ready action must be operational"
            assert report.live_audit.recommended_count == 1, "preflight must include live audit counts"
            assert report.odds_readiness.ready_profiles == 1, "preflight must include odds readiness counts"
            assert not report.issues, "ready preflight should not expose blocking issues"

            for index in range(3):
                repository.upsert_model(
                    "bets",
                    f"preflight-loss-{index}",
                    BetLog(
                        id=f"preflight-loss-{index}",
                        match_id=f"preflight-loss-match-{index}",
                        market_type="asian_handicap",
                        selection="AH_AWAY(+0.5)",
                        odds=1.95,
                        stake_units=0.4,
                        platform="paper",
                        placed_at=datetime(2027, 1, 20 + index, 12, 0),
                        result="loss",
                        profit_units=-0.4,
                    ),
                )

            paused = run_live_preflight(repository, settings, include_past=True)
            assert paused.status == "paused", "loss streak should pause preflight"
            assert paused.ready_to_bet is False, "paused preflight must not approve betting"
            assert paused.action == "do_not_bet_loss_pause"
            assert "live:live_recent_consecutive_losses:3/3" in paused.issues
            assert "live_recent_consecutive_losses:3/3" in paused.live_audit.issues
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "verify-secondary-ready.db"
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{db_path}"
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            from football_analysis.live_preflight import run_live_preflight

            _insert_secondary_live_candidate(repository, "preflight-secondary-live-pass")
            secondary_ready = run_live_preflight(repository, settings, include_past=True)
            assert secondary_ready.live_audit.status == "ready", "secondary live candidate must pass live audit"
            assert secondary_ready.odds_readiness.status == "insufficient", (
                "active profile odds may still be insufficient while a small-stake live candidate is approved"
            )
            assert secondary_ready.status == "ready", (
                "approved live-gate candidates must not be blocked by unrelated active-profile odds gaps"
            )
            assert secondary_ready.ready_to_bet is True
            assert secondary_ready.action == "place_approved_live_bets"
            assert not secondary_ready.issues, "ready preflight should not expose unrelated profile gaps as blockers"
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "verify-stale-odds.db"
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{db_path}"
        settings.strategy_profiles = [_live_i1_profile()]
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            from football_analysis.live_preflight import run_live_preflight

            _insert_live_candidate(
                repository,
                "preflight-stale-odds",
                collected_at=datetime.now(timezone.utc) - timedelta(minutes=240),
            )
            stale = run_live_preflight(repository, settings, include_past=True)
            assert stale.status == "blocked", "stale profile odds must block production preflight"
            assert stale.odds_readiness.ready_profiles == 0, "stale odds must not count as a ready profile"
            assert stale.odds_readiness.refresh_requirements, (
                "blocked profile odds must expose an actionable refresh requirement"
            )
            requirement = stale.odds_readiness.refresh_requirements[0]
            assert requirement.profile_id == "i1_middle_ah_away_live_long_horizon"
            assert requirement.refresh_league_code == "SERIE_A"
            assert requirement.strategy_league_code == "I1"
            assert requirement.market_type == "asian_handicap"
            assert requirement.selections == ["AH_AWAY"]
            assert requirement.required_bookmakers == 2
            assert requirement.needed_ready_matches == 1
            assert any(issue.startswith("odds_older_than_max_minutes:") for issue in requirement.issues)
            market_issues = [
                issue
                for coverage in stale.odds_readiness.market_coverages
                for issue in coverage.issues
            ]
            assert any(issue.startswith("odds_older_than_max_minutes:") for issue in market_issues)
        finally:
            repository.close()

    with TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'cli.db'}"
        subprocess.check_output(["footballctl", "db", "init", "--json"], env=env, text=True, encoding="utf-8")
        completed = subprocess.run(
            ["footballctl", "live-preflight", "--json"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        payload = json.loads(completed.stdout)
        assert "odds_readiness" in payload, "CLI preflight JSON must include odds readiness"
        assert "live_audit" in payload, "CLI preflight JSON must include live audit"
        assert payload["ready_to_bet"] is False, "empty CLI DB must not approve betting"

        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'api.db'}"
        client = TestClient(app)
        assert client.get("/live/preflight").status_code == 200
        api_payload = client.get("/live/preflight").json()
        assert "odds_readiness" in api_payload, "API preflight JSON must include odds readiness"
        assert "live_audit" in api_payload, "API preflight JSON must include live audit"

    print("live preflight verification passed")


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


def _insert_live_candidate(
    repository: StructuredRepository,
    match_id: str,
    collected_at: datetime | None = None,
) -> None:
    kickoff = datetime(2027, 1, 17, 19, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    repository.upsert_model(
        "matches",
        match_id,
        Match(
            id=match_id,
            league="Italy - Serie A",
            home_team=f"{match_id} Home",
            away_team=f"{match_id} Away",
            kickoff_at=kickoff,
            data_completeness=0.92,
        ),
    )
    for index, bookmaker in enumerate(["Bet365", "Pinnacle"]):
        repository.upsert_model(
            "odds",
            f"{match_id}-odds-{index}",
            OddsSnapshot(
                id=f"{match_id}-odds-{index}",
                match_id=match_id,
                market_type="asian_handicap",
                line="+0.5",
                source="odds_api_io",
                bookmaker=bookmaker,
                outcome_odds={"AWAY": 2.12},
                market_average={"AWAY": 1.94},
                best_price={"AWAY": 2.12},
                movement=0.018,
                collected_at=collected_at or datetime.now(timezone.utc),
            ),
        )
    repository.upsert_model(
        "findings",
        f"{match_id}-history",
        AgentFinding(
            id=f"{match_id}-history",
            match_id=match_id,
            agent_name="History Agent",
            summary="Long-horizon profile and matchup context agree with away handicap value.",
            confidence=0.78,
            score_delta=9.0,
        ),
    )


def _insert_secondary_live_candidate(repository: StructuredRepository, match_id: str) -> None:
    kickoff = datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
    repository.upsert_model(
        "matches",
        match_id,
        Match(
            id=match_id,
            league="Brazil - Brasileiro Serie A",
            home_team=f"{match_id} Home",
            away_team=f"{match_id} Away",
            kickoff_at=kickoff,
            data_completeness=0.92,
        ),
    )
    for index, bookmaker in enumerate(["Bet365", "Pinnacle"]):
        repository.upsert_model(
            "odds",
            f"{match_id}-odds-{index}",
            OddsSnapshot(
                id=f"{match_id}-odds-{index}",
                match_id=match_id,
                market_type="1x2",
                source="odds_api_io",
                bookmaker=bookmaker,
                outcome_odds={"HOME": 2.12},
                market_average={"HOME": 1.94},
                best_price={"HOME": 2.12},
                movement=0.018,
                collected_at=datetime.now(timezone.utc),
            ),
        )
    repository.upsert_model(
        "findings",
        f"{match_id}-history",
        AgentFinding(
            id=f"{match_id}-history",
            match_id=match_id,
            agent_name="History Agent",
            summary="Secondary league tier policy and matchup context agree with home value.",
            confidence=0.78,
            score_delta=9.0,
        ),
    )


if __name__ == "__main__":
    main()
