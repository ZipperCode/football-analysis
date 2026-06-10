from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from football_analysis.db import StructuredRepository
from football_analysis.live_audit import audit_live_trading
from football_analysis.models import AgentFinding, Match, OddsSnapshot, RecommendationStatus
from football_analysis.service import AnalysisService
from football_analysis.settings import StrategyProfileSettings, load_settings


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'verify.db'}"
        settings.live_trading.max_daily_stake_units = 0.6
        settings.strategy_profiles = [_live_i1_profile()]
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)
            _insert_live_candidate(repository, "allocator-a-low", score_delta=9.0)
            _insert_live_candidate(repository, "allocator-b-high", score_delta=12.0)

            first_audit = audit_live_trading(repository, settings, include_past=True)
            assert first_audit.recommended_count == 1, "fresh audit must allocate only one live pick"
            assert first_audit.items[0].match_id == "allocator-b-high", "fresh audit must allocate by score, not match id"

            picks = service.picks_today()
            assert len(picks.picks) == 1, "daily stake allocator must keep only one 0.4u pick under a 0.6u cap"
            assert picks.picks[0].match_id == "allocator-b-high", "allocator must keep the highest-value live pick first"
            assert sum(pick.stake_units for pick in picks.picks) <= 0.6, "allocated stake must stay within daily cap"

            low = service.analyze_match("allocator-a-low").recommendation
            assert low.status is RecommendationStatus.paper_candidate, "overflow candidate must be downgraded"
            assert "live_daily_planned_stake_limit:0.80/0.60" in low.risk_tags

            report = audit_live_trading(repository, settings, include_past=True)
            assert report.recommended_count == 1, "audit must count only allocated live picks"
            assert report.paper_candidate_count == 1, "audit must show the overflow candidate as paper"
            assert report.total_live_stake_units == 0.4, "audit live exposure must honor daily cap"
            assert report.gate_counts["live_daily_planned_stake_limit:0.80/0.60"] == 1
        finally:
            repository.close()

    print("live stake allocator verification passed")


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


def _insert_live_candidate(repository: StructuredRepository, match_id: str, score_delta: float) -> None:
    kickoff = datetime.now(ZoneInfo("Asia/Shanghai")).replace(hour=19, minute=45, second=0, microsecond=0)
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
            score_delta=score_delta,
        ),
    )


if __name__ == "__main__":
    main()
