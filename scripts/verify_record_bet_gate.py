from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from football_analysis.db import StructuredRepository
from football_analysis.models import AgentFinding, BetLog, Match, OddsSnapshot
from football_analysis.service import AnalysisService
from football_analysis.settings import StrategyProfileSettings, load_settings


def main() -> None:
    with TemporaryDirectory() as tmp:
        settings = load_settings()
        settings.app.fixture_mode = False
        settings.storage.database_url = f"sqlite:///{Path(tmp) / 'verify.db'}"
        settings.strategy_profiles = [_live_i1_profile()]
        repository = StructuredRepository(settings.storage.database_url)
        repository.initialize()
        try:
            service = AnalysisService(settings, repository)

            blocked = BetLog(
                id="",
                match_id="missing-live-approval",
                market_type="asian_handicap",
                selection="AH_AWAY(+0.5)",
                odds=2.12,
                stake_units=0.4,
                platform="Bet365",
            )
            try:
                service.record_bet(blocked)
            except ValueError as exc:
                assert "live_recommendation_required" in str(exc), "real stake must require live recommendation approval"
            else:
                raise AssertionError("real stake without live approval must be rejected")

            paper = service.record_bet(
                BetLog(
                    id="",
                    match_id="paper-observation",
                    market_type="asian_handicap",
                    selection="AH_AWAY(+0.5)",
                    odds=2.12,
                    stake_units=0.4,
                    platform="paper",
                )
            )
            assert paper.id, "paper observations should remain recordable"

            _insert_live_candidate(repository, "record-live-pass")
            approved = service.analyze_match("record-live-pass").recommendation
            low_price = BetLog(
                id="",
                match_id=approved.match_id,
                market_type=approved.market_type,
                selection="AH_AWAY(+0.5)",
                odds=2.00,
                stake_units=approved.stake_units,
                platform="Bet365",
            )
            try:
                service.record_bet(low_price)
            except ValueError as exc:
                assert "execution_odds_below_minimum" in str(exc), (
                    "real stake must be rejected when execution odds lose too much approved edge"
                )
            else:
                raise AssertionError("real stake with excessive execution odds slippage must be rejected")

            recorded = service.record_bet(
                BetLog(
                    id="",
                    match_id=approved.match_id,
                    market_type=approved.market_type,
                    selection="AH_AWAY(+0.5)",
                    odds=2.12,
                    stake_units=approved.stake_units,
                    platform="Bet365",
                )
            )
            assert recorded.id, "approved live recommendation should be recordable"

            duplicate = BetLog(
                id="",
                match_id=approved.match_id,
                market_type=approved.market_type,
                selection="AH_AWAY(+0.5)",
                odds=2.12,
                stake_units=0.1,
                platform="Pinnacle",
            )
            try:
                service.record_bet(duplicate)
            except ValueError as exc:
                assert "stake_exceeds_recommendation" in str(exc), (
                    "cumulative real stake must not exceed approved size"
                )
            else:
                raise AssertionError("duplicate real-platform stake must be rejected after approved stake is filled")

            oversize = BetLog(
                id="",
                match_id=approved.match_id,
                market_type=approved.market_type,
                selection="AH_AWAY(+0.5)",
                odds=2.12,
                stake_units=approved.stake_units + 0.1,
                platform="Bet365",
            )
            try:
                service.record_bet(oversize)
            except ValueError as exc:
                assert "stake_exceeds_recommendation" in str(exc), "real stake must not exceed approved size"
            else:
                raise AssertionError("oversized real stake must be rejected")

            _insert_live_candidate(repository, "record-after-kickoff")
            late_approved = service.analyze_match("record-after-kickoff").recommendation
            late = BetLog(
                id="",
                match_id=late_approved.match_id,
                market_type=late_approved.market_type,
                selection="AH_AWAY(+0.5)",
                odds=2.12,
                stake_units=late_approved.stake_units,
                platform="Bet365",
                placed_at=datetime(2027, 1, 17, 20, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
            try:
                service.record_bet(late)
            except ValueError as exc:
                assert "live_bet_after_kickoff" in str(exc), "real stake must be recorded before kickoff"
            else:
                raise AssertionError("real stake after kickoff must be rejected")
        finally:
            repository.close()

    print("record-bet gate verification passed")


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


def _insert_live_candidate(repository: StructuredRepository, match_id: str) -> None:
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
            score_delta=12.0,
        ),
    )


if __name__ == "__main__":
    main()
